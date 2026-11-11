"""Wavefront OBJ + MTL export: static meshes in the bind pose (no skeleton, no animation)."""
from __future__ import annotations

import io
import re

import numpy as np

from . import bfres as B
from .gltf import _GREY, _shader_color, texture_file_name


def _safe(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name) or 'unnamed'


def build_obj(models: list[B.Model], name: str, lod: int = 0, texture_png=None, fallback_color=None) -> list[tuple[str, bytes]]:
    """[(name.obj, ...), (name.mtl, ...), (name_textures/X.png, ...)] – textures like gltf.build_glb
    (texture_png(texture, kind) -> (png, has_alpha) | None); helper meshes that only cast shadows are left out."""
    tex_dir = f'{name}_textures'
    files: dict[str, bytes] = {}
    alpha_of: dict[str, bool] = {}

    def tex(texname, kind):
        if not texname or texture_png is None:
            return None
        path = f'{tex_dir}/{texture_file_name(texname, kind)}'
        if path not in files:
            r = texture_png(texname, kind)
            if not r:
                return None
            files[path], alpha_of[path] = r
        return path

    obj = io.StringIO()
    obj.write(f'# exported by NUP Explorer\nmtllib {name}.mtl\n')
    mtl = io.StringIO()
    mtl.write('# exported by NUP Explorer\n')
    done: set[str] = set()
    base = 1
    for model in models:
        for s in model.shapes:
            geo = model.geometry(s, lod) if hasattr(model, 'geometry') else B.shape_geometry(model, s, lod)
            if geo is None or not len(geo.indices):
                continue
            mat = model.materials[s.material] if s.material < len(model.materials) else None
            if mat is not None and mat.shadow_only:
                continue
            mname = _safe(f'{model.name}_{mat.name}') if mat is not None else 'default'
            if mat is not None and mname not in done:
                done.add(mname)
                mtl.write(_material(mname, mat, tex, alpha_of, fallback_color))
            obj.write(f'o {_safe(model.name)}_{_safe(s.name)}\nusemtl {mname}\n')
            n = len(geo.positions)
            np.savetxt(obj, geo.positions, fmt='v %.6f %.6f %.6f')
            has_uv = geo.uv0 is not None
            has_n = geo.normals is not None
            if has_uv:  # glTF/BFRES: v grows downwards, OBJ: upwards
                np.savetxt(obj, np.stack([geo.uv0[:, 0], 1.0 - geo.uv0[:, 1]], 1), fmt='vt %.6f %.6f')
            if has_n:
                np.savetxt(obj, geo.normals, fmt='vn %.5f %.5f %.5f')
            tri = geo.indices.reshape(-1, 3).astype(np.int64) + base
            if has_uv and has_n:
                np.savetxt(obj, np.repeat(tri, 3, axis=1), fmt='f %d/%d/%d %d/%d/%d %d/%d/%d')
            elif has_uv:
                np.savetxt(obj, np.repeat(tri, 2, axis=1), fmt='f %d/%d %d/%d %d/%d')
            elif has_n:
                np.savetxt(obj, np.repeat(tri, 2, axis=1), fmt='f %d//%d %d//%d %d//%d')
            else:
                np.savetxt(obj, tri, fmt='f %d %d %d')
            base += n
    out = [(f'{name}.obj', obj.getvalue().encode('utf-8')), (f'{name}.mtl', mtl.getvalue().encode('utf-8'))]
    return out + list(files.items())


def _material(mname: str, mat: B.Material, tex, alpha_of: dict, fallback_color) -> str:
    mode = mat.render_mode
    eye = mat.eye_layers()
    alb = mat.albedo()
    kd_map = tex('|'.join(eye), 'eye') if eye else tex(alb, 'color')
    lines = [f'\nnewmtl {mname}', 'Ka 0 0 0', 'Ks 0 0 0', 'illum 1']
    if kd_map:
        lines += ['Kd 1 1 1', f'map_Kd {kd_map}']
        if mode in ('MASK', 'BLEND') and alpha_of.get(kd_map):
            lines.append(f'map_d {kd_map}')  # opacity from the alpha channel
        lines.append('d 1')
    else:
        col, blend = _shader_color(mat, mode, fallback_color(mat) if fallback_color is not None else None)
        emm = mat.emissive()
        if emm and col == _GREY and tex(emm, 'color'):
            col = [0.02, 0.02, 0.02, 1.0]  # only an emission map: it is the colour (as in the GLB)
        lines += ['Kd {:.4f} {:.4f} {:.4f}'.format(*col[:3]), f'd {col[3] if blend else 1:.3f}']
    nrm = mat.normal()
    nrm_map = tex(nrm, 'normal') if nrm else None
    if nrm_map:
        lines.append(f'map_Bump {nrm_map}')
    emm = mat.emissive()
    emm_map = tex(emm, 'color') if emm else None
    if emm_map:
        lines += ['Ke 1 1 1', f'map_Ke {emm_map}']
    return '\n'.join(lines) + '\n'
