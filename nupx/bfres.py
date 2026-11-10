"""BFRES v10.4 model reader (FMDL, FVTX, FSHP, FMAT, FSKL) as found in this game.

Offsets (relative to each structure):
  FRES   +0x28 model array, +0x30 model dict, +0x58 skeletal anims, +0xB0 buffer info
         (BufferInfo: u32, u32 size, u64 data offset)
  FMDL   0x78: +0x08 name, +0x18 FSKL, +0x20 FVTX[], +0x28 FSHP[], +0x38 FMAT[],
         +0x68 u16 nVertexBuffer, nShape, nMaterial
  FVTX   0x58: +0x08 attribs (0x10: name, u32 format, u16 offset, u16 buffer), +0x30 buffer sizes,
         +0x38 strides (0x10 each), +0x48 u32 data offset, +0x4C u8 nAttrib, u8 nBuffer,
         +0x50 u32 vertex count, +0x54 u8 skin count
  FSHP   0x60: +0x08 name, +0x18 meshes, +0x20 skin bone indices (u16), +0x50 u16 index,
         material, bone, vertex buffer, +0x58 u16 nSkinBone, u8 skinCount, u8 nMesh
  mesh   0x38: +0x20 u32 index offset, +0x24 primitive (3 = triangles), +0x28 index format
         (0 u8, 1 u16, 2 u32), +0x2C count, +0x30 first vertex (indices are relative)
  FMAT   0xB0: +0x08 name, +0x10 shader info (+0x00 -> ShaderAssign), +0x20 texture names,
         +0x38 sampler dict, +0x40 render-info string values, +0x58 shader param data, +0xA2 u8 nTexture
  ShaderAssign: +0x00 archive name, +0x08 shading model, +0x10 render infos (0x10: u64 name hash,
         u8 type, 2 = string), +0x20 shader params (0x18: u64 0, u64 name hash, u16 data offset, u16 type),
         +0x48 u16 nRenderInfo, nParam, param data size. Names are only stored as 64-bit hashes.
  FSKL   +0x04 flags, +0x10 bones (0x58), +0x18 matrix->bone (u16), +0x20 inverse binds (3x4),
         +0x38 u16 nBone, nSmooth, nRigid
  bone   0x58: +0x00 name, +0x20 i16 index, parent, smooth, rigid, +0x2C flags,
         +0x30 scale, +0x3C rotation (Euler XYZ + 1.0 or quaternion), +0x4C translation
Euler rotations compose as R = Rz*Ry*Rx. Blend index _i0 indexes the matrix->bone list.
"""
from __future__ import annotations

import struct

import numpy as np

from .clusters import ClusterShape
from .clusters import decode as decode_cluster


def _str(raw: bytes, off: int) -> str:
    if not off:
        return ''
    n = struct.unpack_from('<H', raw, off)[0]
    return raw[off + 2:off + 2 + n].decode('utf-8', 'replace')


def _dict_keys(raw: bytes, off: int) -> list[str]:
    if not off:
        return []
    n = struct.unpack_from('<I', raw, off + 4)[0]
    return [_str(raw, struct.unpack_from('<Q', raw, off + 8 + 16 * (i + 1) + 8)[0]) for i in range(n)]


def euler_matrix(x: float, y: float, z: float) -> np.ndarray:
    cx, sx = np.cos(x), np.sin(x)
    cy, sy = np.cos(y), np.sin(y)
    cz, sz = np.cos(z), np.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def quat_matrix(q) -> np.ndarray:
    x, y, z, w = q
    n = np.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def euler_to_quat(x: float, y: float, z: float):
    cx, sx = np.cos(x / 2), np.sin(x / 2)
    cy, sy = np.cos(y / 2), np.sin(y / 2)
    cz, sz = np.cos(z / 2), np.sin(z / 2)
    return (cz * cy * sx - sz * sy * cx, cz * sy * cx + sz * cy * sx,
            sz * cy * cx - cz * sy * sx, cz * cy * cx + sz * sy * sx)


