"""G1T textures (Koei Tecmo, Katana Engine) – version 0060-0062, Switch 2 (platform 0x18).

Reverse engineered from Hyrule Warriors: Age of Imprisonment (magic "GT1G" = "G1TG" stored reversed):
  header   "GT1G", "0600" (version reversed), u32 file size, u32 header size, u32 texture count,
           u32 platform, u32 extra size; at header size: u32 offsets (relative to the header size)
  texture  u8 (mip count << 4) | type (0 = 2D, 1 = cube, 2 = 3D, 3 = array), u8 format, u8 (log2 height << 4) | log2 width,
           u8 0, u32 flags (0x10000000 = extra header follows, 0x02000000 = sRGB), extra header
           (u32 size, u32 ?, u32 layers << 4 in bits 4..11 ...), then the pixel data: tiled arrays store
           every layer with its whole mip chain, linear arrays mip 0 of all layers, then mip 1 ...
           (2D textures are not padded at the end, arrays per layer)
  formats  0x00-0x04 / 0x06-0x08 / 0x59-0x5F linear, 0x09-0x0D / 0x10-0x12 / 0x60-0x66 tiled (Switch block linear, GOB layout of the
           BNTX textures; block height derived from the height; every mip level is padded to whole blocks and a
           layer to 512 x block height bytes)
"""
from __future__ import annotations

import struct

import numpy as np

from . import textures as T

# code: (name, kind, bytes per block, block size, tiled) – kind is the decoder
_BC = [('BC1', 'bc1', 8), ('BC2', 'bc2', 16), ('BC3', 'bc3', 16), ('BC4', 'bc4', 8), ('BC5', 'bc5', 16),
       ('BC6H', 'bc6', 16), ('BC7', 'bc7', 16)]
FORMATS = {
    0x00: ('R8G8B8A8', 'rgba8', 4, 1, False), 0x01: ('B8G8R8A8', 'bgra8', 4, 1, False),
    0x02: ('R32 float', 'r32f', 4, 1, False), 0x03: ('R16G16B16A16 float', 'rgba16f', 8, 1, False),
    0x04: ('R32G32B32A32 float', 'rgba32f', 16, 1, False),
    0x09: ('R8G8B8A8', 'rgba8', 4, 1, True), 0x0a: ('B8G8R8A8', 'bgra8', 4, 1, True),
    0x0b: ('R32 float', 'r32f', 4, 1, True), 0x0c: ('R16G16B16A16 float', 'rgba16f', 8, 1, True),
    0x0d: ('R32G32B32A32 float', 'rgba32f', 16, 1, True),
}
for _i, (_n, _k, _b) in enumerate(_BC):
    FORMATS[0x59 + _i] = (_n, _k, _b, 4, False)
    FORMATS[0x60 + _i] = (_n, _k, _b, 4, True)
    if _i < 3:  # older codes for BC1-BC3
        FORMATS[0x06 + _i] = (_n, _k, _b, 4, False)
        FORMATS[0x10 + _i] = (_n, _k, _b, 4, True)
# decoders shared with BNTX (textures._decode_linear): kind -> BNTX type byte
_BNTX_TYPE = {'bc1': 0x1a, 'bc2': 0x1b, 'bc3': 0x1c, 'bc4': 0x1d, 'bc5': 0x1e, 'bc6': 0x1f, 'bc7': 0x20,
              'rgba8': 0x0b, 'bgra8': 0x0c, 'rgba16f': 0x15}
# DDS (DXGI) formats that keep the original data
_DXGI = {'bc1': (71, 72), 'bc2': (74, 75), 'bc3': (77, 78), 'bc4': (80, 80), 'bc5': (83, 83), 'bc6': (95, 95),
         'bc7': (98, 99), 'rgba8': (28, 29), 'bgra8': (87, 91), 'r32f': (41, 41), 'rgba16f': (10, 10),
         'rgba32f': (2, 2)}
_DIMS = {0: '2D', 1: 'Cube', 2: '3D', 3: '2D-Array'}


