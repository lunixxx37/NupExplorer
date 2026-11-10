"""Skeletal animations (FSKA) in BFRES v10.4 (*.anim.bfres).

Curve format of this BFRES revision (reverse engineered, differs from older BFRES):
  AnimCurve (0x30): u64 frames, u64 keys, u16 flags, u16 key_count, u32 target,
                    f32 start, f32 end, f32 frame_scale, f32 value_scale, f32 value_offset, pad
  flags & 3        frame type: 0 f32, 1 u16 * frame_scale (1/32), 2 u8          (absolute frames)
  (flags>>2) & 3   key type:   0 f32, 1 u16, 2 u8                                (unsigned)
  (flags>>4) & 7   curve type: 0 cubic (Hermite)
  flags & 0x0800   quantized keys: f32 slope_scale, f32 slope_offset, then key_count x (value_q, slope_q)
                   value = value_offset + value_q * value_scale
                   slope = slope_offset + slope_q * slope_scale     (per frame)
  otherwise        key_count x (f32 value, f32 slope), no header
  Interpolation: cubic Hermite between keys; two keys on the same frame = jump.
  flags & 0x80     quaternion curve (rotation mode 0x2000/0x0000): target 0x20, keys are
                   key_count x (x, y, z, w) f32 without slope header; interpolated with slerp.
Targets: scale 0x04/0x08/0x0C, translate 0x10/0x14/0x18, rotate 0x20/0x24/0x28/0x2C.
Rotation is decided PER BONE: BoneAnim flag 0x00100000 = quaternion (base value is a unit
quaternion, rotation curve is a 0x80 quaternion curve); otherwise Euler XYZ (base value
x, y, z + 1.0 filler, scalar curves on 0x20/0x24/0x28). The clip flag 0x2000 only says that
quaternion bones may occur (mixed clip); 0x1000 = all Euler.
Euler rotation composes as R = Rz * Ry * Rx (verified against the inverse bind matrices).
Segment scale compensate (Maya style): a child does not inherit its parent's scale for its
own rotation/scale, but its translation is still scaled by the parent.
"""
from __future__ import annotations

import json
import math
import struct

import numpy as np

FPS = 30.0
_TARGETS = {0x04: ('s', 0), 0x08: ('s', 1), 0x0C: ('s', 2),
            0x10: ('t', 0), 0x14: ('t', 1), 0x18: ('t', 2),
            0x20: ('r', 0), 0x24: ('r', 1), 0x28: ('r', 2), 0x2C: ('r', 3)}
ROT_EULER = 0x1000
ROT_QUAT_MODES = (0x0000, 0x2000)
BONE_QUAT_FLAG = 0x00100000


def _str(raw: bytes, off: int) -> str:
    if not off:
        return ''
    n = struct.unpack_from('<H', raw, off)[0]
    return raw[off + 2:off + 2 + n].decode('utf-8', 'replace')


def _dict_count(raw: bytes, off: int) -> int:
    return struct.unpack_from('<I', raw, off + 4)[0] if off else 0


def _slerp(q0: np.ndarray, q1: np.ndarray, u: np.ndarray) -> np.ndarray:
    d = np.sum(q0 * q1, axis=1)
    q1 = np.where((d < 0)[:, None], -q1, q1)
    d = np.abs(d)
    out = np.empty_like(q0)
    lin = d > 0.9995
    if lin.any():
        r = q0[lin] + (q1[lin] - q0[lin]) * u[lin, None]
        out[lin] = r
    if (~lin).any():
        th = np.arccos(np.clip(d[~lin], -1.0, 1.0))
        st = np.sin(th)
        a = np.sin((1 - u[~lin]) * th) / st
        b = np.sin(u[~lin] * th) / st
        out[~lin] = q0[~lin] * a[:, None] + q1[~lin] * b[:, None]
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


