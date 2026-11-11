"""Koei Tecmo G1M models (Katana Engine games, e.g. Hyrule Warriors: Age of Imprisonment).

Own implementation; the chunk layouts follow the community documentation of the format (Project G1M, eArmada8's
gust_stuff) and were checked against the game's models.

File: "_M1G", version (4 ASCII digits, stored reversed), u32 file size, u32 header size, u32 0, u32 chunk count.
Chunks: magic (reversed: "SM1G" = G1MS), u32 version (ASCII digits, compared as big-endian number), u32 size.
  G1MS skeleton  +0x0C joint data offset, +0x14 u16 joint count, +0x16 u16 size of the global id table, +0x18 u16
                 layer, +0x1C table: global id -> local joint (0xFFFF = not in this model). Joint (48 bytes):
                 scale 3f, u32 parent (0xFFFFFFFF = root, 0x80000000 | id = bone of another model's skeleton),
                 rotation quaternion x, y, z, w, position 3f + 1f. Transforms are relative to the parent.
  G1MG geometry  +0x0C platform, +0x14 bounding box (6f), +0x2C section count; sections: u32 id, u32 size, u32 count
    0x10002 materials: u32 ?, u32 texture count, 2 u32 ?; per texture u16 slot (in the model's texture table),
            u16 layer, u16 type (1 colour, 3 normal map ...), u16 subtype, u16 tile mode u, v
    0x10004 vertex buffers: u32 flags, u32 stride, u32 count (+ u32 from version 0041), data. Entries with flag
            0x80000000 have no data of their own: their vertices are the next bytes of the entry the low bits name
            (a block of stride 1 that holds several buffers)
    0x10005 vertex layouts: u32 count + buffer indices, u32 attribute count, attributes (u16 buffer, u16 offset,
            u8 data type, u8 -, u8 semantic, u8 layer)
    0x10006 joint palettes: u32 count, entries (u32 G1MM index, u32 physics index, u32 joint; 0x80000000 = bone of
            an external skeleton)
    0x10007 index buffers: u32 count, u32 bits (+ u32 from version 0041), data, aligned to 4 bytes
    0x10008 submeshes: 14 u32: flags, layout, palette, bone, ?, shader, material, index buffer, ?, primitive type
            (3 triangle list, 4 strip), vertex offset, vertex count, index offset, index count (indices are absolute)
    0x10009 LOD groups: u32 lod, group, entry, count1, count2 (+ 4 u32 from version 0042), then meshes: name (16),
            u16 type (0 normal, 1 and 2 cloth/physics), u16 ?, u32 NUN id, u32 count, submesh indices
Skin weights: blend indices are three times the palette slot; a missing last weight is 1 - the sum of the others.
Mesh type 2 (physics-driven parts: capes, hair) is skinned like normal meshes; type 1 meshes are driven by the
cloth simulation (NUNO control points, vertices stored relative to them) and are left out.
Textures are not named in the file: the slots point into a texture table of the model (see katana.py).
Units: the games use centimetres; positions are converted to metres (glTF).
"""
from __future__ import annotations

import struct

import numpy as np

from .bfres import Geometry

MAGIC = b'_M1G'
UNIT = 0.01  # centimetres -> metres
SEM_POSITION, SEM_WEIGHT, SEM_INDEX, SEM_NORMAL, SEM_TEXCOORD = 0, 1, 2, 3, 5
TEX_COLOR, TEX_NORMAL = 1, 3
TEX_TYPES = {1: 'colour', 2: 'mask', 3: 'normal', 5: 'detail'}
# data type -> (numpy type, components)
_DTYPES = {0: ('<f4', 1), 1: ('<f4', 2), 2: ('<f4', 3), 3: ('<f4', 4), 5: ('u1', 4), 7: ('<u2', 4), 9: ('<u4', 4),
           10: ('<f2', 2), 11: ('<f2', 4), 13: ('u1', 4)}
_FLOAT_TYPES = (0, 1, 2, 3, 10, 11)


def is_g1m(head: bytes) -> bool:
    return head[:4] == MAGIC


def _version(raw: bytes, off: int) -> int:
    """Chunk version: four ASCII digits stored reversed ('5400' = 0045), as the big-endian number of '0045'."""
    return struct.unpack_from('<I', raw, off)[0]


