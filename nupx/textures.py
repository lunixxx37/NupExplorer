"""BNTX textures as used by this Switch 2 game (BNTX v0x40101, NVN2).

Memory layout is NVIDIA block linear with GOBs of 64 bytes x 8 rows. On Switch 2 the
address bits inside a GOB are ordered [x0..x3, y0, y1, x4, y2, x5] (found empirically;
Switch 1 uses [x0..x3, y0, x4, y1, y2, x5]). Block height = 1 << (layout & 7) GOBs, reduced
per mip level with the same rule as Switch Toolbox.
"""
from __future__ import annotations

import io
import struct

import numpy as np
from PIL import Image

try:
    import texture2ddecoder as t2d
except ImportError:  # pragma: no cover
    t2d = None

ASTC_BLOCKS = {0x2d: (4, 4), 0x2e: (5, 4), 0x2f: (5, 5), 0x30: (6, 5), 0x31: (6, 6), 0x32: (8, 5),
               0x33: (8, 6), 0x34: (8, 8), 0x35: (10, 5), 0x36: (10, 6), 0x37: (10, 8),
               0x38: (10, 10), 0x39: (12, 10), 0x3a: (12, 12)}

# type byte -> (name, block w, block h, bytes per block)
FORMATS = {
    0x02: ('R8', 1, 1, 1), 0x07: ('R5G6B5', 1, 1, 2), 0x09: ('R8G8', 1, 1, 2), 0x0a: ('R16', 1, 1, 2),
    0x0b: ('R8G8B8A8', 1, 1, 4), 0x0c: ('B8G8R8A8', 1, 1, 4), 0x0e: ('R10G10B10A2', 1, 1, 4),
    0x0f: ('R11G11B10', 1, 1, 4), 0x15: ('R16G16B16A16', 1, 1, 8),
    0x1a: ('BC1', 4, 4, 8), 0x1b: ('BC2', 4, 4, 16), 0x1c: ('BC3', 4, 4, 16), 0x1d: ('BC4', 4, 4, 8),
    0x1e: ('BC5', 4, 4, 16), 0x1f: ('BC6H', 4, 4, 16), 0x20: ('BC7', 4, 4, 16),
}
for _t, (_bw, _bh) in ASTC_BLOCKS.items():
    FORMATS[_t] = (f'ASTC {_bw}x{_bh}', _bw, _bh, 16)
KINDS = {1: 'UNORM', 2: 'SNORM', 3: 'UINT', 4: 'SINT', 5: 'FLOAT', 6: 'SRGB', 0x0a: 'UFLOAT'}
DIMS = {0: '1D', 1: '2D', 2: '3D', 3: 'Cube', 4: '1D-Array', 5: '2D-Array', 6: '2D-MS', 7: '2D-MS-Array', 8: 'Cube-Array'}
_SEL_NAMES = {0: '0', 1: '1', 2: 'R', 3: 'G', 4: 'B', 5: 'A'}


class TexInfo:
    __slots__ = ('index', 'name', 'fmt', 'width', 'height', 'depth', 'layers', 'mips', 'layout', 'tile',
                 'comp_sel', 'tdim', 'image_size', 'mip_offsets')

    @property
    def ftype(self):
        return self.fmt >> 8

    @property
    def kind(self):
        return self.fmt & 0xFF

    def format_name(self) -> str:
        f = FORMATS.get(self.ftype)
        base = f[0] if f else f'0x{self.ftype:02X}'
        return f'{base} {KINDS.get(self.kind, hex(self.kind))}'

    def supported(self) -> bool:
        return self.ftype in FORMATS

    def to_dict(self) -> dict:
        return {'index': self.index, 'name': self.name, 'format': self.format_name(), 'format_id': f'0x{self.fmt:04X}',
                'width': self.width, 'height': self.height, 'depth': self.depth, 'layers': self.layers,
                'mips': self.mips, 'dim': DIMS.get(self.tdim, str(self.tdim)),
                'channels': ','.join(_SEL_NAMES.get(c, '?') for c in self.comp_sel),
                'supported': self.supported(), 'image_size': self.image_size}


