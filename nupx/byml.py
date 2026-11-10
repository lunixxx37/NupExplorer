"""BYML (binary YAML, versions 2-7) reader and YAML/JSON writers.

Node types: A0 string, A1 binary, A2 binary+alignment, C0 array, C1 dict, C2 string table,
20/21 hash dicts (v7, u32/u64 hashed keys), D0 bool, D1 int, D2 float, D3 uint,
D4 int64, D5 uint64, D6 double, FF null.
"""
from __future__ import annotations

import base64
import json
import math
import re
import struct


class HashKey(int):
    """Key of a hash dict (shown as hex)."""

    def __str__(self):
        return f'0x{int(self):08X}'


class Binary(bytes):
    pass


class BymlReader:
    def __init__(self, data: bytes):
        if data[:2] == b'YB':
            self.e = '<'
        elif data[:2] == b'BY':
            self.e = '>'
        else:
            raise ValueError('Not a BYML file')
        self.d = data
        e = self.e
        self.version = struct.unpack_from(e + 'H', data, 2)[0]
        key_off, str_off, root_off = struct.unpack_from(e + 'III', data, 4)
        self.keys = self._string_table(key_off) if key_off else []
        self.strings = self._string_table(str_off) if str_off else []
        self.root_off = root_off

    def _u24(self, off):
        b = self.d[off:off + 3]
        return int.from_bytes(b, 'little' if self.e == '<' else 'big')

    def _string_table(self, off):
        d = self.d
        if d[off] != 0xC2:
            raise ValueError(f'BYML: string table expected ({d[off]:#x})')
        n = self._u24(off + 1)
        offs = struct.unpack_from(self.e + f'{n + 1}I', d, off + 4)
        out = []
        for i in range(n):
            s = off + offs[i]
            z = d.index(b'\0', s)
            out.append(d[s:z].decode('utf-8', 'replace'))
        return out

    def root(self):
        if not self.root_off:
            return None
        return self._node(self.d[self.root_off], self.root_off, is_offset=True)

    def _value(self, t, raw, pos):
        """raw: the 4-byte inline value (u32) at pos."""
        e, d = self.e, self.d
        if t == 0xA0:
            return self.strings[raw] if raw < len(self.strings) else f'<str {raw}>'
        if t == 0xD0:
            return bool(raw)
        if t == 0xD1:
            return struct.unpack(e + 'i', struct.pack(e + 'I', raw))[0]
        if t == 0xD2:
            return struct.unpack(e + 'f', struct.pack(e + 'I', raw))[0]
        if t == 0xD3:
            return raw
        if t == 0xD4:
            return struct.unpack_from(e + 'q', d, raw)[0]
        if t == 0xD5:
            return struct.unpack_from(e + 'Q', d, raw)[0]
        if t == 0xD6:
            return struct.unpack_from(e + 'd', d, raw)[0]
        if t == 0xFF:
            return None
        if t in (0xA1, 0xA2):
            size = struct.unpack_from(e + 'I', d, raw)[0]
            start = raw + (8 if t == 0xA2 else 4)
            return Binary(d[start:start + size])
        if t in (0xC0, 0xC1, 0x20, 0x21, 0xC2):
            return self._node(t, raw, is_offset=True)
        return f'<Typ 0x{t:02X}>'

    def _node(self, t, off, is_offset=True):
        d, e = self.d, self.e
        if d[off] != t:
            t = d[off]
        n = self._u24(off + 1)
        if t == 0xC0:
            types = d[off + 4:off + 4 + n]
            vo = off + 4 + ((n + 3) & ~3)
            return [self._value(types[i], struct.unpack_from(e + 'I', d, vo + 4 * i)[0], vo + 4 * i) for i in range(n)]
        if t == 0xC1:
            out = {}
            for i in range(n):
                p = off + 4 + 8 * i
                k = self._u24(p)
                vt = d[p + 3]
                raw = struct.unpack_from(e + 'I', d, p + 4)[0]
                key = self.keys[k] if k < len(self.keys) else f'<key {k}>'
                out[key] = self._value(vt, raw, p + 4)
            return out
        if t in (0x20, 0x21):
            ks = 4 if t == 0x20 else 8
            ent = ks + 4
            types = d[off + 4 + n * ent:off + 4 + n * ent + n]
            out = {}
            for i in range(n):
                p = off + 4 + i * ent
                h = struct.unpack_from(e + ('I' if ks == 4 else 'Q'), d, p)[0]
                raw = struct.unpack_from(e + 'I', d, p + ks)[0]
                out[HashKey(h)] = self._value(types[i], raw, p + ks)
            return out
        if t == 0xC2:
            return self._string_table(off)
        raise ValueError(f'BYML: unknown node type 0x{t:02X}')


def parse(data: bytes):
    return BymlReader(data).root()


# ------------------------------------------------------------------ writers
_PLAIN = re.compile(r'^[A-Za-z_][A-Za-z0-9_./\-]*$')
_RESERVED = {'true', 'false', 'null', 'yes', 'no', 'on', 'off', '~'}


def _scalar(v) -> str:
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, HashKey):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return '.nan' if math.isnan(v) else ('.inf' if v > 0 else '-.inf')
        r = repr(v)
        return r if ('.' in r or 'e' in r) else r + '.0'
    if isinstance(v, Binary):
        b64 = base64.b64encode(bytes(v)).decode('ascii')
        if len(b64) > 120:
            b64 = b64[:120] + '...'
        return f'!!binary "{b64}"  # {len(v)} Bytes'
    s = str(v)
    if s and _PLAIN.match(s) and s.lower() not in _RESERVED:
        return s
    return json.dumps(s, ensure_ascii=False)


def _key(k) -> str:
    return _scalar(k) if not isinstance(k, str) else (k if k and _PLAIN.match(k) else json.dumps(k, ensure_ascii=False))


def to_yaml(obj, indent: int = 0, max_lines: int | None = None) -> str:
    lines: list[str] = []

    def emit(o, ind, prefix=''):
        if max_lines and len(lines) > max_lines:
            return
        pad = ' ' * ind
        if isinstance(o, dict):
            if not o:
                lines.append(pad + prefix + '{}')
                return
            first = True
            for k, v in o.items():
                lead = pad + (prefix if first else ' ' * len(prefix))
                first = False
                if isinstance(v, (dict, list)) and v:
                    lines.append(f'{lead}{_key(k)}:')
                    emit(v, ind + len(prefix) + 2)
                else:
                    lines.append(f'{lead}{_key(k)}: {_scalar(v) if not isinstance(v, (dict, list)) else ("{}" if isinstance(v, dict) else "[]")}')
            return
        if isinstance(o, list):
            if not o:
                lines.append(pad + prefix + '[]')
                return
            for v in o:
                if isinstance(v, dict) and v:
                    emit(v, ind, prefix='- ')
                elif isinstance(v, list) and v:
                    lines.append(pad + '-')
                    emit(v, ind + 2)
                else:
                    lines.append(pad + '- ' + (_scalar(v) if not isinstance(v, (dict, list)) else ('{}' if isinstance(v, dict) else '[]')))
            return
        lines.append(pad + prefix + _scalar(o))

    emit(obj, indent)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines] + [f'# ... truncated (the export contains everything)']
    return '\n'.join(lines) + '\n'


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if isinstance(o, Binary):
        return {'$binary': base64.b64encode(bytes(o)).decode('ascii')}
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return str(o)
    return o


def to_json(obj) -> bytes:
    return json.dumps(_jsonable(obj), ensure_ascii=False, indent=2).encode('utf-8')
