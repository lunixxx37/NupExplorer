"""Minimal glTF 2.0 binary (GLB) writer for BFRES models (+ optional baked animations)."""
from __future__ import annotations

import json
import re
import struct

import numpy as np

from . import bfres as B

FLOAT, USHORT, UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


class GlbBuilder:
    def __init__(self):
        self.gltf = {'asset': {'version': '2.0', 'generator': 'NUP Explorer'}, 'buffers': [{'byteLength': 0}],
                     'bufferViews': [], 'accessors': [], 'nodes': [], 'meshes': [], 'materials': [],
                     'textures': [], 'images': [], 'samplers': [], 'skins': [], 'animations': [],
                     'scenes': [{'nodes': []}], 'scene': 0}
        self.bin = bytearray()

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {'buffer': 0, 'byteOffset': len(self.bin), 'byteLength': len(data)}
        if target:
            v['target'] = target
        self.bin += data
        self.gltf['bufferViews'].append(v)
        return len(self.gltf['bufferViews']) - 1

    def accessor(self, arr: np.ndarray, ctype: int, atype: str, target: int | None = None,
                 minmax: bool = False, normalized: bool = False) -> int:
        dt = {FLOAT: '<f4', USHORT: '<u2', UINT: '<u4'}[ctype]
        a = np.ascontiguousarray(arr, dtype=dt)
        acc = {'bufferView': self.view(a.tobytes(), target), 'componentType': ctype,
               'count': int(a.shape[0]), 'type': atype}
        if normalized:
            acc['normalized'] = True
        if minmax:
            a2 = a.reshape(a.shape[0], -1)
            acc['min'] = [float(x) for x in a2.min(0)]
            acc['max'] = [float(x) for x in a2.max(0)]
        self.gltf['accessors'].append(acc)
        return len(self.gltf['accessors']) - 1

    def finish(self) -> bytes:
        g = self.gltf
        for k in ('materials', 'textures', 'images', 'samplers', 'skins', 'animations', 'meshes'):
            if not g[k]:
                del g[k]
        while len(self.bin) % 4:
            self.bin.append(0)
        g['buffers'][0]['byteLength'] = len(self.bin)
        js = json.dumps(g, separators=(',', ':')).encode('utf-8')
        js += b' ' * ((-len(js)) % 4)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        return (struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(js), b'JSON') + js
                + struct.pack('<I4s', len(self.bin), b'BIN\x00') + bytes(self.bin))


# untextured surfaces the game draws with special shaders (refraction, liquids): shown translucent
_GLASSY = ('glass', 'liquid', 'water', 'bubble', 'drop', 'drool', 'membrane', 'potion', 'bottle', 'crystal',
           'structuralcolor')
_WATER = [0.16, 0.32, 0.42]
_GREY = [0.62, 0.62, 0.62, 1.0]  # no colour information at all
_SHADOW = re.compile(r'shadow(?!temple)')  # blob/fake shadows, not the Shadow Temple


def _shader_color(mat: B.Material, mode: str | None, ramp: list[float] | None):
    """(baseColorFactor, blend) for a material without a usable colour texture. The game computes these
    colours in its shaders; approximations: colour ramp (hair) > shader colour parameters > grey."""
    name = mat.name.lower()
    pal = mat.palette_color()
    if 'eyeshadow' in name or (mode == 'BLEND' and 'eye' in name):
        return [1.0, 1.0, 1.0, 0.0], True  # eye gloss / eye shadow overlays: pure shader effects
    if mat.shadow_only or _SHADOW.search(name):
        return [0.02, 0.02, 0.03, 0.45], True  # shadow proxies / blob shadows (only shown on request)
    if mat.is_water or 'water' in name:
        return (pal or _WATER) + [0.45], True
    if mode == 'BLEND':
        return (pal or [1.0, 1.0, 1.0]) + [0.15 if pal else 0.08], True  # glass, fades: faint
    if any(k in name for k in _GLASSY) or (mode == 'MASK' and mat.blends):
        return (pal or [0.85, 0.9, 0.9]) + [0.3], True  # glass, liquids, alpha-blended shader effects
    col = ramp or pal
    return (col + [1.0]) if col else list(_GREY), False


