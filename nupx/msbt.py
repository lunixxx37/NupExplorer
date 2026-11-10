"""MSBT message files (Nintendo LMS): labels + texts with control tags.

Tags are rendered as {group.type} or {group.type:param} (param shown as text when it is a
length-prefixed string, otherwise as hex). Supports UTF-8, UTF-16 and UTF-32 files.
"""
from __future__ import annotations

import csv
import io
import json
import struct


def _tag_param(p: bytes, e: str) -> str:
    if not p:
        return ''
    if len(p) >= 2:
        n = struct.unpack_from(e + 'H', p, 0)[0]
        if 0 < n <= len(p) - 2:
            s = p[2:2 + n]
            try:
                txt = s.decode('utf-8') if b'\x00' not in s else s.decode('utf-16-le' if e == '<' else 'utf-16-be')
                if txt.isprintable():
                    return txt
            except UnicodeDecodeError:
                pass
    return p.hex()


def _decode_text(b: bytes, enc: int, e: str) -> str:
    out = []
    if enc == 0:  # UTF-8, tags introduced by single bytes 0x0E / 0x0F
        i = 0
        buf = bytearray()
        n = len(b)
        while i < n:
            c = b[i]
            if c == 0:
                break
            if c == 0x0E and i + 7 <= n:
                out.append(buf.decode('utf-8', 'replace'))
                buf = bytearray()
                g, t, sz = struct.unpack_from(e + 'HHH', b, i + 1)
                p = b[i + 7:i + 7 + sz]
                prm = _tag_param(p, e)
                out.append(f'{{{g}.{t}:{prm}}}' if prm else f'{{{g}.{t}}}')
                i += 7 + sz
                continue
            if c == 0x0F and i + 5 <= n:
                out.append(buf.decode('utf-8', 'replace'))
                buf = bytearray()
                g, t = struct.unpack_from(e + 'HH', b, i + 1)
                out.append(f'{{/{g}.{t}}}')
                i += 5
                continue
            buf.append(c)
            i += 1
        out.append(buf.decode('utf-8', 'replace'))
        return ''.join(out)
    width = 2 if enc == 1 else 4
    fmt = e + ('H' if width == 2 else 'I')
    codec = ('utf-16-le' if e == '<' else 'utf-16-be') if width == 2 else ('utf-32-le' if e == '<' else 'utf-32-be')
    i = 0
    buf = bytearray()
    n = len(b) - width + 1
    while i < n:
        c = struct.unpack_from(fmt, b, i)[0]
        if c == 0:
            break
        if c == 0x0E:
            out.append(buf.decode(codec, 'replace'))
            buf = bytearray()
            g, t, sz = struct.unpack_from(e + 'HHH', b, i + width)
            p = b[i + width + 6:i + width + 6 + sz]
            prm = _tag_param(p, e)
            out.append(f'{{{g}.{t}:{prm}}}' if prm else f'{{{g}.{t}}}')
            i += width + 6 + sz
            continue
        if c == 0x0F:
            out.append(buf.decode(codec, 'replace'))
            buf = bytearray()
            g, t = struct.unpack_from(e + 'HH', b, i + width)
            out.append(f'{{/{g}.{t}}}')
            i += width + 4
            continue
        buf += b[i:i + width]
        i += width
    out.append(buf.decode(codec, 'replace'))
    return ''.join(out)


def parse(b: bytes) -> dict:
    if b[:8] != b'MsgStdBn':
        raise ValueError('Not an MSBT file')
    e = '<' if b[8:10] == b'\xff\xfe' else '>'
    enc = b[0x0C]
    nsec = struct.unpack_from(e + 'H', b, 0x0E)[0]
    o = 0x20
    secs = {}
    for _ in range(nsec):
        mg = b[o:o + 4]
        sz = struct.unpack_from(e + 'I', b, o + 4)[0]
        secs[mg] = (o + 0x10, sz)
        o = (o + 0x10 + sz + 15) & ~15
    labels = {}
    if b'LBL1' in secs:
        lo, _ = secs[b'LBL1']
        nb = struct.unpack_from(e + 'I', b, lo)[0]
        for k in range(nb):
            cnt, off = struct.unpack_from(e + 'II', b, lo + 4 + k * 8)
            p = lo + off
            for _ in range(cnt):
                ln = b[p]
                name = b[p + 1:p + 1 + ln].decode('utf-8', 'replace')
                idx = struct.unpack_from(e + 'I', b, p + 1 + ln)[0]
                labels[idx] = name
                p += 1 + ln + 4
    rows = []
    if b'TXT2' in secs:
        to, ts = secs[b'TXT2']
        cnt = struct.unpack_from(e + 'I', b, to)[0]
        offs = struct.unpack_from(e + f'{cnt}I', b, to + 4)
        for i in range(cnt):
            s = to + offs[i]
            en = to + (offs[i + 1] if i + 1 < cnt else ts)
            rows.append({'index': i, 'label': labels.get(i, f'#{i}'), 'text': _decode_text(b[s:en], enc, e)})
    return {'encoding': {0: 'UTF-8', 1: 'UTF-16', 2: 'UTF-32'}.get(enc, str(enc)), 'rows': rows}


def to_json(parsed: dict) -> bytes:
    return json.dumps({r['label']: r['text'] for r in parsed['rows']}, ensure_ascii=False, indent=2).encode('utf-8')


def to_csv(parsed: dict, compare: dict | None = None, lang_a: str = 'Text', lang_b: str = 'Vergleich') -> bytes:
    bio = io.StringIO()
    w = csv.writer(bio, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    if compare:
        cmp = {r['label']: r['text'] for r in compare['rows']}
        w.writerow(['Label', lang_a, lang_b])
        for r in parsed['rows']:
            w.writerow([r['label'], r['text'], cmp.get(r['label'], '')])
    else:
        w.writerow(['Label', lang_a])
        for r in parsed['rows']:
            w.writerow([r['label'], r['text']])
    return ('﻿' + bio.getvalue()).encode('utf-8')
