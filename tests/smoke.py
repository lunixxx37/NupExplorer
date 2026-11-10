"""Smoke test: decode random samples of every asset type and check plausibility.

  python tests/smoke.py [path/to/file.nup] [samples per type]
Checks
  textures  decode without error; smoothness (garbled swizzle = noise); mip 2 ~ downscaled mip 0
  audio     decode; sample count matches header; zero-crossing rate far from white noise
  models    inverse bind matrices reproduce (R = Rz*Ry*Rx); indices inside their vertex slice;
            blend indices map to the shape's skin bone list; GLB structurally valid
  anims     clips bake without NaN
  packed    files the container stores zstd-compressed unpack to their size
  msbt/byml parse without exceptions
  regressions for Ocarina of Time and Mario Kart World run when their files exist
"""
import collections
import json
import os
import random
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from nupx import anim as A  # noqa: E402
from nupx import audio, bfres, byml, gltf, msbt, textures  # noqa: E402
from nupx.convert import eye_available, ramp_color  # noqa: E402
from nupx.vfs import VFS, classify  # noqa: E402


def tv(a):
    a = a[..., :3].astype(np.int32)
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


def check_glb(glb: bytes):
    assert glb[:4] == b'glTF' and struct.unpack_from('<I', glb, 8)[0] == len(glb)
    n = struct.unpack_from('<I', glb, 12)[0]
    js = json.loads(glb[20:20 + n])
    binstart = 20 + n + 8
    blen = js['buffers'][0]['byteLength']
    for acc in js['accessors']:
        bv = js['bufferViews'][acc['bufferView']]
        assert bv['byteOffset'] + bv['byteLength'] <= blen
    nj = len(js['skins'][0]['joints']) if js.get('skins') else 0
    for mesh in js.get('meshes', []):
        at = mesh['primitives'][0]['attributes']
        if 'JOINTS_0' in at:
            acc = js['accessors'][at['JOINTS_0']]
            bv = js['bufferViews'][acc['bufferView']]
            j = np.frombuffer(glb, '<u2', acc['count'] * 4, binstart + bv['byteOffset'])
            assert j.max() < nj, 'joint index out of range'
            acc = js['accessors'][at['WEIGHTS_0']]
            bv = js['bufferViews'][acc['bufferView']]
            w = np.frombuffer(glb, '<f4', acc['count'] * 4, binstart + bv['byteOffset']).reshape(-1, 4)
            assert np.allclose(w.sum(1), 1.0, atol=1e-3), 'weights do not sum to 1'
        pos = js['accessors'][at['POSITION']]
        assert 'min' in pos and 'max' in pos