class G1TTexture:
    """One texture of a G1T file; mirrors the attributes of textures.TexInfo that the viewer uses."""
    __slots__ = ('index', 'name', 'fmt', 'width', 'height', 'depth', 'layers', 'mips', 'tdim', 'srgb', 'flags',
                 'offset', 'size', 'comp_sel', 'image_size')

    @property
    def ftype(self):  # for code that checks BNTX type bytes (data textures are not images)
        k = FORMATS[self.fmt][1] if self.fmt in FORMATS else ''
        return {'r32f': 0x14, 'rgba16f': 0x15, 'rgba32f': 0x15}.get(k, _BNTX_TYPE.get(k, -1))

    @property
    def kind(self):
        return 6 if self.srgb else 1

    def supported(self) -> bool:
        return self.fmt in FORMATS

    def format_name(self) -> str:
        f = FORMATS.get(self.fmt)
        base = f[0] if f else f'0x{self.fmt:02X}'
        return f'{base}{" SRGB" if self.srgb else ""}{" (tiled)" if f and f[4] else ""}'

    def to_dict(self) -> dict:
        return {'index': self.index, 'name': self.name, 'format': self.format_name(), 'format_id': f'G1T 0x{self.fmt:02X}',
                'width': self.width, 'height': self.height, 'depth': self.depth, 'layers': self.layers,
                'mips': self.mips, 'dim': _DIMS.get(self.tdim, str(self.tdim)), 'channels': 'R,G,B,A',
                'supported': self.supported(), 'image_size': self.size}


def is_g1t(raw: bytes) -> bool:
    return raw[:4] == b'GT1G'


