"""Koei Tecmo (Katana Engine) resource packs: *.fdata and single-entry *.file.

Layout (little endian, reverse engineered from Hyrule Warriors: Age of Imprisonment):
  *.fdata  "PDRK0000", u32 header size (0x10), u32 0, then IDRK entries aligned to 16 bytes
  *.file   one IDRK entry
  IDRK     "IDRK0000", u64 entry size, u64 stored size, u64 size, u32 parameter count, u32 KTID (name hash),
           u32 type KTID, u32 flags, parameters ...; the data are the last <stored size> bytes of the entry
  flags & 0x400000  data compressed as chunks: [u16 zlib size][8 bytes][zlib stream] ... until a size of 0
                    (otherwise stored as is)
Entries only have hashes, no names: they are listed as 0x<KTID>.<extension>, the extension comes from the
payload magic, which Koei Tecmo stores reversed (GT1G = G1T texture, _M1G = G1M model, ...).
"""
from __future__ import annotations

import struct
import zlib

COMPRESSED = 0x400000
MAGICS = (b'PDRK0000', b'IDRK0000')
_EXT = {b'GT1G': 'g1t', b'\x1aE\xdf\xa3': 'webm', b'SARC': 'sarc'}


def is_pack(head: bytes) -> bool:
    return head[:8] in MAGICS


def scan(read, size: int):
    """Entries of a pack from their headers only: yields (o, entry size, stored size, size, flags, KTID, type KTID)."""
    o = 0x10 if read(0, 8) == b'PDRK0000' else 0
    while o + 0x30 <= size:
        h = read(o, 0x30)
        if h[:8] != b'IDRK0000':
            break
        esz, ssz, usz = struct.unpack_from('<QQQ', h, 8)
        _params, ktid, ktype, flags = struct.unpack_from('<IIII', h, 0x20)
        if esz < 0x30 or ssz > esz:
            break
        yield o, esz, ssz, usz, flags, ktid, ktype
        o = (o + esz + 15) & ~15


def entry_head(read, e, n: int) -> bytes:
    """The first n bytes (or fewer) of an entry's data, unpacking only the chunks needed."""
    o, esz, ssz, usz, flags = e[:5]
    start, end = o + esz - ssz, o + esz
    if not (flags & COMPRESSED) or ssz == usz:
        return read(start, min(n, usz))
    out = bytearray()
    pos = start
    while pos + 2 <= end and len(out) < n:
        clen = struct.unpack_from('<H', read(pos, 2))[0]
        if not clen:
            break
        out += zlib.decompress(read(pos + 10, clen))
        pos += 10 + clen
    return bytes(out[:n])


def entry_bytes(read, e) -> bytes:
    """Unpacked data of an entry (o, entry size, stored size, size, flags, ...)."""
    o, esz, ssz, usz, flags = e[:5]
    start, end = o + esz - ssz, o + esz
    if not (flags & COMPRESSED) or ssz == usz:
        return read(start, usz)
    out = bytearray()
    pos = start
    while pos + 2 <= end and len(out) < usz:
        clen = struct.unpack_from('<H', read(pos, 2))[0]
        if not clen:
            break
        out += zlib.decompress(read(pos + 10, clen))
        pos += 10 + clen
    if len(out) != usz:
        raise ValueError(f'entry 0x{e[5]:08x}: unpacked to {len(out)} bytes instead of {usz}')
    return bytes(out)


def extension(magic: bytes) -> str:
    """File extension for a payload magic: known ones, else the reversed magic (_M1G -> g1m), else 'bin'."""
    m = magic[:4]
    if m in _EXT:
        return _EXT[m]
    if len(m) == 4 and all(48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 or b == 95 for b in m) \
            and any(65 <= (b & 0xDF) <= 90 for b in m):
        return m[::-1].decode('ascii').strip('_').lower()
    return 'bin'


class KtPack:
    """Same interface as sarc.Sarc (entries, names(), get(), size()); data are read on demand.

    read(offset, length) -> bytes gives random access to the pack, size is its length."""

    def __init__(self, read, size: int):
        self._read = read
        self.entries: dict[str, tuple] = {}
        o = 0x10 if read(0, 8) == b'PDRK0000' else 0
        while o + 0x30 <= size:
            h = read(o, 0x30)
            if h[:8] != b'IDRK0000':
                break
            esz, ssz, usz = struct.unpack_from('<QQQ', h, 8)
            _params, ktid, ktype, flags = struct.unpack_from('<IIII', h, 0x20)
            if esz < 0x30 or ssz > esz:
                break
            e = (o, esz, ssz, usz, flags, ktid, ktype)
            name = f'0x{ktid:08x}.{extension(self._head(e, 8))}'
            if name in self.entries:  # same hash twice (different types)
                stem, _, ext = name.rpartition('.')
                k = 2
                while f'{stem}_{k}.{ext}' in self.entries:
                    k += 1
                name = f'{stem}_{k}.{ext}'
            self.entries[name] = e
            o = (o + esz + 15) & ~15

    @staticmethod
    def _compressed(e) -> bool:
        return bool(e[4] & COMPRESSED) and e[2] != e[3]

    def _head(self, e, n: int) -> bytes:
        o, esz, ssz, usz = e[:4]
        start = o + esz - ssz
        if not self._compressed(e):
            return self._read(start, min(n, usz))
        clen = struct.unpack_from('<H', self._read(start, 2))[0]
        if not clen:
            return b''
        try:
            return zlib.decompressobj().decompress(self._read(start + 10, clen), n)
        except zlib.error:
            return b''

    def names(self):
        return list(self.entries)

    def size(self, name: str) -> int:
        return self.entries[name][3]

    def reader(self, name: str):
        """(read(offset, length), size) of an entry stored uncompressed (random access), else None."""
        e = self.entries[name]
        if self._compressed(e):
            return None
        start, usz = e[0] + e[1] - e[2], e[3]
        return (lambda o, n: self._read(start + o, max(0, min(n, usz - o)))), usz

    def ktid(self, name: str) -> tuple[int, int]:
        """(KTID, type KTID) of an entry."""
        e = self.entries[name]
        return e[5], e[6]

    def get(self, name: str) -> bytes:
        return entry_bytes(self._read, self.entries[name])
