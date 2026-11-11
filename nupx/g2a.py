"""Koei Tecmo skeletal animations: G2A (bit-packed cubic curves) and G1A (float cubic curves).

Own implementation; the layouts follow the community documentation of the formats (Project G1M) and were checked
against the animations of Hyrule Warriors: Age of Imprisonment. One file holds one clip.

G2A ("_A2G", version 0040): +0x0C f32 frame rate, +0x10 u32: bits 0-13 length in frames, bits 20-31 size of the
bone table / 4; +0x14 u32 size of the timing section; the bone table starts at +0x20 (+0x1C before version 0040).
  bone entry (u32): bits 0-3 track count, bits 4-13 bone id (bits 4-11 in version 0050; the ids wrap around: an id
  smaller than the previous one adds 1024 resp. 256), the rest = byte offset of the bone's tracks in the timing
  section (bits 14-31, resp. 12-31 in version 0050).
  track: u16 type (0 rotation, 1 position, 2 scale), u16 key count n, u32 index of its first data record, n u16
  key frames starting at 0, padded to 4 bytes.
  data (after the timing section): one 32-byte record per key; four u64 rows hold the coefficients c1..c4 of
  p(T) = c1 + c2 T + c3 T^2 + c4 T^3 for x, y, z: bits 60-63 exponent e, three signed 20-bit mantissas (x bits
  40-59, y 20-39, z 0-19), coefficient = mantissa * 2^(e - 15). Record k covers the frames key[k] .. key[k+1]
  (the last record up to the end of the clip), T runs from 0 to 1 over the segment.
G1A ("_A1G", version 0042): +0x10 f32 duration in seconds, +0x30 u16 bone count, bone table at +0x34 (u32 bone id,
  u32 offset / 16 of the bone's block from +0x30). Block: u32 type (2 = rotation, 4 = rotation + position,
  6 = scale + rotation + position, 8 = scale + rotation), per channel u32 key count and u32 offset / 16 from the
  block; per channel the keys' coefficients (a, b, c, d as f32) followed by the key end times (f32, seconds).
  A key covers the time since the previous key (from 0): value = a u^3 + b u^2 + c u + d, u = 0..1.
Rotations: G2A stores a rotation vector (axis x angle), G1A a quaternion (x, y, z, w). Values replace the bind pose
of the bone; positions are in centimetres (converted to metres like the models, see g1m.UNIT). Bone ids are the
global ids of the model's skeleton (G1MS table): bones are named bone_<id> on both sides.
"""
from __future__ import annotations

import struct

import numpy as np

from .g1m import UNIT

ROT, POS, SCALE = 0, 1, 2
G1A_CHANNELS = {2: (('r', 4),), 4: (('r', 4), ('t', 3)), 6: (('s', 3), ('r', 4), ('t', 3)), 8: (('s', 3), ('r', 4))}
G1A_FPS = 30.0


def is_anim(head: bytes) -> bool:
    return head[:4] in (b'_A2G', b'_A1G')


def _rotvec_quat(rv: np.ndarray) -> np.ndarray:
    """Rotation vectors (n, 3) -> quaternions (n, 4) x, y, z, w."""
    ang = np.linalg.norm(rv, axis=1, keepdims=True)
    small = ang < 1.2e-5
    s = np.where(small, 0.5, np.sin(ang * 0.5) / np.where(small, 1.0, ang))
    return np.concatenate([rv * s, np.cos(ang * 0.5)], 1)


def _coefficients(raw: bytes, offset: int, count: int) -> np.ndarray:
    """count 32-byte records -> (count, 4 coefficients, 3 axes)."""
    rows = np.frombuffer(raw, '<u8', 4 * count, offset).reshape(count, 4)
    e = (rows >> np.uint64(60)).astype(np.int64)
    out = np.empty((count, 4, 3))
    for a, shift in enumerate((40, 20, 0)):
        m = ((rows >> np.uint64(shift)) & np.uint64(0xFFFFF)).astype(np.int64)
        m = np.where(m & 0x80000, m - (1 << 20), m)
        out[:, :, a] = m * np.exp2(e - 15.0)
    return out


class Track:
    __slots__ = ('kind', 'keys', 'coefs')


class BoneAnim:
    __slots__ = ('name', 'bone_id', 'tracks')