class Curve:
    __slots__ = ('target', 'flags', 'start', 'end', 'frames', 'values', 'slopes', 'ctype', 'quat')

    def evaluate(self, t: np.ndarray) -> np.ndarray:
        f, v, s = self.frames, self.values, self.slopes
        n = len(f)
        if self.quat:
            if n == 1:
                return np.tile(v[0], (len(t), 1))
            idx = np.clip(np.searchsorted(f, t, side='right') - 1, 0, n - 2)
            dt = f[idx + 1] - f[idx]
            u = np.clip((t - f[idx]) / np.where(dt == 0, 1.0, dt), 0.0, 1.0)
            u = np.where(dt == 0, 1.0, u)
            u = np.where(t <= f[0], 0.0, u)
            return _slerp(v[idx], v[idx + 1], u)
        if n == 1:
            return np.full(t.shape, v[0])
        idx = np.clip(np.searchsorted(f, t, side='right') - 1, 0, n - 2)
        f0 = f[idx]
        f1 = f[idx + 1]
        dt = f1 - f0
        dts = np.where(dt == 0, 1.0, dt)
        u = np.clip((t - f0) / dts, 0.0, 1.0)
        if self.ctype == 0:
            u2 = u * u
            u3 = u2 * u
            val = ((2 * u3 - 3 * u2 + 1) * v[idx] + (u3 - 2 * u2 + u) * dt * s[idx]
                   + (-2 * u3 + 3 * u2) * v[idx + 1] + (u3 - u2) * dt * s[idx + 1])
        elif self.ctype == 1:
            val = v[idx] + (v[idx + 1] - v[idx]) * u
        else:  # step / baked: hold value
            val = v[idx].copy()
        val = np.where(dt == 0, v[idx + 1], val)
        val = np.where(t <= f[0], v[0], val)
        val = np.where(t >= f[-1], v[-1], val)
        return val


class BoneAnim:
    __slots__ = ('name', 'flags', 'base', 'curves')


class SkelAnim:
    __slots__ = ('name', 'flags', 'frame_count', 'bones', 'curve_count')

    @property
    def loop(self) -> bool:
        return bool(self.flags & 0x4)

    @property
    def rot_mode(self) -> int:
        return self.flags & 0x7000

    @property
    def rot_supported(self) -> bool:
        return self.rot_mode == ROT_EULER or self.rot_mode in ROT_QUAT_MODES

    @property
    def scale_mode(self) -> int:
        return self.flags & 0x0300


def _read_curve(raw: bytes, co: int) -> Curve:
    fo, ko = struct.unpack_from('<QQ', raw, co)
    fl, nk, tgt = struct.unpack_from('<HHI', raw, co + 0x10)
    start, end, fscale, vscale, voff = struct.unpack_from('<5f', raw, co + 0x18)
    c = Curve()
    c.target = tgt
    c.flags = fl
    c.start = start
    c.end = end
    c.ctype = (fl >> 4) & 7
    c.quat = bool(fl & 0x80)
    ft = fl & 3
    if ft == 0:
        frames = np.frombuffer(raw, '<f4', nk, fo).astype(np.float64)
    elif ft == 1:
        frames = np.frombuffer(raw, '<u2', nk, fo).astype(np.float64) * (fscale or 1.0 / 32)
    elif ft == 2:
        frames = np.frombuffer(raw, 'u1', nk, fo).astype(np.float64)
    else:
        raise ValueError(f'Unknown frame type {ft}')
    kt = (fl >> 2) & 3
    c.frames = frames
    if c.quat:
        if kt != 0:
            raise ValueError(f'Quantized quaternion curve (key type {kt}) not supported')
        q = np.frombuffer(raw, '<f4', 4 * nk, ko).astype(np.float64).reshape(nk, 4)
        c.values = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
        c.slopes = None
        return c
    if not fl & 0x0800:
        if kt != 0:
            raise ValueError(f'Curve format 0x{fl:04X} not supported')
        q = np.frombuffer(raw, '<f4', 2 * nk, ko).astype(np.float64).reshape(nk, 2)
        c.values = q[:, 0].copy()
        c.slopes = q[:, 1].copy()
        return c
    sscale, soff = struct.unpack_from('<2f', raw, ko)
    dt = {0: '<f4', 1: '<u2', 2: 'u1'}.get(kt)
    if dt is None:
        raise ValueError(f'Unknown key type {kt}')
    q = np.frombuffer(raw, dt, 2 * nk, ko + 8).astype(np.float64).reshape(nk, 2)
    if kt == 0 and vscale == 0.0:
        vscale = 1.0
    if kt == 0 and sscale == 0.0:
        sscale = 1.0
    c.values = voff + q[:, 0] * vscale
    c.slopes = soff + q[:, 1] * sscale
    return c


