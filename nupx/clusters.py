"""Decoder for the game's compressed "cluster" geometry (mostly Land_/Obj_ map parts).

Such shapes have FSHP flag 0x10, no meshes and a blank FVTX. Their geometry lives in the model's
buffer as meshlets (<= 64 vertices / 124 triangles) for up to 16 LOD levels. Layout (reverse
engineered, verified against bounding boxes, face normals, tangent frames and texel density):

FSHP+0x18 -> cluster mesh header
  +0x10 -> (u32 block size, u32 alignment)     +0x18 -> u64 block offset (relative to the buffer)
  +0x20 -> u32[nLod] triangles per LOD          +0x28 -> u32[nLod] meshlets per LOD, f32[nLod] LOD errors
  +0x3C u32 max vertices (64), +0x40 u32 max triangles (124), +0x45 u8 nLod
block (at buffer + block offset)
  +0x00 f32[3] position origin, +0x0C f32 position step (0.0005), +0x10 u32 vertex count,
  +0x14 u32[16] cumulative meshlet count per LOD, +0x54 meshlet records, then meshlet data
meshlet record (32 bytes + 8 per extra vertex attribute; the record size follows from the data offset)
  +0x00 u64  3 x 21 bit: meshlet origin in position steps
  +0x08 u64  3 x 21 bit: extent in position steps -> bits per axis = bit_length(extent)
  +0x10 u16 ?, u8 triangle count, u8 vertex count
  +0x14 u32  bits 0-3: normal precision k (normal = 2 x (6 + k) bits octahedral), bits 4-31: data
             offset relative to the first record
  +0x18 u32  bits 0-8: bits per vertex
  +0x1C u32  2 = tangent present (1 sign bit + 2 x 8 bit octahedral), 0 = no tangent
  then per UV set:  u32 (i16 u offset, i16 v offset), u32 (bits 24-27 u bits - 1, bits 28-31 v bits - 1,
                    bits 0-3 / 12-15 exponents); uv = (offset + value) * 2^(exponent - 16)
  then per colour:  u32 low byte: bit c = 0 -> channel c stored per vertex (8 bit, or 32 bit when the
                    high nibble is not 0xF), u32 RGBA constant for the other channels
meshlet data: bit stream (LSB first): vertices x bits-per-vertex [x, y, z, normal, (sign, tangent),
  uv sets ..., colours ...], then triangles x 3 indices of bit_length(vertex count - 1) bits.
"""
from __future__ import annotations

import struct

import numpy as np


class ClusterShape:
    __slots__ = ('block', 'lod_count', 'cum', 'origin', 'step', 'rec_size', 'n_uv', 'n_col')

    def __init__(self, raw: bytes, mesh_ptr: int, data_base: int):
        size_ptr, off_ptr = struct.unpack_from('<QQ', raw, mesh_ptr + 0x10)
        self.block = data_base + struct.unpack_from('<Q', raw, off_ptr)[0]
        self.lod_count = max(1, raw[mesh_ptr + 0x45])
        b = self.block
        self.origin = np.array(struct.unpack_from('<3f', raw, b), np.float64)
        self.step = struct.unpack_from('<f', raw, b + 12)[0]
        self.cum = list(struct.unpack_from('<16I', raw, b + 0x14))
        total = self.cum[self.lod_count - 1]
        first = struct.unpack_from('<I', raw, b + 0x54 + 0x14)[0] >> 4
        self.rec_size = first // total if total else 32
        if self.rec_size < 32 or (self.rec_size - 32) % 8:
            raise ValueError(f'Unknown cluster record size {self.rec_size}')
        self.n_uv, self.n_col = self._attribute_split(raw, total)

    def records(self, raw: bytes, lod: int):
        lod = min(max(lod, 0), self.lod_count - 1)
        lo = self.cum[lod - 1] if lod > 0 else 0
        hi = self.cum[lod]
        return [raw[self.block + 0x54 + self.rec_size * i:self.block + 0x54 + self.rec_size * (i + 1)]
                for i in range(lo, hi)]

    def _attribute_split(self, raw: bytes, total: int) -> tuple[int, int]:
        """How many of the 8-byte attribute blocks are UV sets (they come before the colours):
        the split for which the bit count per vertex adds up in every record."""
        nblk = (self.rec_size - 32) // 8
        votes = [0] * (nblk + 1)
        for i in range(min(total, 64)):
            rec = raw[self.block + 0x54 + self.rec_size * i:self.block + 0x54 + self.rec_size * (i + 1)]
            for n_uv in range(nblk + 1):
                if _vertex_bits(rec, n_uv, nblk - n_uv) == (struct.unpack_from('<I', rec, 0x18)[0] & 0x1FF):
                    votes[n_uv] += 1
        n_uv = max(range(nblk + 1), key=lambda n: (votes[n], -abs(n - _uv_pattern_count(raw, self))))
        if votes[n_uv] == 0:
            raise ValueError('Cluster geometry vertex layout does not match')
        return n_uv, nblk - n_uv


