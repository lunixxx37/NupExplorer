"""Reader for the NUP container format (OT.nup).

Layout (little endian), reverse engineered:
  0x000  "NUP\\x01" ... u64 toc_offset @0x08, u64 file_size @0x40
  TOC    "TOC\\x01", u32 section_count, u64 dir_count, u64 file_count,
         u64 strpool_offset (relative to TOC), u64 strpool_length, u64 pad      (0x30 bytes)
         section descriptors, 0x40 each: u64 title_id, u32 root_dir, u32 exefs_dir, u32 romfs_dir, ...
  dirs   24 B each: u32 name_off, u32 name_len, u32 child_start, u32 child_count, u64 reserved
  files  80 B each: u32 name_off, u32 name_len, u64 size, u64 offset, u64 stored_size, u8 sha256[32],
         u64 compression, u64 reserved
         compression 0: stored as is (stored_size == size)
         compression 1: stored as one zstd frame of stored_size bytes that unpacks to size bytes
                        (newer NUP files, e.g. Mario Kart World; OT.nup does not use it)
  child  u32 list; value < dir_count -> directory, else file (value - dir_count)
  string pool

Section 0 is the base game, section 1 the update. The merged view lets the update win,
which is what the game itself sees.
"""
from __future__ import annotations

import hashlib
import mmap
import os
import pickle
import struct
import threading
import time

import numpy as np

try:  # Python 3.14+
    from compression.zstd import ZstdDecompressor as _ZstdDecompressor
    from compression.zstd import decompress as _zstd_unpack
except ImportError:  # fallback: zstandard package
    import zstandard as _zs

    _ZstdDecompressor = None

    def _zstd_unpack(data: bytes) -> bytes:
        return _zs.ZstdDecompressor().decompressobj().decompress(data)

INDEX_VERSION = 3  # 3: files the update repeats unchanged count as base game files

_DIR_DT = np.dtype([('name_off', '<u4'), ('name_len', '<u4'), ('child_start', '<u4'),
                    ('child_count', '<u4'), ('reserved', '<u8')])
_FILE_DT = np.dtype([('name_off', '<u4'), ('name_len', '<u4'), ('size', '<u8'), ('offset', '<u8'),
                     ('stored_size', '<u8'), ('sha', 'V32'), ('compression', '<u8'), ('reserved', '<u8')])
assert _DIR_DT.itemsize == 24 and _FILE_DT.itemsize == 80


def _sort_key(name: str):
    return name.lower()


