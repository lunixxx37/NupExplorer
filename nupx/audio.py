"""BWAV audio (Nintendo): PCM16, DSP-ADPCM and Nintendo Opus.

Header: "BWAV", BOM, u16 version, u32 crc, u16 prefetch, u16 channels, then per channel 0x4C bytes:
  +0x00 u16 codec (0 PCM16, 1 DSP-ADPCM, 2 Opus), +0x02 u16 pan, +0x04 u32 rate,
  +0x08 u32 samples (non-prefetch), +0x0C u32 samples, +0x10 s16 coefs[16],
  +0x30 u32 offset (non-prefetch), +0x34 u32 offset, +0x38 u32 flag,
  +0x3C u32 loop_end (0xFFFFFFFF = no loop), +0x40 u32 loop_start
Opus channel data: chunk 0x80000001 (header: version, channels, frame size, rate, data offset,
..., pre-skip) and chunk 0x80000004 with packets [BE u32 size][BE u32 final range][packet].
Opus is remuxed into Ogg pages and decoded by libsndfile (soundfile).
"""
from __future__ import annotations

import binascii
import io
import struct
import wave
from array import array

import numpy as np

CODECS = {0: 'PCM16', 1: 'DSP-ADPCM', 2: 'Opus'}


class BwavInfo:
    def __init__(self, data: bytes):
        if data[:4] != b'BWAV':
            raise ValueError('Not a BWAV file')
        self.prefetch = struct.unpack_from('<H', data, 0x0C)[0]
        self.channels = struct.unpack_from('<H', data, 0x0E)[0]
        self.ch = []
        for i in range(self.channels):
            o = 0x10 + i * 0x4C
            codec, pan, rate, ns_np, ns = struct.unpack_from('<HHIII', data, o)
            coefs = struct.unpack_from('<16h', data, o + 0x10)
            off_np, off = struct.unpack_from('<II', data, o + 0x30)
            flag, loop_end, loop_start = struct.unpack_from('<III', data, o + 0x38)
            self.ch.append({'codec': codec, 'rate': rate, 'samples': ns, 'coefs': coefs, 'offset': off,
                            'loop_end': loop_end, 'loop_start': loop_start})
        c0 = self.ch[0] if self.ch else {}
        self.codec = c0.get('codec', 0)
        self.rate = c0.get('rate', 48000)
        self.samples = c0.get('samples', 0)
        le = c0.get('loop_end', 0xFFFFFFFF)
        self.loop = le != 0xFFFFFFFF and le > c0.get('loop_start', 0)
        self.loop_start = c0.get('loop_start', 0) if self.loop else None
        self.loop_end = min(le, self.samples) if self.loop else None

    def to_dict(self) -> dict:
        return {'codec': CODECS.get(self.codec, str(self.codec)), 'rate': self.rate, 'channels': self.channels,
                'samples': self.samples, 'duration': self.samples / self.rate if self.rate else 0,
                'loop': self.loop, 'loop_start': self.loop_start, 'loop_end': self.loop_end,
                'prefetch': bool(self.prefetch)}


# ------------------------------------------------------------------ DSP-ADPCM
_NIB_HI = [(b >> 4) - 16 if (b >> 4) >= 8 else (b >> 4) for b in range(256)]
_NIB_LO = [(b & 15) - 16 if (b & 15) >= 8 else (b & 15) for b in range(256)]