class Bone:
    __slots__ = ('name', 'index', 'parent', 'smooth', 'rigid', 'flags', 'scale', 'rotate', 'translate',
                 'rot_is_quat', 'ssc', 'local', 'world')

    def quat(self):
        if self.rot_is_quat:
            x, y, z, w = self.rotate
            n = np.sqrt(x * x + y * y + z * z + w * w) or 1.0
            return (x / n, y / n, z / n, w / n)
        return euler_to_quat(*self.rotate[:3])


class Skeleton:
    def __init__(self, raw: bytes, off: int):
        self.flags = struct.unpack_from('<I', raw, off + 4)[0]
        bones_off, m2b_off, _inv_off = struct.unpack_from('<QQQ', raw, off + 0x10)
        nb, nsm, nrg = struct.unpack_from('<HHH', raw, off + 0x38)
        self.num_smooth = nsm
        self.num_rigid = nrg
        self.m2b = np.frombuffer(raw, '<u2', nsm + nrg, m2b_off).astype(np.int64) if m2b_off and nsm + nrg else np.zeros(0, np.int64)
        self.bones: list[Bone] = []
        for i in range(nb):
            o = bones_off + i * 0x58
            b = Bone()
            b.name = _str(raw, struct.unpack_from('<Q', raw, o)[0])
            b.index, b.parent, b.smooth, b.rigid = struct.unpack_from('<hhhh', raw, o + 0x20)
            b.flags = struct.unpack_from('<I', raw, o + 0x2C)[0]
            b.scale = struct.unpack_from('<3f', raw, o + 0x30)
            b.rotate = struct.unpack_from('<4f', raw, o + 0x3C)
            b.translate = struct.unpack_from('<3f', raw, o + 0x4C)
            mode = (b.flags & 0x7000) or (self.flags & 0x7000)
            b.rot_is_quat = mode != 0x1000
            b.ssc = bool(b.flags & 0x00800000)
            self.bones.append(b)
        for b in self.bones:
            r = quat_matrix(b.rotate) if b.rot_is_quat else euler_matrix(*b.rotate[:3])
            m = np.eye(4)
            m[:3, :3] = r * np.asarray(b.scale)
            m[:3, 3] = b.translate
            b.local = m
        for b in self.bones:  # parents always precede children
            b.world = b.local if b.parent < 0 else self.bones[b.parent].world @ b.local

    def by_name(self) -> dict[str, Bone]:
        return {b.name: b for b in self.bones}


class Attr:
    __slots__ = ('name', 'fmt', 'offset', 'buffer')