def parse_bntx(raw: bytes) -> list[TexInfo]:
    if raw[:4] != b'BNTX':
        raise ValueError('Not a BNTX file')
    if raw[0x20:0x24] not in (b'NX  ', b'Ounc'):  # 'Ounc' = Switch 2 (codename Ounce)
        raise ValueError('BNTX: platform block missing')
    count = struct.unpack_from('<I', raw, 0x24)[0]
    info_arr = struct.unpack_from('<Q', raw, 0x28)[0]
    out = []
    for i in range(count):
        bo = struct.unpack_from('<Q', raw, info_arr + 8 * i)[0]
        if raw[bo:bo + 4] != b'BRTI':
            raise ValueError('BNTX: BRTI expected')
        t = TexInfo()
        t.index = i
        t.tile, _swz, t.mips = struct.unpack_from('<HHH', raw, bo + 0x12)
        t.fmt, _acc, t.width, t.height, t.depth, t.layers, t.layout = struct.unpack_from('<IIiiiii', raw, bo + 0x1C)
        t.image_size = struct.unpack_from('<I', raw, bo + 0x50)[0]
        cs = struct.unpack_from('<I', raw, bo + 0x58)[0]
        t.comp_sel = (cs & 0xFF, (cs >> 8) & 0xFF, (cs >> 16) & 0xFF, (cs >> 24) & 0xFF)
        t.tdim = struct.unpack_from('<I', raw, bo + 0x5C)[0]
        name_off = struct.unpack_from('<Q', raw, bo + 0x60)[0]
        n = struct.unpack_from('<H', raw, name_off)[0]
        t.name = raw[name_off + 2:name_off + 2 + n].decode('utf-8', 'replace')
        mip_ptr = struct.unpack_from('<Q', raw, bo + 0x70)[0]
        t.mips = max(1, t.mips)
        t.layers = max(1, t.layers)
        t.depth = max(1, t.depth)
        t.mip_offsets = list(struct.unpack_from(f'<{t.mips}Q', raw, mip_ptr))
        out.append(t)
    return out