def _uv_pattern_count(raw: bytes, cs: ClusterShape) -> int:
    """Number of leading attribute blocks that look like UV descriptors (tie breaker only)."""
    rec = raw[cs.block + 0x54:cs.block + 0x54 + cs.rec_size]
    n = 0
    for j in range((cs.rec_size - 32) // 8):
        hi = struct.unpack_from('<I', rec, 36 + 8 * j)[0]
        if (hi & 0x00FF0FF0) == 0 and (hi >> 12) & 15 and hi & 15:
            n += 1
        else:
            break
    return n


def _colour_bits(lo: int) -> int:
    varying = sum(1 for c in range(4) if not (lo >> c) & 1)
    return varying * (8 if (lo >> 4) & 15 == 15 else 32)


def _layout(rec: bytes, n_uv: int, n_col: int):
    """Per-vertex field layout: list of (name, bit offset, bit width)."""
    q1 = int.from_bytes(rec[8:16], 'little')
    ext = [(q1 >> s) & 0x1FFFFF for s in (0, 21, 42)]
    k = rec[0x14] & 15
    tangent = struct.unpack_from('<I', rec, 0x1C)[0] == 2
    nb = 6 + k
    fields = []
    pos = 0
    for a, e in zip('xyz', ext):
        fields.append((a, pos, e.bit_length()))
        pos += e.bit_length()
    fields += [('nx', pos, nb), ('ny', pos + nb, nb)]
    pos += 2 * nb
    if tangent:
        fields += [('ts', pos, 1), ('tx', pos + 1, 8), ('ty', pos + 9, 8)]
        pos += 17
    for j in range(n_uv):
        hi = struct.unpack_from('<I', rec, 36 + 8 * j)[0]
        wu, wv = ((hi >> 24) & 15) + 1, ((hi >> 28) & 15) + 1
        fields += [(f'u{j}', pos, wu), (f'v{j}', pos + wu, wv)]
        pos += wu + wv
    for j in range(n_col):
        lo = struct.unpack_from('<I', rec, 32 + 8 * (n_uv + j))[0]
        w = 8 if (lo >> 4) & 15 == 15 else 32
        for c in range(4):
            if not (lo >> c) & 1:
                fields.append((f'c{j}{c}', pos, w))
                pos += w
    return fields, pos


def _vertex_bits(rec: bytes, n_uv: int, n_col: int) -> int:
    return _layout(rec, n_uv, n_col)[1]


def _bits_le(block: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(block, np.uint8), bitorder='little')


def _read(bitarr: np.ndarray, starts: np.ndarray, width: int) -> np.ndarray:
    if width == 0:
        return np.zeros(len(starts), np.int64)
    idx = starts[:, None] + np.arange(width)[None, :]
    return (bitarr[idx].astype(np.int64) << np.arange(width)[None, :]).sum(1)


def _oct_decode(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    z = 1.0 - np.abs(x) - np.abs(y)
    t = np.clip(-z, 0.0, None)
    x = x + np.where(x >= 0, -t, t)
    y = y + np.where(y >= 0, -t, t)
    n = np.stack([x, y, z], 1)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def decode(raw: bytes, cs: ClusterShape, lod: int = 0):
    """(positions float64 Nx3, normals Nx3, uv0 Nx2 or None, indices Mx3 uint32) of one LOD."""
    P, N, UV, I = [], [], [], []
    base = 0
    first_data = cs.block + 0x54
    for rec in cs.records(raw, lod):
        nv = rec[0x13]
        nt = rec[0x12]
        bpv = struct.unpack_from('<I', rec, 0x18)[0] & 0x1FF
        fields, total = _layout(rec, cs.n_uv, cs.n_col)
        if total != bpv:
            raise ValueError(f'Cluster vertex size {total} != {bpv}')
        ib = max(1, (nv - 1).bit_length())
        nbits = nv * bpv + nt * 3 * ib
        off = first_data + (struct.unpack_from('<I', rec, 0x14)[0] >> 4)
        bitarr = _bits_le(raw[off:off + (nbits + 7) // 8])
        vstart = np.arange(nv, dtype=np.int64) * bpv
        f = {name: (o, w) for name, o, w in fields}
        q0 = int.from_bytes(rec[0:8], 'little')
        origin = np.array([(q0 >> s) & 0x1FFFFF for s in (0, 21, 42)], np.int64)
        q = np.stack([_read(bitarr, vstart + f[a][0], f[a][1]) for a in 'xyz'], 1) + origin
        P.append(q * cs.step + cs.origin)
        nb = f['nx'][1]
        scale = float((1 << nb) - 1)
        N.append(_oct_decode(_read(bitarr, vstart + f['nx'][0], nb) / scale * 2 - 1,
                             _read(bitarr, vstart + f['ny'][0], nb) / scale * 2 - 1))
        if cs.n_uv:
            u_off, v_off, hi = struct.unpack_from('<hhI', rec, 32)
            eu, ev = hi & 15, (hi >> 12) & 15
            u = (u_off + _read(bitarr, vstart + f['u0'][0], f['u0'][1])) * 2.0 ** (eu - 16)
            v = (v_off + _read(bitarr, vstart + f['v0'][0], f['v0'][1])) * 2.0 ** (ev - 16)
            UV.append(np.stack([u, v], 1))
        istart = nv * bpv + np.arange(nt * 3, dtype=np.int64) * ib
        I.append(_read(bitarr, istart, ib).reshape(-1, 3) + base)
        base += nv
    if not P:
        return None
    return (np.concatenate(P), np.concatenate(N), np.concatenate(UV) if UV else None,
            np.concatenate(I).astype(np.uint32))