class VertexBuffer:
    def __init__(self, raw: bytes, off: int, data_base: int):
        attr_arr = struct.unpack_from('<Q', raw, off + 0x08)[0]
        size_arr, stride_arr = struct.unpack_from('<QQ', raw, off + 0x30)
        buf_off = struct.unpack_from('<I', raw, off + 0x48)[0]
        nattr, nbuf = raw[off + 0x4C], raw[off + 0x4D]
        self.count = struct.unpack_from('<I', raw, off + 0x50)[0]
        self.skin_count = raw[off + 0x54]
        self.attrs: dict[str, Attr] = {}
        for i in range(nattr):
            o = attr_arr + i * 0x10
            a = Attr()
            a.name = _str(raw, struct.unpack_from('<Q', raw, o)[0])
            a.fmt = struct.unpack_from('<I', raw, o + 8)[0] & 0xFFFF
            a.offset, a.buffer = struct.unpack_from('<HH', raw, o + 0x0C)
            self.attrs[a.name] = a
        self.buffers = []
        pos = data_base + buf_off
        for i in range(nbuf):
            size = struct.unpack_from('<I', raw, size_arr + 0x10 * i)[0]
            stride = struct.unpack_from('<I', raw, stride_arr + 0x10 * i)[0]
            self.buffers.append((pos, size, stride))
            pos += size
            pos = (pos + 7) & ~7
        self.raw = raw

    _LAYOUT = {0x02: (1, 1), 0x09: (1, 2), 0x0A: (2, 1), 0x0B: (1, 4), 0x12: (2, 2), 0x14: (4, 1),
               0x15: (2, 4), 0x17: (4, 2), 0x18: (4, 3), 0x19: (4, 4)}

    def read(self, name: str, start: int = 0, count: int | None = None) -> np.ndarray | None:
        a = self.attrs.get(name)
        if a is None:
            return None
        if count is None:
            count = self.count - start
        pos, size, stride = self.buffers[a.buffer]
        base = pos + start * stride + a.offset
        layout, kind = a.fmt >> 8, a.fmt & 0xFF
        if layout == 0x0E:  # 10_10_10_2 packed
            v = np.ndarray((count,), '<u4', self.raw, base, (stride,)).astype(np.int64)
            comps = [(v >> s) & 0x3FF for s in (0, 10, 20)] + [(v >> 30) & 3]
            if kind == 2:
                xyz = [np.where(c >= 512, c - 1024, c) / 511.0 for c in comps[:3]]
                w = np.where(comps[3] >= 2, comps[3] - 4, comps[3]).astype(np.float64)
                return np.clip(np.stack(xyz + [w], 1), -1, 1).astype(np.float32)
            return np.stack([c / 1023.0 for c in comps[:3]] + [comps[3] / 3.0], 1).astype(np.float32)
        if layout not in self._LAYOUT:
            raise ValueError(f'Vertex format 0x{a.fmt:04X} ({name}) not supported')
        size_c, n = self._LAYOUT[layout]
        if kind == 5:
            dt = {2: '<f2', 4: '<f4'}[size_c]
        elif kind in (2, 4, 8):
            dt = {1: 'i1', 2: '<i2', 4: '<i4'}[size_c]
        else:
            dt = {1: 'u1', 2: '<u2', 4: '<u4'}[size_c]
        arr = np.ndarray((count, n), dt, self.raw, base, (stride, size_c))
        if kind == 1:
            return (arr / float((1 << (8 * size_c)) - 1)).astype(np.float32)
        if kind == 2:
            return np.maximum(arr / float((1 << (8 * size_c - 1)) - 1), -1.0).astype(np.float32)
        if kind == 5:
            return arr.astype(np.float32)
        return arr.astype(np.int64) if kind in (3, 4) else arr.astype(np.float32)


class Mesh:
    __slots__ = ('index_offset', 'prim', 'index_fmt', 'count', 'first_vertex')


class Shape:
    """meshes: classic index buffers per LOD; cluster: compressed meshlet geometry (see clusters.py)."""
    __slots__ = ('name', 'index', 'material', 'bone', 'vb', 'skin_count', 'skin_bones', 'meshes', 'cluster',
                 'cluster_error')


# Render-info and shader-param names are stored as 64-bit hashes; these were identified by comparing
# materials across all models of the game (values in brackets):
RI_SHADOW_ONLY = bytes.fromhex('c449e543484e03dc')  # '1' = only drawn into shadow maps (shadow proxies)
RI_BLEND_MODE = bytes.fromhex('9637173a98b4d882')   # 'none' / 'color' (blending on)
# three colours of the uber shader; for materials without a colour texture they carry the surface colour
# (gems, liquids, glass, horse mane ...); textured materials mostly leave them white
P_COLORS = (bytes.fromhex('ad91cd2be731e168'), bytes.fromhex('d8c1e8467d2747e6'),
            bytes.fromhex('6e1f54c64e468701'))
_PARAM_FLOATS = {12: 1, 13: 2, 14: 3, 15: 4}  # shader param types float .. float4


