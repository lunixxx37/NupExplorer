"""Smoke test: decode random samples of every asset type and check plausibility.

  python tests/smoke.py [path/to/file.nup] [samples per type]
Checks
  textures  decode without error; smoothness (garbled swizzle = noise); mip 2 ~ downscaled mip 0
  audio     decode; sample count matches header; zero-crossing rate far from white noise
  models    inverse bind matrices reproduce (R = Rz*Ry*Rx); indices inside their vertex slice;
            blend indices map to the shape's skin bone list; GLB structurally valid
  anims     clips bake without NaN
  packed    files the container stores zstd-compressed unpack to their size
  formats   converted export in every selectable format (audio read back, DDS header, glTF/OBJ references)
  ktpack    Koei Tecmo packs (.fdata/.file) list their entries, entries unpack to their size
  g1t       G1T textures in those packs decode (all layers), mip 2 ~ downscaled mip 0
  video     WebM videos are recognised, also as .bin
  ktsr      Koei Tecmo sound banks (.srsa) list readable sound names; sampled sounds (in the bank or in the stream
            file, Blowfish) decode to the sample count of their KA1A header; external sounds are found
  g1m       Koei Tecmo models: geometry inside the model's bounding box, GLB structurally valid, at least 90 % of the
            material textures found through the game's resource links (kidsobjdb)
  g2a       Koei Tecmo animations on models with linked motions: frames, finite values, unit quaternions; the skinned
            mesh keeps its shape (median edge stretch of the worst 5 % of edges < 25 %; wrong rotations tear it)
  names     recovered Koei Tecmo names: shown, used by the export, found by the search; G1T / sound bank names
            from the name databases hash to the file id
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
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from nupx import anim as A  # noqa: E402
from nupx import audio, bfres, byml, gltf, msbt, textures  # noqa: E402
from nupx.convert import EXPORT_FORMATS, convert_file, eye_available, load_models, ramp_color  # noqa: E402
from nupx.katana import file_ktid, ktid_hash  # noqa: E402
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
        by_type[v.type_of(p)].append(p)
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
        for t in textures.parse(raw)[:2]:
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
        # several files in one GLB: clip names that occur in more than one file get the file as prefix
        ok = [a for a in anims if a.rot_supported][:2]
        clips, _skipped = A.bake_files([('FileA', ok), ('FileB', ok)])
        names = [h['name'] for h, _ in clips]
        assert len(set(names)) == len(names) == 2 * len(ok), f'clip names not unique: {names}'

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

    def t_formats(spec):
        """Converted export in every selectable format (audio, textures, models)."""
        import io
        import soundfile as sf
        kind, p = spec
        for f in EXPORT_FORMATS[kind]:
            items = convert_file(v, p, formats={kind: f})
            name, data = items[0]
            if kind == 'audio':
                pcm, _rate, info = audio.decode(v.read(p))
                back, _r = sf.read(io.BytesIO(data), dtype='int16', always_2d=True)
                assert abs(len(back) - len(pcm)) <= 1152, f'{f}: {len(back)} samples instead of {len(pcm)}'
                assert back.shape[1] == (min(2, pcm.shape[1]) if f == 'mp3' else pcm.shape[1]), f'{f}: channels'
            elif kind == 'texture':
                if f == 'dds':
                    assert data[:4] == b'DDS ' and data[84:88] == b'DX10' and struct.unpack_from('<I', data, 128)[0], 'DDS header'
                else:
                    Image.open(io.BytesIO(data)).load()
            elif f == 'gltf':
                js = json.loads(data)
                names = {n for n, _ in items}
                assert all(urllib.parse.unquote(i['uri']) in names for i in js.get('images', [])), 'glTF texture files'
            elif f == 'obj':
                txt = data.decode()
                nv = txt.count('\nv ')
                idx = [int(t.split('/')[0]) for line in txt.splitlines() if line.startswith('f ') for t in line.split()[1:]]
                assert idx and max(idx) <= nv, 'OBJ face indices'

    def t_ktpack(p):
        """Koei Tecmo packs (.fdata / .file): every entry is listed, sampled entries unpack to their size."""
        arc = v.open_archive(p)
        names = arc.sarc.names()
        assert names, 'empty pack'
        for m in random.sample(names, min(3, len(names))):
            if arc.sarc.size(m) <= 64 << 20:
                assert len(v.read(f'{p}::{m}')) == arc.sarc.size(m), m

    g1t_errs = []

    def t_g1t(p):
        """G1T textures inside Koei Tecmo packs: every layer decodes, mip 2 matches the downscaled mip 0."""
        from nupx import g1t
        raw = v.read(p)
        for t in g1t.parse(raw):
            if not t.supported():
                continue
            img = textures.decode(raw, t, 0, t.layers - 1)
            assert img.shape[:2] == (t.height, t.width)
            if t.mips > 2 and min(t.width, t.height) >= 64:
                i0 = np.ascontiguousarray(textures.decode(raw, t, 0, 0)[..., :3])  # RGB only: masks may sit in alpha 0
                m2 = textures.decode(raw, t, 2, 0)[..., :3].astype(np.int32)
                small = np.asarray(Image.fromarray(i0).resize((m2.shape[1], m2.shape[0]), Image.BOX)).astype(np.int32)
                g1t_errs.append(float(np.abs(small - m2).mean()))

    def t_video(p):
        """WebM videos, also when they are stored as .bin (content sniffing)."""
        assert v.type_of(p) == 'video' and v.c.read_head(p, 4) == b'\x1aE\xdf\xa3'

    ka1a_zcrs = []

    def t_ktsr(p):
        """KTSR sound bank: readable names, sampled sounds decode (internal and from the stream file)."""
        arc = v.open_archive(p)
        bank = arc.sarc
        names = bank.names()
        assert names, 'no sounds'
        assert all(n.isprintable() for n in names), 'unreadable sound names'
        ext = [n for n in names if bank.sound(n).external]
        picks = random.sample(names, min(2, len(names))) + random.sample(ext, min(1, len(ext)))
        for m in picks:
            mp = f'{p}::{m}'
            data = v.read(mp)
            assert v.head(mp, 0x28) == data[:0x28], 'head() differs from read()'
            pcm, rate, info = audio.decode(data)
            assert pcm.shape == (info.samples, info.channels), f'{m}: {pcm.shape} for {info.samples} samples'
            if pcm.shape[0] > 4800 and np.abs(pcm).max() > 300:
                # loudest channel (5.1 voices: the centre), active 20 ms windows only: the pauses of voice
                # recordings carry a faint high-frequency noise floor with a noise-like crossing rate
                f = pcm.astype(np.float64)
                a = f[:, int(np.argmax((f ** 2).mean(0)))]
                w = a[:len(a) // 960 * 960].reshape(-1, 960)
                e = np.sqrt((w ** 2).mean(1))
                act = w[e > 0.1 * e.max()] if len(e) else w
                if len(act):
                    ka1a_zcrs.append(float((np.diff(np.sign(act), axis=1) != 0).mean()))

    g1m_tex = [0, 0]  # texture references found / total

    def t_g1m(p):
        """Koei Tecmo model: geometry within the bounding box of the file, valid GLB, textures linked."""
        models = load_models(v, p)
        m = models[0]
        refs = [t for mt in m.materials for t in mt.textures]
        g1m_tex[0] += sum(bool(t) for t in refs)
        g1m_tex[1] += len(refs)
        lo, hi = np.asarray(m.bbox[:3]), np.asarray(m.bbox[3:])
        tol = 0.05 * float(np.max(hi - lo)) + 1e-3
        for s in m.shapes:
            g = m.geometry(s)
            if g is None or not len(g.positions) or not np.ptp(g.positions, 0).any():
                continue  # cloth-driven or placeholder meshes (all vertices at the origin)
            assert g.indices.max() < len(g.positions), f'{s.name}: index out of range'
            assert (g.positions.min(0) >= lo - tol).all() and (g.positions.max(0) <= hi + tol).all(), \
                f'{s.name}: geometry outside the bounding box'
        check_glb(gltf.build_glb(models, 0, texture_uri=lambda name, kind: 'x.png' if name else None))

    g2a_stretch = []

    def skin_stretch(model, header, data, frame):
        """95th percentile of the relative edge length change of the skinned mesh at a frame vs. the bind pose."""
        skel = model.skeleton
        idx = {h['name']: i for i, h in enumerate(header['bones'])}
        local = []
        for b in skel.bones:
            i = idx.get(b.name)
            if i is None:
                local.append(b.local)
                continue
            x, y, z, w = (data[i, frame, 3:7] / np.linalg.norm(data[i, frame, 3:7])).astype(np.float64)
            r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            m = np.eye(4)
            m[:3, :3] = r * data[i, frame, 7:10]
            m[:3, 3] = data[i, frame, 0:3]
            local.append(m)
        world = [None] * len(skel.bones)
        for j, b in enumerate(skel.bones):  # parents can follow their children: resolve recursively
            chain = []
            k = j
            while k >= 0 and world[k] is None:
                chain.append(k)
                k = skel.bones[k].parent
            for k in reversed(chain):
                p = skel.bones[k].parent
                world[k] = local[k] if p < 0 else world[p] @ local[k]
        skin = np.stack([world[j] @ np.linalg.inv(b.world) for j, b in enumerate(skel.bones)])
        out = []
        for s in model.shapes:
            g = model.geometry(s)
            if g is None or g.joints is None:
                continue
            P = np.concatenate([g.positions.astype(np.float64), np.ones((len(g.positions), 1))], 1)
            M = np.einsum('vk,vkij->vij', g.weights.astype(np.float64), skin[g.joints.astype(np.int64)])
            Q = np.einsum('vij,vj->vi', M, P)[:, :3]
            t = g.indices.reshape(-1, 3).astype(np.int64)
            for a, b in ((0, 1), (1, 2), (2, 0)):
                l0 = np.linalg.norm(g.positions[t[:, a]] - g.positions[t[:, b]], axis=1)
                l1 = np.linalg.norm(Q[t[:, a]] - Q[t[:, b]], axis=1)
                ok = l0 > 1e-4
                out.append(np.abs(l1[ok] / l0[ok] - 1))
        return float(np.percentile(np.concatenate(out), 95)) if out else 0.0

    def t_g2a(p):
        """Animations of a model with motions linked by the game: bake two clips, check values and mesh shape."""
        models = load_models(v, p)
        m = models[0]
        for ap in v.anim_candidates(p)[:2]:
            clip = A.parse_anims(v.read(ap), 'x')[0]
            header, data = A.bake(clip, m.skeleton)
            assert header['frames'] == clip.frame_count + 1 and data.shape[1] == header['frames'], 'frame count'
            assert np.isfinite(data).all(), 'non-finite values'
            assert np.allclose(np.linalg.norm(data[:, :, 3:7], axis=2), 1.0, atol=1e-3), 'quaternions not normalised'
            g2a_stretch.append(skin_stretch(m, header, data, header['frames'] // 2))

    def t_names(item):
        """A recovered Koei Tecmo name: shown, used for exports, found by the search; file names from the name
        databases hash to the file's id (model names are those of the game's model objects, UI texture names come
        from the game's name files)."""
        p, title, ext = item
        kt = int(p.rpartition('::')[2][2:10], 16)
        assert v.describe(p)[0] == title, 'name not shown'
        assert v.export_name(p) != p.rpartition('::')[2], 'export keeps the hash name'
        if ext != 'g1m' and v.katana().data()['file_names'].get(kt) == title:
            assert kt in (file_ktid(ext.upper(), title), ktid_hash(title)), 'name does not hash to the file id'
        assert any(i['path'] == p for i in v.search(title)['items']), 'not found by the search'

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

    def pick(items, k):  # games without some type (Koei Tecmo packs instead of Nintendo formats)
        return random.sample(items, min(k, len(items)))

    run('texture', pick(by_type['texture'], n), t_texture)
    run('audio', pick(by_type['audio'], n), t_audio)
    run('model', pick(by_type['model'], n), t_model)
    run('anim', pick(by_type['anim'], max(5, n // 4)), t_anim)
    idle_specs = [
        ('romfs/Model/Player_Child.Player_Child.bfres.zs', 'romfs/Model/Player_Child_04_Animation.anim.bfres.zs', 'Wait'),
        ('romfs/Model/Animal_Cow.Animal_Cow.bfres.zs', 'romfs/Model/Animal_Cow_Animation.anim.bfres.zs', 'Wait'),
    ]
    run('idle', [s for s in idle_specs if s[0] in v.c.files and s[1] in v.c.files], t_idle_pose)
    gp = 'romfs/Model/Dm_DeathMtTrail_0030_Npc_Goron_GON_001_A.anim.bfres.zs'
    run('visib', [gp] if gp in v.c.files else [], t_goron_vis)
    rp = 'romfs/Model/Npc_Zora_ZU_Ruto_Child.Npc_Zora_ZU_Ruto_Child.bfres.zs'
    lp = 'romfs/Model/Player_Child.Player_Child.bfres.zs'
    run('render', [x for x in (rp, lp) if x in v.c.files] + pick(by_type['model'], n), t_render_modes)
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
    kt = [p for p in by_type['archive'] if p.lower().endswith(('.fdata', '.file'))]
    banks, g1ms = [], []
    for p in sorted(kt):
        if p.endswith('.fdata'):
            for m in v.open_archive(p).sarc.names():
                if m.endswith('.srsa'):
                    banks.append(f'{p}::{m}')
                elif m.endswith('.g1m'):
                    g1ms.append(f'{p}::{m}')
    short_sounds = []  # a short Koei Tecmo sound for the export formats test
    for b in pick(banks, 5):
        bank = v.open_archive(b).sarc
        short_sounds += [f'{b}::{m}' for m in bank.names() if m.endswith('.ka1a') and bank.sound(m).samples < 30 * 48000]
    fmt_specs = [(k, pick(lst, 1)[0]) for k, lst in (('audio', by_type['audio']), ('audio', short_sounds),
                 ('texture', by_type['texture']), ('model', [p for p in by_type['model'] if p.startswith('romfs/Model/')]),
                 ('model', g1ms[::max(1, len(g1ms) // 50)])) if lst]
    run('formats', fmt_specs, t_formats)
    run('ktpack', pick(kt, n), t_ktpack)
    g1ts = [f'{p}::{m}' for p in pick([x for x in kt if x.endswith('.fdata')], 30) for m in v.open_archive(p).sarc.names()
            if m.endswith('.g1t')]
    run('g1t', pick(g1ts, n), t_g1t)
    run('ktsr', pick(banks, n), t_ktsr)
    run('g1m', pick(g1ms, n), t_g1m)
    linked = []
    if g1ms:
        kd = v.katana().data()
        linked = [p for p in g1ms if int(p.rpartition('::')[2][2:10], 16) in kd['model_anims']]
    run('g2a', pick(linked, 10), t_g2a)
    titled = []
    if g1ms:  # sample each kind of name (UI texture names from name files would outnumber the rest)
        file_names = v.katana().data()['file_names']
        groups = collections.defaultdict(list)
        for p, title, _text, ext, _size in v.katana().search_entries():
            if title:
                ui = ext == 'g1t' and file_names.get(int(p.rpartition('::')[2][2:10], 16)) != title
                groups[f'{ext} (UI)' if ui else ext].append((p, title, ext))
        print('Names:', ', '.join(f'{k} {len(x)}' for k, x in sorted(groups.items())))
        titled = [it for x in groups.values() for it in pick(x, max(5, n // 4))]
    run('names', titled, t_names)
    run('video', pick(by_type['video'], 10), t_video)
    run('byml', pick(by_type['byml'], n), t_byml)
    run('msbt', random.sample(msbt_files, min(n, len(msbt_files))), t_msbt)

    pc = lambda a, q: f'{np.percentile(a, q):.3g}' if len(a) else '-'  # noqa: E731
    print('Results (ok/total, time):')
    for k, (ok, tot, dt) in results.items():
        print(f'  {k:8s} {ok:4d}/{tot:<4d} {dt:6.1f}s')
    print(f'Textures: smoothness p50={pc(tex_tv, 50)} p95={pc(tex_tv, 95)} | Mip2 vs. Mip0 p50={pc(mip_err, 50)} p95={pc(mip_err, 95)}')
    print(f'Audio: Zero-Crossing-Rate p50={pc(zcrs, 50)} p95={pc(zcrs, 95)} (white noise ~0.5)')
    print(f'Skeletons: |InvBind*World - I| max={max(inv_err) if inv_err else 0:.2e} ({len(inv_err)} Bones)')
    if g1t_errs:
        print(f'G1T: Mip2 vs. Mip0 p50={pc(g1t_errs, 50)} p95={pc(g1t_errs, 95)}')
        if np.percentile(g1t_errs, 95) > 25:
            fails.append(('g1t', '-', 'mip levels do not match (layout?)'))
    if g2a_stretch:
        print(f'G2A: skinned mesh edge stretch (p95 per clip) p50={pc(g2a_stretch, 50)} max={max(g2a_stretch):.3g}')
        if np.median(g2a_stretch) > 0.25:
            fails.append(('g2a', '-', 'animated meshes tear (rotation convention?)'))
    if g1m_tex[1]:
        print(f'G1M: textures linked {g1m_tex[0]}/{g1m_tex[1]} ({g1m_tex[0] / g1m_tex[1]:.1%})')
        if g1m_tex[0] < 0.9 * g1m_tex[1]:
            fails.append(('g1m', '-', 'textures of the models not found'))
    if ka1a_zcrs:
        print(f'KA1A: Zero-Crossing-Rate p50={pc(ka1a_zcrs, 50)} p95={pc(ka1a_zcrs, 95)} (white noise ~0.5)')
        if np.percentile(ka1a_zcrs, 95) > 0.35:
            fails.append(('ktsr', '-', 'decoded sounds look like noise'))
    known = sum(c for k, c in render_modes.items() if k) / max(1, sum(render_modes.values()))
    print(f'Materials: render mode recognised {known:.1%} {dict(render_modes)}')
    if render_modes and known < 0.95:  # only for games with Nintendo (BFRES) models
        fails.append(('render', '-', f'only {known:.1%} of the materials have a render mode'))
    for f in fails[:15]:
        print('  ERROR', f)
    ok = (not fails and (not inv_err or max(inv_err) < 1e-3)
          and (not mip_err or np.percentile(mip_err, 95) < 25) and (not zcrs or np.percentile(zcrs, 95) < 0.35))
    print('RESULT:', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
