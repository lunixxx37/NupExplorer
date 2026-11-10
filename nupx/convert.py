"""Conversions shared by the viewer endpoints and the exporter."""
from __future__ import annotations

import re

from . import audio, bfres, byml, gltf, msbt, textures
from .vfs import VFS, classify

_STRIP = [(re.compile(r'\.bntx(\.zs)?$', re.I), ''), (re.compile(r'\.bwav$', re.I), ''),
          (re.compile(r'\.bfres(\.zs)?$', re.I), ''), (re.compile(r'\.msbt$', re.I), ''),
          (re.compile(r'\.b?g?yml(\.zs)?$', re.I), ''), (re.compile(r'\.byml(\.zs)?$', re.I), '')]


def base_name(name: str) -> str:
    for rx, rep in _STRIP:
        n2 = rx.sub(rep, name)
        if n2 != name:
            return n2
    return name


def raw_name(name: str) -> str:
    return name[:-3] if name.lower().endswith('.zs') else name


def _decode_named(vfs: VFS, name: str, maxdim: int | None):
    p = vfs.find_texture(name)
    if not p:
        return None
    raw = vfs.read(p)
    t = textures.parse_bntx(raw)[0]
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


def convert_file(vfs: VFS, path: str, fmt: str | None = None) -> list[tuple[str, bytes]]:
    """Convert one file to a viewable format. Returns [(file name, bytes)]."""
    name = path.rpartition('::')[2].rpartition('/')[2]
    t = classify(name)
    data = vfs.read(path)
    base = base_name(name)
    if t == 'texture':
        texs = textures.parse_bntx(data)
        out = []
        for tex in texs:
            if not tex.supported():
                continue
            stem = base if len(texs) == 1 else f'{base}_{tex.name}'
            for layer in range(tex.layers):
                img = textures.decode(data, tex, 0, layer)
                suffix = f'_L{layer}' if tex.layers > 1 else ''
                out.append((f'{stem}{suffix}.png', textures.png_bytes(img, 6)))
        if not out:
            raise ValueError('No supported texture format')
        return out
    if t == 'audio':
        pcm, rate, _info = audio.decode(data)
        return [(base + '.wav', audio.wav_bytes(pcm, rate))]
    if t == 'model':
        models = bfres.parse_models(data)
        if not models or all(s.cluster_error for m in models for s in m.shapes):  # nothing to convert: raw file
            return [(raw_name(name), data)]
        glb = gltf.build_glb(models, 0, texture_png=lambda n, k: texture_png_for_model(vfs, n, k, None, 6),
                             extras={'source': path}, fallback_color=lambda mt: ramp_color(vfs, mt))
        return [(base + '.glb', glb)]
    if t == 'msbt':
        parsed = msbt.parse(data)
        if fmt == 'csv':
            return [(base + '.csv', msbt.to_csv(parsed))]
        return [(base + '.json', msbt.to_json(parsed))]
    if t == 'byml':
        obj = byml.parse(data)
        if fmt == 'json':
            return [(base + '.json', byml.to_json(obj))]
        return [(base + '.yml', byml.to_yaml(obj).encode('utf-8'))]
    return [(raw_name(name), data)]