class Clip:
    """One animation (same attributes as anim.SkelAnim where the viewer uses them)."""
    loop = False
    rot_mode = 'G2A'
    rot_supported = True

    def __init__(self, name: str, fmt: str, fps: float, frame_count: int, bones: list[BoneAnim]):
        self.name, self.format, self.fps, self.frame_count, self.bones = name, fmt, fps, frame_count, bones

    # ------------------------------------------------------------------ evaluation
    def _g2a_values(self, tr: Track, nf: int) -> np.ndarray:
        out = np.empty((nf, 3))
        keys = list(tr.keys) + [max(self.frame_count, tr.keys[-1] + 1)]
        f_all = np.arange(nf)
        for k in range(len(tr.keys)):
            k1, k2 = keys[k], keys[k + 1]
            sel = (f_all >= k1) & (f_all < k2) if k < len(tr.keys) - 1 else f_all >= k1
            if not sel.any():
                continue
            T = (f_all[sel] - k1) / max(1, k2 - k1)
            c = tr.coefs[k]
            out[sel] = c[0] + np.outer(T, c[1]) + np.outer(T * T, c[2]) + np.outer(T * T * T, c[3])
        out[f_all < keys[0]] = out[keys[0]] if keys[0] < nf else out[0]
        return out

    def _g1a_values(self, tr: Track, nf: int) -> np.ndarray:
        times = np.asarray(tr.keys, np.float64)
        t = np.arange(nf) / self.fps
        idx = np.clip(np.searchsorted(times, t, side='right'), 0, len(times) - 1)
        t0 = np.where(idx > 0, times[np.maximum(idx - 1, 0)], 0.0)
        t1 = times[idx]
        u = np.clip((t - t0) / np.where(t1 - t0 > 1e-9, t1 - t0, 1.0), 0.0, 1.0)
        a, b, c, d = (tr.coefs[idx, i] for i in range(4))
        return ((a * u + b) * u + c) * u + d

    def _values(self, tr: Track, nf: int) -> np.ndarray:
        if tr.keys is None:  # evaluated per frame already (G1A)
            return tr.coefs
        return self._g2a_values(tr, nf) if self.format == 'g2a' else self._g1a_values(tr, nf)

    def _tracks(self, ba: BoneAnim, nf: int) -> list[Track]:
        return ba.tracks

    def bake(self, skeleton=None) -> tuple[dict, np.ndarray]:
        """(header, float32 data (bones, frames, 10) = t3, q4, s3) like anim.bake."""
        nf = max(1, int(self.frame_count) + 1)
        bind = {b.name: b for b in skeleton.bones} if skeleton is not None else {}
        bones = [ba for ba in self.bones if not bind or ba.name in bind]
        data = np.zeros((len(bones), nf, 10), np.float32)
        out = []
        for bi, ba in enumerate(bones):
            sk = bind.get(ba.name)
            T = np.tile(np.asarray(sk.translate if sk is not None else (0.0, 0.0, 0.0), np.float64), (nf, 1))
            Q = np.tile(np.asarray(sk.quat() if sk is not None else (0.0, 0.0, 0.0, 1.0), np.float64), (nf, 1))
            S = np.tile(np.asarray(sk.scale if sk is not None else (1.0, 1.0, 1.0), np.float64), (nf, 1))
            animated = set()
            for tr in self._tracks(ba, nf):
                vals = self._values(tr, nf)
                if tr.kind == 'r':
                    Q = _rotvec_quat(vals) if self.format == 'g2a' else vals
                elif tr.kind == 't':
                    T = vals * UNIT
                else:
                    S = vals
                animated.add(tr.kind)
            Q = Q / np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-12)
            flip = np.concatenate([[1.0], np.sign(np.sum(Q[1:] * Q[:-1], axis=1))])  # keep consecutive keys close
            Q = Q * np.cumprod(np.where(flip == 0, 1.0, flip))[:, None]
            data[bi, :, 0:3] = T
            data[bi, :, 3:7] = Q
            data[bi, :, 7:10] = S
            out.append({'name': ba.name, 'ssc': False, 'quat': True, 'animated': sorted(animated)})
        header = {'name': self.name, 'frames': nf, 'frame_count': self.frame_count, 'fps': self.fps,
                  'loop': False, 'rot_mode': self.rot_mode, 'bones': out, 'warnings': []}
        return header, data


