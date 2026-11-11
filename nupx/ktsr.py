"""Koei Tecmo KTSR sound banks (Katana Engine games, e.g. Hyrule Warriors: Age of Imprisonment).

Reverse engineered from the game data and cross-checked with vgmstream's KTSR reader (src/meta/ktsr.c and
ka1a.c, https://github.com/vgmstream/vgmstream, ISC license).

Files: asset banks *.srsa ("ktsl2asbin": sound headers and the data of short sounds) and stream files *.srst
("ktsl2stbin": the data of long sounds); both are a 16-byte wrapper ("ASRS"/"TSRS", u32 0, u64 size) around KTSR.

KTSR header (little endian): "KTSR", u32 type (0x1A487B77 asset bank, 0xFCDD9402 stream), +0x0b u8 platform,
+0x0c u32 audio id, +0x18 u32 size, +0x20 u8 key length + key (Blowfish key of the stream data); chunks from +0x40
(u32 id, u32 size, ...):
  0x70CBCCC5 sound   +0x08 id, +0x0c flags (0x8 = name obfuscated), +0x10 sub-header count, +0x14 offset of the
                     sub-header offsets, +0x18 name offset (offsets relative to the chunk)
  0x368C88BD config  +0x08 sound id, +0x0c flags (0x200 = name obfuscated), +0x28 name offset
  sub-header 0xE96FD86A / 0x2EB6CA6F (data in the bank): +0x0c channels, +0x10 u8 format (1 = KA1A), +0x14 rate,
                     +0x18 samples, +0x20 s32 loop start (-1 = none), +0x28 offset of the s32 bitrate mode,
                     +0x30 / +0x34 offsets of the per-channel data offsets / sizes (relative to the sub-header)
  sub-header 0xD71F3A79 (data in a stream file): +0x0c channels, +0x14 format (0x1001 = KA1A file), +0x18 rate,
                     +0x1c samples, +0x24 s32 loop start, +0x34 offset (from the stream's KTSR header), +0x38 size
Stream file: KTSR header, then one chunk per sound: 0x15F4D409, u32 chunk size, u32 sound id, u32 data offset
(0x20), u32 data size, data = a complete KA1A file, Blowfish (ECB) encrypted with the stream header's key (blocks
counted from the KTSR header).
Names: XOR-ed with the C rand() sequence (seed = seed * 0x343FD + 0x269EC3, byte = seed >> 16 & 0xFF) seeded with
the audio id; the terminating zero is obfuscated as well.
Which stream file belongs to a bank is not stored in the bank: candidates are checked against the chunks in front
of the sounds' data (chunk id, sound id, size), see find_stream(). Sounds whose bank offset does not fit (banks the
update did not replace, pointing into a stream file it did replace) are looked up by their id in the chunk chain.
"""
from __future__ import annotations

import struct
from functools import lru_cache

from . import ka1a
from .blowfish import Blowfish

MAGIC = b'KTSR'
WRAPPERS = (b'ASRS', b'TSRS')
TYPE_BANK = 0x1A487B77
TYPE_STREAM = 0xFCDD9402
CHUNK_SOUND = 0x70CBCCC5
CHUNK_CONFIG = 0x368C88BD
CHUNK_STREAM_SOUND = 0x15F4D409
SUB_INTERNAL = (0xE96FD86A, 0x2EB6CA6F)
SUB_EXTERNAL = 0xD71F3A79
FORMAT_KA1A = 0x0001       # internal sounds: per-channel KA1A frames
FORMAT_KA1A_FILE = 0x1001  # external sounds: KA1A file
_BAD_CHARS = set('\\/:*?"<>|')


def ktsr_start(head: bytes) -> int:
    """Offset of the KTSR header (0, or 16 behind an ASRS/TSRS wrapper), -1 if there is none."""
    if head[:4] == MAGIC:
        return 0
    if head[:4] in WRAPPERS and head[16:20] == MAGIC:
        return 16
    return -1