class Material:
    """+0x40 holds the render-info string values; among them the render state mode
    ('opaque' / 'mask' / 'translucent' / 'custom') and the displayed faces ('front' / 'both')."""
    __slots__ = ('name', 'textures', 'samplers', 'render', 'render_info', 'colors')

    @property
    def shadow_only(self) -> bool:
        """Invisible helper geometry: shadow proxies, light blockers (drawn only into shadow maps)."""
        return self.render_info.get(RI_SHADOW_ONLY) == '1'

    @property
    def blends(self) -> bool:
        """Colour blending enabled in the render state (alpha from the shader)."""
        return self.render_info.get(RI_BLEND_MODE) == 'color'

    @property
    def is_water(self) -> bool:
        """Custom render state without blending: the water shader (refraction) – drawn translucent."""
        return 'custom' in self.render and self.render_info.get(RI_BLEND_MODE) == 'none'

    def palette_color(self) -> list[float] | None:
        """Surface colour from the shader's colour parameters (linear RGB), None if they are unused (white)."""
        cols = [c[:3] for c in self.colors if len(c) >= 3]
        if not cols or all(abs(x - 1.0) < 1e-3 for c in cols for x in c):
            return None
        return [float(min(1.0, max(0.0, sum(c[i] for c in cols) / len(cols)))) for i in range(3)]

    @property
    def render_mode(self) -> str | None:
        vals = set(self.render)
        if 'translucent' in vals or any(v.lower().startswith('xlu') for v in vals):
            return 'BLEND'
        if 'mask' in vals:
            return 'MASK'
        if 'custom' in vals:  # custom render state: blended (e.g. additive eye gloss) if a blend mode is set
            return 'BLEND' if ('color' in vals or 'add' in vals) else 'OPAQUE'
        if 'opaque' in vals:
            return 'OPAQUE'
        return None

    @property
    def double_sided(self) -> bool:
        vals = set(self.render)
        if 'both' in vals:
            return True
        if 'front' in vals:
            return False
        return True

    def texture_for(self, sampler_prefix: str, suffixes: tuple[str, ...]) -> str | None:
        for s, t in zip(self.samplers, self.textures):
            if s == sampler_prefix:
                return t
        for t in self.textures:
            if t.endswith(suffixes):
                return t
        return None

    def albedo(self):
        """Colour texture: only textures on albedo slots (_a0, _a1 ...). Other slots can hold
        textures named *_Alb that are masks (e.g. eye shadow on _gn3, stored in the red channel)."""
        if self.samplers:
            named = dict(zip(self.samplers, self.textures))
            if '_a0' in named:
                return named['_a0']
            for s, t in zip(self.samplers, self.textures):
                if s.startswith('_a') and s[2:].isdigit():
                    return t
            return None
        for t in self.textures:  # no sampler names: fall back to the naming convention
            if t.endswith(('_Alb', '_alb', '_BaseColor')):
                return t
        return None

    def eye_layers(self):
        """(iris, sclera, mask) for eye materials that blend two albedo layers through an iris mask.
        Only eye materials: terrain/wall blend materials have the same slot layout."""
        named = dict(zip(self.samplers, self.textures))
        a0, a1 = named.get('_a0'), named.get('_a1')
        if not (a0 and a1):
            return None
        mask = next((t for s, t in named.items() if s.startswith('_gn') and 'Mask' in t and 'Eye' in t), None)
        return (a0, a1, mask) if mask else None

    def ramp_textures(self) -> list[str]:
        """Textures on generic slots (_gn*); colour ramps there replace a missing albedo (hair)."""
        return [t for s, t in zip(self.samplers, self.textures) if s.startswith('_gn')]

    def normal(self):
        return self.texture_for('_n0', ('_Nrm', '_nrm'))

    def emissive(self):
        """Emission map (*_Emm on the _e0 / _em0 slot): lights, screens, boost rings. Other emission
        slots (_ec0 *_Emc, _elfil0 *_ElFil) are masks the shader combines with colours."""
        for s, t in zip(self.samplers, self.textures):
            if s in ('_e0', '_em0') and t.rpartition('_')[2].startswith('Emm'):
                return t
        return None