class Bone:
    __slots__ = ('name', 'index', 'parent', 'global_id', 'external_parent', 'scale', 'rotate', 'translate', 'flags',
                 'rot_is_quat', 'ssc', 'local', 'world')

    def quat(self):
        x, y, z, w = self.rotate
        n = float(np.sqrt(x * x + y * y + z * z + w * w)) or 1.0
        return (x / n, y / n, z / n, w / n)


class Skeleton:
    def __init__(self, raw: bytes, off: int, version: int):
        joff = struct.unpack_from('<I', raw, off + 0x0C)[0]
        njoint, nids, self.layer = struct.unpack_from('<3H', raw, off + 0x14)
        ids = struct.unpack_from(f'<{nids}H', raw, off + 0x1C)
        global_of = {}
        if version >= 0x30303332:
            for gid, local in enumerate(ids):
                if local != 0xFFFF:
                    global_of.setdefault(local, gid)
        self.bones: list[Bone] = []
        for j in range(njoint):
            sx, sy, sz, parent, qx, qy, qz, qw, px, py, pz, _w = struct.unpack_from('<3fI4f4f', raw, off + joff + 48 * j)
            b = Bone()
            b.index, b.global_id = j, global_of.get(j, j)
            b.name = f'bone_{b.global_id}'
            b.external_parent = None
            if parent == 0xFFFFFFFF:
                b.parent = -1
            elif parent & 0x80000000:  # attached to a bone of another model's skeleton
                b.parent, b.external_parent = -1, parent & 0x7FFFFFFF
            else:
                b.parent = parent if parent < njoint else -1
            b.scale, b.rotate, b.translate = (sx, sy, sz), (qx, qy, qz, qw), (px * UNIT, py * UNIT, pz * UNIT)
            b.flags, b.rot_is_quat, b.ssc = 1, True, False
            x, y, z, w = b.quat()
            r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            m = np.eye(4)
            m[:3, :3] = r * np.asarray(b.scale)
            m[:3, 3] = b.translate
            b.local = m
            self.bones.append(b)
        done: set[int] = set()

        def world(b: Bone):
            if b.index in done:
                return b.world
            b.world = b.local if b.parent < 0 or b.parent == b.index else world(self.bones[b.parent]) @ b.local
            done.add(b.index)
            return b.world

        for b in self.bones:
            world(b)

    def by_name(self) -> dict[str, Bone]:
        return {b.name: b for b in self.bones}


class Material:
    """Texture slots of a G1MG material; textures are resolved to G1T files afterwards (Model.set_textures)."""
    __slots__ = ('name', 'slots', 'textures', 'samplers', 'render', 'render_info', 'colors')

    shadow_only = blends = is_water = False
    render_mode = None   # the alpha channel of the colour texture decides (cut-out leaves, fences)
    double_sided = True

    def _first(self, kind: int) -> str | None:
        for (slot, layer, ttype, _sub), tex in zip(self.slots, self.textures):
            if ttype == kind and layer == 0 and tex:
                return tex
        return None

    def albedo(self):
        return self._first(TEX_COLOR)

    def normal(self):
        return self._first(TEX_NORMAL)

    def emissive(self):
        return None

    def eye_layers(self):
        return None

    def ramp_textures(self) -> list[str]:
        return []

    def palette_color(self):
        return None


class Mesh:
    """One LOD of a shape for the model summary (index count)."""
    __slots__ = ('count',)


class Shape:
    __slots__ = ('name', 'index', 'material', 'bone', 'meshes', 'skin_count', 'skin_bones', 'cluster', 'cluster_error',
                 'layout', 'palette', 'ib', 'prim', 'v_offset', 'v_count', 'i_offset', 'i_count', 'mesh_type')