def dsp_decode(buf: bytes, nsamples: int, coefs, hist1: int = 0, hist2: int = 0) -> np.ndarray:
    out = array('h', bytes(2 * nsamples))
    nframes = -(-nsamples // 14)
    buf = bytes(buf[:nframes * 8]) + bytes(8)
    hi, lo = _NIB_HI, _NIB_LO
    i = 0
    pos = 0
    for _ in range(nframes):
        hdr = buf[pos]
        scale = 1 << (hdr & 0xF)
        pi = (hdr >> 4) * 2
        c1 = coefs[pi]
        c2 = coefs[pi + 1]
        pos += 1
        end = min(i + 14, nsamples)
        for b in buf[pos:pos + 7]:
            for nib in (hi[b], lo[b]):
                if i >= end:
                    break
                s = (((nib * scale) << 11) + 1024 + c1 * hist1 + c2 * hist2) >> 11
                if s > 32767:
                    s = 32767
                elif s < -32768:
                    s = -32768
                out[i] = s
                i += 1
                hist2 = hist1
                hist1 = s
        pos += 7
    return np.frombuffer(out, np.int16)


# ------------------------------------------------------------------ Opus -> Ogg
_BITREV = bytes(int(f'{b:08b}'[::-1], 2) for b in range(256))


def _rev32(x: int) -> int:
    return int(f'{x:032b}'[::-1], 2)


def ogg_crc(data: bytes) -> int:
    """Ogg CRC (poly 0x04C11DB7, not reflected, init 0) computed via the C-implemented zlib CRC."""
    r = (~binascii.crc32(data.translate(_BITREV), 0xFFFFFFFF)) & 0xFFFFFFFF
    return _rev32(r)


def _ogg_page(packets: list, granule: int, serial: int, seq: int, flags: int) -> bytes:
    segs = bytearray()
    for p in packets:
        n = len(p)
        while n >= 255:
            segs.append(255)
            n -= 255
        segs.append(n)
    hdr = struct.pack('<4sBBqIIIB', b'OggS', 0, flags, granule, serial, seq, 0, len(segs)) + bytes(segs)
    page = bytearray(hdr + b''.join(packets))
    struct.pack_into('<I', page, 22, ogg_crc(bytes(page)))
    return bytes(page)


def _opus_samples(p: bytes) -> int:
    toc = p[0]
    cfg = toc >> 3
    if cfg < 12:
        fs = (480, 960, 1920, 2880)[cfg & 3]
    elif cfg < 16:
        fs = (480, 960)[cfg & 1]
    else:
        fs = (120, 240, 480, 960)[cfg & 3]
    c = toc & 3
    nf = 1 if c == 0 else 2 if c in (1, 2) else (p[1] & 0x3F if len(p) > 1 else 1)
    return fs * nf


def nx_opus_to_ogg(data: bytes, off: int, nsamples: int) -> bytes:
    if struct.unpack_from('<I', data, off)[0] != 0x80000001:
        raise ValueError('Opus header missing')
    _ver, nch, _fsize, rate, doff, _a, _b, preskip = struct.unpack_from('<BBHIIIII', data, off + 8)
    pos = off + doff
    if struct.unpack_from('<I', data, pos)[0] != 0x80000004:
        raise ValueError('Opus data block missing')
    dsize = struct.unpack_from('<I', data, pos + 4)[0]
    pos += 8
    end = pos + dsize
    packets = []
    while pos + 8 <= end:
        sz = struct.unpack_from('>I', data, pos)[0]
        packets.append(bytes(data[pos + 8:pos + 8 + sz]))
        pos += 8 + sz
    nch = max(1, nch)
    head = b'OpusHead' + struct.pack('<BBHIhB', 1, nch, preskip, rate or 48000, 0, 0)
    tags = b'OpusTags' + struct.pack('<I', 9) + b'NupExplor' + struct.pack('<I', 0)
    serial = 0x4E555058
    pages = [_ogg_page([head], 0, serial, 0, 2), _ogg_page([tags], 0, serial, 1, 0)]
    gp = 0
    seq = 2
    final_gp = nsamples + preskip
    for i in range(0, len(packets), 64):
        grp = packets[i:i + 64]
        gp += sum(_opus_samples(p) for p in grp)
        last = i + 64 >= len(packets)
        pages.append(_ogg_page(grp, min(gp, final_gp) if last else gp, serial, seq, 4 if last else 0))
        seq += 1
    return b''.join(pages)


def _opus_decode(data: bytes, off: int, nsamples: int) -> np.ndarray:
    import soundfile as sf
    ogg = nx_opus_to_ogg(data, off, nsamples)
    pcm, _sr = sf.read(io.BytesIO(ogg), dtype='int16', always_2d=True)
    return pcm[:, 0]


# ------------------------------------------------------------------ public API
def decode(data: bytes) -> tuple[np.ndarray, int, BwavInfo]:
    """Return (pcm int16 [n, channels], rate, info)."""
    info = BwavInfo(data)
    chans = []
    for c in info.ch:
        ns = c['samples']
        if c['codec'] == 0:
            ch = np.frombuffer(data, '<i2', ns, c['offset']).copy()
        elif c['codec'] == 1:
            ch = dsp_decode(data[c['offset']:], ns, c['coefs'])
        elif c['codec'] == 2:
            ch = _opus_decode(data, c['offset'], ns)
        else:
            raise ValueError(f'Audio codec {c["codec"]} not supported')
        if len(ch) < ns:
            ch = np.concatenate([ch, np.zeros(ns - len(ch), np.int16)])
        chans.append(ch[:ns])
    n = min(len(c) for c in chans) if chans else 0
    pcm = np.stack([c[:n] for c in chans], axis=1) if chans else np.zeros((0, 1), np.int16)
    return pcm, info.rate, info


def wav_bytes(pcm: np.ndarray, rate: int) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, 'wb') as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(pcm, dtype='<i2').tobytes())
    return bio.getvalue()