def ktsr_type(head: bytes) -> int | None:
    o = ktsr_start(head)
    return struct.unpack_from('<I', head, o + 4)[0] if o >= 0 and len(head) >= o + 8 else None


def is_bank(head: bytes) -> bool:
    return ktsr_type(head) == TYPE_BANK


def deobfuscate(raw: bytes, seed: int) -> bytes:
    """Name bytes -> plain bytes up to the (obfuscated) terminating zero."""
    out = bytearray()
    for b in raw:
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        c = b ^ ((seed >> 16) & 0xFF)
        if not c:
            break
        out.append(c)
    return bytes(out)


def _text(raw: bytes) -> str:
    """Name bytes -> text usable as a file name (ASCII, rarely Shift-JIS)."""
    for enc in ('utf-8', 'cp932'):
        try:
            s = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        s = raw.decode('latin-1')
    return ''.join('_' if ch in _BAD_CHARS or ord(ch) < 32 else ch for ch in s).strip(' .')


@lru_cache(maxsize=8)
def _cipher(key: bytes) -> Blowfish:
    return Blowfish(key)


class Sound:
    __slots__ = ('id', 'flags', 'name', 'external', 'channels', 'rate', 'samples', 'loop_start', 'format', 'mode',
                 'starts', 'sizes', 'offset', 'size')

    def __init__(self, sid: int, flags: int):
        self.id, self.flags, self.name = sid, flags, ''
        self.external = False
        self.channels = self.rate = self.samples = self.offset = self.size = 0
        self.loop_start = -1
        self.format = self.mode = None
        self.starts: list[int] = []
        self.sizes: list[int] = []

    @property
    def is_ka1a(self) -> bool:
        return self.format == (FORMAT_KA1A_FILE if self.external else FORMAT_KA1A) and \
            (self.external or self.mode in ka1a.MODES)


class Bank:
    """Sounds of a KTSR asset bank."""

    def __init__(self, data: bytes):
        b = ktsr_start(data)
        if b < 0 or ktsr_type(data) != TYPE_BANK:
            raise ValueError('Not a KTSR sound bank')
        self.data, self.base = data, b
        self.platform = data[b + 0x0b]
        self.audio_id = self._u32(0x0c)
        end = min(self._u32(0x18) or len(data) - b, len(data) - b)
        self.sounds: list[Sound] = []
        config_names: dict[int, str] = {}
        pos = 0x40
        while pos + 8 <= end:
            cid, csize = self._u32(pos), self._u32(pos + 4)
            if csize < 8 or pos + csize > end:
                break
            try:
                if cid == CHUNK_SOUND:
                    s = self._sound(pos, csize)
                    if s is not None:
                        self.sounds.append(s)
                elif cid == CHUNK_CONFIG:
                    sid, flags = self._u32(pos + 8), self._u32(pos + 0x0c)
                    no = self._u32(pos + 0x28)
                    if no and no < csize and not config_names.get(sid):
                        config_names[sid] = self._name(pos + no, pos + csize, flags & 0x200)
            except (struct.error, IndexError):
                pass
            pos += csize
        for s in self.sounds:  # like vgmstream: the config name if there is one, else the sound's own name
            s.name = config_names.get(s.id) or s.name

    def _u32(self, o: int) -> int:
        return struct.unpack_from('<I', self.data, self.base + o)[0]

    def _s32(self, o: int) -> int:
        return struct.unpack_from('<i', self.data, self.base + o)[0]

    def _name(self, start: int, end: int, obfuscated) -> str:
        raw = self.data[self.base + start:self.base + min(end, start + 255)]
        return _text(deobfuscate(raw, self.audio_id) if obfuscated else raw.split(b'\0')[0])

    def _sound(self, pos: int, csize: int) -> Sound | None:
        sid, flags, count, table, name_off = struct.unpack_from('<5I', self.data, self.base + pos + 8)
        if not count:
            return None
        s = Sound(sid, flags)
        if name_off and name_off < csize:
            s.name = self._name(pos + name_off, pos + csize, flags & 0x8)
        sub = pos + self._u32(pos + table)
        kind = self._u32(sub)
        if kind == SUB_EXTERNAL:
            s.external = True
            s.channels, s.format, s.rate, s.samples = (self._u32(sub + 0x0c), self._u32(sub + 0x14),
                                                       self._u32(sub + 0x18), self._u32(sub + 0x1c))
            s.loop_start = self._s32(sub + 0x24)
            s.offset, s.size = self._u32(sub + 0x34), self._u32(sub + 0x38)
        elif kind in SUB_INTERNAL:
            s.channels = self._u32(sub + 0x0c)
            s.format = self.data[self.base + sub + 0x10]
            s.rate, s.samples = self._u32(sub + 0x14), self._u32(sub + 0x18)
            s.loop_start = self._s32(sub + 0x20)
            s.mode = self._s32(sub + self._u32(sub + 0x28))
            starts, sizes = self._u32(sub + 0x30), self._u32(sub + 0x34)
            s.starts = [sub + self._u32(sub + starts + 4 * c) for c in range(s.channels)]
            s.sizes = [self._u32(sub + sizes + 4 * c) for c in range(s.channels)]
        else:
            s.format = None  # sub-header type not known
        return s

    def channel_data(self, s: Sound) -> list[bytes]:
        b = self.base
        return [self.data[b + o:b + o + n] for o, n in zip(s.starts, s.sizes)]