def build_glb(models: list[B.Model], lod: int = 0, texture_uri=None, texture_png=None,
              animations=None, extras: dict | None = None, fallback_color=None) -> bytes:
    """texture_uri(name, kind) -> uri | None   (viewer: textures loaded via HTTP)
    texture_png(name, kind) -> (png_bytes, has_alpha) | None   (export: embedded textures)
    kind: 'color', 'normal' or 'eye' (name = 'iris|sclera|mask', composited)
    fallback_color(material) -> [r, g, b] linear | None   (colour when no albedo texture exists)
    animations: list of (header, data) from anim.bake for the first model's skeleton."""
    gb = GlbBuilder()
    g = gb.gltf
    g['samplers'].append({'magFilter': 9729, 'minFilter': 9987, 'wrapS': 10497, 'wrapT': 10497})
    root = {'name': 'Model', 'children': []}
    g['nodes'].append(root)
    g['scenes'][0]['nodes'].append(0)
    tex_cache: dict[tuple, tuple] = {}

    def texture(name: str, kind: str):
        key = (name, kind)
        if key in tex_cache:
            return tex_cache[key]
        res = (None, False)
        if texture_png is not None:
            r = texture_png(name, kind)
            if r:
                png, alpha = r
                g['images'].append({'bufferView': gb.view(png), 'mimeType': 'image/png', 'name': name})
                g['textures'].append({'source': len(g['images']) - 1, 'sampler': 0, 'name': name})
                res = (len(g['textures']) - 1, alpha)
        elif texture_uri is not None:
            uri = texture_uri(name, kind)
            if uri:
                g['images'].append({'uri': uri, 'name': name})
                g['textures'].append({'source': len(g['images']) - 1, 'sampler': 0, 'name': name})
                res = (len(g['textures']) - 1, False)
        tex_cache[key] = res
        return res

    scene_extras = {'models': [], 'bones': []}
    first_bone_nodes = None
    for mi, model in enumerate(models):
        mroot = {'name': model.name, 'children': []}
        g['nodes'].append(mroot)
        mroot_idx = len(g['nodes']) - 1
        root['children'].append(mroot_idx)
        skel = model.skeleton
        bone_nodes = []
        skin_idx = None
        if skel is not None and skel.bones:
            base = len(g['nodes'])
            for b in skel.bones:
                n = {'name': b.name, 'translation': [float(x) for x in b.translate],
                     'rotation': [float(x) for x in b.quat()], 'scale': [float(x) for x in b.scale]}
                g['nodes'].append(n)
                bone_nodes.append(len(g['nodes']) - 1)
            for b in skel.bones:
                if b.parent >= 0:
                    g['nodes'][base + b.parent].setdefault('children', []).append(base + b.index)
                else:
                    mroot['children'].append(base + b.index)
            inv = np.stack([np.linalg.inv(b.world).T.reshape(16) for b in skel.bones]).astype(np.float32)
            g['skins'].append({'joints': bone_nodes, 'inverseBindMatrices': gb.accessor(inv, FLOAT, 'MAT4'),
                               'name': model.name})
            skin_idx = len(g['skins']) - 1
        if first_bone_nodes is None:
            first_bone_nodes = bone_nodes
            scene_extras['bones'] = [b.name for b in skel.bones] if skel else []
        mat_index = {}
        shapes_info = []
        for s in model.shapes:
            geo = B.shape_geometry(model, s, lod)
            if geo is None:
                continue
            mat = model.materials[s.material] if s.material < len(model.materials) else None
            if mat is not None and s.material not in mat_index:
                # render state from the material: opaque / mask (alpha test) / translucent, displayed faces
                mode = mat.render_mode
                gm = {'name': mat.name, 'doubleSided': mat.double_sided,
                      'pbrMetallicRoughness': {'metallicFactor': 0.0, 'roughnessFactor': 0.85}}
                alb = mat.albedo()
                eye = mat.eye_layers()
                emm = mat.emissive()
                ei = texture(emm, 'color')[0] if emm else None
                if ei is not None:
                    gm['emissiveTexture'] = {'index': ei}
                    gm['emissiveFactor'] = [1.0, 1.0, 1.0]
                ti = None
                if eye:  # iris + sclera blended through the iris mask (what the eye shader does)
                    ti, alpha = texture('|'.join(eye), 'eye')
                elif alb:
                    ti, alpha = texture(alb, 'color')
                if ti is not None:
                    gm['pbrMetallicRoughness']['baseColorTexture'] = {'index': ti}
                    # the alpha channel is only opacity for 'mask'/'translucent' materials;
                    # opaque materials often keep other data (masks) in it
                    if mode == 'MASK' or (mode is None and alpha):
                        gm['alphaMode'] = 'MASK'
                        gm['alphaCutoff'] = 0.5
                    elif mode == 'BLEND':
                        gm['alphaMode'] = 'BLEND'
                else:
                    col, blend = _shader_color(mat, mode, fallback_color(mat) if fallback_color is not None else None)
                    if ei is not None and col == _GREY:  # only an emission map (boost rings, lamps): it is the colour
                        col = [0.02, 0.02, 0.02, 1.0]
                    gm['pbrMetallicRoughness']['baseColorFactor'] = col
                    if blend:
                        gm['alphaMode'] = 'BLEND'
                nrm = mat.normal()
                if nrm:
                    ti, _ = texture(nrm, 'normal')
                    if ti is not None:
                        gm['normalTexture'] = {'index': ti}
                gm['extras'] = {'textures': mat.textures, 'samplers': mat.samplers, 'albedo': alb, 'normal': nrm,
                                'emissive': emm, 'render_mode': mode, 'shadow_only': mat.shadow_only}
                g['materials'].append(gm)
                mat_index[s.material] = len(g['materials']) - 1
            attrs = {'POSITION': gb.accessor(geo.positions, FLOAT, 'VEC3', ARRAY_BUFFER, minmax=True)}
            if geo.normals is not None:
                attrs['NORMAL'] = gb.accessor(geo.normals, FLOAT, 'VEC3', ARRAY_BUFFER)
            if geo.uv0 is not None:
                attrs['TEXCOORD_0'] = gb.accessor(geo.uv0, FLOAT, 'VEC2', ARRAY_BUFFER)
            if geo.uv1 is not None:
                attrs['TEXCOORD_1'] = gb.accessor(geo.uv1, FLOAT, 'VEC2', ARRAY_BUFFER)
            skinned = skin_idx is not None and geo.joints is not None
            if skinned:
                attrs['JOINTS_0'] = gb.accessor(geo.joints, USHORT, 'VEC4', ARRAY_BUFFER)
                attrs['WEIGHTS_0'] = gb.accessor(geo.weights, FLOAT, 'VEC4', ARRAY_BUFFER)
            prim = {'attributes': attrs, 'indices': gb.accessor(geo.indices, UINT, 'SCALAR', ELEMENT_ARRAY_BUFFER),
                    'mode': 4}
            if mat is not None:
                prim['material'] = mat_index[s.material]
            g['meshes'].append({'name': s.name, 'primitives': [prim]})
            node = {'name': s.name, 'mesh': len(g['meshes']) - 1}
            if skinned:
                node['skin'] = skin_idx
                root['children'].append(len(g['nodes']))  # skinned meshes live at the scene root
            else:
                mroot['children'].append(len(g['nodes']))
            g['nodes'].append(node)
            vis_bone = skel.bones[s.bone] if skel is not None and s.bone < len(skel.bones) else None
            shadow_only = bool(mat is not None and mat.shadow_only)
            shapes_info.append({'name': s.name, 'node': len(g['nodes']) - 1, 'material': mat.name if mat else '',
                                'lods': len(s.meshes), 'skin': s.skin_count, 'triangles': int(len(geo.indices) // 3),
                                'vertices': int(len(geo.positions)),
                                # visibility is controlled per bone (bone flag bit 0 = visible by default)
                                'vis_bone': vis_bone.name if vis_bone is not None else None,
                                'visible': (bool(vis_bone.flags & 1) if vis_bone is not None else True)
                                and not shadow_only,
                                'shadow_only': shadow_only})
        scene_extras['models'].append({'name': model.name, 'shapes': shapes_info, 'lod_count': model.lod_count,
                                       'bone_nodes': bone_nodes, 'compressed_geometry': model.compressed_geometry,
                                       'cluster_errors': [f'{s.name}: {s.cluster_error}' for s in model.shapes
                                                          if s.cluster_error]})
    if animations and first_bone_nodes:
        _add_animations(gb, models[0].skeleton, first_bone_nodes, animations)
    if extras:
        scene_extras.update(extras)
    g['scenes'][0]['extras'] = scene_extras
    return gb.finish()


def _add_animations(gb: GlbBuilder, skel: B.Skeleton, bone_nodes: list[int], animations):
    g = gb.gltf
    names = {b.name: i for i, b in enumerate(skel.bones)}
    for header, data in animations:
        nf = data.shape[1]
        times = (np.arange(nf, dtype=np.float32) / np.float32(header.get('fps', 30.0)))
        t_all = gb.accessor(times, FLOAT, 'SCALAR', minmax=True)
        t_one = None
        samplers, channels = [], []
        for bi, binfo in enumerate(header['bones']):
            if binfo['name'] not in names:
                continue
            node = bone_nodes[names[binfo['name']]]
            for path, sl, atype in (('translation', slice(0, 3), 'VEC3'), ('rotation', slice(3, 7), 'VEC4'),
                                    ('scale', slice(7, 10), 'VEC3')):
                vals = data[bi, :, sl]
                if np.allclose(vals, vals[0], atol=1e-6):
                    if t_one is None:
                        t_one = gb.accessor(np.zeros(1, np.float32), FLOAT, 'SCALAR', minmax=True)
                    inp, out = t_one, vals[:1]
                else:
                    inp, out = t_all, vals
                samplers.append({'input': inp, 'output': gb.accessor(out, FLOAT, atype), 'interpolation': 'LINEAR'})
                channels.append({'sampler': len(samplers) - 1, 'target': {'node': node, 'path': path}})
        if channels:
            g['animations'].append({'name': header['name'], 'samplers': samplers, 'channels': channels})
