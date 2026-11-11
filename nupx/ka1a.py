"""KA1A audio decoder (Koei Tecmo) – a Python/numpy port of vgmstream's KA1A decoder.

Ported from vgmstream (https://github.com/vgmstream/vgmstream, src/coding/libs/ka1a_dec.c and ka1a_dec_data.h,
ISC license), where the codec was reverse engineered by bnnm with help from Kelebek1 and AceKombat.

KA1A is a mono transform codec with a constant bit rate (11 modes, -5..5). Every frame yields 512 samples:
  f32 scale factor (negative: bands 8-11 carry more and bands 17-20 fewer coefficients)
  bit-packed positions of the coefficients of bands 8-20 (LSB first)
  8-bit codes: bands 0-7 at fixed positions, one main code per band 8-20, then the remaining codes
  (4 bits relative to the band's main coefficient in modes -5..0, 8 bits above)
512 spectral coefficients -> 256-point complex FFT between two twiddle passes -> 1024 samples, windowed and
overlapped with the previous frame. The first frame of a stream only primes the overlap.
All frames of a stream are decoded at once (numpy over frames).

KA1A file (as stored for external KTSR sounds; vgmstream plays it as .ka1a), little endian:
  "KA1A", u32 data size, s32 channels, s32 tracks, s32 rate, s32 samples, s32 loop start, s32 loop length
  (0 = no loop), s32 bitrate mode, u32 0; data from 0x28: frames interleaved over channels x tracks
  (frame 0 of every channel, frame 1 of every channel, ...).
"""
from __future__ import annotations

import struct
from functools import lru_cache

import numpy as np

FRAME_SAMPLES = 512
MODES = range(-5, 6)

BAND_CODES = (
    (5, 5, 5, 5, 5, 5, 5, 5, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3),
    (5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 3, 3, 3, 3, 3, 3, 3, 3, 3),
    (5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 3, 3, 3, 3),
    (5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5),
    (5, 5, 5, 5, 5, 5, 5, 5, 7, 7, 7, 7, 7, 7, 7, 5, 5, 5, 5, 5, 5),
    (5, 5, 5, 5, 5, 5, 5, 5, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7),
    (5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6),
    (5, 5, 5, 5, 5, 5, 5, 5, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7),
    (5, 5, 5, 5, 5, 5, 5, 5, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8),
    (5, 5, 5, 5, 5, 5, 5, 5, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9),
    (5, 5, 5, 5, 5, 5, 5, 5, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10),
)
BITRATE_SUBMODE = (0, 0, 0, 2, 2, 2, 4, 3, 2, 1, 0)
BAND_STEPS = (0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 100, 120, 140, 170, 200, 240, 300, 390)
BAND_STEP_BITS = (0, 0, 0, 0, 0, 0, 0, 0, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 7, 7)
MAX_BANDS = 21

