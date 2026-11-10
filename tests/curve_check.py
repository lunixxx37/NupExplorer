"""Acceptance test for the BFRES v10.4 animation curve decoder.

Checks on a random sample of *.anim.bfres files:
  - frames monotone, last key == EndFrame
  - value at the first key == BoneAnim base value (where present)
  - curves continuous between keys (no wild jumps), little overshoot
  - looping clips end where they start
Usage: python tests/curve_check.py [path/to/OT.nup] [n_files]
"""
import collections
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from nupx import anim as A  # noqa: E402
from nupx.vfs import VFS  # noqa: E402


def pct(a, qs=(50, 90, 99, 100)):
    if not len(a):
        return '-'
    a = np.asarray(a)
    return ' '.join(f'p{q}={np.percentile(a, q):.4g}' for q in qs)


def main():
    nup = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('NUP_FILE')
    if not nup:
        sys.exit(f'usage: python {sys.argv[0]} path/to/file.nup  (or set NUP_FILE)')
    n_files = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    cache = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'NupExplorer', 'cache')
    v = VFS(nup, cache_dir=cache, log=lambda *a: None)
    files = v.all_anims()
    random.seed(1)
    sample = random.sample(files, min(n_files, len(files)))
    stats = collections.Counter()
    combos = collections.Counter()
    rotmodes = collections.Counter()
    base_err, jumps, loop_err, overshoot = [], [], [], []
    quat_base_err, quat_steps = [], []
    fails = []
    t0 = time.time()
    ncurves = 0
    for p in sample:
        try:
            anims = A.parse_anims(v.read(p))
        except Exception as e:  # noqa: BLE001
            fails.append((p, repr(e)))
            continue
        for a in anims:
            rotmodes[hex(a.rot_mode)] += 1
            for ba in a.bones:
                for c in ba.curves:
                    ncurves += 1
                    combos[(c.flags & 3, (c.flags >> 2) & 3, (c.flags >> 4) & 7, hex(c.flags & 0xFF00))] += 1
                    tg = A._TARGETS.get(c.target)
                    if tg is None:
                        stats[f'unknown_target_{c.target:#x}'] += 1
                        continue
                    ch, comp = tg
                    if np.any(np.diff(c.frames) < 0):
                        stats['frames_not_monotone'] += 1
                    if abs(c.frames[-1] - c.end) > 1e-3:
                        stats['last_key!=end'] += 1
                    if c.quat:
                        stats['quat_curves'] += 1
                        q0 = c.evaluate(np.array([c.frames[0]]))[0]
                        if 'r' in ba.base:
                            quat_base_err.append(1.0 - abs(float(np.dot(q0, ba.base['r']))))
                        t = np.arange(c.frames[0], c.frames[-1] + 1e-9, 0.25)
                        if len(t) > 2:
                            qq = c.evaluate(t)
                            d = np.clip(np.abs(np.sum(qq[1:] * qq[:-1], axis=1)), 0, 1)
                            quat_steps.append(float(np.degrees(2 * np.arccos(d)).max()))
                        continue
                    base = ba.base.get(ch)
                    if base is not None and a.rot_mode == A.ROT_EULER:
                        v0 = float(c.evaluate(np.array([c.frames[0]]))[0])
                        d = abs(v0 - base[comp])
                        if ch == 'r':
                            d = d % (2 * math.pi)
                            d = min(d, 2 * math.pi - d)
                        base_err.append(d)
                    t = np.arange(c.frames[0], c.frames[-1] + 1e-9, 0.25)
                    if len(t) < 3:
                        continue
                    val = c.evaluate(t)
                    if not np.all(np.isfinite(val)):
                        stats['nan_or_inf'] += 1
                        continue
                    # Hermite curves may legitimately bulge between two keys (explicit slopes),
                    # so normalise by key range plus slope magnitude over one frame.
                    rng = float(np.ptp(c.values)) + float(np.abs(c.slopes).max()) + 1e-6
                    dv = np.abs(np.diff(val))
                    mask = np.ones(len(dv), bool)
                    dup = c.frames[1:][np.diff(c.frames) == 0]
                    for fd in dup:
                        mask &= ~((t[:-1] < fd) & (t[1:] >= fd))
                    if mask.any() and rng > 1e-4:
                        jumps.append(float(dv[mask].max()) / rng)
                        overshoot.append(float(max(val.max() - c.values.max(), c.values.min() - val.min(), 0)) / rng)
                    if a.loop and ch != 'r' and rng > 1e-4:
                        loop_err.append(abs(float(val[-1] - val[0])) / rng)
    print(f'{len(sample)} files, {ncurves} curves in {time.time() - t0:.1f}s; parse errors: {len(fails)}')
    for f in fails[:10]:
        print('  FAIL', f)
    print('Rotation modes (clips):', dict(rotmodes))
    print('Flag combinations (frame,key,curve,hi):', combos.most_common(12))
    print('Anomalies:', dict(stats))
    print('Start value vs. base value deviation:', pct(base_err))
    print('Max. jump per 1/4 frame (rel. to value range):', pct(jumps))
    print('Overshoot (rel.):', pct(overshoot))
    print('Loop clips |end-start| (rel.):', pct(loop_err))
    print('Quaternion curves: 1-|dot(start, base)|:', pct(quat_base_err), '| max. rotation step per 1/4 frame (deg):', pct(quat_steps))
    ok = (not fails and np.percentile(base_err, 99) < 1e-2 and np.percentile(jumps, 99) < 0.35
          and np.max(jumps) < 5 and not stats.get('frames_not_monotone') and not stats.get('nan_or_inf')
          and (not quat_base_err or np.percentile(quat_base_err, 99) < 1e-3))
    print('RESULT:', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
