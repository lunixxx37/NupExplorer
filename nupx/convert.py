"""Conversions shared by the viewer endpoints and the exporter."""
from __future__ import annotations

import re

from . import audio, bfres, byml, g1m, gltf, msbt, objfile, textures
from .vfs import VFS

_STRIP = [(re.compile(r'\.(bntx|g1t)(\.zs)?$', re.I), ''), (re.compile(r'\.(bwav|ka1a)$', re.I), ''),
          (re.compile(r'\.(bfres|g1m)(\.zs)?$', re.I), ''), (re.compile(r'\.msbt$', re.I), ''),
          (re.compile(r'\.b?g?yml(\.zs)?$', re.I), ''), (re.compile(r'\.byml(\.zs)?$', re.I), '')]


def base_name(name: str) -> str:
    for rx, rep in _STRIP:
        n2 = rx.sub(rep, name)
        if n2 != name:
            return n2
    return name


def raw_name(name: str) -> str:
    return name[:-3] if name.lower().endswith('.zs') else name


def load_models(vfs: VFS, path: str, raw: bytes | None = None) -> list:
    """Models of a BFRES or G1M file; G1M textures are looked up in the game's resource links."""
    raw = vfs.read(path) if raw is None else raw
    if not g1m.is_g1m(raw):
        return bfres.parse_models(raw)
    stem = path.rpartition('::')[2].rpartition('/')[2].rpartition('.')[0]
    models = g1m.parse_models(raw, stem)
    try:
        ktid = int(stem[2:10], 16) if stem.startswith('0x') else None
    except ValueError:
        ktid = None
    if ktid is not None:
        paths = vfs.katana().model_textures(ktid)
        for m in models:
            m.set_textures(paths)
    return models


def _decode_named(vfs: VFS, name: str, maxdim: int | None):
    p = vfs.find_texture(name)
    if not p:
        return None
    raw = vfs.read(p)
    t = textures.parse(raw)[0]
    return textures.decode(raw, t, textures.choose_mip(t, maxdim)), t


IRIS_SCALE = 0.5   # the eye shader shrinks iris + mask around the UV centre (shader parameter); approximation
PUPIL_SCALE = 0.36  # pupil radius relative to the iris radius; the shader draws the pupil itself (approximation)
# common iris of the game; stands in for iris textures missing from the game files (Nabooru, Npc_Hylia_WU_002)
IRIS_FALLBACK = 'Eye_Inner_Iris_CmnTex_Chara_Eye_Alb'


def eye_available(vfs: VFS, layers: str) -> bool:
    """Can eye_composite() build this eye ('iris|sclera|mask')?"""
    iris, sclera, mask = layers.split('|')
    return bool((vfs.find_texture(iris) or vfs.find_texture(IRIS_FALLBACK))
                and vfs.find_texture(sclera) and vfs.find_texture(mask))


def eye_composite(vfs: VFS, iris: str, sclera: str, mask: str, maxdim: int | None = 1024):
    """Eye shader approximation: sclera everywhere, iris (scaled by IRIS_SCALE around the centre)
    where the iris mask is white. Without the scale the iris would fill the whole visible eye."""
    import numpy as np
    from PIL import Image
    a = _decode_named(vfs, iris, maxdim) or _decode_named(vfs, IRIS_FALLBACK, maxdim)
    b = _decode_named(vfs, sclera, maxdim)
    m = _decode_named(vfs, mask, maxdim)
    if a is None or b is None or m is None:
        return a[0] if a is not None else None
    ia = a[0]
    h, w = ia.shape[:2]
    k = 1.0 / IRIS_SCALE
    affine = (k, 0, w / 2 * (1 - k), 0, k, h / 2 * (1 - k))  # output pixel -> source pixel
    iris_img = Image.fromarray(ia, 'RGBA').transform((w, h), Image.AFFINE, affine, Image.BILINEAR)
    mask_img = Image.fromarray(m[0], 'RGBA').resize((w, h), Image.BILINEAR).transform(
        (w, h), Image.AFFINE, affine, Image.BILINEAR, fillcolor=(0, 0, 0, 0))
    sb = np.asarray(Image.fromarray(b[0], 'RGBA').resize((w, h), Image.BILINEAR)).astype(np.float32)
    mm = np.asarray(mask_img)[..., 0:1].astype(np.float32) / 255.0
    out = sb * (1 - mm) + np.asarray(iris_img).astype(np.float32) * mm
    # pupil: the iris texture only has a dark-blue centre, the shader paints the black pupil on top
    iris_r = float(np.sqrt((mm[..., 0] > 0.5).mean() / np.pi))  # iris radius in UV (after scaling)
    if iris_r > 0:
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.sqrt(((xx + 0.5) / w - 0.5) ** 2 + ((yy + 0.5) / h - 0.5) ** 2)
        rp = PUPIL_SCALE * iris_r
        edge = 0.25 * rp
        pupil = np.clip((rp + edge - d) / (2 * edge), 0.0, 1.0)[..., None]
        out[..., :3] = out[..., :3] * (1 - 0.92 * pupil)
    out[..., 3] = 255
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


_RAMP_CACHE: dict = {}