OVERLAP_WINDOW = np.array([
    0.00041374451, 0.00063187029, 0.00083242479, 0.0010303947, 0.0012312527, 0.0014377162, 0.0016513923, 0.001873354,
    0.0021043862, 0.0023451056, 0.0025960256, 0.0028575913, 0.0031302026, 0.0034142293, 0.003710018, 0.0040178993,
    0.0043381932, 0.00467121, 0.0050172545, 0.0053766258, 0.00574962, 0.0061365301, 0.0065376465, 0.0069532581,
    0.007383653, 0.0078291167, 0.008289936, 0.0087663941, 0.0092587769, 0.0097673666, 0.010292448, 0.010834301,
    0.01139321, 0.011969455, 0.012563316, 0.013175075, 0.01380501, 0.0144534, 0.015120523, 0.015806656,
    0.016512074, 0.017237054, 0.017981868, 0.018746791, 0.019532094, 0.020338045, 0.021164915, 0.022012968,
    0.022882473, 0.023773693, 0.02468689, 0.025622323, 0.026580252, 0.027560933, 0.028564619, 0.02959156,
    0.030642008, 0.031716209, 0.032814406, 0.033936843, 0.035083756, 0.036255382, 0.037451953, 0.038673703,
    0.039920855, 0.041193634, 0.042492259, 0.043816946, 0.045167912, 0.046545364, 0.047949508, 0.049380545,
    0.050838675, 0.05232409, 0.053836983, 0.055377539, 0.056945939, 0.058542356, 0.06016697, 0.061819945,
    0.063501447, 0.065211624, 0.066950649, 0.068718657, 0.070515797, 0.07234221, 0.074198022, 0.07608337,
    0.07799837, 0.07994315, 0.081917815, 0.083922468, 0.085957222, 0.088022165, 0.090117387, 0.092242986,
    0.094399013, 0.096585557, 0.098802686, 0.10105046, 0.10332893, 0.10563815, 0.10797815, 0.11034897,
    0.11275065, 0.1151832, 0.11764663, 0.12014097, 0.12266621, 0.12522236, 0.12780938, 0.13042729,
    0.13307604, 0.13575561, 0.13846597, 0.14120705, 0.14397883, 0.14678125, 0.14961423, 0.15247771,
    0.15537159, 0.15829581, 0.16125028, 0.16423489, 0.16724953, 0.17029409, 0.17336844, 0.17647249,
    0.17960605, 0.18276905, 0.18596126, 0.18918259, 0.19243285, 0.19571187, 0.19901948, 0.2023555,
    0.20571974, 0.20911199, 0.21253204, 0.21597971, 0.21945477, 0.22295699, 0.22648615, 0.23004198,
    0.23362428, 0.23723276, 0.2408672, 0.2445273, 0.24821278, 0.25192341, 0.25565886, 0.25941887,
    0.26320317, 0.26701137, 0.27084324, 0.27469841, 0.27857658, 0.28247747, 0.28640065, 0.29034585,
    0.29431269, 0.29830083, 0.30230993, 0.30633962, 0.31038952, 0.31445926, 0.31854844, 0.32265672,
    0.32678369, 0.33092892, 0.33509207, 0.33927271, 0.34347042, 0.3476848, 0.35191545, 0.35616189,
    0.36042371, 0.36470053, 0.36899185, 0.37329727, 0.37761635, 0.38194862, 0.38629359, 0.3906509,
    0.39502001, 0.3994005, 0.4037919, 0.40819371, 0.41260549, 0.41702676, 0.42145702, 0.42589581,
    0.43034267, 0.43479711, 0.43925858, 0.44372663, 0.44820082, 0.45268059, 0.45716542, 0.4616549,
    0.4661485, 0.47064567, 0.47514597, 0.47964889, 0.4841539, 0.48866051, 0.49316826, 0.49767655,
    0.50218499, 0.50669295, 0.51120001, 0.5157057, 0.52020943, 0.52471071, 0.52920908, 0.53370398,
    0.53819495, 0.54268152, 0.54716307, 0.5516392, 0.55610937, 0.56057316, 0.56502998, 0.56947935,
    0.57392085, 0.57835394, 0.5827781, 0.58719289, 0.59159786, 0.59599245, 0.60037625, 0.60474873,
    0.6091094, 0.61345792, 0.61779374, 0.62211639, 0.62642545, 0.63072038, 0.63500077, 0.63926625,
    0.6435163, 0.64775056, 0.65196848, 0.65616965, 0.66035372, 0.66452026, 0.66866881, 0.67279899,
    0.67691034, 0.6810025, 0.6850751, 0.68912768, 0.69315994, 0.69717139, 0.7011618, 0.70513064,
    0.70907766, 0.71300244, 0.7169047, 0.72078407, 0.72464013, 0.72847265, 0.73228133, 0.73606575,
    0.73982555, 0.74356061, 0.74727046, 0.75095487, 0.75461364, 0.7582463, 0.76185274, 0.76543266,
    0.76898569, 0.77251172, 0.77601039, 0.77948159, 0.78292501, 0.78634042, 0.78972763, 0.79308641,
    0.79641658, 0.79971796, 0.80299026, 0.80623347, 0.80944729, 0.81263155, 0.81578618, 0.81891102,
    0.82200587, 0.82507062, 0.82810515, 0.83110934, 0.83408308, 0.83702624, 0.83993882, 0.84282064,
    0.84567159, 0.84849167, 0.85128081, 0.85403895, 0.85676599, 0.85946196, 0.86212677, 0.86476046,
    0.86736292, 0.86993414, 0.87247425, 0.87498307, 0.87746072, 0.87990719, 0.88232255, 0.88470674,
    0.88705987, 0.88938189, 0.89167297, 0.89393318, 0.89616245, 0.89836091, 0.90052873, 0.90266585,
    0.90477246, 0.90684867, 0.90889448, 0.91091013, 0.91289562, 0.91485113, 0.91677684, 0.91867274,
    0.92053914, 0.9223761, 0.92418379, 0.92596233, 0.92771196, 0.92943287, 0.93112504, 0.93278885,
    0.9344244, 0.936032, 0.93761164, 0.93916368, 0.94068825, 0.94218558, 0.94365591, 0.94509935,
    0.94651628, 0.94790679, 0.9492712, 0.95060962, 0.95192248, 0.95320988, 0.95447206, 0.95570934,
    0.95692199, 0.95811015, 0.95927411, 0.96041423, 0.96153063, 0.96262366, 0.96369362, 0.96474063,
    0.96576512, 0.96676731, 0.96774739, 0.96870577, 0.96964264, 0.97055829, 0.97145301, 0.97232717,
    0.97318095, 0.97401464, 0.97482848, 0.97562289, 0.97639805, 0.97715431, 0.97789192, 0.97861117,
    0.97931236, 0.97999579, 0.98066169, 0.98131043, 0.98194218, 0.98255736, 0.98315614, 0.98373884,
    0.9843058, 0.98485726, 0.98539352, 0.98591483, 0.98642153, 0.9869138, 0.98739201, 0.98785633,
    0.98830712, 0.98874468, 0.98916918, 0.98958093, 0.98998028, 0.99036741, 0.99074256, 0.99110597,
    0.991458, 0.99179888, 0.99212885, 0.99244815, 0.99275702, 0.9930557, 0.99334443, 0.9936235,
    0.99389309, 0.99415344, 0.99440479, 0.99464744, 0.99488151, 0.99510723, 0.99532485, 0.9955346,
    0.99573666, 0.99593133, 0.99611866, 0.99629897, 0.99647242, 0.99663919, 0.99679953, 0.99695361,
    0.9971016, 0.99724364, 0.99737996, 0.99751073, 0.99763614, 0.9977563, 0.99787146, 0.99798179,
    0.99808735, 0.99818838, 0.99828494, 0.99837732, 0.99846554, 0.99854976, 0.99863017, 0.99870688,
    0.99878007, 0.99884975, 0.99891615, 0.99897939, 0.99903959, 0.99909681, 0.99915117, 0.99920285,
    0.9992519, 0.99929845, 0.99934256, 0.9993844, 0.99942398, 0.99946147, 0.99949694, 0.99953043,
    0.99956208, 0.99959201, 0.99962014, 0.99964666, 0.9996717, 0.99969524, 0.99971735, 0.99973816,
    0.99975771, 0.99977601, 0.99979317, 0.99980927, 0.99982429, 0.99983829, 0.99985141, 0.99986362,
    0.99987501, 0.99988562, 0.99989551, 0.99990469, 0.99991322, 0.99992108, 0.99992836, 0.99993503,
    0.99994129, 0.99994701, 0.99995226, 0.99995708, 0.99996156, 0.99996561, 0.99996936, 0.9999727,
    0.9999758, 0.9999786, 0.99998116, 0.99998349, 0.99998552, 0.99998742, 0.99998909, 0.99999058,
    0.99999189, 0.99999309, 0.99999416, 0.99999511, 0.99999589, 0.99999666, 0.99999726, 0.99999779,
    0.99999827, 0.99999863, 0.99999899, 0.99999923, 0.99999946, 0.99999964, 0.99999976, 0.99999988,
], np.float32)