def parse_anims(raw: bytes) -> list[SkelAnim]:
    if raw[:4] != b'FRES':
        raise ValueError('Not a BFRES file')
    arr, dic = struct.unpack_from('<QQ', raw, 0x58)
    n = _dict_count(raw, dic)
    out = []
    for i in range(n):
        o = arr + i * 0x50
        if raw[o:o + 4] != b'FSKA':
            break
        a = SkelAnim()
        a.flags = struct.unpack_from('<I', raw, o + 4)[0]
        a.name = _str(raw, struct.unpack_from('<Q', raw, o + 8)[0])
        bone_arr = struct.unpack_from('<Q', raw, o + 0x28)[0]
        a.frame_count, a.curve_count, _baked, nbone, _nuser = struct.unpack_from('<iiIHH', raw, o + 0x40)
        a.bones = []
        for b in range(nbone):
            q = bone_arr + b * 0x38
            ba = BoneAnim()
            ba.name = _str(raw, struct.unpack_from('<Q', raw, q)[0])
            cur, base = struct.unpack_from('<QQ', raw, q + 8)
            ba.flags = struct.unpack_from('<I', raw, q + 0x28)[0]
            ncurve = raw[q + 0x2E]
            bd = {}
            p = base
            if base:
                if ba.flags & 0x08:
                    bd['s'] = struct.unpack_from('<3f', raw, p); p += 12
                if ba.flags & 0x10:
                    bd['r'] = struct.unpack_from('<4f', raw, p); p += 16
                if ba.flags & 0x20:
                    bd['t'] = struct.unpack_from('<3f', raw, p); p += 12
            ba.base = bd
            ba.curves = [_read_curve(raw, cur + k * 0x30) for k in range(ncurve)] if cur else []
            a.bones.append(ba)
        out.append(a)
    return out


# ---------------------------------------------------------------- rotations
def euler_to_quat(e: np.ndarray) -> np.ndarray:
    """Euler XYZ (radians, R = Rz*Ry*Rx) -> quaternion (x, y, z, w). e: (n, 3)."""
    hx, hy, hz = e[:, 0] * 0.5, e[:, 1] * 0.5, e[:, 2] * 0.5
    cx, sx = np.cos(hx), np.sin(hx)
    cy, sy = np.cos(hy), np.sin(hy)
    cz, sz = np.cos(hz), np.sin(hz)
    # q = qz * qy * qx
    w = cz * cy * cx + sz * sy * sx
    x = cz * cy * sx - sz * sy * cx
    y = cz * sy * cx + sz * cy * sx
    z = sz * cy * cx - cz * sy * sx
    return np.stack([x, y, z, w], axis=1)


def _fix_quat_continuity(q: np.ndarray) -> np.ndarray:
    """Flip signs so consecutive quaternions stay in the same hemisphere (clean slerp)."""
    if len(q) < 2:
        return q
    d = np.sum(q[1:] * q[:-1], axis=1)
    flips = np.cumprod(np.where(d < 0, -1.0, 1.0))
    q = q.copy()
    q[1:] *= flips[:, None]
    return q


