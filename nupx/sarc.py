"""SARC archive reader (Nintendo; used by *.pack, *.sarc, *.blarc ...)."""
from __future__ import annotations

import struct


class Sarc:
    def __init__(self, data: bytes):
        if data[:4] != b'SARC':
            raise ValueError('Not a SARC archive')
        bom = data[6:8]
        self.le = bom == b'\xff\xfe'
        e = '<' if self.le else '>'
        hdr_len, _bom, _size, data_off = struct.unpack_from(e + 'HHII', data, 4)
        sfat = hdr_len
        if data[sfat:sfat + 4] != b'SFAT':
            raise ValueError('SARC: SFAT missing')
        sfat_len, count, _hash_key = struct.unpack_from(e + 'HHI', data, sfat + 4)
        nodes_off = sfat + sfat_len
        sfnt = nodes_off + count * 16
        if data[sfnt:sfnt + 4] != b'SFNT':
            raise ValueError('SARC: SFNT missing')
        sfnt_len = struct.unpack_from(e + 'H', data, sfnt + 4)[0]
        names_off = sfnt + sfnt_len
        self.data = data
        self.entries: dict[str, tuple[int, int]] = {}
        for i in range(count):
            h, attr, start, end = struct.unpack_from(e + 'IIII', data, nodes_off + i * 16)
            if attr & 0x01000000:
                o = names_off + (attr & 0xFFFF) * 4
                z = data.index(b'\0', o)
                name = data[o:z].decode('utf-8', 'replace')
            else:
                name = f'{h:08x}.bin'
            self.entries[name] = (data_off + start, data_off + end)

    def names(self):
        return list(self.entries)

    def get(self, name: str) -> bytes:
        s, e = self.entries[name]
        return self.data[s:e]

    def size(self, name: str) -> int:
        s, e = self.entries[name]
        return e - s