def _parse_g2a(raw: bytes, name: str) -> Clip:
    ver = struct.unpack_from('<I', raw, 4)[0]
    fps = struct.unpack_from('<f', raw, 12)[0] or 60.0
    packed, timing_size = struct.unpack_from('<II', raw, 16)
    length = packed & 0x3FFF
    table_size = ((packed >> 20) & 0xFFF) << 2
    start = 0x20 if ver in (0x30303430, 0x30303530) else 0x1C
    small = ver == 0x30303530
    data0 = start + table_size + timing_size
    bones: dict[int, BoneAnim] = {}
    last, wrap = -1, 0
    for i in range(table_size // 4):
        w = struct.unpack_from('<I', raw, start + 4 * i)[0]
        count = w & 0xF
        bone, toff = ((w >> 4) & 0xFF, w >> 12) if small else ((w >> 4) & 0x3FF, w >> 14)
        if bone < last:
            wrap += 1
        last = bone
        bone += wrap * (256 if small else 1024)
        q = (start + table_size + toff) & ~3
        ba = bones.get(bone)
        if ba is None:
            ba = bones[bone] = BoneAnim()
            ba.name, ba.bone_id, ba.tracks = f'bone_{bone}', bone, []
        for _ in range(count):
            kind, nk, first = struct.unpack_from('<HHI', raw, q)
            keys = struct.unpack_from(f'<{nk}H', raw, q + 8)
            q = (q + 8 + 2 * nk + 3) & ~3
            if kind > 2 or not nk:
                continue
            tr = Track()
            tr.kind, tr.keys = 'rts'[kind], keys
            tr.coefs = _coefficients(raw, data0 + 32 * first, nk)
            ba.tracks.append(tr)
    return Clip(name, 'g2a', fps, length, list(bones.values()))


def _parse_g1a(raw: bytes, name: str) -> Clip:
    duration = struct.unpack_from('<f', raw, 16)[0]
    ver = struct.unpack_from('<I', raw, 4)[0]
    nbone = struct.unpack_from('<H', raw, 48)[0]
    bones = []
    for i in range(nbone):
        bone, off = struct.unpack_from('<II', raw, 52 + 8 * i)
        block = 48 + off * 16
        kind = struct.unpack_from('<I', raw, block)[0]
        layout = G1A_CHANNELS.get(kind)
        if layout is None:
            continue
        ba = BoneAnim()
        ba.name, ba.bone_id, ba.tracks = f'bone_{bone}', bone, []
        ch = 0
        for tkind, ncomp in layout:
            comps = []
            for _c in range(ncomp):
                nk, doff = struct.unpack_from('<II', raw, block + 4 + 8 * ch)
                ch += 1
                base = block + doff * 16
                if ver > 0x30303430:
                    coefs = np.frombuffer(raw, '<f4', 4 * nk, base).reshape(nk, 4)
                    times = np.frombuffer(raw, '<f4', nk, base + 16 * nk)
                else:
                    times = np.frombuffer(raw, '<f4', nk, base)
                    coefs = np.frombuffer(raw, '<f4', 4 * nk, base + 4 * nk).reshape(nk, 4)
                comps.append((times.astype(np.float64), coefs.astype(np.float64)))
            # evaluate the components on a common time grid later: store per component
            for ci, (times, coefs) in enumerate(comps):
                tr = Track()
                tr.kind, tr.keys, tr.coefs = (tkind, ci, ncomp), times, coefs
                ba.tracks.append(tr)
        bones.append(ba)
    clip = Clip(name, 'g1a', G1A_FPS, max(1, int(round(duration * G1A_FPS))), bones)
    return clip


def parse(raw: bytes, name: str = 'clip') -> list[Clip]:
    if raw[:4] == b'_A2G':
        return [_parse_g2a(raw, name)]
    if raw[:4] == b'_A1G':
        return [_G1A(_parse_g1a(raw, name))]
    raise ValueError('Not a G1A/G2A animation')


class _G1A(Clip):
    """G1A: one scalar curve per component; per bone they are evaluated and assembled into rotation (quaternion
    x, y, z, w), position and scale tracks."""
    rot_mode = 'G1A'

    def __init__(self, c: Clip):
        super().__init__(c.name, 'g1a', c.fps, c.frame_count, c.bones)

    def _tracks(self, ba: BoneAnim, nf: int) -> list[Track]:
        groups: dict[str, list] = {}
        for tr in ba.tracks:
            kind, ci, ncomp = tr.kind
            groups.setdefault(kind, [None] * ncomp)[ci] = tr
        out = []
        for kind, comps in groups.items():
            if any(c is None for c in comps):
                continue
            t = Track()
            t.kind, t.keys = kind, None
            t.coefs = np.stack([self._g1a_values(c, nf) for c in comps], 1)
            out.append(t)
        return out