class StreamFile:
    """A KTSR stream file (ktsl2stbin). read(offset, length) gives access to the file (with or without wrapper)."""

    def __init__(self, read, size: int):
        head = read(0, 0x40)
        b = ktsr_start(head)
        if b < 0 or ktsr_type(head) != TYPE_STREAM:
            raise ValueError('Not a KTSR stream file')
        self._read, self.base = read, b
        self.size = min(struct.unpack_from('<I', head, b + 0x18)[0] or size - b, size - b)
        n = head[b + 0x20]
        self.key = bytes(head[b + 0x21:b + 0x21 + n]) if 0 < n < 0x20 else b''
        self._index: dict[int, tuple[int, int]] | None = None

    def matches(self, s: Sound) -> bool:
        """Is the sound's data where the bank says? (chunk in front of the data: id, sound id, size)"""
        if s.offset < 0x20 or s.offset + s.size > self.size:
            return False
        cid, _csize, sid, _doff, dsize = struct.unpack('<5I', self._read(self.base + s.offset - 0x20, 0x14))
        return cid == CHUNK_STREAM_SOUND and sid == s.id and dsize == s.size

    def index(self) -> dict[int, tuple[int, int]]:
        """{sound id: (offset, size)} from the chain of sound chunks. Cached."""
        if self._index is None:
            idx = {}
            pos = 0x40
            while pos + 0x20 <= self.size:
                cid, csize, sid, doff, dsize = struct.unpack('<5I', self._read(self.base + pos, 0x14))
                if cid != CHUNK_STREAM_SOUND or csize < 0x20 or doff + dsize > csize:
                    break
                idx.setdefault(sid, (pos + doff, dsize))
                pos += csize
            self._index = idx
        return self._index

    def locate(self, s: Sound) -> tuple[int, int] | None:
        """(offset, size) of the sound's data: where the bank says, else found by the sound id (banks the
        update left unchanged can point to old offsets in a stream file it replaced)."""
        if self.matches(s):
            return s.offset, s.size
        return self.index().get(s.id)

    def read(self, offset: int, size: int) -> bytes:
        """size bytes of sound data at offset (from the KTSR header), decrypted."""
        start = offset & ~7
        data = self._read(self.base + start, (offset + size - start + 7) & ~7)
        if self.key:
            data = _cipher(self.key).decrypt_ecb(data)
        return data[offset - start:offset - start + size]