# twiddle factors cos/-sin(2pi (i + 1/8) / 1024) of the passes before and after the FFT
TWIDDLES = np.exp(-2j * np.pi * (np.arange(256) + 0.125) / 1024).astype(np.complex64)
BLOCK_FRAMES = 2048  # frames decoded at once (bounds the memory of long streams)


def frame_info(mode: int) -> tuple[int, int]:
    """(bytes of coefficient positions, bytes of codes) of a frame in a bitrate mode (-5..5)."""
    index = mode + 5
    coefs_bits = sum(8 * c for c in BAND_CODES[index][:8])
    steps_bits = 0
    for band in range(8, MAX_BANDS):
        codes = BAND_CODES[index][band]
        steps_bits += BAND_STEP_BITS[band] * codes
        coefs_bits += 8 + 4 * (codes - 1) if index <= 5 else 8 * codes
    return (steps_bits + 7) >> 3, (coefs_bits + 7) >> 3


def frame_size(mode: int) -> int:
    steps, coefs = frame_info(mode)
    return 4 + steps + coefs


def mode_for_frame_size(size: int) -> int | None:
    return next((m for m in MODES if frame_size(m) == size), None)


@lru_cache(maxsize=None)
def _layout(index: int, negative: bool):
    """Bit fields of the coefficient positions and the operations that place the codes (in decode order)."""
    codes = list(BAND_CODES[index])
    if negative:
        mod = BITRATE_SUBMODE[index]
        for b in range(8, 12):
            codes[b] += mod
        for b in range(17, 21):
            codes[b] -= mod
    fields = []  # (bit offset, bits)
    ops = []     # ('direct', dst, -, code) ('main', base, field, code) ('sub4', base, field, code, high) ('code8', ...)
    code_pos = 0
    for band in range(8):
        for _ in range(codes[band]):
            ops.append(('direct', code_pos, None, code_pos))
            code_pos += 1
    bit = 0

    def field(bits):
        nonlocal bit
        fields.append((bit, bits))
        bit += bits
        return len(fields) - 1

    sub_pos = code_pos + (MAX_BANDS - 8)
    high = False
    for band in range(8, MAX_BANDS):
        base, sbits = BAND_STEPS[band], BAND_STEP_BITS[band]
        ops.append(('main', base, field(sbits), code_pos))
        code_pos += 1
        for _ in range(1, codes[band]):
            f = field(sbits)
            if index <= 5:
                ops.append(('sub4', base, f, sub_pos, high))
                if high:
                    sub_pos += 1
                high = not high
            else:
                ops.append(('code8', base, f, sub_pos))
                sub_pos += 1
    return fields, ops


