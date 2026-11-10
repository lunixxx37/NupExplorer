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

from .container import NupContainer
from .sarc import Sarc
from .util import ByteLRU
from .zs import ZstdCodec, is_zstd

TYPE_LABELS = {
    'texture': 'Texture', 'audio': 'Audio', 'model': '3D model', 'anim': 'Animation',
    'msbt': 'Text (MSBT)', 'byml': 'Data (BYML)', 'archive': 'Archive', 'other': 'File',
}
ARCHIVE_EXTS = ('.pack', '.sarc', '.blarc', '.bfarc')


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
    if n.endswith(('.byml', '.bgyml')):
        return 'byml'
    if n.endswith(ARCHIVE_EXTS):
        return 'archive'
    return 'other'


def _sort_key(s: str):
    return s.lower()


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

    def anim_candidates(self, model_path: str, limit: int = 60) -> list[str]:
        """Animation files that probably belong to a model (name heuristics)."""
        base = model_path.rpartition('::')[2].rpartition('/')[2]
        stem = re.sub(r'\.(bfres)(\.zs)?$', '', base, flags=re.I)
        parts = stem.split('.')
        keys = [k for k in dict.fromkeys(parts) if k]
        roots = set()
        for k in keys:
            roots.add(k)
            toks = k.split('_')
            for n in range(len(toks), 1, -1):
                roots.add('_'.join(toks[:n]))
        anims = [p for p in self.c.files if p.lower().endswith('.anim.bfres.zs') or p.lower().endswith('.anim.bfres')]
        scored = []
        for p in anims:
            n = p.rpartition('/')[2]
            stem_a = n.split('.anim.')[0]
            best = 0
            for r in roots:
                if stem_a == r or stem_a == r + '_Animation':
                    best = max(best, 1000 + len(r))
                elif stem_a.startswith(r + '_') or stem_a.startswith(r + '.'):
                    best = max(best, 100 + len(r))
            if best:
                scored.append((-best, n.lower(), p))
        scored.sort()
        return [p for _, _, p in scored[:limit]]

    def model_candidates(self, anim_path: str, limit: int = 40) -> list[str]:
        """Models that probably fit an animation file (name heuristics)."""
        base = anim_path.rpartition('::')[2].rpartition('/')[2]
        stem = base.split('.anim.')[0]
        stem = re.sub(r'_Animation$', '', stem)
        toks = stem.split('_')
        # every contiguous run of name parts (longest first), e.g. Dm_Graveyard_0010_Player_Child -> Player_Child
        roots = []
        for n in range(len(toks), 0, -1):
            for i in range(0, len(toks) - n + 1):
                r = '_'.join(toks[i:i + n])
                if (n >= 2 or len(toks) == 1) and not r[0].isdigit() and r not in roots:
                    roots.append(r)
        models = [p for p in self.c.files if p.startswith('romfs/Model/') and classify(p) == 'model'
                  and '.Skeleton.' not in p]
        scored = []
        for p in models:
            n = p.rpartition('/')[2]
            group = n.split('.')[0]
            for rank, r in enumerate(roots):
                if group == r or group.startswith(r + '_'):
                    scored.append((rank, group != r, n.lower(), p))
                    break
        scored.sort()
        return [p for *_, p in scored[:limit]]

    def all_anims(self) -> list[str]:
        return sorted((p for p in self.c.files if classify(p) == 'anim'), key=str.lower)

    def close(self):
        self.c.close()