# ------------------------------------------------------------------ swizzle
def deswizzle(data, wb: int, hb: int, bpp: int, bh_log2: int) -> bytes:
    bh = 1 << bh_log2
    pitch_gobs = -(-(wb * bpp) // 64)
    need = -(-hb // (8 * bh)) * 512 * bh * pitch_gobs
    buf = np.frombuffer(data, np.uint8, min(len(data), need))
    if len(buf) < need:
        buf = np.concatenate([buf, np.zeros(need - len(buf), np.uint8)])
    ys = np.arange(hb, dtype=np.int64)[:, None]
    xs = (np.arange(wb, dtype=np.int64) * bpp)[None, :]
    addr = ((ys // (8 * bh)) * 512 * bh * pitch_gobs + (xs // 64) * 512 * bh
            + ((ys % (8 * bh)) // 8) * 512
            + ((xs >> 5) & 1) * 256 + ((ys >> 2) & 1) * 128 + ((xs >> 4) & 1) * 64
            + ((ys >> 1) & 1) * 32 + (ys & 1) * 16 + (xs % 16))
    return buf[addr[..., None] + np.arange(bpp)].tobytes()


def _pow2_round_up(x: int) -> int:
    return 1 << max(0, x - 1).bit_length()


def mip_layout(t: TexInfo, mip: int):
    """(width, height, blocks w, blocks h, block-height log2) for a mip level."""
    _, bw, bh, _bpp = FORMATS[t.ftype]
    bh_log2 = t.layout & 7
    lines = (1 << bh_log2) * 8
    shift = 0
    for m in range(mip + 1):
        w = max(1, t.width >> m)
        h = max(1, t.height >> m)
        hb = -(-h // bh)
        if _pow2_round_up(hb) < lines:
            shift += 1
    return w, h, -(-w // bw), hb, max(0, bh_log2 - shift)


def choose_mip(t: TexInfo, maxdim: int | None) -> int:
    if not maxdim:
        return 0
    m = 0
    while m + 1 < t.mips and max(t.width >> m, t.height >> m) > maxdim:
        m += 1
    return m


# ------------------------------------------------------------------ decoding
def _f11(v):
    e = (v >> 6) & 0x1F
    m = v & 0x3F
    return np.where(e == 0, m / 64.0 * 2.0 ** -14, (1 + m / 64.0) * 2.0 ** (e.astype(np.float64) - 15))


def _f10(v):
    e = (v >> 5) & 0x1F
    m = v & 0x1F
    return np.where(e == 0, m / 32.0 * 2.0 ** -14, (1 + m / 32.0) * 2.0 ** (e.astype(np.float64) - 15))


def _to8(x):
    return (np.clip(x, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _snorm_fix(lin: bytes, ftype: int) -> bytes:
    """Decode SNORM BC4/BC5 with an UNORM decoder: flip the sign bit of the endpoints."""
    a = np.frombuffer(lin, np.uint8).copy().reshape(-1, 8)
    a[:, 0] ^= 0x80
    a[:, 1] ^= 0x80
    return a.tobytes()


def _decode_linear(lin: bytes, t: TexInfo, w: int, h: int, wb: int, hb: int) -> np.ndarray:
    """Return canonical RGBA uint8 (before channel swizzle), shape (h, w, 4)."""
    ft, kind = t.ftype, t.kind
    _, bw, bh, bpp = FORMATS[ft]
    pw, ph = wb * bw, hb * bh
    if ft in ASTC_BLOCKS or ft in (0x1a, 0x1c, 0x1d, 0x1e, 0x1f, 0x20):
        if t2d is None:
            raise RuntimeError('texture2ddecoder is missing')
        if ft in ASTC_BLOCKS:
            px = t2d.decode_astc(lin, pw, ph, bw, bh)
        elif ft == 0x1a:
            px = t2d.decode_bc1(lin, pw, ph)
        elif ft == 0x1c:
            px = t2d.decode_bc3(lin, pw, ph)
        elif ft == 0x1d:
            px = t2d.decode_bc4(_snorm_fix(lin, ft) if kind == 2 else lin, pw, ph)
        elif ft == 0x1e:
            px = t2d.decode_bc5(_snorm_fix(lin, ft) if kind == 2 else lin, pw, ph)
        elif ft == 0x1f:
            px = t2d.decode_bc6(lin, pw, ph)
        else:
            px = t2d.decode_bc7(lin, pw, ph)
        bgra = np.frombuffer(px, np.uint8).reshape(ph, pw, 4)[:h, :w]
        rgba = bgra[..., [2, 1, 0, 3]].copy()
        if ft == 0x1d:
            rgba[..., 1] = 0
            rgba[..., 2] = 0
            rgba[..., 3] = 255
        elif ft == 0x1e:
            rgba[..., 2] = 0
            rgba[..., 3] = 255
        return rgba
    if ft == 0x1b:  # BC2 via Pillow
        img = Image.frombytes('RGBA', (pw, ph), lin, 'bcn', 2)
        return np.asarray(img)[:h, :w].copy()
    a = np.frombuffer(lin, np.uint8, wb * hb * bpp)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., 3] = 255
    if ft == 0x02:
        out[..., 0] = a.reshape(h, w)
    elif ft == 0x09:
        a = a.reshape(h, w, 2)
        out[..., 0] = a[..., 0]
        out[..., 1] = a[..., 1]
    elif ft == 0x0a:
        v = a.view('<u2').reshape(h, w).astype(np.float64)
        if kind == 1:
            out[..., 0] = _to8(v / 65535.0)
        else:  # integer data: stretch to full range for viewing
            mx = max(1.0, float(v.max()))
            out[..., 0] = _to8(v / mx)
    elif ft == 0x07:
        v = a.view('<u2').reshape(h, w).astype(np.uint32)
        out[..., 0] = ((v >> 11) & 31) * 255 // 31
        out[..., 1] = ((v >> 5) & 63) * 255 // 63
        out[..., 2] = (v & 31) * 255 // 31
    elif ft in (0x0b, 0x0c):
        a = a.reshape(h, w, 4)
        out[:] = a[..., [2, 1, 0, 3]] if ft == 0x0c else a
    elif ft == 0x0e:
        v = a.view('<u4').reshape(h, w)
        out[..., 0] = ((v & 1023) * 255 // 1023).astype(np.uint8)
        out[..., 1] = (((v >> 10) & 1023) * 255 // 1023).astype(np.uint8)
        out[..., 2] = (((v >> 20) & 1023) * 255 // 1023).astype(np.uint8)
        out[..., 3] = ((v >> 30) * 85).astype(np.uint8)
    elif ft == 0x0f:
        v = a.view('<u4').reshape(h, w)
        r, g, b = _f11(v & 0x7FF), _f11((v >> 11) & 0x7FF), _f10((v >> 22) & 0x3FF)
        rgb = np.stack([r, g, b], -1)
        rgb = rgb / (1.0 + rgb)  # simple tone mapping for HDR data
        out[..., :3] = _to8(rgb ** (1 / 2.2))
    elif ft == 0x15:
        if kind == 5:
            v = a.view('<f2').reshape(h, w, 4).astype(np.float64)
            out[:] = _to8(v)
            if np.nanmax(np.abs(v[..., :3])) > 1.0:  # data texture (positions etc.): normalise
                c = v[..., :3]
                lo, hi = np.nanmin(c), np.nanmax(c)
                out[..., :3] = _to8((c - lo) / max(1e-9, hi - lo))
        else:
            v = a.view('<u2').reshape(h, w, 4).astype(np.float64) / 65535.0
            out[:] = _to8(v)
    else:
        raise ValueError(f'Format 0x{t.fmt:04X} not supported')
    return out


def _apply_comp_sel(rgba: np.ndarray, sel) -> np.ndarray:
    if tuple(sel) == (2, 3, 4, 5):
        return rgba
    h, w, _ = rgba.shape
    out = np.empty_like(rgba)
    for i, s in enumerate(sel):
        if s == 0:
            out[..., i] = 0
        elif s == 1:
            out[..., i] = 255
        elif 2 <= s <= 5:
            out[..., i] = rgba[..., s - 2]
        else:
            out[..., i] = rgba[..., i]
    return out


def decode(raw: bytes, t: TexInfo, mip: int = 0, layer: int = 0, swizzle: bool = True) -> np.ndarray:
    """Decode one mip level of one array layer to RGBA uint8 (h, w, 4)."""
    if not t.supported():
        raise ValueError(f'Texture format {t.format_name()} ({t.fmt:#06x}) is not supported')
    mip = max(0, min(mip, t.mips - 1))
    layer = max(0, min(layer, t.layers - 1))
    _, bw, bh, bpp = FORMATS[t.ftype]
    w, h, wb, hb, bhl = mip_layout(t, mip)
    stride = t.image_size // t.layers
    off = t.mip_offsets[mip] + layer * stride
    end = t.mip_offsets[0] + (layer + 1) * stride
    data = raw[off:max(off, end)]
    if t.tile == 0:
        lin = deswizzle(data, wb, hb, bpp, bhl)
    else:
        lin = bytes(data[:wb * hb * bpp])
    rgba = _decode_linear(lin, t, w, h, wb, hb)
    if swizzle:
        rgba = _apply_comp_sel(rgba, t.comp_sel)
    return np.ascontiguousarray(rgba)


def to_normal_map(rgba: np.ndarray, t: TexInfo) -> np.ndarray:
    """Turn a 2-channel normal map (X in R, Y in G after swizzle) into a full RGB normal map."""
    if t.comp_sel[2] not in (0, 1):
        return rgba  # blue channel carries data: already a 3-channel normal map
    x = rgba[..., 0].astype(np.float32) / 127.5 - 1.0
    y = rgba[..., 1].astype(np.float32) / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    out = np.empty_like(rgba)
    out[..., 0] = rgba[..., 0]
    out[..., 1] = rgba[..., 1]
    out[..., 2] = _to8(z * 0.5 + 0.5)
    out[..., 3] = 255
    return out


def png_bytes(rgba: np.ndarray, level: int = 1) -> bytes:
    bio = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(bio, 'PNG', compress_level=level)
    return bio.getvalue()


def has_alpha(rgba: np.ndarray) -> bool:
    return bool((rgba[..., 3] < 250).any())
