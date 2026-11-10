"""Quality check of all 3D models: data-level problems that make models look wrong.

  python tests/model_qa.py [path/to/OT.nup] [report.json]

Per model file:
  - parse / geometry errors, NaN positions, non-triangle meshes (skipped by the viewer)
  - materials: unknown render mode, referenced colour texture missing and what the viewer shows
    instead (colour ramp / shader colour parameters / translucent / grey), no colour source at all
    (renders grey), eye materials outside the known iris/sclera/mask pattern or with missing layers,
    other referenced textures missing, shadow-only helper materials (hidden in the viewer)
  - skinning: more than 4 influences (approximated), all meshes hidden by default
  - compressed meshlet geometry (clusters.py): shapes that cannot be decoded
Writes a JSON report and prints a summary with examples.
"""
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from nupx import bfres  # noqa: E402
from nupx.convert import eye_available, ramp_color  # noqa: E402
from nupx.gltf import _GREY, _shader_color  # noqa: E402
from nupx.vfs import VFS, classify  # noqa: E402


def category(path: str) -> str:
    n = path.rpartition('/')[2]
    head = n.split('.')[0].split('_')[0]
    return head if head in ('Npc', 'Enemy', 'Animal', 'Player', 'Obj', 'Item', 'Weapon', 'Armor', 'Land',
                            'Cmn', 'Dm', 'Ride', 'Field', 'Skeleton') else 'Other'


def main():
    nup = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('NUP_FILE')
    if not nup:
        sys.exit(f'usage: python {sys.argv[0]} path/to/file.nup  (or set NUP_FILE)')
    out = sys.argv[2] if len(sys.argv) > 2 else None
    cache = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'NupExplorer', 'cache')
    v = VFS(nup, cache_dir=cache, log=lambda *a: None)
    files = sorted(p for p in v.c.files if classify(p) == 'model')
    issues = collections.defaultdict(list)   # kind -> [(path, detail)]
    stats = collections.Counter()
    t0 = time.time()
    for i, p in enumerate(files):
        try:
            models = bfres.parse_models(v.read(p))
        except Exception as e:  # noqa: BLE001
            issues['parse_error'].append((p, repr(e)[:120]))
            continue
        if not models:
            stats['files_without_model'] += 1
            continue
        stats['model_files'] += 1
        for m in models:
            for s in m.shapes:  # compressed meshlet geometry (clusters.py)
                if s.cluster is not None:
                    stats['cluster_shapes'] += 1
                elif s.cluster_error:
                    issues['cluster_geometry_undecodable'].append((p, f'{s.name}: {s.cluster_error}'))
            for mt in m.materials:
                stats['materials'] += 1
                mode = mt.render_mode
                if mode is None:
                    issues['render_mode_unknown'].append((p, f'{mt.name}: {mt.render[:12]}'))
                if mt.shadow_only:
                    stats['materials_shadow_only_hidden'] += 1
                eye = mt.eye_layers()
                alb = mt.albedo()
                if eye:
                    stats['eye_composites'] += 1
                    if not eye_available(v, '|'.join(eye)):
                        issues['eye_texture_missing'].append((p, f'{mt.name}: {[t for t in eye if not v.find_texture(t)]}'))
                    elif not v.find_texture(eye[0]):
                        issues['eye_iris_missing_common_iris_used'].append((p, f'{mt.name}: {eye[0]}'))
                elif 'eye_inner' in mt.name.lower() or 'eyeball' in mt.name.lower():
                    issues['eye_not_composited'].append((p, f'{mt.name}: {list(zip(mt.samplers, mt.textures))[:6]}'))
                emm = mt.emissive()
                if not eye and not (alb and v.find_texture(alb)) and not mt.shadow_only:
                    # what the viewer shows instead of a colour texture (same rules as gltf.build_glb)
                    ramp = ramp_color(v, mt)
                    col, blend = _shader_color(mt, mode, ramp)
                    if emm and v.find_texture(emm) and col == _GREY:  # glows with its emission map
                        col = [0.02, 0.02, 0.02, 1.0]
                        stats['untextured_emission_map'] += 1
                    src = ('ramp' if ramp and col[:3] == ramp else 'shader_colour' if mt.palette_color()
                           and col[:3] == mt.palette_color() else 'default')
                    look = ('invisible' if col[3] == 0 else 'translucent' if blend else
                            'grey' if col == [0.62, 0.62, 0.62, 1.0] else 'coloured')
                    stats[f'untextured_{look}_{src}'] += 1
                    detail = f'{mt.name}: {alb or list(zip(mt.samplers, mt.textures))[:5]}'
                    if alb:  # a colour texture is referenced but not in the game files
                        issues[f'albedo_missing_{look}'].append((p, detail))
                    elif look == 'grey' and mode != 'BLEND':
                        issues['no_colour_source_grey'].append((p, detail))
                other_missing = [t for t in mt.textures if t != alb and not v.find_texture(t)]
                if other_missing:
                    stats['materials_with_other_missing_textures'] += 1
                    issues['other_texture_missing'].append((p, f'{mt.name}: {other_missing[:4]}'))
            vis = []
            for s in m.shapes:
                stats['shapes'] += 1
                if not s.meshes and s.cluster is None:
                    continue
                if s.meshes and s.meshes[0].prim != 3:
                    issues['non_triangle_mesh'].append((p, f'{s.name}: primitive {s.meshes[0].prim}'))
                    continue
                try:
                    g = bfres.shape_geometry(m, s, 0)
                except Exception as e:  # noqa: BLE001
                    issues['geometry_error'].append((p, f'{s.name}: {repr(e)[:100]}'))
                    continue
                if g is None:
                    issues['geometry_none'].append((p, s.name))
                    continue
                if not np.isfinite(g.positions).all():
                    issues['nan_positions'].append((p, s.name))
                ext = float(np.ptp(g.positions, axis=0).max()) if len(g.positions) else 0
                if ext > 2000:
                    issues['huge_extent'].append((p, f'{s.name}: {ext:.0f}'))
                if s.skin_count > 4:
                    stats['shapes_more_than_4_influences'] += 1
                if g.uv0 is None and m.materials and s.material < len(m.materials) and m.materials[s.material].albedo():
                    issues['textured_without_uv'].append((p, s.name))
                sk = m.skeleton
                shadow = s.material < len(m.materials) and m.materials[s.material].shadow_only
                stats['shapes_shadow_only_hidden'] += bool(shadow)
                if sk is not None and s.bone < len(sk.bones):
                    vis.append(bool(sk.bones[s.bone].flags & 1) and not shadow)
                else:
                    vis.append(not shadow)
            if vis and not any(vis):
                issues['all_shapes_hidden_by_default'].append((p, m.name))
            elif vis and not all(vis):
                stats['models_with_hidden_variants'] += 1
        if (i + 1) % 1000 == 0:
            print(f'  {i + 1}/{len(files)} ({time.time() - t0:.0f}s)', flush=True)
    print(f'\n{len(files)} model files checked in {time.time() - t0:.0f}s')
    print('Statistics:', dict(stats))
    print('\nFindings (count, examples):')
    for kind, lst in sorted(issues.items(), key=lambda x: -len(x[1])):
        files_n = len({p for p, _ in lst})
        cats = collections.Counter(category(p) for p, _ in lst)
        print(f'- {kind}: {len(lst)} in {files_n} files | categories {dict(cats.most_common(6))}')
        for p, d in lst[:4]:
            print(f'     {p.rpartition("/")[2][:60]} :: {d[:110]}')
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'stats': stats, 'issues': issues}, f, indent=1, ensure_ascii=False)


if __name__ == '__main__':
    main()