def _convert(code: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """8-bit code -> coefficient: exp((|code| - 127) * 0.0866) * scale, bit 7 = sign, 0 = 0."""
    signed = code.astype(np.int8).astype(np.float32)
    neg = (code & 0x80) != 0
    mag = np.where(neg, -signed, signed)
    coef = np.exp((mag - np.float32(127.0)) * np.float32(0.086643398)) * np.where(neg, -scale, scale)
    return np.where(code == 0, np.float32(0.0), coef).astype(np.float32)


def _unpack(frames: np.ndarray, mode: int) -> np.ndarray:
    """(n, frame size) bytes -> (n, 1024) spectral coefficients (only the first 512 are used)."""
    index = mode + 5
    steps_size, _coefs_size = frame_info(mode)
    n = len(frames)
    out = np.zeros((n, 1024), np.float32)
    scale = np.ascontiguousarray(frames[:, :4]).view('<f4')[:, 0].astype(np.float32)
    negative = scale < 0
    for neg in (False, True):
        rows = np.nonzero(negative == neg)[0]
        if not len(rows):
            continue
        fields, ops = _layout(index, neg)
        f = frames[rows]
        sf = np.abs(scale[rows])
        codes = f[:, 4 + steps_size:]
        bits = np.unpackbits(f[:, 4:4 + steps_size], axis=1, bitorder='little').astype(np.int64)
        values = [bits[:, o:o + w] @ (1 << np.arange(w)) for o, w in fields]
        dst = out[rows]
        r = np.arange(len(rows))
        main = None
        for op in ops:
            kind = op[0]
            if kind == 'direct':
                dst[:, op[1]] = _convert(codes[:, op[3]], sf)
            elif kind == 'main':
                main = _convert(codes[:, op[3]], sf)
                dst[r, op[1] + values[op[2]]] = main
            elif kind == 'sub4':
                c = codes[:, op[3]]
                sub = ((c >> 4) if op[4] else c) & 0x0F
                dst[r, op[1] + values[op[2]]] = (sub.astype(np.float32) * np.float32(0.14285715) - np.float32(1.0)) * main
            else:  # code8
                dst[r, op[1] + values[op[2]]] = _convert(codes[:, op[3]], sf)
        out[rows] = dst
    return out


def _transform(coefs: np.ndarray) -> np.ndarray:
    """(n, >=512) spectra -> (n, 1024) samples (before windowing).

    The original runs its own 256-point FFT routine; it computes the standard forward DFT (checked against
    numpy to 1e-7), so numpy's FFT is used here."""
    z = coefs[:, 0:512:2] + 1j * coefs[:, 1:512:2][:, ::-1]
    z = np.fft.fft(z * TWIDDLES, axis=1) * TWIDDLES * (1 / 512)
    re, im = z.real.astype(np.float32), z.imag.astype(np.float32)
    out = np.empty((len(coefs), 1024), np.float32)
    out[:, 0:256:2], out[:, 1:256:2] = re[:, 128:256], -im[:, 127::-1]
    out[:, 256:768:2], out[:, 257:768:2] = im, -re[:, ::-1]
    out[:, 768::2], out[:, 769::2] = -re[:, :128], im[:, 255:127:-1]
    return out


def decode(data: bytes, mode: int, samples: int | None = None) -> np.ndarray:
    """Mono KA1A stream (frames back to back) -> float32 samples (about -1..1)."""
    size = frame_size(mode)
    n = len(data) // size
    if n < 2:
        return np.zeros(0, np.float32)
    frames = np.frombuffer(data, np.uint8, n * size).reshape(n, size)
    out = np.empty((n - 1) * FRAME_SAMPLES, np.float32)  # the first frame only primes the overlap
    pos, tail = 0, None
    for start in range(0, n, BLOCK_FRAMES):
        pcm = _transform(_unpack(frames[start:start + BLOCK_FRAMES], mode))
        a = pcm[:, :512] * OVERLAP_WINDOW
        b = pcm[:, 512:] * OVERLAP_WINDOW[::-1]
        block = (a[1:] + b[:-1]) if tail is None else (a + np.concatenate([tail[None], b[:-1]]))
        out[pos:pos + block.size] = block.reshape(-1)
        pos += block.size
        tail = b[-1]
    return out[:samples] if samples is not None else out


# ------------------------------------------------------------------ KA1A file
MAGIC = b'KA1A'
HEADER_SIZE = 0x28
_HEADER = struct.Struct('<4sI7iI')


class Info:
    """Header of a KA1A file (to_dict() like audio.BwavInfo)."""

    def __init__(self, data: bytes):
        if data[:4] != MAGIC:
            raise ValueError('Not a KA1A file')
        (_m, self.data_size, ch, tracks, self.rate, self.samples, loop_start, loop_length, self.mode,
         _r) = _HEADER.unpack_from(data)
        if self.mode not in MODES:
            raise ValueError(f'Unknown KA1A bitrate mode {self.mode}')
        self.channels = max(1, ch) * max(1, tracks)
        self.loop = loop_length > 0
        self.loop_start = loop_start if self.loop else None
        self.loop_end = min(loop_start + loop_length, self.samples) if self.loop else None

    def to_dict(self) -> dict:
        return {'codec': f'KA1A (mode {self.mode})', 'rate': self.rate, 'channels': self.channels,
                'samples': self.samples, 'duration': self.samples / self.rate if self.rate else 0,
                'loop': self.loop, 'loop_start': self.loop_start, 'loop_end': self.loop_end, 'prefetch': False}


def build_file(channels: list[bytes], rate: int, samples: int, mode: int, loop_start: int | None = None) -> bytes:
    """KA1A file from per-channel frame data (sounds stored inside a KTSR bank have no header)."""
    size = frame_size(mode)
    n = min(len(c) for c in channels) // size
    body = np.stack([np.frombuffer(c, np.uint8, n * size).reshape(n, size) for c in channels], 1).tobytes()
    looping = loop_start is not None and 0 <= loop_start < samples
    head = _HEADER.pack(MAGIC, len(body), len(channels), 1, rate, samples, loop_start if looping else 0,
                        samples - loop_start if looping else 0, mode, 0)
    return head + body


def decode_file(data: bytes) -> tuple[np.ndarray, int, Info]:
    """KA1A file -> (float32 samples [n, channels], rate, header)."""
    info = Info(data)
    size = frame_size(info.mode)
    nch = info.channels
    n = (len(data) - HEADER_SIZE) // (size * nch)
    frames = np.frombuffer(data, np.uint8, n * size * nch, HEADER_SIZE).reshape(n, nch, size)
    chans = [decode(frames[:, c].tobytes(), info.mode, info.samples) for c in range(nch)]
    m = min((len(c) for c in chans), default=0)
    pcm = np.stack([c[:m] for c in chans], 1) if chans else np.zeros((0, 1), np.float32)
    return pcm, info.rate, info