# ---------------------------------------------------------------- baking
def bake(anim: SkelAnim, skeleton=None) -> tuple[dict, np.ndarray]:
    """Evaluate a clip at every frame.

    skeleton: optional bfres.Skeleton for bind-pose fallbacks and segment scale compensation.
    Returns (header, data) where data is float32 (bones, frames, 10) = t3, q4, s3.
    """
    nf = max(1, int(math.ceil(anim.frame_count)) + 1)
    t = np.arange(nf, dtype=np.float64)
    warnings = []
    rot_mode = anim.rot_mode
    if not anim.rot_supported:
        warnings.append(f'Rotation mode 0x{rot_mode:04X} not supported – rotations stay in the rest pose')
    bind = {}
    if skeleton is not None:
        for b in skeleton.bones:
            bind[b.name] = b
    bones_out = []
    data = np.zeros((len(anim.bones), nf, 10), np.float32)
    scales = {}
    for bi, ba in enumerate(anim.bones):
        sk = bind.get(ba.name)
        bs = ba.base.get('s') or (sk.scale if sk is not None else (1.0, 1.0, 1.0))
        bt = ba.base.get('t') or (sk.translate if sk is not None else (0.0, 0.0, 0.0))
        if 'r' in ba.base:
            br = ba.base['r']
        elif sk is not None:
            br = sk.rotate
        else:
            br = (0.0, 0.0, 0.0, 1.0)
        S = np.tile(np.asarray(bs, np.float64), (nf, 1))
        T = np.tile(np.asarray(bt, np.float64), (nf, 1))
        R = np.tile(np.asarray(br, np.float64), (nf, 1))
        animated = set()
        for c in ba.curves:
            tg = _TARGETS.get(c.target)
            if tg is None:
                continue
            ch, comp = tg
            vals = c.evaluate(t)
            if c.quat:
                R[:, :] = vals
            elif ch == 's':
                S[:, comp] = vals
            elif ch == 't':
                T[:, comp] = vals
            else:
                R[:, comp] = vals
            animated.add(ch)
        # rotation encoding is per bone: quaternion only for bones flagged as such
        quat_bone = bool(ba.flags & BONE_QUAT_FLAG) or any(c.quat for c in ba.curves)
        if 'r' in ba.base or 'r' in animated:
            rot_is_quat = quat_bone
        elif sk is not None:
            rot_is_quat = sk.rot_is_quat  # rotation taken from the bind pose
        else:
            rot_is_quat = True  # (0, 0, 0, 1): identity either way
        if not anim.rot_supported:
            # unknown mode: keep the bind rotation
            if sk is not None:
                R = np.tile(np.asarray(sk.rotate, np.float64), (nf, 1))
                rot_is_quat = sk.rot_is_quat
            else:
                R = np.tile(np.asarray((0.0, 0.0, 0.0, 1.0)), (nf, 1))
                rot_is_quat = True
        if rot_is_quat:
            Q = R / np.maximum(np.linalg.norm(R, axis=1, keepdims=True), 1e-12)
        else:
            Q = euler_to_quat(R[:, :3])
        Q = _fix_quat_continuity(Q)
        scales[ba.name] = S
        data[bi, :, 0:3] = T
        data[bi, :, 3:7] = Q
        data[bi, :, 7:10] = S
        bones_out.append({'name': ba.name, 'ssc': bool(ba.flags & 0x00800000),
                          'quat': rot_is_quat, 'animated': sorted(animated)})
    # Segment scale compensate (Maya style): the child's rotation/scale ignore the parent's scale,
    # its translation does not. three.js always inherits the parent scale, so divide the child's
    # own scale by the parent's (exact for uniform parent scale, approximated otherwise).
    if skeleton is not None:
        nonuniform = False
        for bi, ba in enumerate(anim.bones):
            sk = bind.get(ba.name)
            if sk is None or not (ba.flags & 0x00800000 or sk.ssc) or sk.parent < 0:
                continue
            pname = skeleton.bones[sk.parent].name
            ps = scales.get(pname)
            if ps is None:
                ps = np.tile(np.asarray(skeleton.bones[sk.parent].scale, np.float64), (nf, 1))
            if np.allclose(ps, 1.0, atol=1e-5):
                continue
            spread = (ps.max(axis=1) - ps.min(axis=1)) / np.maximum(np.abs(ps).mean(axis=1), 1e-6)
            if float(spread.max()) > 0.1:  # visible shear only beyond ~10 % non-uniformity
                nonuniform = True
            u = ps.mean(axis=1, keepdims=True)
            u = np.where(np.abs(u) < 1e-6, 1.0, u)  # parent scaled to 0 (hidden): nothing to compensate
            data[bi, :, 7:10] /= u
        if nonuniform:
            warnings.append('Non-uniform scaling with segment scale compensation is approximated')
    header = {'name': anim.name, 'frames': nf, 'frame_count': anim.frame_count, 'fps': FPS,
              'loop': anim.loop, 'rot_mode': rot_mode, 'bones': bones_out, 'warnings': warnings}
    return header, data