def _shader_values(raw: bytes, fmat: int, render: list[str]):
    """({render-info name hash: string value}, [colour params]) of one material; empty on odd layouts."""
    info: dict[bytes, str] = {}
    colors: list[tuple] = []
    try:
        si = struct.unpack_from('<Q', raw, fmat + 0x10)[0]
        sa = struct.unpack_from('<Q', raw, si)[0] if si and si + 8 <= len(raw) else 0
        if not sa or sa + 0x4E > len(raw):
            return info, colors
        rarr, parr = struct.unpack_from('<Q', raw, sa + 0x10)[0], struct.unpack_from('<Q', raw, sa + 0x20)[0]
        nri, npar, dsize = struct.unpack_from('<HHH', raw, sa + 0x48)
        if rarr and rarr + 16 * nri <= len(raw):
            # the string values at FMAT+0x40 follow the string-typed render infos in order
            names = [raw[rarr + 16 * k:rarr + 16 * k + 8] for k in range(nri) if raw[rarr + 16 * k + 8] == 2]
            info = dict(zip(names, render))
        data = struct.unpack_from('<Q', raw, fmat + 0x58)[0]
        if parr and data and parr + 24 * npar <= len(raw) and data + dsize <= len(raw):
            found = {}
            for k in range(npar):
                e = parr + 24 * k
                h = raw[e + 8:e + 16]
                if h in P_COLORS:
                    off, typ = struct.unpack_from('<HH', raw, e + 16)
                    n = _PARAM_FLOATS.get(typ, 0)
                    if n >= 3 and off + 4 * n <= dsize:
                        found[h] = struct.unpack_from(f'<{n}f', raw, data + off)
            colors = [found[h] for h in P_COLORS if h in found]
    except struct.error:
        pass
    return info, colors


def _string_list(raw: bytes, ptr: int, lo: int, hi: int, limit: int = 64) -> list[str]:
    """Consecutive string pointers starting at ptr (stops at the first non-string entry)."""
    out = []
    if not ptr or ptr + 8 > len(raw):
        return out
    for j in range(limit):
        if ptr + 8 * j + 8 > len(raw):
            break
        q = struct.unpack_from('<Q', raw, ptr + 8 * j)[0]
        if not (lo <= q < hi) or q + 2 > len(raw):
            break
        n = struct.unpack_from('<H', raw, q)[0]
        s = raw[q + 2:q + 2 + n]
        if n > 128 or not all(32 <= c < 127 for c in s):
            break
        out.append(s.decode('ascii'))
    return out


class Model:
    def __init__(self, raw: bytes, off: int, data_base: int, str_lo: int = 0, str_hi: int = 0):
        self.name = _str(raw, struct.unpack_from('<Q', raw, off + 0x08)[0])
        skl = struct.unpack_from('<Q', raw, off + 0x18)[0]
        fvtx, fshp = struct.unpack_from('<QQ', raw, off + 0x20)
        fmat = struct.unpack_from('<Q', raw, off + 0x38)[0]
        nvb, nsh, nmat = struct.unpack_from('<HHH', raw, off + 0x68)
        self.skeleton = Skeleton(raw, skl) if skl and raw[skl:skl + 4] == b'FSKL' else None
        self.vbs = [VertexBuffer(raw, fvtx + i * 0x58, data_base) for i in range(nvb)]
        # some models (mostly Land_/Obj_ map parts) keep their geometry as compressed meshlets inside the
        # buffer: the FVTX headers are blank, the shapes (flag 0x10) have no meshes -> clusters.py
        self.compressed_geometry = any(raw[fvtx + i * 0x58:fvtx + i * 0x58 + 4] != b'FVTX' for i in range(nvb))
        self.materials: list[Material] = []
        for i in range(nmat):
            o = fmat + i * 0xB0
            m = Material()
            m.name = _str(raw, struct.unpack_from('<Q', raw, o + 0x08)[0])
            ntex = raw[o + 0xA2]
            ta = struct.unpack_from('<Q', raw, o + 0x20)[0]
            m.textures = [_str(raw, struct.unpack_from('<Q', raw, ta + 8 * j)[0]) for j in range(ntex)] if ta else []
            m.samplers = _dict_keys(raw, struct.unpack_from('<Q', raw, o + 0x38)[0])
            m.render = _string_list(raw, struct.unpack_from('<Q', raw, o + 0x40)[0], str_lo, str_hi)
            m.render_info, m.colors = _shader_values(raw, o, m.render)
            self.materials.append(m)
        self.shapes: list[Shape] = []
        for i in range(nsh):
            o = fshp + i * 0x60
            s = Shape()
            s.name = _str(raw, struct.unpack_from('<Q', raw, o + 0x08)[0])
            mesh_arr, skin_arr = struct.unpack_from('<QQ', raw, o + 0x18)
            s.index, s.material, s.bone, s.vb = struct.unpack_from('<HHHH', raw, o + 0x50)
            nskin = struct.unpack_from('<H', raw, o + 0x58)[0]
            s.skin_count = raw[o + 0x5A]
            nmesh = raw[o + 0x5B]
            s.skin_bones = list(struct.unpack_from(f'<{nskin}H', raw, skin_arr)) if skin_arr and nskin else []
            s.meshes = []
            s.cluster = None
            s.cluster_error = None
            if raw[o + 4] & 0x10 and mesh_arr and not nmesh:  # compressed meshlet geometry
                try:
                    s.cluster = ClusterShape(raw, mesh_arr, data_base)
                except (ValueError, struct.error, ZeroDivisionError) as e:
                    s.cluster_error = str(e)
            for k in range(nmesh):
                mo = mesh_arr + k * 0x38
                m = Mesh()
                m.index_offset, m.prim, m.index_fmt, m.count, m.first_vertex = struct.unpack_from('<IIIII', raw, mo + 0x20)
                m.index_offset += data_base
                s.meshes.append(m)
            self.shapes.append(s)
        self.raw = raw

    @property
    def lod_count(self) -> int:
        return max((s.cluster.lod_count if s.cluster is not None else len(s.meshes) for s in self.shapes), default=0)

    def indices(self, m: Mesh) -> np.ndarray:
        dt = {0: 'u1', 1: '<u2', 2: '<u4'}.get(m.index_fmt)
        if dt is None:
            raise ValueError(f'Unknown index format {m.index_fmt}')
        return np.frombuffer(self.raw, dt, m.count, m.index_offset).astype(np.int64)


