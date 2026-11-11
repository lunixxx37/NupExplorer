"""Koei Tecmo (Katana Engine) resource links: which textures belong to a G1M model.

A G1M material names its textures only by slot number. The game links them through objects of its kidsobjdb
databases (*.kod): "_DOK0000", u32 header size; then records "?DOK0000", u32 size (padded to 4 bytes). Objects
("IDOK0000"): u32 size, u32 object id, u32 type hash, u32 property count, per property (u32 value type, u32 count,
u32 name hash), then all values packed (value sizes per type: 0/1 = 1 byte, 2/3 = 2, 4/5/8 = 4, 12 = 8, 13 = 12,
10 = 16). Other records ("RDOK0000" ...) are skipped.
  model object    0xD40B3C8F   property 0x8AB68B3F = KTID of the G1M file, 0xF92C5190 = texture set object
  texture set     0x3059B9C3   property 0x7A1E1EF8 = KTID of the texture table: pairs (u32 slot, u32 texture object)
  texture object  0xFF7DBFD4   property 0x6C7321D2 = KTID of the G1T file
(identified in Hyrule Warriors: Age of Imprisonment by following references between the objects and files).
Animations (G1A/G2A) play on every skeleton that has their bone ids (global ids of the G1MS table). The motions of
a character come from its actor object:
  actor 0xD186CEEB  0x68A6F779 = visual objects (0xC4B9B28D, 0x0B6E1578 = model object), 0x7245DD82 = motion
                    entries (0x11A85121, 0x9E1E3953 = motion objects 0x34F2EC8B / 0x3B6F1DAB, 0x1927F731 = KTID
                    of the G1A/G2A file)
Only some actors list motions; models without them get the motions of models with the same skeleton, then every
animation whose bones the skeleton has (most bones first).
Names: every id is the KTID hash of a name (ktid_hash below; algorithm as documented by the Cethleann tools, MIT):
files 'R_<EXT>［name］' (fullwidth brackets, extension upper case, e.g. 'R_G1T［...］', 'R_SRSA［CE_Ganondorf］') or the
plain file name ('Field_Common.kidsobjdb'), objects '<Class>［name］［scene］'. The complete name lists of the game
(root.rdb.name and ~3,900 more) are referenced but not shipped; 52 small name databases ("_RNK"/"IRNK": id, name,
type, property names) remain. Real names therefore come from: UI textures (object 0x79991E79: 0xC8BB66DA = G1T,
0x2276FC56 = name file: u32 1 + string), the shipped name databases (texture objects -> G1T, verified by hashing
'R_G1T［name］'; model / visual objects -> G1M; plain file names), sister textures found by varying verified names
(suffix _kidsalb/_kidsnmh/..., numbers; each verified by its hash), sound banks (prefixes of their sound names,
verified by 'R_SRSA［prefix］') and the sounds. Motion objects are not in the shipped lists, so animations keep
their ids. Everything else gets a description of its content.
The links are collected once per NUP – every kidsobjdb is read – and cached next to the file index.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import struct
import threading
import time

import numpy as np

from .ktpack import MAGICS, entry_bytes, entry_head, scan

VERSION = 8
TYPE_NDB = 0xBF6B52C7
TYPE_G1T, TYPE_KOD, TYPE_TABLE = 0xAFBEC60C, 0x20A6A0BB, 0x8E39AA37
TYPE_G1M, TYPE_ANIM, TYPE_NAME = 0x563BDEF1, 0x6FA91671, 0xF20DE437
TYPE_BANK, TYPE_STREAM = 0xBBD39F2D, 0x0D34474D
OBJ_MODEL, OBJ_TEXSET, OBJ_TEXTURE = 0xD40B3C8F, 0x3059B9C3, 0xFF7DBFD4
OBJ_ACTOR, OBJ_VISUAL, OBJ_MENTRY, OBJ_MOTIONS = 0xD186CEEB, 0xC4B9B28D, 0x11A85121, (0x34F2EC8B, 0x3B6F1DAB)
OBJ_TEXNAME = 0x79991E79
P_G1M, P_TEXSET, P_TABLE, P_G1T = 0x8AB68B3F, 0xF92C5190, 0x7A1E1EF8, 0x6C7321D2
P_VISUALS, P_MENTRIES, P_MODELOBJ, P_MOTIONS, P_ANIM = 0x68A6F779, 0x7245DD82, 0x0B6E1578, 0x9E1E3953, 0x1927F731
P_NAMED_G1T, P_NAME = 0xC8BB66DA, 0x2276FC56
_WANTED = {OBJ_MODEL: (P_G1M, P_TEXSET), OBJ_TEXSET: (P_TABLE,), OBJ_TEXTURE: (P_G1T,),
           OBJ_ACTOR: (P_VISUALS, P_MENTRIES), OBJ_VISUAL: (P_MODELOBJ,), OBJ_MENTRY: (P_MOTIONS,),
           OBJ_MOTIONS[0]: (P_ANIM,), OBJ_MOTIONS[1]: (P_ANIM,), OBJ_TEXNAME: (P_NAMED_G1T, P_NAME)}
# pack contents in descriptions: type -> (singular, plural), in display order
KINDS = {TYPE_G1M: ('model', 'models'), TYPE_G1T: ('texture', 'textures'), TYPE_ANIM: ('animation', 'animations'),
         TYPE_BANK: ('sound bank', 'sound banks'), TYPE_STREAM: ('sound stream', 'sound streams'),
         0xB097D41F: ('effect', 'effects'), 0x54738C76: ('collision', 'collisions'), 0x5B2970FC: ('font', 'fonts'),
         TYPE_KOD: ('database', 'databases')}
# entries listed in the search: type -> extension of the entry name
SEARCH_EXT = {TYPE_G1T: 'g1t', TYPE_G1M: 'g1m', TYPE_ANIM: None, TYPE_BANK: 'srsa', TYPE_KOD: 'kod', TYPE_NDB: 'knr'}
NAMED_ONLY = (TYPE_KOD, TYPE_NDB)  # databases: in the search only when their name is known


def ktid_hash(text: str) -> int:
    """Koei Tecmo name hash: seed = first byte * 31; each further UTF-8 byte b (signed) adds 31^(i+1) * b."""
    data = text.encode('utf-8')
    if not data:
        return 0
    h = (data[0] * 31) & 0xFFFFFFFF
    k = 31
    for b in data[1:]:
        h = (h + 31 * k * (b - 256 if b >= 128 else b)) & 0xFFFFFFFF
        k = (k * 31) & 0xFFFFFFFF
    return h


def file_ktid(ext: str, name: str) -> int:
    """KTID of a resource file: hash of 'R_<EXT>［name］'."""
    return ktid_hash(f'R_{ext.upper()}［{name}］')


def parse_name_db(data: bytes) -> list[tuple[int, list[str]]]:
    """Name database ("_RNK0000", records "IRNK0000": u32 size, u32 id, u32 string count, string offsets, strings)."""
    if data[:4] != b'_RNK':
        return []
    pos = struct.unpack_from('<I', data, 8)[0]
    out = []
    while pos + 20 <= len(data) and data[pos:pos + 4] == b'IRNK':
        size, kt, n = struct.unpack_from('<3I', data, pos + 8)
        if size < 20 or n > 4096:
            break
        strs = []
        for off in struct.unpack_from(f'<{n}I', data, pos + 20):
            end = data.find(b'\0', pos + off)
            strs.append(data[pos + off:end if end >= 0 else len(data)].decode('utf-8', 'replace'))
        out.append((kt, strs))
        pos += size
        if data[pos:pos + 4] != b'IRNK':
            pos = (pos + 3) & ~3
    return out


_INNER = None


def _inner(name: str) -> list[str]:
    """The bracketed parts of an object name: 'FE4Model［set－a］［MP_000］' -> ['set－a', 'MP_000']."""
    global _INNER
    if _INNER is None:
        import re
        _INNER = re.compile('［([^］]*)］')
    return _INNER.findall(name)


TEX_KINDS = ('kidsalb', 'kidsnmh', 'kidsrfr', 'kidsocc', 'kidsalem', 'kidsdt2', 'kidsdt2m', 'kidsmmm', 'kidsnml', 'kidsemi',
             'kidsmsk', 'kidsrgh', 'kidsmtl', 'kidshgt', 'kidsspc', 'kidsao', 'kidsalp', 'kidsdif')


def count_text(n: int, kind: tuple[str, str]) -> str:
    return f'{n:,} {kind[0] if n == 1 else kind[1]}'


def pack_description(counts: dict[int, int]) -> str:
    """'62 sound banks', '8,901 animations · 2,886 effects · 58 models' ..."""
    parts = [count_text(counts[t], k) for t, k in KINDS.items() if counts.get(t)]
    rest = sum(n for t, n in counts.items() if t not in KINDS)
    if not parts and rest:
        parts.append(f'{rest:,} file{"s" if rest != 1 else ""}')
    return ' · '.join(parts[:3])


def bank_label(names: list[str]) -> str:
    """Common part of the sound names of a bank: BGM001_TITLE_Normal, BGM002_TITLE_Map -> 'BGM'."""
    names = [n for n in names if n]
    if not names:
        return ''
    prefix = os.path.commonprefix(names)
    if len(names) > 1:
        prefix = prefix.rstrip('0123456789').rstrip('_-. ')
    if len(prefix) >= 3:
        return prefix
    firsts = {}
    for n in names:
        tok = n.split('_')[0].rstrip('0123456789')
        firsts[tok] = firsts.get(tok, 0) + 1
    best = sorted(firsts.items(), key=lambda kv: -kv[1])[:2]
    return ', '.join(t for t, _ in best if t)
_VALUE_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 8: 4, 10: 16, 12: 8, 13: 12}


def anim_head_info(head: bytes):
    """(format, fps, length in frames, sorted bone ids) from the start of a G1A/G2A file, or None."""
    if head[:4] == b'_A2G' and len(head) >= 0x20:
        ver = struct.unpack_from('<I', head, 4)[0]
        fps = struct.unpack_from('<f', head, 12)[0] or 60.0
        packed = struct.unpack_from('<I', head, 16)[0]
        size = ((packed >> 20) & 0xFFF) << 2
        start = 0x20 if ver in (0x30303430, 0x30303530) else 0x1C
        small = ver == 0x30303530
        ids, last, wrap = [], -1, 0
        for i in range(min(size, len(head) - start) // 4):
            w = struct.unpack_from('<I', head, start + 4 * i)[0]
            b = (w >> 4) & (0xFF if small else 0x3FF)
            if b < last:
                wrap += 1
            last = b
            ids.append(b + wrap * (256 if small else 1024))
        return 'g2a', fps, packed & 0x3FFF, sorted(set(ids))
    if head[:4] == b'_A1G' and len(head) >= 0x34:
        duration = struct.unpack_from('<f', head, 16)[0]
        n = struct.unpack_from('<H', head, 48)[0]
        ids = [struct.unpack_from('<I', head, 52 + 8 * i)[0] for i in range(min(n, (len(head) - 52) // 8))]
        return 'g1a', 30.0, max(1, round(duration * 30)), sorted(set(ids))
    return None


def skeleton_ids(head: bytes):
    """Global bone ids of a G1M's skeleton (from the start of the file), or None."""
    if head[:4] != b'_M1G' or len(head) < 24:
        return None
    pos, nch = struct.unpack_from('<I', head, 12)[0], struct.unpack_from('<I', head, 20)[0]
    for _ in range(nch):
        if pos + 0x1C > len(head):
            return None
        magic, ver, size = head[pos:pos + 4][::-1], struct.unpack_from('<I', head, pos + 4)[0], struct.unpack_from('<I', head, pos + 8)[0]
        if magic == b'G1MS':
            njoint, nids = struct.unpack_from('<HH', head, pos + 0x14)
            if ver < 0x30303332 or pos + 0x1C + 2 * nids > len(head):
                return list(range(njoint))
            table = struct.unpack_from(f'<{nids}H', head, pos + 0x1C)
            return [g for g, local in enumerate(table) if local != 0xFFFF]
        if size < 12:
            return None
        pos += size
    return None


