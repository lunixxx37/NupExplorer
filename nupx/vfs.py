"""Virtual filesystem on top of the NUP container.

Paths:
  romfs/Model/X.bfres.zs                    file in the merged container view
  romfs/Pack/Actor/A.pack.zs::Actor/B.bgyml file inside an archive (SARC)
  romfs/Pack/Actor/A.pack.zs::Actor/        directory inside an archive (trailing slash)
Archives may be nested (a member can itself be an archive).
"""
from __future__ import annotations

import os
import re
import threading
import zlib

from .anim import has_skeletal as has_skeletal_anims
from .anim import skeletal_bone_names
from .container import NupContainer
from .sarc import Sarc
from .util import ByteLRU
from .zs import ZstdCodec, is_zstd

TYPE_LABELS = {
    'texture': 'Texture', 'audio': 'Audio', 'model': '3D model', 'anim': 'Animation',
    'msbt': 'Text (MSBT)', 'byml': 'Data (BYML)', 'archive': 'Archive', 'other': 'File',
}
ARCHIVE_EXTS = ('.pack', '.sarc', '.blarc', '.bfarc', '.msarc')


def classify(name: str) -> str:
    n = name.lower()
    if n.endswith('.zs'):
        n = n[:-3]
    if n.endswith('.bntx'):
        return 'texture'
    if n.endswith('.bwav'):
        return 'audio'
    if n.endswith('.anim.bfres'):
        return 'anim'
    if n.endswith('.bfres'):
        return 'model'
    if n.endswith('.msbt'):
        return 'msbt'
    if n.endswith(('.byml', '.bgyml', '.byaml')):
        return 'byml'
    if n.endswith(ARCHIVE_EXTS):
        return 'archive'
    return 'other'


def _sort_key(s: str):
    return s.lower()


def _variant_stem(stem: str) -> str:
    """DriverA_Yoshi_Kart -> Driver_Yoshi_Kart. A capital letter appended to a word names a set of
    animations for the models of that word (Mario Kart World: DriverA_* animates Driver_* models)."""
    return '_'.join(t[:-1] if re.fullmatch(r'[A-Z][a-z]{2,}[A-Z]', t) else t for t in stem.split('_'))