class Model:
    lod_count = 1
    compressed_geometry = False

    def __init__(self, raw: bytes, name: str = 'G1M'):
        if raw[:4] != MAGIC:
            raise ValueError('Not a G1M file')
        self.raw, self.name = raw, name
        self.skeleton: Skeleton | None = None
        self.materials: list[Material] = []
        self.shapes: list[Shape] = []
        self.vbs: list[tuple[int, int, int] | None] = []
        self.layouts: list[tuple[tuple, list]] = []
        self.palettes: list[list[int]] = []
        self.ibs: list[tuple[int, int, int] | None] = []
        self.bbox = None
        hsize, nchunk = struct.unpack_from('<I', raw, 12)[0], struct.unpack_from('<I', raw, 20)[0]
        pos = hsize
        geometry = None
        for _ in range(nchunk):
            magic, ver, size = raw[pos:pos + 4][::-1], _version(raw, pos + 4), struct.unpack_from('<I', raw, pos + 8)[0]
            if size < 12:
                break
            if magic == b'G1MS' and self.skeleton is None:
                self.skeleton = Skeleton(raw, pos, ver)
            elif magic == b'G1MG' and geometry is None:
                geometry = (pos, ver)
            pos += size
        if geometry is None:
            raise ValueError('G1M without geometry')
        self._parse_geometry(*geometry)

    # ------------------------------------------------------------------ parsing
    def _parse_geometry(self, pos: int, ver: int):
        raw = self.raw
        self.bbox = tuple(x * UNIT for x in struct.unpack_from('<6f', raw, pos + 0x14))
        nsec = struct.unpack_from('<I', raw, pos + 0x2C)[0]
        newer = ver > 0x30303430
        sp = pos + 0x30
        secs = {}
        for _ in range(nsec):
            sid, ssize, count = struct.unpack_from('<3I', raw, sp)
            secs.setdefault(sid, (sp + 12, count))
            if ssize < 12:
                break
            sp += ssize
        if 0x10002 in secs:
            q, count = secs[0x10002]
            for i in range(count):
                _u1, ntex, _u2, _u3 = struct.unpack_from('<4I', raw, q)
                q += 16
                m = Material()
                m.name = f'material_{i}'
                m.slots = [struct.unpack_from('<4H', raw, q + 12 * t) for t in range(ntex)]
                m.textures = [''] * ntex
                m.samplers = [f'{TEX_TYPES.get(t, f"type{t}")}{f"_{lay}" if lay else ""}' for _s, lay, t, _st in m.slots]
                m.render, m.render_info, m.colors = [], {}, []
                q += 12 * ntex
                self.materials.append(m)
        if 0x10004 in secs:
            q, count = secs[0x10004]
            cursor: dict[int, int] = {}
            for i in range(count):
                flags, stride, n = struct.unpack_from('<3I', raw, q)
                q += 16 if newer else 12
                if flags & 0x80000000:  # data inside an earlier block entry
                    block = flags & 0x7FFFFFFF
                    start = cursor.get(block)
                    self.vbs.append((start, stride, n) if start is not None else None)
                    if start is not None:
                        cursor[block] = start + stride * n
                else:
                    self.vbs.append((q, stride, n))
                    cursor[i] = q
                    q += stride * n
        if 0x10005 in secs:
            q, count = secs[0x10005]
            for _ in range(count):
                nb = struct.unpack_from('<I', raw, q)[0]
                bufs = struct.unpack_from(f'<{nb}I', raw, q + 4)
                q += 4 + 4 * nb
                na = struct.unpack_from('<I', raw, q)[0]
                attrs = [struct.unpack_from('<HHBBBB', raw, q + 4 + 8 * a) for a in range(na)]
                q += 4 + 8 * na
                self.layouts.append((bufs, attrs))
        if 0x10006 in secs:
            q, count = secs[0x10006]
            for _ in range(count):
                n = struct.unpack_from('<I', raw, q)[0]
                self.palettes.append([struct.unpack_from('<3I', raw, q + 4 + 12 * k)[2] & 0x7FFFFFFF for k in range(n)])
                q += 4 + 12 * n
        if 0x10007 in secs:
            q, count = secs[0x10007]
            for _ in range(count):
                n, bits = struct.unpack_from('<2I', raw, q)
                q += 12 if newer else 8
                self.ibs.append((q, n, bits) if bits in (8, 16, 32) else None)
                q += n * bits // 8
                q = (q + 3) & ~3
        subs = []
        if 0x10008 in secs:
            q, count = secs[0x10008]
            subs = [struct.unpack_from('<14I', raw, q + 56 * i) for i in range(count)]
        groups = []
        if 0x10009 in secs:
            q, count = secs[0x10009]
            for _ in range(count):
                lod, _grp, _entry, c1, c2 = struct.unpack_from('<5I', raw, q)
                q += 20 + (16 if ver >= 0x30303432 else 0)
                meshes = []
                for _m in range(c1 + c2):
                    name = raw[q:q + 16].split(b'\0')[0].decode('latin-1')
                    mtype, _unk, _nun, ni = struct.unpack_from('<2H2I', raw, q + 16)
                    meshes.append((name, mtype, struct.unpack_from(f'<{ni}I', raw, q + 28)))
                    q += 28 + 4 * ni
                groups.append((lod, meshes))
        self._build_shapes(subs, groups)

    def _build_shapes(self, subs, groups):
        lod0 = [g for g in groups if g[0] == 0] or groups[:1]
        listed: list[tuple[str, int, int]] = []  # (name, mesh type, submesh)
        seen = set()
        for _lod, meshes in lod0:
            for name, mtype, ids in meshes:
                for k, si in enumerate(ids):
                    if si < len(subs) and si not in seen:
                        seen.add(si)
                        listed.append((f'{name or "mesh"}_{k}' if len(ids) > 1 else (name or 'mesh'), mtype, si))
        if not groups:
            listed = [(f'submesh_{i}', 0, i) for i in range(len(subs))]
        for name, mtype, si in listed:
            f = subs[si]
            s = Shape()
            s.name, s.index, s.mesh_type = name, si, mtype
            (_flags, s.layout, s.palette, _bone, _u, _shader, s.material, s.ib, _u2, s.prim,
             s.v_offset, s.v_count, s.i_offset, s.i_count) = f
            pal = self.palettes[s.palette] if s.palette < len(self.palettes) else []
            s.bone = pal[0] if pal else 0
            s.skin_bones = list(pal)
            s.skin_count = self._influences(s)
            m = Mesh()
            m.count = s.i_count if s.prim == 3 else max(0, s.i_count - 2) * 3
            s.meshes = [m]
            s.cluster, s.cluster_error = None, None
            self.shapes.append(s)

    def _influences(self, s: Shape) -> int:
        if s.layout >= len(self.layouts):
            return 0
        return sum(4 for (_b, _o, _t, _d, sem, _l) in self.layouts[s.layout][1] if sem == SEM_INDEX)

    @property
    def skipped_meshes(self) -> list[str]:
        """Meshes that are positioned by the cloth simulation (not shown)."""
        return [s.name for s in self.shapes if s.mesh_type == 1]

    # ------------------------------------------------------------------ textures
    def texture_slots(self) -> set[int]:
        return {slot for m in self.materials for slot, *_ in m.slots}

    def set_textures(self, paths: dict[int, str]):
        """Texture table of the model: slot -> texture name (path of the G1T file)."""
        for m in self.materials:
            m.textures = [paths.get(slot, '') for slot, *_ in m.slots]

    # ------------------------------------------------------------------ geometry
    def _attr(self, layout, attr, start: int, count: int) -> np.ndarray | None:
        buf, offset, dtype, _d, _sem, _layer = attr
        if dtype not in _DTYPES or buf >= len(layout[0]) or layout[0][buf] >= len(self.vbs):
            return None
        vb = self.vbs[layout[0][buf]]
        if vb is None:
            return None
        base, stride, n = vb
        npt, comps = _DTYPES[dtype]
        size = np.dtype(npt).itemsize
        first = base + offset + start * stride
        if start + count > n or first + (count - 1) * stride + comps * size > len(self.raw):
            return None
        return np.ndarray((count, comps), npt, self.raw, first, (stride, size))

    def _triangles(self, s: Shape) -> np.ndarray:
        ib = self.ibs[s.ib] if s.ib < len(self.ibs) else None
        if ib is None:
            return np.zeros(0, np.int64)
        base, n, bits = ib
        dt = {8: 'u1', 16: '<u2', 32: '<u4'}[bits]
        count = min(s.i_count, max(0, n - s.i_offset))
        idx = np.frombuffer(self.raw, dt, count, base + s.i_offset * bits // 8).astype(np.int64)
        if s.prim == 4:  # triangle strip with restart index
            restart = (1 << bits) - 1
            tris = []
            run = []
            for v in idx.tolist() + [restart]:
                if v == restart:
                    for i in range(len(run) - 2):
                        a, b, c = (run[i], run[i + 1], run[i + 2]) if i % 2 == 0 else (run[i + 1], run[i], run[i + 2])
                        if a != b and b != c and a != c:
                            tris += (a, b, c)
                    run = []
                else:
                    run.append(v)
            return np.asarray(tris, np.int64)
        if s.prim != 3:
            return np.zeros(0, np.int64)
        return idx[:len(idx) - len(idx) % 3]

    def geometry(self, s: Shape, lod: int = 0) -> Geometry | None:
        if s.layout >= len(self.layouts) or s.mesh_type == 1:  # type 1: positioned by the cloth simulation
            return None
        layout = self.layouts[s.layout]
        idx = self._triangles(s)
        if not len(idx):
            return None
        lo, hi = int(idx.min()), int(idx.max())
        count = hi - lo + 1
        found = {}
        for a in layout[1]:
            key = (a[4], a[5])
            if key not in found:
                arr = self._attr(layout, a, lo, count)
                if arr is not None:
                    found[key] = (a[2], arr)
        if (SEM_POSITION, 0) not in found:
            return None
        g = Geometry()
        g.warnings = []
        g.indices = (idx - lo).astype(np.uint32)
        pos = found[(SEM_POSITION, 0)][1][:, :3].astype(np.float64) * UNIT
        nrm = found.get((SEM_NORMAL, 0))
        nrm = nrm[1][:, :3].astype(np.float64) if nrm is not None and nrm[0] in _FLOAT_TYPES else None
        g.uv0 = g.uv1 = None
        for layer, attr in ((0, 'uv0'), (1, 'uv1')):
            uv = found.get((SEM_TEXCOORD, layer))
            if uv is not None and uv[0] in _FLOAT_TYPES and uv[1].shape[1] >= 2:
                setattr(g, attr, uv[1][:, :2].astype(np.float32))
        skel = self.skeleton
        pal = self.palettes[s.palette] if s.palette < len(self.palettes) else []
        g.joints = g.weights = None
        if skel is not None and skel.bones and pal:
            nb = len(skel.bones)
            ind_l = [found[(SEM_INDEX, k)][1].astype(np.int64) for k in (0, 1) if (SEM_INDEX, k) in found]
            if ind_l:
                ind = np.concatenate(ind_l, 1)
                if (ind % 3 == 0).all():  # blend indices are 3 x the palette slot
                    ind //= 3
                joints = np.asarray(pal, np.int64)[np.clip(ind, 0, len(pal) - 1)]
                w_l = []
                for k in (0, 1):
                    w = found.get((SEM_WEIGHT, k))
                    if w is not None:
                        wa = w[1].astype(np.float32)
                        if w[0] == 13:
                            wa = wa / 255.0
                        w_l.append(wa)
                if w_l:
                    wts = np.concatenate(w_l, 1)[:, :ind.shape[1]]
                    if wts.shape[1] < ind.shape[1]:  # the last weight is implicit
                        rest = np.clip(1.0 - wts.sum(1, keepdims=True), 0.0, 1.0)
                        pad = np.zeros((len(wts), ind.shape[1] - wts.shape[1]), np.float32)
                        pad[:, :1] = rest
                        wts = np.concatenate([wts, pad], 1)
                else:
                    wts = np.zeros(ind.shape, np.float32)
                    wts[:, 0] = 1.0
                if wts.shape[1] > 4:
                    order = np.argsort(-wts, axis=1)[:, :4]
                    wts = np.take_along_axis(wts, order, 1)
                    joints = np.take_along_axis(joints, order, 1)
                k = wts.shape[1]
                J = np.zeros((count, 4), np.uint16)
                W = np.zeros((count, 4), np.float32)
                J[:, :k] = np.clip(joints[:, :k], 0, nb - 1)
                W[:, :k] = wts
                tot = W.sum(1, keepdims=True)
                W = np.where(tot > 0, W / np.where(tot > 0, tot, 1), np.array([1, 0, 0, 0], np.float32))
                W[W < 1e-6] = 0
                g.joints, g.weights = J, W
            else:  # rigid: the vertices are in the space of the palette's bone
                j = min(pal[0], nb - 1)
                w = skel.bones[j].world
                pos = pos @ w[:3, :3].T + w[:3, 3]
                if nrm is not None:
                    nrm = nrm @ w[:3, :3].T
                g.joints = np.full((count, 4), j, np.uint16)
                g.weights = np.tile(np.array([1, 0, 0, 0], np.float32), (count, 1))
        if nrm is not None:
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = np.where(ln > 1e-8, nrm / np.where(ln > 1e-8, ln, 1), np.array([0, 1, 0]))
        g.positions = pos.astype(np.float32)
        g.normals = nrm.astype(np.float32) if nrm is not None else None
        return g


def parse_models(raw: bytes, name: str = 'G1M') -> list[Model]:
    return [Model(raw, name)]