def ramp_color(vfs: VFS, mat) -> list[float] | None:
    """Average colour (linear RGB) of a colour ramp on a generic slot – stands in for a missing albedo
    (e.g. Link's hair: the shader colours the strands from a 64x8 gradient)."""
    import numpy as np
    for name in mat.ramp_textures():
        if name in _RAMP_CACHE:
            if _RAMP_CACHE[name] is not None:
                return _RAMP_CACHE[name]
            continue
        res = None
        d = _decode_named(vfs, name, None)
        if d is not None:
            img, t = d
            is_srgb = t.kind == 6 and t.comp_sel[1] not in (0, 1, 2)
            if is_srgb and t.height <= 16 and t.width >= 4 * t.height:
                c = img[..., :3].reshape(-1, 3).mean(0) / 255.0
                lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
                res = [float(x) for x in lin]
        _RAMP_CACHE[name] = res
        if res is not None:
            return res
    return None


def texture_png_for_model(vfs: VFS, name: str, kind: str, maxdim: int | None = 1024, level: int = 3):
    if kind == 'eye':
        img = eye_composite(vfs, *name.split('|'), maxdim=maxdim)
        return (textures.png_bytes(img, level), False) if img is not None else None
    d = _decode_named(vfs, name, maxdim)
    if d is None:
        return None
    img, t = d
    if kind == 'normal':
        img = textures.to_normal_map(img, t)
    return textures.png_bytes(img, level), (kind == 'color' and textures.has_alpha(img))


# converted export formats per file type: {type: {key: label}}; the first one is the default
EXPORT_FORMATS = {
    'audio': {k: label for k, (label, _ext) in audio.FORMATS.items()},
    'texture': textures.EXPORT_FORMATS,
    'model': {'glb': 'GLB (textures embedded)', 'gltf': 'glTF + .bin + PNG textures',
              'obj': 'OBJ + MTL (static, no skeleton)'},
    'msbt': {'json': 'JSON', 'csv': 'CSV'},
    'byml': {'yml': 'YAML', 'json': 'JSON'},
}
DEFAULT_FORMATS = {t: next(iter(opts)) for t, opts in EXPORT_FORMATS.items()}


def normalize_formats(formats: dict | None) -> dict:
    """Known formats only, defaults for the rest."""
    out = dict(DEFAULT_FORMATS)
    for t, f in (formats or {}).items():
        if f in EXPORT_FORMATS.get(t, {}):
            out[t] = f
    return out


def convert_file(vfs: VFS, path: str, fmt: str | None = None, formats: dict | None = None) -> list[tuple[str, bytes]]:
    """Convert one file to a viewable format. Returns [(file name, bytes)].

    formats: export format per file type (see EXPORT_FORMATS); fmt overrides it for this file's type."""
    name = vfs.export_name(path)  # the real name where a Koei Tecmo game has one
    t = vfs.type_of(path)
    data = vfs.read(path)
    base = base_name(name)
    fmts = normalize_formats(formats)
    if fmt and fmt in EXPORT_FORMATS.get(t, {}):
        fmts[t] = fmt
    if t == 'texture':
        texs = textures.parse(data)
        out = []
        for tex in texs:
            if not tex.supported():
                continue
            stem = base if len(texs) == 1 else f'{base}_{tex.name}'
            if fmts['texture'] == 'dds' and not (tex.tdim == 2 and tex.depth > 1):  # 3D textures: images
                out.append((f'{stem}.dds', textures.dds_bytes(data, tex)))
                continue
            for layer in range(tex.layers):
                img = textures.decode(data, tex, 0, layer)
                suffix = f'_L{layer}' if tex.layers > 1 else ''
                if fmts['texture'] == 'tga':
                    out.append((f'{stem}{suffix}.tga', textures.tga_bytes(img)))
                else:
                    out.append((f'{stem}{suffix}.png', textures.png_bytes(img, 6)))
        if not out:
            raise ValueError('No supported texture format')
        return out
    if t == 'audio':
        pcm, rate, _info = audio.decode(data)
        ext, encoded = audio.encode(pcm, rate, fmts['audio'])
        return [(base + ext, encoded)]
    if t == 'model':
        models = load_models(vfs, path, data)
        if not models or all(s.cluster_error for m in models for s in m.shapes):  # nothing to convert: raw file
            return [(raw_name(name), data)]
        png = lambda n, k: texture_png_for_model(vfs, n, k, None, 6)  # noqa: E731
        colour = lambda mt: ramp_color(vfs, mt)  # noqa: E731
        if fmts['model'] == 'gltf':
            return gltf.build_gltf(models, base, 0, texture_png=png, extras={'source': path}, fallback_color=colour)
        if fmts['model'] == 'obj':
            return objfile.build_obj(models, base, 0, texture_png=png, fallback_color=colour)
        glb = gltf.build_glb(models, 0, texture_png=png, extras={'source': path}, fallback_color=colour)
        return [(base + '.glb', glb)]
    if t == 'msbt':
        parsed = msbt.parse(data)
        if fmts['msbt'] == 'csv':
            return [(base + '.csv', msbt.to_csv(parsed))]
        return [(base + '.json', msbt.to_json(parsed))]
    if t == 'byml':
        obj = byml.parse(data)
        if fmts['byml'] == 'json':
            return [(base + '.json', byml.to_json(obj))]
        return [(base + '.yml', byml.to_yaml(obj).encode('utf-8'))]
    if t == 'video':  # WebM videos (Koei Tecmo stores them as .bin)
        return [(raw_name(name).rpartition('.')[0] + '.webm' if '.' in name else name + '.webm', data)]
    low = name.lower()
    if low.endswith(('.srsa', '.srst')) and data[16:20] == b'KTSR':
        # Koei Tecmo sound: the KTSR inside the 16-byte SRSA/SRST wrapper, under the names vgmstream knows
        return [(name.rpartition('.')[0] + ('.ktsl2asbin' if low.endswith('.srsa') else '.ktsl2stbin'), data[16:])]
    return [(raw_name(name), data)]