# ---------------------------------------------------------------- bone visibility (FBVS)
class VisAnim:
    """Bone visibility animation. Layout (0x60): +0x08 name, +0x28 curves, +0x30 base bits,
    +0x38 bone names, +0x50 i32 frame_count, +0x58 u16 count, u16 curve_count.
    Curves are boolean step curves (type 6) whose keys are packed bits; curve.target = bone slot."""
    __slots__ = ('name', 'frame_count', 'names', 'base', 'curves')

    def track(self) -> dict[str, list[list[int]]]:
        """{bone: [[frame, visible], ...]} – first entry at frame 0, then only changes."""
        out = {}
        animated = {idx: (fr, vals) for idx, fr, vals in self.curves}
        for i, name in enumerate(self.names):
            if i in animated:
                fr, vals = animated[i]
                seq = [[0, int(vals[0]) if len(vals) and fr[0] <= 0 else int(self.base[i])]]
                for f, val in zip(fr, vals):
                    f = int(round(f))
                    if f <= 0:
                        seq[0][1] = int(val)
                    elif int(val) != seq[-1][1]:
                        seq.append([f, int(val)])
                out[name] = seq
            else:
                out[name] = [[0, int(self.base[i])]]
        return out


def parse_vis_anims(raw: bytes) -> list[VisAnim]:
    if raw[:4] != b'FRES':
        return []
    arr, dic = struct.unpack_from('<QQ', raw, 0x78)
    n = _dict_count(raw, dic)
    out = []
    for i in range(n):
        o = arr + i * 0x60
        if raw[o:o + 4] != b'FBVS':
            break
        va = VisAnim()
        va.name = _str(raw, struct.unpack_from('<Q', raw, o + 8)[0])
        curves_ptr, bits_ptr, names_ptr = struct.unpack_from('<QQQ', raw, o + 0x28)
        va.frame_count = struct.unpack_from('<i', raw, o + 0x50)[0]
        count, ncurve = struct.unpack_from('<HH', raw, o + 0x58)
        va.names = [_str(raw, struct.unpack_from('<Q', raw, names_ptr + 8 * k)[0]) for k in range(count)]
        va.base = [(raw[bits_ptr + k // 8] >> (k % 8)) & 1 if bits_ptr else 1 for k in range(count)]
        va.curves = []
        for c in range(ncurve if curves_ptr else 0):
            co = curves_ptr + c * 0x30
            fo, ko = struct.unpack_from('<QQ', raw, co)
            fl, nk, tgt = struct.unpack_from('<HHI', raw, co + 0x10)
            start, _end, fscale = struct.unpack_from('<3f', raw, co + 0x18)
            ftype = fl & 3
            ctype = (fl >> 4) & 7
            if ctype == 7 or not fo:  # baked: one key per frame
                frames = start + np.arange(nk, dtype=np.float64)
            elif ftype == 0:
                frames = np.frombuffer(raw, '<f4', nk, fo).astype(np.float64)
            elif ftype == 1:
                frames = np.frombuffer(raw, '<u2', nk, fo).astype(np.float64) * (fscale or 1.0 / 32)
            else:
                frames = np.frombuffer(raw, 'u1', nk, fo).astype(np.float64)
            vals = np.array([(raw[ko + k // 8] >> (k % 8)) & 1 for k in range(nk)], np.int8)
            va.curves.append((tgt, frames, vals))
        out.append(va)
    return out


def clip_list(anims: list[SkelAnim]) -> list[dict]:
    return [{'index': i, 'name': a.name, 'frames': a.frame_count, 'loop': a.loop,
             'bones': len(a.bones), 'rot_mode': a.rot_mode,
             'supported': a.rot_supported} for i, a in enumerate(anims)]


def pack_clip(header: dict, data: np.ndarray) -> bytes:
    """[u32 header_len][header json][pad to 4][float32 data]"""
    hj = json.dumps(header, separators=(',', ':')).encode('utf-8')
    pad = (-(4 + len(hj))) % 4
    return struct.pack('<I', len(hj) + pad) + hj + b' ' * pad + data.astype('<f4').tobytes()