class NupContainer:
    """Read-only view of a .nup file. Thread-safe for reads (mmap slicing)."""

    def __init__(self, path: str, cache_dir: str | None = None, log=print):
        self.path = os.path.abspath(path)
        self._fh = open(self.path, 'rb')
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        self._lock = threading.Lock()
        st = os.stat(self.path)
        self.file_size = st.st_size
        self._read_header()
        cache_file = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            key = hashlib.sha1(self.path.lower().encode('utf-8')).hexdigest()[:16]
            cache_file = os.path.join(cache_dir, f'index_{key}.pkl')
        stamp = (INDEX_VERSION, st.st_size, int(st.st_mtime))
        loaded = False
        if cache_file and os.path.exists(cache_file):
            try:
                t0 = time.time()
                with open(cache_file, 'rb') as f:
                    data = pickle.load(f)
                if data.get('stamp') == stamp:
                    self.layers = data['layers']
                    self.files = data['files']
                    self.dirs = data['dirs']
                    loaded = True
                    log(f'Index loaded from cache ({time.time() - t0:.2f}s)')
            except Exception as e:  # corrupt cache -> rebuild
                log(f'Index cache unusable: {e}')
        if not loaded:
            t0 = time.time()
            self._build_index()
            log(f'Index built ({time.time() - t0:.2f}s): {len(self.files)} files')
            if cache_file:
                try:
                    tmp = cache_file + '.tmp'
                    with open(tmp, 'wb') as f:
                        pickle.dump({'stamp': stamp, 'layers': self.layers, 'files': self.files,
                                     'dirs': self.dirs}, f, protocol=pickle.HIGHEST_PROTOCOL)
                    os.replace(tmp, cache_file)
                except OSError as e:
                    log(f'Index cache not written: {e}')

    # ------------------------------------------------------------------ parsing
    def _read_header(self):
        mm = self._mm
        if mm[0:4] != b'NUP\x01':
            raise ValueError('Not a NUP file (magic missing)')
        self.toc_off = struct.unpack_from('<Q', mm, 8)[0]
        if mm[self.toc_off:self.toc_off + 4] != b'TOC\x01':
            raise ValueError('NUP: TOC not found')
        self.nsec = struct.unpack_from('<I', mm, self.toc_off + 4)[0]
        self.ndir, self.nfile, sp_rel, self.sp_len = struct.unpack_from('<4Q', mm, self.toc_off + 8)
        self.sp_off = self.toc_off + sp_rel
        sec_off = self.toc_off + 0x30
        self.sections = []
        for i in range(self.nsec):
            tid, root, exefs, romfs = struct.unpack_from('<Q3I', mm, sec_off + i * 0x40)
            self.sections.append({'title_id': f'{tid:016X}', 'root': root, 'exefs': exefs, 'romfs': romfs})
        self.dir_off = sec_off + 0x40 * self.nsec
        self.file_off = self.dir_off + 24 * self.ndir
        self.child_off = self.file_off + 80 * self.nfile
        self.nchild = (self.sp_off - self.child_off) // 4

    def _build_index(self):
        mm = self._mm
        sp = bytes(mm[self.sp_off:self.sp_off + self.sp_len])
        dirs = np.frombuffer(mm, _DIR_DT, self.ndir, self.dir_off)
        files = np.frombuffer(mm, _FILE_DT, self.nfile, self.file_off)
        child = np.frombuffer(mm, '<u4', self.nchild, self.child_off).tolist()
        d_name = [sp[o:o + n].decode('utf-8', 'replace')
                  for o, n in zip(dirs['name_off'].tolist(), dirs['name_len'].tolist())]
        f_name = [sp[o:o + n].decode('utf-8', 'replace')
                  for o, n in zip(files['name_off'].tolist(), files['name_len'].tolist())]
        f_off = files['offset'].tolist()
        f_size = files['size'].tolist()
        f_sha = files['sha'].tolist()
        cs = dirs['child_start'].tolist()
        cc = dirs['child_count'].tolist()
        ndir = self.ndir
        merged: dict[str, tuple] = {}
        self.layers = []
        for li, sec in enumerate(self.sections):
            root = sec['root']
            self.layers.append(d_name[root])
            stack = [(root, '')]
            while stack:
                di, prefix = stack.pop()
                for k in range(cs[di], cs[di] + cc[di]):
                    c = child[k]
                    if c < ndir:
                        stack.append((c, prefix + d_name[c] + '/'))
                    else:
                        fi = c - ndir
                        path = prefix + f_name[fi]
                        prev = merged.get(path)
                        # later sections (the update) override earlier ones; an update that repeats a file
                        # unchanged (same SHA-256) leaves it a base game file
                        if prev is None or f_sha[prev[2]] != f_sha[fi]:
                            merged[path] = (f_off[fi], f_size[fi], fi, li)
        self.files = merged
        # directory listing of the merged view
        tmp: dict[str, tuple[set, list]] = {'': (set(), [])}
        for p in merged:
            parent, _, name = p.rpartition('/')
            node = tmp.get(parent)
            if node is None:
                # create the chain of parents
                parts = parent.split('/')
                for i in range(len(parts)):
                    d = '/'.join(parts[:i + 1])
                    if d not in tmp:
                        tmp[d] = (set(), [])
                        up = '/'.join(parts[:i])
                        tmp[up][0].add(parts[i])
                node = tmp[parent]
            node[1].append(name)
        self.dirs = {k: (sorted(v[0], key=_sort_key), sorted(v[1], key=_sort_key)) for k, v in tmp.items()}

    # ------------------------------------------------------------------ access
    def read(self, vpath: str) -> bytes:
        off, size, fi, _li = self.files[vpath]
        rec = self.file_off + fi * 80
        comp = struct.unpack_from('<Q', self._mm, rec + 0x40)[0]
        if comp == 0:
            return self._mm[off:off + size]
        if comp != 1:
            raise ValueError(f'NUP: unknown compression {comp} for {vpath}')
        stored = struct.unpack_from('<Q', self._mm, rec + 0x18)[0]
        data = _zstd_unpack(self._mm[off:off + stored])
        if len(data) != size:
            raise ValueError(f'NUP: {vpath} unpacked to {len(data)} bytes instead of {size}')
        return data

    def read_head(self, vpath: str, n: int) -> bytes:
        """The first n bytes (or the whole file if it is smaller) without reading everything."""
        off, size, fi, _li = self.files[vpath]
        rec = self.file_off + fi * 80
        if struct.unpack_from('<Q', self._mm, rec + 0x40)[0] == 0:
            return self._mm[off:off + min(size, n)]
        stored = struct.unpack_from('<Q', self._mm, rec + 0x18)[0]
        # a zstd block holds at most 128 KiB, so that much input always yields the first n bytes
        part = self._mm[off:off + min(stored, n + 0x20100)]
        if _ZstdDecompressor is None:
            return _zstd_unpack(self._mm[off:off + stored])[:n]
        return _ZstdDecompressor().decompress(part, max_length=n)

    def reader(self, vpath: str):
        """(read(offset, length), size) for a file stored unpacked: reads straight from the mapped file."""
        off, size, _fi, _li = self.files[vpath]
        if self.is_packed(vpath):
            raise ValueError(f'{vpath} is stored compressed')
        mm = self._mm

        def read(o: int, n: int) -> bytes:
            o = max(0, min(o, size))
            return mm[off + o:off + min(size, o + n)]
        return read, size

    def is_packed(self, vpath: str) -> bool:
        """True if the container stores this file zstd-compressed (record compression 1)."""
        fi = self.files[vpath][2]
        return struct.unpack_from('<Q', self._mm, self.file_off + fi * 80 + 0x40)[0] != 0

    def sha256(self, vpath: str) -> str:
        fi = self.files[vpath][2]
        o = self.file_off + fi * 80 + 0x20
        return self._mm[o:o + 32].hex()

    def layer_of(self, vpath: str) -> str:
        return self.layers[self.files[vpath][3]]

    def info(self) -> dict:
        return {
            'path': self.path,
            'name': os.path.basename(self.path),
            'size': self.file_size,
            'layers': self.layers,
            'sections': [{'title_id': s['title_id'], 'name': self.layers[i]} for i, s in enumerate(self.sections)],
            'file_count': len(self.files),
        }

    def close(self):
        try:
            self._mm.close()
        finally:
            self._fh.close()