def main():
    nup = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('NUP_FILE')
    if not nup:
        sys.exit(f'usage: python {sys.argv[0]} path/to/file.nup  (or set NUP_FILE)')
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    cache = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'NupExplorer', 'cache')
    v = VFS(nup, cache_dir=cache, log=lambda *a: None)
    by_type = collections.defaultdict(list)
    for p in v.c.files:
        by_type[classify(p)].append(p)
    random.seed(12345)
    results = {}
    fails = []

    def run(kind, items, fn):
        ok = 0
        t0 = time.time()
        for p in items:
            try:
                fn(p)
                ok += 1
            except Exception as e:  # noqa: BLE001
                fails.append((kind, p, f'{type(e).__name__}: {e}'))
        results[kind] = (ok, len(items), time.time() - t0)

    tex_tv = []
    mip_err = []

    def t_texture(p):
        raw = v.read(p)
        for t in textures.parse_bntx(raw)[:2]:
            img = textures.decode(raw, t, 0, 0)
            if t.ftype not in (0x0a, 0x12, 0x15, 0x0f) and min(img.shape[:2]) > 1:  # data/HDR textures, 1-px LUTs
                tex_tv.append(tv(img))
            if t.mips > 2 and min(t.width, t.height) >= 64:
                m2 = textures.decode(raw, t, 2, 0)
                small = np.asarray(Image.fromarray(img).resize((m2.shape[1], m2.shape[0]), Image.BOX)).astype(np.int32)
                mip_err.append(float(np.abs(small[..., :3] - m2[..., :3].astype(np.int32)).mean()))

    zcrs = []

    def t_audio(p):
        pcm, rate, info = audio.decode(v.read(p))
        assert pcm.shape[0] == info.samples, 'sample count'
        if pcm.shape[0] > 4800 and np.abs(pcm).max() > 300:
            a = pcm[:, 0].astype(np.float64)
            zcrs.append(float((np.diff(np.sign(a)) != 0).mean()))

    inv_err = []

    def t_model(p):
        models = bfres.parse_models(v.read(p))
        for m in models:
            sk = m.skeleton
            for s in m.shapes:
                g = bfres.shape_geometry(m, s, 0)
                if g is None:
                    continue
                assert g.indices.max() < len(g.positions)
                assert np.isfinite(g.positions).all()
                if s.skin_count and sk is not None and s.skin_bones:
                    used = set(np.unique(g.joints[g.weights > 0]).tolist())
                    assert used <= set(s.skin_bones) | {sk.bones[s.bone].index}, 'skin bones mismatch'
        if models:
            check_glb(gltf.build_glb(models, 0))
        # inverse bind self test via the stored matrices
        raw = v.read(p)
        for m in models:
            sk = m.skeleton
            if sk is None or not sk.num_smooth:
                continue
            fskl = raw.find(b'FSKL')
            inv_off = struct.unpack_from('<Q', raw, fskl + 0x20)[0]
            for k in range(min(sk.num_smooth, 40)):
                b = sk.bones[sk.m2b[k]]
                inv = np.eye(4)
                inv[:3, :] = np.frombuffer(raw, '<f4', 12, inv_off + k * 48).reshape(3, 4)
                inv_err.append(float(np.abs(inv @ b.world - np.eye(4)).max()))

    def t_anim(p):
        raw = v.read(p)
        anims = A.parse_anims(raw)
        for a in anims[:3]:
            hdr, data = A.bake(a)
            assert np.isfinite(data).all(), 'NaN in baked clip'
        for va in A.parse_vis_anims(raw):
            tr = va.track()
            assert len(tr) == len(va.names), 'visibility track incomplete'

    render_modes = collections.Counter()

    def t_render_modes(p):
        """Regression: alpha test only where the material says 'mask' (Zora skin keeps masks in alpha)."""
        for m in bfres.parse_models(v.read(p)):
            for mt in m.materials:
                render_modes[mt.render_mode] += 1
            modes = {mt.name: mt.render_mode for mt in m.materials}
            if 'Ruto_Child' in p:
                assert modes.get('Mt_Skin') == 'OPAQUE' and modes.get('Mt_Eyelash') == 'MASK', modes
            if p.endswith('Player_Child.Player_Child.bfres.zs'):
                mats = {mt.name: mt for mt in m.materials}
                # eye shadow is a red mask on a _gn slot, not a colour texture
                assert mats['Mt_EyeShadow_test'].albedo() is None, 'eye shadow mask used as colour'
                assert mats['Mt_Eye_Inner'].eye_layers() is not None, 'eye layers not found'
                col = ramp_color(v, mats['Mt_Hairs'])  # blond hair from the colour ramp
                assert col is not None and col[0] > col[2] * 2, f'hair colour {col}'

    def t_shader_materials(spec):
        """Regression (model QA): colours from shader parameters, shadow proxies, eye detection, fallbacks."""
        path, check = spec
        mats = {mt.name: mt for m in bfres.parse_models(v.read(path)) for mt in m.materials}
        if check == 'gem':  # Goron's Ruby: no colour texture, red from the shader colour parameters
            col = mats['Mt_Item_Stone_Gem'].palette_color()
            assert col is not None and col[0] > 3 * col[1] and col[0] > 3 * col[2], f'gem colour {col}'
            assert mats['Mt_Item_Stone_Fire'].palette_color() is None, 'textured material got a shader colour'
        elif check == 'shadow':  # shadow proxy: only drawn into shadow maps
            assert mats['Mt_BakeShadowProxy_Outer'].shadow_only, 'shadow proxy not detected'
            assert sum(mt.shadow_only for mt in mats.values()) == 1, 'normal materials flagged shadow-only'
        elif check == 'wall':  # blend walls share the eye slot layout but are no eyes
            assert not any(mt.eye_layers() for mt in mats.values()), 'wall material treated as eye'
        elif check == 'nabooru':  # iris texture missing in the game files -> common iris
            assert eye_available(v, '|'.join(mats['Mt_Eye_Inner'].eye_layers())), 'eye fallback'
        assert v.find_texture('Metal_GanonCastle_RepeatWall_A_Alb'), 'texture alias lookup'

    def t_cluster(p):
        """Regression: compressed meshlet geometry (clusters.py) of the Fire Temple pillar."""
        m = bfres.parse_models(v.read(p))[0]
        s = m.shapes[0]
        assert s.cluster is not None and m.lod_count == 10, 'cluster shape not detected'
        g0 = bfres.shape_geometry(m, s, 0)
        g9 = bfres.shape_geometry(m, s, 9)
        assert len(g0.indices) // 3 == 4972 and len(g9.indices) // 3 == 247, 'triangle counts per LOD'
        assert g0.indices.max() < len(g0.positions)
        lo, hi = g0.positions.min(0), g0.positions.max(0)  # round pillar on the origin, 3.18 m high
        assert np.allclose(lo, [-0.778, -0.034, -0.817], atol=0.005) and np.allclose(hi, [0.778, 3.143, 0.817], atol=0.005), (lo, hi)
        assert np.allclose(np.linalg.norm(g0.normals, axis=1), 1, atol=1e-3)
        assert g0.uv0.min() > -0.01 and g0.uv0[:, 0].min() > 0.99 and g0.uv0[:, 0].max() < 2.01 and g0.uv0[:, 1].max() < 1.01, 'uv'
        check_glb(gltf.build_glb([m], 0))

    def t_goron_vis(p):
        """Regression: rolling Goron clips show the ball form, not the normal body."""
        va = A.parse_vis_anims(v.read(p))[0].track()
        assert va['BallBody'][0][1] == 1 and va['NormalBody'][0][1] == 0, 'Goron ball visibility'

    def angle(q1, q2):
        q1 = q1 / np.linalg.norm(q1)
        q2 = q2 / np.linalg.norm(q2)
        return float(np.degrees(2 * np.arccos(min(1.0, abs(float(np.dot(q1, q2)))))))

    def t_idle_pose(spec):
        """Regression: an idle clip must start close to the bind pose (catches wrong rotation encodings)."""
        model_path, anim_path, clip = spec
        sk = bfres.parse_models(v.read(model_path))[0].skeleton
        bq = {b.name: np.array(b.quat()) for b in sk.bones}
        a = [x for x in A.parse_anims(v.read(anim_path)) if x.name == clip][0]
        hdr, data = A.bake(a, sk)
        dev = [angle(data[i, 0, 3:7].astype(float), bq[b['name']]) for i, b in enumerate(hdr['bones']) if b['name'] in bq]
        assert np.median(dev) < 5, f'{clip}: median deviation from bind {np.median(dev):.1f} deg'
        for i, b in enumerate(hdr['bones']):
            if b['name'] in ('Skl_Root', 'Spine_1', 'Spine_2'):
                d = angle(data[i, 0, 3:7].astype(float), bq[b['name']])
                assert d < 20, f'{clip}: {b["name"]} {d:.1f} deg from bind'

    def t_packed(p):
        """Regression: files the container stores zstd-compressed (record compression 1, e.g. Mario Kart World)."""
        data = v.c.read(p)
        assert len(data) == v.c.files[p][1], 'unpacked size'
        assert v.c.read_head(p, 64) == data[:64], 'read_head'

    def t_matching(spec):
        """Regression: animation <-> model matching by name (Mario Kart World DriverA_* / costumes)."""
        anim_path, model_path, costume_path, base_anim = spec
        assert v.model_candidates(anim_path)[:1] == [model_path], v.model_candidates(anim_path)[:3]
        bones = {b.name for m in bfres.parse_models(v.read(costume_path)) for b in m.skeleton.bones}
        assert base_anim in v.anim_candidates(costume_path, bones=bones), 'base character animations for a costume'
        assert not v.has_skeletal('romfs/Model/Driver_YoshiHachimaki.anim.bfres.zs'), 'material-only file'

    def t_r16g16(p):
        """Regression: R16G16 UNORM height maps (format 0x12), alpha forced to 0 by the channel mapping."""
        raw = v.read(p)
        t = textures.parse_bntx(raw)[0]
        img = textures.decode(raw, t, 0, 0)
        assert t.ftype == 0x12 and img[..., 3].min() == 255 and img[..., 0].std() > 5, 'height map'

    def t_byml(p):
        obj = byml.parse(v.read(p))
        byml.to_yaml(obj)
        byml.to_json(obj)

    msbt_files = []
    for p in by_type['archive']:
        if p.startswith('romfs/Mals/'):
            arc = v.open_archive(p)
            msbt_files += [f'{p}::{m}' for m in arc.sarc.names() if m.endswith('.msbt')]

    def t_msbt(p):
        r = msbt.parse(v.read(p))
        assert r['rows'] is not None

    run('texture', random.sample(by_type['texture'], n), t_texture)
    run('audio', random.sample(by_type['audio'], n), t_audio)
    run('model', random.sample(by_type['model'], n), t_model)
    run('anim', random.sample(by_type['anim'], max(5, n // 4)), t_anim)
    idle_specs = [
        ('romfs/Model/Player_Child.Player_Child.bfres.zs', 'romfs/Model/Player_Child_04_Animation.anim.bfres.zs', 'Wait'),
        ('romfs/Model/Animal_Cow.Animal_Cow.bfres.zs', 'romfs/Model/Animal_Cow_Animation.anim.bfres.zs', 'Wait'),
    ]
    run('idle', [s for s in idle_specs if s[0] in v.c.files and s[1] in v.c.files], t_idle_pose)
    gp = 'romfs/Model/Dm_DeathMtTrail_0030_Npc_Goron_GON_001_A.anim.bfres.zs'
    run('visib', [gp] if gp in v.c.files else [], t_goron_vis)
    rp = 'romfs/Model/Npc_Zora_ZU_Ruto_Child.Npc_Zora_ZU_Ruto_Child.bfres.zs'
    lp = 'romfs/Model/Player_Child.Player_Child.bfres.zs'
    run('render', [x for x in (rp, lp) if x in v.c.files] + random.sample(by_type['model'], n), t_render_modes)
    shader_specs = [('romfs/Model/Item_Stone_Fire.Item_Stone_Fire.bfres.zs', 'gem'),
                    ('romfs/Model/Land_Gerudo_FortressEntrance_A.Land_Gerudo_FortressEntrance_A_01.bfres.zs', 'shadow'),
                    ('romfs/Model/Land_ShadowTemple_Building_A.Land_ShadowTemple_Room11_A_01.bfres.zs', 'wall'),
                    ('romfs/Model/Npc_Gerudo_GEU_Nabooru.Npc_Gerudo_GEU_Nabooru.bfres.zs', 'nabooru')]
    run('shader', [s for s in shader_specs if s[0] in v.c.files], t_shader_materials)
    cp = 'romfs/Model/Obj_FireTemple_FirePillar_A.Obj_FireTemple_FirePillar_A_01.bfres.zs'
    run('cluster', [cp] if cp in v.c.files else [], t_cluster)
    packed = [p for p in v.c.files if v.c.is_packed(p)]
    run('packed', random.sample(packed, min(n, len(packed))), t_packed)
    mk = 'romfs/Model/'
    match_spec = (mk + 'DriverA_YoshiHachimaki_Kart.anim.bfres.zs', mk + 'Driver_YoshiHachimaki.Driver_YoshiHachimaki.bfres.zs',
                  mk + 'Driver_YoshiCasual.Driver_YoshiCasual.bfres.zs', mk + 'DriverA_Yoshi_Kart.anim.bfres.zs')
    run('matching', [match_spec] if all(x in v.c.files for x in match_spec) else [], t_matching)
    hp = 'romfs/Tex/Map_GroundHeight_0_0_GroundHeight.bntx.zs'
    run('r16g16', [hp] if hp in v.c.files else [], t_r16g16)
    run('byml', random.sample(by_type['byml'], n), t_byml)
    run('msbt', random.sample(msbt_files, min(n, len(msbt_files))), t_msbt)

    pc = lambda a, q: f'{np.percentile(a, q):.3g}' if len(a) else '-'  # noqa: E731
    print('Results (ok/total, time):')
    for k, (ok, tot, dt) in results.items():
        print(f'  {k:8s} {ok:4d}/{tot:<4d} {dt:6.1f}s')
    print(f'Textures: smoothness p50={pc(tex_tv, 50)} p95={pc(tex_tv, 95)} | Mip2 vs. Mip0 p50={pc(mip_err, 50)} p95={pc(mip_err, 95)}')
    print(f'Audio: Zero-Crossing-Rate p50={pc(zcrs, 50)} p95={pc(zcrs, 95)} (white noise ~0.5)')
    print(f'Skeletons: |InvBind*World - I| max={max(inv_err) if inv_err else 0:.2e} ({len(inv_err)} Bones)')
    known = sum(c for k, c in render_modes.items() if k) / max(1, sum(render_modes.values()))
    print(f'Materials: render mode recognised {known:.1%} {dict(render_modes)}')
    if known < 0.95:
        fails.append(('render', '-', f'only {known:.1%} of the materials have a render mode'))
    for f in fails[:15]:
        print('  ERROR', f)
    ok = (not fails and (not inv_err or max(inv_err) < 1e-3)
          and (not mip_err or np.percentile(mip_err, 95) < 25) and (not zcrs or np.percentile(zcrs, 95) < 0.35))
    print('RESULT:', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