def _camel_parents(tok: str) -> list[str]:
    """YoshiHachimaki -> ['Yoshi'], BabyMarioSwim -> ['BabyMario', 'Baby'], DKOffroad -> ['DK']."""
    parts = re.split(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', tok)
    out = [''.join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]
    return [p for p in out if len(p) >= 2]


class Archive:
    def __init__(self, data: bytes):
        self.sarc = Sarc(data)
        dirs: dict[str, tuple[set, list]] = {'': (set(), [])}
        for name in self.sarc.names():
            parent, _, base = name.rpartition('/')
            if parent not in dirs:
                parts = parent.split('/')
                for i in range(len(parts)):
                    d = '/'.join(parts[:i + 1])
                    if d not in dirs:
                        dirs[d] = (set(), [])
                        dirs['/'.join(parts[:i])][0].add(parts[i])
            dirs[parent][1].append(base)
        self.dirs = {k: (sorted(v[0], key=_sort_key), sorted(v[1], key=_sort_key)) for k, v in dirs.items()}
        self.nbytes = len(data)


class VFS:
    def __init__(self, nup_path: str, cache_dir: str | None = None, log=print):
        self.c = NupContainer(nup_path, cache_dir=cache_dir, log=log)
        self.zstd = ZstdCodec()
        dic_path = 'romfs/Pack/ZsDic.pack.zs'
        if dic_path in self.c.files:
            pack = Sarc(self.zstd.decompress(self.c.read(dic_path)))
            for name in pack.names():
                if name.endswith('.zsdic'):
                    self.zstd.add_dict(name, pack.get(name))
        self._archives = ByteLRU(512 * 1024 * 1024)
        self._arc_lock = threading.Lock()
        self._search_cache = None
        self._tex_index = None
        self._tex_alias: dict[str, str | None] = {}
        self._skel_cache: dict[str, bool] = {}
        self._bone_cache: dict[str, frozenset] = {}
        self._anims: list[str] | None = None
        self._models: list[str] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reading
    def is_dir(self, path: str) -> bool:
        if '::' in path:
            return path.endswith('/')
        return path == '' or path in self.c.dirs

    def exists(self, path: str) -> bool:
        if '::' not in path:
            return path in self.c.files or path in self.c.dirs
        try:
            arc_path, _, member = path.rpartition('::')
            arc = self.open_archive(arc_path)
            return member in arc.sarc.entries or member.rstrip('/') in arc.dirs
        except Exception:
            return False

    def read_stored(self, path: str) -> bytes:
        """Bytes as stored (possibly zstd compressed)."""
        if '::' in path:
            arc_path, _, member = path.rpartition('::')
            return self.open_archive(arc_path).sarc.get(member)
        return self.c.read(path)

    def read(self, path: str) -> bytes:
        """Bytes after removing the zstd layer."""
        data = self.read_stored(path)
        if is_zstd(data):
            data = self.zstd.decompress(data)
        return data

    def open_archive(self, path: str) -> Archive:
        arc = self._archives.get(path)
        if arc is None:
            data = self.read(path)
            arc = Archive(data)
            self._archives.put(path, arc, arc.nbytes)
        return arc

    # ------------------------------------------------------------------ listing
    def _entry(self, name: str, path: str, size: int, layer: str | None = None) -> dict:
        t = classify(name)
        e = {'name': name, 'path': path, 'kind': 'file', 'type': t, 'size': size}
        if layer:
            e['layer'] = layer
        return e

    def list_dir(self, path: str) -> list[dict]:
        out = []
        if '::' not in path and (path == '' or path in self.c.dirs):
            subdirs, files = self.c.dirs[path]
            prefix = path + '/' if path else ''
            for d in subdirs:
                p = prefix + d
                sd, sf = self.c.dirs[p]
                out.append({'name': d, 'path': p, 'kind': 'dir', 'count': len(sd) + len(sf)})
            upd = self.c.layers[-1] if len(self.c.layers) > 1 else None
            for f in files:
                p = prefix + f
                off, size, _fi, li = self.c.files[p]
                e = self._entry(f, p, size)
                if upd and self.c.layers[li] == upd:
                    e['upd'] = 1
                out.append(e)
            return out
        # inside an archive
        if path.endswith('/') and '::' in path:
            arc_path, _, inner = path.rpartition('::')
            inner = inner.rstrip('/')
        else:
            arc_path, inner = path, ''
        arc = self.open_archive(arc_path)
        subdirs, files = arc.dirs.get(inner, ([], []))
        prefix = inner + '/' if inner else ''
        for d in subdirs:
            dd = prefix + d
            sd, sf = arc.dirs[dd]
            out.append({'name': d, 'path': f'{arc_path}::{dd}/', 'kind': 'dir', 'count': len(sd) + len(sf)})
        for f in files:
            m = prefix + f
            out.append(self._entry(f, f'{arc_path}::{m}', arc.sarc.size(m)))
        return out

    def meta(self, path: str) -> dict:
        name = path.rstrip('/').rpartition('::')[2].rpartition('/')[2]
        m = {'path': path, 'name': name, 'type': classify(name), 'type_label': TYPE_LABELS[classify(name)]}
        if '::' in path:
            arc_path, _, member = path.rpartition('::')
            arc = self.open_archive(arc_path)
            m['size'] = arc.sarc.size(member)
            m['archive'] = arc_path
        else:
            off, size, fi, li = self.c.files[path]
            m['size'] = size
            m['layer'] = self.c.layers[li]
            m['sha256'] = self.c.sha256(path)
        return m

    def etag(self, path: str) -> str:
        if '::' in path:
            arc_path = path.split('::', 1)[0]
            return self.c.sha256(arc_path)[:16] + '-' + format(zlib.crc32(path.encode('utf-8')), '08x')
        return self.c.sha256(path)[:20]

    # ------------------------------------------------------------------ search
    def _search_index(self):
        with self._lock:
            if self._search_cache is None:
                paths = sorted(self.c.files, key=str.lower)
                self._search_cache = (paths, [p.lower() for p in paths], [classify(p) for p in paths])
            return self._search_cache

    def search(self, q: str, ftype: str | None = None, limit: int = 5000) -> dict:
        paths, lower, types = self._search_index()
        terms = q.lower().split()
        hits = []
        total = 0
        for i, lp in enumerate(lower):
            if ftype and types[i] != ftype:
                continue
            if terms and not all(t in lp for t in terms):
                continue
            total += 1
            if len(hits) < limit:
                p = paths[i]
                hits.append(self._entry(p.rpartition('/')[2], p, self.c.files[p][1]))
        return {'total': total, 'items': hits}

    # ------------------------------------------------------------------ helpers
    def texture_index(self) -> dict[str, str]:
        """Texture name (lower case, without .bntx[.zs]) -> path, for material lookup."""
        with self._lock:
            if self._tex_index is None:
                idx = {}
                for p in self.c.files:
                    lp = p.lower()
                    if lp.endswith('.bntx.zs') or lp.endswith('.bntx'):
                        n = p.rpartition('/')[2]
                        n = n[:-8] if lp.endswith('.bntx.zs') else n[:-5]
                        key = n.lower()
                        # prefer romfs/Tex over other locations
                        if key not in idx or p.startswith('romfs/Tex/'):
                            idx[key] = p
                self._tex_index = idx
            return self._tex_index

    def find_texture(self, name: str) -> str | None:
        idx = self.texture_index()
        key = name.lower()
        p = idx.get(key)
        if p is not None or not key:
            return p
        # some materials name a texture without the prefix of the file that exists, e.g.
        # Metal_GanonCastle_RepeatWall_A_Alb -> CmnTex_Map_Destructed_Metal_GanonCastle_RepeatWall_A_Alb
        if key not in self._tex_alias:
            hits = [v for k, v in idx.items() if k.endswith('_' + key)]
            self._tex_alias[key] = hits[0] if len(hits) == 1 else None
        return self._tex_alias[key]

    def head(self, path: str, n: int) -> bytes:
        """The first n bytes after removing the zstd layer, without unpacking the whole file."""
        data = self.read_stored(path) if '::' in path else self.c.read_head(path, n + 0x20100)
        return self.zstd.decompress(data, head=n) if is_zstd(data) else data[:n]

    def has_skeletal(self, path: str) -> bool:
        """Does an animation file contain skeletal animations (not only material ones)? Cached."""
        hit = self._skel_cache.get(path)
        if hit is None:
            try:
                hit = has_skeletal_anims(self.head(path, 0x60))
            except Exception:  # noqa: BLE001 - unreadable: keep it in the lists
                hit = True
            self._skel_cache[path] = hit
        return hit

    def anim_bones(self, path: str) -> frozenset:
        """Bone names animated by an animation file. Cached."""
        hit = self._bone_cache.get(path)
        if hit is None:
            try:
                hit = frozenset(skeletal_bone_names(self.read(path)))
            except Exception:  # noqa: BLE001
                hit = frozenset()
            self._bone_cache[path] = hit
        return hit

    def anim_candidates(self, model_path: str, limit: int = 60, bones: set | None = None) -> list[str]:
        """Animation files with skeletal animations that probably belong to a model (name heuristics).

        Ranked: animations named after the model (Animal_Cow -> Animal_Cow_Animation, Driver_YoshiIce ->
        DriverA_YoshiIce_Kart), then those of the base character (Driver_YoshiIce -> DriverA_Yoshi_Kart).
        The latter are only kept if they animate at least half of the model's bones (bones = its skeleton).
        """
        base = model_path.rpartition('::')[2].rpartition('/')[2]
        stem = re.sub(r'\.(bfres)(\.zs)?$', '', base, flags=re.I)
        keys = [k for k in dict.fromkeys(stem.split('.')) if k]
        roots: dict[str, int] = {}  # root -> tier (0 = model name, 1 = camel-case parent)
        for k in keys:
            toks = k.split('_')
            for r in [k] + ['_'.join(toks[:n]) for n in range(len(toks), 1, -1)]:
                roots[r] = 0
        for r in list(roots):
            pre, _, last = r.rpartition('_')
            for parent in _camel_parents(last) if pre else ():  # never a bare word like 'Enemy'
                roots.setdefault(f'{pre}_{parent}', 1)
        scored = []
        for p in self.all_anims():
            n = p.rpartition('/')[2]
            sa = n.split('.anim.')[0]
            best = None
            for s in {sa, _variant_stem(sa)}:
                for r, tier in roots.items():
                    if s == r or s == r + '_Animation':
                        key = (tier, 0, -len(r))
                    elif s.startswith(r + '_') or s.startswith(r + '.'):
                        key = (tier, 1, -len(r))
                    else:
                        continue
                    if best is None or key < best:
                        best = key
            if best is not None:
                scored.append((best, n.lower(), p))
        scored.sort()
        out = []
        for (tier, *_), _n, p in scored:
            if not self.has_skeletal(p):
                continue
            if tier:  # weaker match by the base character's name: the skeleton has to fit
                hit = len(self.anim_bones(p) & bones) if bones else 0
                if hit < 2 or hit < 0.5 * len(bones):
                    continue
            out.append(p)
            if len(out) >= limit:
                break
        return out

    def model_candidates(self, anim_path: str, limit: int = 40) -> list[str]:
        """Models that probably fit an animation file (name heuristics).

        Ranked: the model named in the animation (Dm_Graveyard_0010_Player_Child -> Player_Child,
        DriverA_Yoshi_Kart -> Driver_Yoshi), then its costumes (Driver_YoshiIce), then the base character
        of a costume animation (DriverA_YoshiIce_Kart -> Driver_Yoshi).
        """
        base = anim_path.rpartition('::')[2].rpartition('/')[2]
        stem = base.split('.anim.')[0]
        stem = re.sub(r'_Animation$', '', stem)
        roots: list[tuple[str, int]] = []  # (root, tier) in order of preference
        seen = set()

        def add(r, tier):
            if r and not r[0].isdigit() and r not in seen:
                seen.add(r)
                roots.append((r, tier))

        for s in dict.fromkeys((stem, _variant_stem(stem))):
            toks = s.split('_')
            # every contiguous run of name parts (longest first), e.g. Dm_Graveyard_0010_Player_Child -> Player_Child
            for n in range(len(toks), 0, -1):
                for i in range(0, len(toks) - n + 1):
                    if n >= 2 or len(toks) == 1:
                        add('_'.join(toks[i:i + n]), 0)
        for r, _tier in list(roots):
            pre, _, last = r.rpartition('_')
            for parent in _camel_parents(last) if pre else ():  # never a bare word like 'Enemy'
                add(f'{pre}_{parent}', 1)
        scored = []
        for p in self._model_paths():
            n = p.rpartition('/')[2]
            group = n.split('.')[0]
            best = None
            for rank, (r, tier) in enumerate(roots):
                if group == r or group.startswith(r + '_'):
                    key = (tier, 0, rank, group != r)
                elif group.startswith(r) and group[len(r):len(r) + 1].isupper():  # costume: Driver_Yoshi -> Driver_YoshiIce
                    key = (tier, 1, rank, True)
                else:
                    continue
                if best is None or key < best:
                    best = key
            if best is not None:
                scored.append((best, n.lower(), p))
        scored.sort()
        return [p for *_, p in scored[:limit]]

    def all_anims(self) -> list[str]:
        if self._anims is None:
            self._anims = sorted((p for p in self.c.files if classify(p) == 'anim'), key=str.lower)
        return self._anims

    def _model_paths(self) -> list[str]:
        if self._models is None:
            self._models = [p for p in self.c.files if p.startswith('romfs/Model/') and classify(p) == 'model'
                            and '.Skeleton.' not in p]
        return self._models

    def close(self):
        self.c.close()