def parse_models(raw: bytes) -> list[Model]:
    if raw[:4] != b'FRES':
        raise ValueError('Not a BFRES file')
    marr, mdict = struct.unpack_from('<QQ', raw, 0x28)
    n = struct.unpack_from('<I', raw, mdict + 4)[0] if mdict else 0
    binfo = struct.unpack_from('<Q', raw, 0xB0)[0]
    data_base = struct.unpack_from('<Q', raw, binfo + 8)[0] if binfo else 0
    # string pool: from the string table pointer up to the vertex/index data (or the relocation table)
    str_lo = struct.unpack_from('<Q', raw, 0xD0)[0]
    str_hi = data_base if data_base > str_lo else struct.unpack_from('<I', raw, 0x18)[0]
    return [Model(raw, marr + i * 0x78, data_base, str_lo, str_hi)
            for i in range(n) if raw[marr + i * 0x78:marr + i * 0x78 + 4] == b'FMDL']


class Geometry:
    """Triangle mesh of one shape at one LOD, in model space, ready for glTF."""
    __slots__ = ('positions', 'normals', 'uv0', 'uv1', 'joints', 'weights', 'indices', 'warnings')


def shape_geometry(model: Model, shape: Shape, lod: int = 0) -> Geometry | None:
    g = Geometry()
    g.warnings = []
    if shape.cluster is not None:  # compressed meshlets
        dec = decode_cluster(model.raw, shape.cluster, lod)
        if dec is None:
            return None
        pos, nrm, uv0, idx = dec
        count = len(pos)
        g.indices = idx.reshape(-1)
        g.uv0 = uv0.astype(np.float32) if uv0 is not None else None
        g.uv1 = None
        vb = None
    else:
        if not shape.meshes:
            return None
        mesh = shape.meshes[min(lod, len(shape.meshes) - 1)]
        if mesh.prim != 3:
            return None
        vb = model.vbs[shape.vb]
        idx = model.indices(mesh)
        if not len(idx):
            return None
        lo, hi = int(idx.min()), int(idx.max())
        start = mesh.first_vertex + lo
        count = hi - lo + 1
        g.indices = (idx - lo).astype(np.uint32)
        pos = vb.read('_p0', start, count)
        if pos is None:
            return None
        pos = pos[:, :3].astype(np.float64)
        nrm = vb.read('_n0', start, count)
        nrm = nrm[:, :3].astype(np.float64) if nrm is not None else None
        uv0 = vb.read('_u0', start, count)
        uv1 = vb.read('_u1', start, count)
        g.uv0 = uv0[:, :2] if uv0 is not None else None
        g.uv1 = uv1[:, :2] if uv1 is not None else None
    skel = model.skeleton
    sc = shape.skin_count if vb is not None else 0  # cluster geometry is rigid
    if skel is None or not skel.bones:
        g.joints = None
        g.weights = None
    elif sc == 0:
        bone = skel.bones[shape.bone] if shape.bone < len(skel.bones) else skel.bones[0]
        w = bone.world
        pos = pos @ w[:3, :3].T + w[:3, 3]
        if nrm is not None:
            nrm = nrm @ w[:3, :3].T
        g.joints = np.full((count, 4), bone.index, np.uint16)
        g.weights = np.tile(np.array([1, 0, 0, 0], np.float32), (count, 1))
    else:
        i0 = vb.read('_i0', start, count)
        i1 = vb.read('_i1', start, count)
        if i0 is None:
            ind = np.zeros((count, 1), np.int64)
        else:
            ind = i0.astype(np.int64)
            if i1 is not None:
                ind = np.concatenate([ind, i1.astype(np.int64)], 1)
        ind = ind[:, :sc]
        m2b = skel.m2b
        bones_idx = m2b[np.clip(ind, 0, max(0, len(m2b) - 1))] if len(m2b) else np.zeros_like(ind)
        if sc == 1:
            worlds = np.stack([b.world for b in skel.bones])
            wm = worlds[bones_idx[:, 0]]
            pos = np.einsum('nij,nj->ni', wm[:, :3, :3], pos) + wm[:, :3, 3]
            if nrm is not None:
                nrm = np.einsum('nij,nj->ni', wm[:, :3, :3], nrm)
            g.joints = np.zeros((count, 4), np.uint16)
            g.joints[:, 0] = bones_idx[:, 0]
            g.weights = np.tile(np.array([1, 0, 0, 0], np.float32), (count, 1))
        else:
            w0 = vb.read('_w0', start, count)
            w1 = vb.read('_w1', start, count)
            if w0 is None:
                wts = np.full(ind.shape, 1.0 / sc, np.float32)
            else:
                wts = w0.astype(np.float32)
                if w1 is not None:
                    wts = np.concatenate([wts, w1.astype(np.float32)], 1)
                wts = wts[:, :sc]
            if wts.shape[1] > 4:  # keep the 4 strongest influences
                order = np.argsort(-wts, axis=1)[:, :4]
                wts = np.take_along_axis(wts, order, 1)
                bones_idx = np.take_along_axis(bones_idx, order, 1)
            k = wts.shape[1]
            J = np.zeros((count, 4), np.uint16)
            W = np.zeros((count, 4), np.float32)
            J[:, :k] = bones_idx[:, :k]
            W[:, :k] = wts
            s = W.sum(1, keepdims=True)
            W = np.where(s > 0, W / np.where(s > 0, s, 1), np.array([1, 0, 0, 0], np.float32))
            W[W < 1e-6] = 0
            g.joints = J
            g.weights = W
    if nrm is not None:
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = np.where(ln > 1e-8, nrm / np.where(ln > 1e-8, ln, 1), np.array([0, 1, 0]))
    g.positions = pos.astype(np.float32)
    g.normals = nrm.astype(np.float32) if nrm is not None else None
    return g


def model_summary(models: list[Model]) -> dict:
    out = []
    for m in models:
        out.append({
            'name': m.name,
            'bones': len(m.skeleton.bones) if m.skeleton else 0,
            'shapes': [{'name': s.name, 'material': m.materials[s.material].name if s.material < len(m.materials) else '',
                        'lods': len(s.meshes), 'skin': s.skin_count,
                        'triangles': (s.meshes[0].count // 3) if s.meshes else 0} for s in m.shapes],
            'materials': [{'name': mt.name, 'textures': mt.textures, 'samplers': mt.samplers,
                           'render_mode': mt.render_mode, 'double_sided': mt.double_sided} for mt in m.materials],
            'lod_count': m.lod_count,
        })
    return {'models': out}