def parse(raw: bytes) -> list[G1TTexture]:
    if not is_g1t(raw):
        raise ValueError('Not a G1T texture')
    hsize, count = struct.unpack_from('<II', raw, 0x0C)
    offsets = [hsize + struct.unpack_from('<I', raw, hsize + 4 * i)[0] for i in range(count)]
    out = []
    for i, o in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < count else len(raw)
        b0, fmt, dims = raw[o], raw[o + 1], raw[o + 2]
        flags = struct.unpack_from('<I', raw, o + 4)[0]
        pos = o + 8
        ext = b''
        if flags & 0x10000000:
            esz = struct.unpack_from('<I', raw, pos)[0]
            ext = raw[pos:pos + esz]
            pos += esz
        t = G1TTexture()
        t.index = i
        t.name = f'Texture {i}' if count > 1 else 'Texture'
        t.fmt = fmt
        t.flags = flags
        t.srgb = bool(flags & 0x02000000)
        t.mips = max(1, b0 >> 4)
        t.tdim = b0 & 15
        t.width, t.height = 1 << (dims & 15), 1 << (dims >> 4)
        if len(ext) >= 0x14:  # non power of two sizes
            ew, eh = struct.unpack_from('<II', ext, 0x0C)
            if ew and eh:
                t.width, t.height = ew, eh
        layers = (struct.unpack_from('<I', ext, 8)[0] >> 4) & 0xFF if len(ext) >= 12 else 0
        t.depth = 1
        t.layers = max(1, layers) * (6 if t.tdim == 1 else 1)  # cube maps: six faces per layer
        t.offset = pos
        t.size = end - pos
        t.comp_sel = (2, 3, 4, 5)
        t.image_size = t.size
        if t.tdim == 2 and fmt in FORMATS:  # 3D texture: as many slices as the data hold
            t.layers = max(1, t.size // max(1, _layer_stride(t)))
        out.append(t)
    return out


def _block_height_log2(hb: int, limit: int = 4) -> int:
    """Block height (in GOBs, log2) the GPU uses for a surface of hb block rows."""
    b = 0
    while b < limit and (8 << b) < hb:
        b += 1
    return b


def _levels(t: G1TTexture):
    """[(offset in layer, w, h, blocks w, blocks h, block height log2)] for every mip level, layer stride."""
    _name, _kind, bpb, blk, tiled = FORMATS[t.fmt]
    out = []
    off = 0
    base = None
    for m in range(t.mips):
        w, h = max(1, t.width >> m), max(1, t.height >> m)
        wb, hb = -(-w // blk), -(-h // blk)
        if tiled:
            bhl = _block_height_log2(hb, 4 if base is None else base)
            base = bhl if base is None else base
            size = -(-wb * bpb // 64) * -(-hb // (8 << bhl)) * (512 << bhl)
        else:
            bhl = 0
            size = wb * hb * bpb
        out.append((off, w, h, wb, hb, bhl))
        off += size
    if tiled:
        align = 512 << (base or 0)
        off = -(-off // align) * align
    return out, off


def _layer_stride(t: G1TTexture) -> int:
    return _levels(t)[1]


def linear_data(raw: bytes, t: G1TTexture, mip: int = 0, layer: int = 0):
    """Untiled block data of one mip level of one layer: (data, w, h, blocks w, blocks h)."""
    _name, _kind, bpb, _blk, tiled = FORMATS[t.fmt]
    levels, stride = _levels(t)
    mip = max(0, min(mip, t.mips - 1))
    layer = max(0, min(layer, t.layers - 1))
    off, w, h, wb, hb, bhl = levels[mip]
    if tiled or t.layers == 1:  # tiled arrays: every layer holds its whole mip chain
        start = t.offset + layer * stride + off
    else:  # linear arrays: mip 0 of every layer, then mip 1 of every layer, ...
        size = wb * hb * bpb
        start = t.offset + off * t.layers + layer * size
    data = raw[start:t.offset + t.size]
    lin = T.deswizzle(data, wb, hb, bpb, bhl) if tiled else bytes(data[:wb * hb * bpb]).ljust(wb * hb * bpb, b'\0')
    return lin, w, h, wb, hb


class _AsBntx:
    """Minimal stand-in so the BNTX decoders (textures._decode_linear) can decode G1T data."""
    def __init__(self, ftype: int, kind: int):
        self.ftype, self.kind = ftype, kind


def decode(raw: bytes, t: G1TTexture, mip: int = 0, layer: int = 0) -> np.ndarray:
    """One mip level of one layer as RGBA uint8 (h, w, 4)."""
    if not t.supported():
        raise ValueError(f'G1T format 0x{t.fmt:02X} is not supported')
    _name, kind, _bpb, _blk, _tiled = FORMATS[t.fmt]
    lin, w, h, wb, hb = linear_data(raw, t, mip, layer)
    if kind in _BNTX_TYPE:
        rgba = T._decode_linear(lin, _AsBntx(_BNTX_TYPE[kind], 5 if kind == 'rgba16f' else 1), w, h, wb, hb)
        if kind == 'bc4':  # single channel (masks): grey
            rgba[..., 1] = rgba[..., 0]
            rgba[..., 2] = rgba[..., 0]
        return np.ascontiguousarray(rgba)
    n = 1 if kind == 'r32f' else 4
    v = np.frombuffer(lin, '<f4', w * h * n).reshape(h, w, n).astype(np.float64)
    if np.nanmax(np.abs(v[..., :3])) > 1.0 or np.nanmin(v[..., :3]) < 0.0:  # data, not colours: normalise
        lo, hi = float(np.nanmin(v[..., :3])), float(np.nanmax(v[..., :3]))
        v[..., :3] = (v[..., :3] - lo) / max(1e-9, hi - lo)
    out = np.empty((h, w, 4), np.uint8)
    if n == 1:
        g = T._to8(v[..., 0])
        out[..., 0] = out[..., 1] = out[..., 2] = g
        out[..., 3] = 255
    else:
        out[:] = T._to8(v)
    return out


def dds_bytes(raw: bytes, t: G1TTexture) -> bytes:
    """DDS (DX10 header) with every mip level and layer; BC1-BC7 and plain formats keep their data."""
    if not t.supported():
        raise ValueError(f'G1T format 0x{t.fmt:02X} is not supported')
    _name, kind, bpb, blk, _tiled = FORMATS[t.fmt]
    dxgi = _DXGI[kind][1 if t.srgb else 0]
    parts = [linear_data(raw, t, m, layer)[0] for layer in range(t.layers) for m in range(t.mips)]
    compressed = blk > 1
    cube = t.tdim == 1 and t.layers % 6 == 0
    first = len(parts[0]) if compressed else t.width * bpb
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if t.mips > 1 else 0) | (0x80000 if compressed else 0x8)
    caps = 0x1000 | (0x400008 if t.mips > 1 else 0) | (0x8 if cube else 0)
    pixel_format = struct.pack('<II4s5I', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    header = (struct.pack('<7I', 124, flags, t.height, t.width, first, 0, t.mips) + bytes(44) + pixel_format
              + struct.pack('<5I', caps, 0xFE00 if cube else 0, 0, 0, 0))
    dx10 = struct.pack('<5I', dxgi, 3, 0x4 if cube else 0, t.layers // 6 if cube else t.layers, 0)
    return b'DDS ' + header + dx10 + b''.join(parts)