def parse_kod(data: bytes):
    """Objects of the wanted types: yields (object id, type, {property: tuple of u32 values})."""
    if data[:4] != b'_DOK' or len(data) < 12:
        return
    pos = struct.unpack_from('<I', data, 8)[0]
    end = len(data)
    while pos + 24 <= end and data[pos + 1:pos + 4] == b'DOK':  # records: IDOK = object, others (RDOK ...) skipped
        size, oid, otype, n = struct.unpack_from('<4I', data, pos + 8)
        if size < 12:
            break
        wanted = _WANTED.get(otype) if data[pos] == 0x49 else None
        if wanted:
            props = {}
            o = pos + 24 + 12 * n
            for i in range(n):
                vtype, count, name = struct.unpack_from('<3I', data, pos + 24 + 12 * i)
                vsize = _VALUE_SIZE.get(vtype)
                if vsize is None:
                    break
                if name in wanted and vsize == 4 and count:
                    props[name] = struct.unpack_from(f'<{count}I', data, o)
                o += vsize * count
            yield oid, otype, props
        pos += size
        if data[pos + 1:pos + 4] != b'DOK':  # records are padded to 4 bytes
            pos = (pos + 3) & ~3


class KatanaIndex:
    def __init__(self, vfs, cache_dir: str | None = None, log=print):
        self.vfs, self.cache_dir, self.log = vfs, cache_dir, log
        self._lock = threading.Lock()
        self._data = None

    def _cache_file(self):
        if not self.cache_dir:
            return None
        key = hashlib.sha1(self.vfs.c.path.lower().encode('utf-8')).hexdigest()[:16]
        return os.path.join(self.cache_dir, f'katana_{key}.pkl')

    def _stamp(self):
        st = os.stat(self.vfs.c.path)
        return (VERSION, st.st_size, int(st.st_mtime))

    def data(self) -> dict:
        with self._lock:
            if self._data is None:
                cf = self._cache_file()
                if cf and os.path.exists(cf):
                    try:
                        with open(cf, 'rb') as f:
                            d = pickle.load(f)
                        if d.get('stamp') == self._stamp():
                            self._data = d
                    except Exception:  # noqa: BLE001 - rebuild
                        pass
                if self._data is None:
                    t0 = time.time()
                    self._data = self._build()
                    self.log(f'Koei Tecmo resource links indexed ({time.time() - t0:.1f}s)')
                    if cf:
                        try:
                            tmp = cf + '.tmp'
                            with open(tmp, 'wb') as f:
                                pickle.dump(self._data, f, protocol=pickle.HIGHEST_PROTOCOL)
                            os.replace(tmp, cf)
                        except OSError:
                            pass
            return self._data

    def _build(self) -> dict:
        v = self.vfs
        packs: list[str] = []
        where: dict[int, int] = {}   # KTID of a G1T / texture table / G1M / animation / name -> pack index
        kods, g1ms, anim_entries, name_entries, banks, name_dbs = [], [], [], [], [], []
        pack_counts: list[dict] = []
        entries = []                 # search: (pack index, KTID, type, size)
        file_ids: dict[int, int] = {}  # every pack entry: KTID -> type
        for p in sorted(v.c.files):
            if not p.lower().endswith(('.fdata', '.file')):
                continue
            try:
                read, size, _ = v.random_access(p)
                if read(0, 8) not in MAGICS:
                    continue
                pi = len(packs)
                packs.append(p)
                counts: dict[int, int] = {}
                for e in scan(read, size):
                    t = e[6]
                    counts[t] = counts.get(t, 0) + 1
                    file_ids.setdefault(e[5], t)
                    if t == TYPE_NDB:
                        name_dbs.append((read, e))
                    if t in SEARCH_EXT:
                        entries.append((pi, e[5], t, e[3]))
                    if t in (TYPE_G1T, TYPE_TABLE, TYPE_G1M, TYPE_ANIM, TYPE_NAME):
                        where.setdefault(e[5], pi)
                        if t == TYPE_G1M:
                            g1ms.append((read, e))
                        elif t == TYPE_ANIM:
                            anim_entries.append((read, e))
                        elif t == TYPE_NAME:
                            name_entries.append((read, e))
                    elif t == TYPE_KOD:
                        kods.append((read, e))
                    elif t == TYPE_BANK:
                        banks.append((pi, read, e))
                pack_counts.append(counts)
            except Exception:  # noqa: BLE001 - unreadable pack
                continue
        name_text = {}
        for read, e in name_entries:
            try:
                raw = entry_head(read, e, 0x200)
                name_text[e[5]] = raw[4:].split(b'\0')[0].decode('utf-8', 'replace').strip()
            except Exception:  # noqa: BLE001
                continue
        objs: dict[int, tuple] = {}
        for read, e in kods:
            try:
                data = entry_bytes(read, e)
            except Exception:  # noqa: BLE001
                continue
            for oid, otype, props in parse_kod(data):
                objs.setdefault(oid, (otype, props))
        g1m_texset: dict[int, int] = {}
        texset_table: dict[int, int] = {}
        texture_g1t: dict[int, int] = {}
        tex_names: dict[int, str] = {}
        for oid, (otype, props) in objs.items():
            if otype == OBJ_MODEL:
                if P_G1M in props and P_TEXSET in props:
                    g1m_texset.setdefault(props[P_G1M][0], props[P_TEXSET][0])
            elif otype == OBJ_TEXSET:
                if P_TABLE in props:
                    texset_table.setdefault(oid, props[P_TABLE][0])
            elif otype == OBJ_TEXTURE and P_G1T in props:
                texture_g1t.setdefault(oid, props[P_G1T][0])
            elif otype == OBJ_TEXNAME and P_NAMED_G1T in props and P_NAME in props:
                name = name_text.get(props[P_NAME][0])
                if name:
                    tex_names.setdefault(props[P_NAMED_G1T][0], name)
        # motions listed by actors: actor -> visuals -> model objects -> G1M; actor -> entries -> motions -> file
        model_anims: dict[int, set] = {}
        for oid, (otype, props) in objs.items():
            if otype != OBJ_ACTOR or P_MENTRIES not in props:
                continue
            files = set()
            for me in props[P_MENTRIES]:
                entry = objs.get(me)
                for mo in (entry[1].get(P_MOTIONS, ()) if entry and entry[0] == OBJ_MENTRY else ()):
                    motion = objs.get(mo)
                    if motion and motion[0] in OBJ_MOTIONS:
                        files.update(motion[1].get(P_ANIM, ()))
            for vis in props.get(P_VISUALS, ()):
                visual = objs.get(vis)
                for mo in (visual[1].get(P_MODELOBJ, ()) if visual and visual[0] == OBJ_VISUAL else ()):
                    model = objs.get(mo)
                    if model and model[0] == OBJ_MODEL:
                        for g in model[1].get(P_G1M, ()):
                            model_anims.setdefault(g, set()).update(files)
        anims = {}
        for read, e in anim_entries:
            try:
                info = anim_head_info(entry_head(read, e, 0x8000))
            except Exception:  # noqa: BLE001
                info = None
            if info:
                fmt, fps, length, ids = info
                anims[e[5]] = (fmt, fps, length, np.asarray(ids, '<u2').tobytes())
        skeletons = {}
        for read, e in g1ms:
            try:
                ids = skeleton_ids(entry_head(read, e, 0x10000))
            except Exception:  # noqa: BLE001
                ids = None
            if ids and len(ids) > 1:
                skeletons[e[5]] = np.asarray(ids, '<u2').tobytes()
        # sound banks: their sounds (name, size) for the search, a label from the names for descriptions
        from . import ktsr
        bank_sounds: dict[tuple[int, int], list] = {}
        bank_labels: dict[int, str] = {}
        stream_users: dict[str, set] = {}
        candidates = None
        for pi, read, e in banks:
            try:
                arc = ktsr.BankArchive(entry_bytes(read, e))
            except Exception:  # noqa: BLE001
                continue
            bank_sounds[(pi, e[5])] = [(n, arc.size(n)) for n in arc.names()]
            label = bank_label([s.name for s in arc.bank.sounds])
            if label:
                bank_labels.setdefault(e[5], label)
            if any(s.external for s in arc.bank.sounds):  # which stream file holds its sounds
                if candidates is None:
                    candidates = v._ktsr_stream_files()
                bank_dir = packs[pi].rpartition('/')[0]
                st = ktsr.find_stream(arc.bank, candidates, bank_dir)
                path = next((p for p, sf in candidates if sf is st), None) if st is not None else None
                if path and label:
                    stream_users.setdefault(path.split('::')[0], set()).add(label)
        file_names = self._names(objs, name_dbs, file_ids, bank_sounds)
        pack_desc = {}
        for pi, p in enumerate(packs):
            users = stream_users.get(p)
            if users and pack_counts[pi].get(TYPE_STREAM):
                pack_desc[p] = 'sound stream · ' + ', '.join(sorted(users)[:3])
            else:
                text = pack_description(pack_counts[pi])
                if text:
                    pack_desc[p] = text
        return {'stamp': self._stamp(), 'packs': packs, 'where': where, 'g1m_texset': g1m_texset,
                'texset_table': texset_table, 'texture_g1t': texture_g1t, 'anims': anims, 'skeletons': skeletons,
                'model_anims': {g: sorted(a & anims.keys()) for g, a in model_anims.items() if a & anims.keys()},
                'tex_names': tex_names, 'bank_labels': bank_labels, 'bank_sounds': bank_sounds,
                'pack_desc': pack_desc, 'entries': entries, 'file_names': file_names}

    @staticmethod
    def _names(objs: dict, name_dbs: list, file_ids: dict, bank_sounds: dict) -> dict[int, str]:
        """Real names of files that can be verified: see the module docstring. Returns {KTID: name}."""
        names: dict[int, str] = {}
        obj_name: dict[int, str] = {}
        strings: set[str] = set()
        for read, e in name_dbs:
            try:
                records = parse_name_db(entry_bytes(read, e))
            except Exception:  # noqa: BLE001
                continue
            for kt, strs in records:
                strings.update(s for s in strs if s)
                if strs and strs[0] and ktid_hash(strs[0]) == kt:
                    obj_name[kt] = strs[0]
        for s in strings:
            h = ktid_hash(s)
            if h in file_ids and '［' not in s:  # plain file names (Field_Common.kidsobjdb, *.name ...)
                names.setdefault(h, s)
            elif h in objs:  # objects named anywhere in the lists (e.g. visual objects of other scenes)
                obj_name.setdefault(h, s)
        g1t_ids = {k for k, t in file_ids.items() if t == TYPE_G1T}
        texture_names = []
        for oid, (otype, props) in objs.items():
            parts = _inner(obj_name.get(oid, ''))
            if not parts:
                continue
            if otype == OBJ_TEXTURE and P_G1T in props:
                g = props[P_G1T][0]
                if g in g1t_ids and file_ktid('G1T', parts[0]) == g:
                    names.setdefault(g, parts[0])
                    texture_names.append(parts[0])
            elif otype == OBJ_MODEL and P_G1M in props:  # the game's name of the model resource
                names.setdefault(props[P_G1M][0], parts[0].replace('－', '-'))
        for oid, (otype, props) in objs.items():  # visual objects name the models of their model objects
            parts = _inner(obj_name.get(oid, ''))
            if parts and otype == OBJ_VISUAL:
                for mo in props.get(P_MODELOBJ, ()):
                    model = objs.get(mo)
                    if model and model[0] == OBJ_MODEL and P_G1M in model[1]:
                        names.setdefault(model[1][P_G1M][0], parts[0].replace('－', '-'))
        # sister textures: other kinds (_kidsalb -> _kidsnmh ...) and neighbouring numbers, each verified
        frontier = set(texture_names)
        for _round in range(3):
            found = set()
            for n in frontier:
                stem, _, kind = n.rpartition('_')
                if not kind.startswith('kids'):
                    continue
                cands = [f'{stem}_{k}' for k in TEX_KINDS]
                base, digits = stem.rstrip('0123456789'), stem[len(stem.rstrip('0123456789')):]
                if digits:
                    num = int(digits)
                    for i in range(max(0, num - 6), num + 7):
                        cands += [f'{base}{i:0{len(digits)}d}_{k}' for k in TEX_KINDS]
                for c in cands:
                    h = file_ktid('G1T', c)
                    if h in g1t_ids and h not in names:
                        names[h] = c
                        found.add(c)
            frontier = found
            if not found:
                break
        # sound banks: the bank name is a prefix of its sound names (R_SRSA［prefix］)
        prefixes: dict[int, set] = {}
        for (pi, kt), snds in bank_sounds.items():
            p = prefixes.setdefault(kt, set())
            for n, _size in snds:
                toks = n.rpartition('.')[0].split('_')
                p.update('_'.join(toks[:i]) for i in range(1, len(toks) + 1))
        for kt, cands in prefixes.items():
            for c in cands:
                if file_ktid('SRSA', c) == kt:
                    names[kt] = c
                    break
        return names

    # ------------------------------------------------------------------ names and descriptions
    def loaded(self) -> dict | None:
        """The index if it is already in memory (never builds it: for listings that must not wait)."""
        return self._data

    def describe(self, path: str) -> tuple[str | None, str | None]:
        """(real name, description) of a file or pack entry, from the loaded index; (None, None) if unknown."""
        d = self._data
        if d is None:
            return None, None
        name = path.rpartition('::')[2].rpartition('/')[2]
        low = name.lower()
        ktid = None
        if low.startswith('0x') and '.' in low:
            try:
                ktid = int(low[2:10], 16)
            except ValueError:
                ktid = None
        title = d.get('file_names', {}).get(ktid) if ktid is not None else None
        if '::' not in path:  # pack: its description (a stream file also has the name of its bank)
            return (title if low.endswith('.file') else None), d['pack_desc'].get(path)
        if ktid is None:
            return None, None
        ext = low.rpartition('.')[2]
        if ext == 'g1t':
            return title or d['tex_names'].get(ktid), None
        if ext == 'srsa':
            label = d['bank_labels'].get(ktid)
            if getattr(self, '_bank_n', None) is None or self._bank_n[0] is not d:
                self._bank_n = (d, {k: len(s) for (pi, k), s in d['bank_sounds'].items()})
            n = self._bank_n[1].get(ktid)
            parts = [x for x in (None if title else label, f'{n:,} sounds' if n is not None else None) if x]
            return title, ' · '.join(parts) or None
        if ext == 'g1m':
            sk = d['skeletons'].get(ktid)
            motions = len(d['model_anims'].get(ktid, ()))
            parts = [f'{len(sk) // 2} bones' if sk else 'static']
            if motions:
                parts.append(f'{motions} motions')
            return title, ' · '.join(parts)
        if ext in ('g2a', 'g1a'):
            a = d['anims'].get(ktid)
            return title, (f'{a[2] / (a[1] or 60):.1f} s · {len(a[3]) // 2} bones' if a else None)
        return title, None

    def search_entries(self):
        """Pack entries for the search: yields (path, name shown, search text, type, size)."""
        d = self._data
        if d is None:
            return
        packs = d['packs']
        for pi, ktid, t, size in d['entries']:
            ext = SEARCH_EXT[t] or (d['anims'][ktid][0] if ktid in d['anims'] else None)
            if ext is None:
                continue
            path = f'{packs[pi]}::0x{ktid:08x}.{ext}'
            title, desc = self.describe(path)
            if t in NAMED_ONLY and not title:
                continue
            yield path, title, ' '.join(x for x in (title, desc) if x), ext, size
            if t == TYPE_BANK:
                for name, ssize in d['bank_sounds'].get((pi, ktid), ()):
                    yield f'{path}::{name}', None, '', name.rpartition('.')[2], ssize

    # ------------------------------------------------------------------ animations
    def _anim_sets(self) -> dict[int, frozenset]:
        if getattr(self, '_aset', None) is None:
            self._aset = {k: frozenset(np.frombuffer(a[3], '<u2').tolist()) for k, a in self.data()['anims'].items()}
        return self._aset

    def anim_info(self, ktid: int):
        """(format, fps, length in frames, number of bones) of an animation file, or None."""
        a = self.data()['anims'].get(ktid)
        return (a[0], a[1], a[2], len(a[3]) // 2) if a else None

    def anim_candidates(self, g1m_ktid: int, limit: int = 300) -> list[int]:
        """Animations for a G1M: the motions of its actor (or of models with the same skeleton), then every animation
        whose bones the skeleton has, the ones moving most of its bones first."""
        d = self.data()
        sk = d['skeletons'].get(g1m_ktid)
        if sk is None:
            return []
        bones = frozenset(np.frombuffer(sk, '<u2').tolist())
        linked = list(d['model_anims'].get(g1m_ktid, ()))
        if not linked:
            same = [g for g, s in d['skeletons'].items() if s == sk and g in d['model_anims']]
            linked = sorted({a for g in same for a in d['model_anims'][g]})
        sets = self._anim_sets()
        fit = [(len(ids), k) for k, ids in sets.items() if ids and ids <= bones]
        first = [k for k in linked if k in sets and sets[k] <= bones]
        seen = set(first)
        rest = [k for n, k in sorted(fit, reverse=True) if k not in seen]
        return (first + rest)[:limit]

    def model_candidates(self, anim_ktid: int, limit: int = 40) -> list[int]:
        """Models whose skeleton has every bone of an animation: models whose actor lists it first, then the
        smallest fitting skeletons (closest to the animation)."""
        d = self.data()
        ids = self._anim_sets().get(anim_ktid)
        if not ids:
            return []
        linked = [g for g, a in d['model_anims'].items() if anim_ktid in a]
        fit = []
        for g, sk in d['skeletons'].items():
            if len(sk) // 2 >= len(ids):
                bones = frozenset(np.frombuffer(sk, '<u2').tolist())
                if ids <= bones:
                    fit.append((len(bones), g))
        linked = [g for g in linked if g in d['skeletons']]
        seen = set(linked)
        return (linked + [g for n, g in sorted(fit) if g not in seen])[:limit]

    def path(self, ktid: int, ext: str) -> str | None:
        return self._path(ktid, ext)

    def _path(self, ktid: int, ext: str) -> str | None:
        d = self.data()
        if ext == 'anim':
            a = d['anims'].get(ktid)
            if a is None:
                return None
            ext = a[0]
        pi = d['where'].get(ktid)
        if pi is None:
            return None
        pack = d['packs'][pi]
        path = f'{pack}::0x{ktid:08x}.{ext}'
        if self.vfs.exists(path):
            return path
        arc = self.vfs.open_archive(pack)  # renamed entry (same KTID twice in the pack)
        for name in arc.sarc.names():
            if arc.sarc.ktid(name)[0] == ktid:
                return f'{pack}::{name}'
        return None

    def model_textures(self, g1m_ktid: int) -> dict[int, str]:
        """{texture slot: path of the G1T file} of a G1M (empty if the model has no texture set)."""
        d = self.data()
        texset = d['g1m_texset'].get(g1m_ktid)
        table = d['texset_table'].get(texset) if texset is not None else None
        tpath = self._path(table, 'bin') if table is not None else None
        if tpath is None:
            return {}
        raw = self.vfs.read(tpath)
        out = {}
        for o in range(0, len(raw) - 7, 8):
            slot, tex = struct.unpack_from('<II', raw, o)
            g = d['texture_g1t'].get(tex)
            p = self._path(g, 'g1t') if g is not None else None
            if p:
                out[slot] = p
        return out