class BankArchive:
    """The sounds of an asset bank as archive entries <name>.ka1a (interface of sarc.Sarc: entries, names(), get(),
    size(); plus head()). Internal sounds get a KA1A header, external ones are read from the bank's stream file,
    which find_stream(bank) locates (called once, when the first external sound is read)."""

    def __init__(self, data: bytes, find_stream=None):
        self.bank = Bank(data)
        self._find = find_stream
        self._stream: StreamFile | None = None
        self._looked = False
        self.entries: dict[str, Sound] = {}
        for s in self.bank.sounds:
            if s.format is None:
                continue
            ext = 'ka1a' if s.is_ka1a else 'bin'
            stem = s.name or f'0x{s.id:08x}'
            name, k = f'{stem}.{ext}', 2
            while name in self.entries:
                name, k = f'{stem}_{k}.{ext}', k + 1
            self.entries[name] = s

    def names(self) -> list[str]:
        return list(self.entries)

    def sound(self, name: str) -> Sound:
        return self.entries[name]

    def size(self, name: str) -> int:
        s = self.entries[name]
        if s.external:
            return s.size
        if s.is_ka1a:
            n = min(s.sizes, default=0) // ka1a.frame_size(s.mode)
            return ka1a.HEADER_SIZE + n * ka1a.frame_size(s.mode) * len(s.sizes)
        return sum(s.sizes)

    def stream(self) -> StreamFile | None:
        if not self._looked:
            self._looked = True
            if self._find is not None and any(s.external for s in self.bank.sounds):
                self._stream = self._find(self.bank)
        return self._stream

    def _locate(self, s: Sound) -> tuple[StreamFile, int, int]:
        st = self.stream()
        if st is None:
            raise FileNotFoundError('The stream file with the data of this sound bank is not in the NUP')
        loc = st.locate(s)
        if loc is None:
            raise FileNotFoundError(f'Sound 0x{s.id:08x} is not in the stream file of this bank')
        return st, loc[0], loc[1]

    def get(self, name: str) -> bytes:
        s = self.entries[name]
        if s.external:
            st, offset, size = self._locate(s)
            data = st.read(offset, size)
            if s.is_ka1a and data[:4] != ka1a.MAGIC:
                raise ValueError(f'Sound 0x{s.id:08x}: unexpected data in the stream file (no KA1A header)')
            return data
        chans = self.bank.channel_data(s)
        if not s.is_ka1a:
            return b''.join(chans)
        return ka1a.build_file(chans, s.rate, s.samples, s.mode, s.loop_start if s.loop_start >= 0 else None)

    def head(self, name: str, n: int) -> bytes:
        s = self.entries[name]
        if s.external:
            st, offset, size = self._locate(s)
            return st.read(offset, min(n, size))
        return self.get(name)[:n]


def find_stream(bank: Bank, candidates, bank_dir: str = '') -> StreamFile | None:
    """The stream file holding the bank's external sounds among candidates [(path, StreamFile)]: most sounds found
    (at least half of up to 8 probes, by offset or by sound id), ties broken by the folder (bank folder/data
    first; every language has its own stream files with the same sound ids)."""
    ext = [s for s in bank.sounds if s.external]
    if not ext:
        return None
    probes = ext[::max(1, len(ext) // 8)][:8]
    for by_id in (False, True):  # by offset first: cheap, no chunk index needed
        best = None
        for path, sf in candidates:
            try:
                score = sum((sf.locate(s) is not None) if by_id else sf.matches(s) for s in probes)
            except (struct.error, OSError, ValueError):
                continue
            if not score or score * 2 < len(probes):
                continue
            sdir = path.split('::')[0].rpartition('/')[0]
            near = (sdir == bank_dir + '/data', sdir == bank_dir, sdir.startswith(bank_dir + '/'),
                    -abs(sdir.count('/') - bank_dir.count('/')))
            if best is None or (score, near) > best[0]:
                best = ((score, near), sf)
        if best:
            return best[1]
    return None
