"""Batch export into a folder: converted (PNG/WAV/GLB/JSON/YAML) or raw (decompressed).

Large jobs run in a process pool (spawn); every worker opens its own VFS (index from cache).
Archives are exported as folders named like the archive (without .zs).
"""
from __future__ import annotations

import multiprocessing
import os
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

from .convert import convert_file, raw_name
from .vfs import VFS, classify

_W: dict = {}


def _init_worker(nup_path: str, cache_dir: str | None):
    _W['vfs'] = VFS(nup_path, cache_dir=cache_dir, log=lambda *a: None)


def _long(p: str) -> str:
    if os.name == 'nt':
        p = os.path.abspath(p)
        if len(p) > 240 and not p.startswith('\\\\?\\'):
            p = '\\\\?\\' + p
    return p


def out_dir_for(target: str, vpath: str) -> str:
    """Folder that receives the outputs of vpath (archives become folders)."""
    parts = vpath.split('::')
    segs = [raw_name(p) for p in parts[:-1]]
    segs.append(parts[-1].rpartition('/')[0])
    rel = [s for seg in segs for s in seg.split('/') if s]
    return os.path.join(target, *rel)


def _write_items(vfs: VFS, vpath: str, mode: str, target: str, skip: bool) -> int:
    name = vpath.rpartition('::')[2].rpartition('/')[2]
    if mode == 'raw':
        items = [(raw_name(name), vfs.read(vpath))]
    else:
        items = convert_file(vfs, vpath)
    d = out_dir_for(target, vpath)
    n = 0
    for fname, data in items:
        out = _long(os.path.join(d, fname))
        if skip and os.path.exists(out):
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(data)
        n += 1
    return n


def run_task(vfs: VFS, task: tuple) -> tuple:
    kind, vpath, mode, target, skip = task
    written = 0
    errors = []
    try:
        if kind == 'archive':  # convert all members of an archive (recursively for nested archives)
            arc = vfs.open_archive(vpath)
            for member in arc.sarc.names():
                mp = f'{vpath}::{member}'
                try:
                    if classify(member) == 'archive':
                        w, e = run_task(vfs, ('archive', mp, mode, target, skip))[1:3]
                        written += w
                        errors += e
                    else:
                        written += _write_items(vfs, mp, mode, target, skip)
                except Exception as e:  # noqa: BLE001
                    errors.append((mp, f'{type(e).__name__}: {e}'))
        else:
            written += _write_items(vfs, vpath, mode, target, skip)
    except Exception as e:  # noqa: BLE001
        errors.append((vpath, f'{type(e).__name__}: {e}'))
    return vpath, written, errors


def _worker_task(task: tuple) -> tuple:
    return run_task(_W['vfs'], task)


def expand(vfs: VFS, paths: list[str], mode: str, target: str, skip: bool) -> list[tuple]:
    tasks = []
    seen = set()

    def add(kind, p):
        if (kind, p) not in seen:
            seen.add((kind, p))
            tasks.append((kind, p, mode, target, skip))

    def add_file(p):
        if mode == 'convert' and classify(p) == 'archive':
            add('archive', p)
        else:
            add('file', p)

    for p in paths:
        if '::' not in p and (p == '' or p in vfs.c.dirs):
            prefix = p + '/' if p else ''
            for f in sorted(vfs.c.files):
                if f.startswith(prefix):
                    add_file(f)
        elif p.endswith('/') and '::' in p:
            arc_path, _, inner = p.rpartition('::')
            arc = vfs.open_archive(arc_path)
            for m in arc.sarc.names():
                if m.startswith(inner):
                    add_file(f'{arc_path}::{m}')
        else:
            add_file(p)
    return tasks


class ExportManager:
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.Lock()
        self.state: dict = {'running': False}
        self._cancel = threading.Event()
        self._executor = None

    @property
    def running(self) -> bool:
        return bool(self.state.get('running'))

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def cancel(self):
        self._cancel.set()
        ex = self._executor
        if ex is not None:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass

    def start(self, vfs: VFS, cache_dir: str | None, paths: list[str], mode: str, target: str, skip: bool) -> dict:
        with self.lock:
            if self.state.get('running'):
                raise RuntimeError('An export is already running')
            self._cancel.clear()
            self.state = {'running': True, 'mode': mode, 'target': target, 'done': 0, 'total': 0, 'written': 0,
                          'errors': 0, 'current': 'Collecting files…', 'started': time.time(),
                          'finished': False, 'cancelled': False}
        threading.Thread(target=self._run, args=(vfs, cache_dir, paths, mode, target, skip),
                         name='export', daemon=True).start()
        return self.status()

    def _publish(self, force=False):
        now = time.time()
        if force or now - getattr(self, '_last_pub', 0) > 0.25:
            self._last_pub = now
            self.emit('export', self.status())

    def _run(self, vfs, cache_dir, paths, mode, target, skip):
        errors_all = []
        try:
            tasks = expand(vfs, paths, mode, target, skip)
            with self.lock:
                self.state['total'] = len(tasks)
            self._publish(True)
            if len(tasks) <= 12:
                for t in tasks:
                    if self._cancel.is_set():
                        break
                    with self.lock:
                        self.state['current'] = t[1]
                    self._publish()
                    vp, w, errs = run_task(vfs, t)
                    self._account(w, errs, errors_all)
            else:
                ctx = multiprocessing.get_context('spawn')
                workers = max(1, min(6, (os.cpu_count() or 2) - 1))
                with ProcessPoolExecutor(workers, mp_context=ctx, initializer=_init_worker,
                                         initargs=(vfs.c.path, cache_dir), max_tasks_per_child=400) as ex:
                    self._executor = ex
                    futs = [ex.submit(_worker_task, t) for t in tasks]
                    for fu in as_completed(futs):
                        if self._cancel.is_set():
                            break
                        try:
                            vp, w, errs = fu.result()
                        except Exception as e:  # noqa: BLE001
                            vp, w, errs = '?', 0, [('?', repr(e))]
                        with self.lock:
                            self.state['current'] = vp
                        self._account(w, errs, errors_all)
                    if self._cancel.is_set():
                        ex.shutdown(wait=False, cancel_futures=True)
                self._executor = None
        except Exception as e:  # noqa: BLE001
            errors_all.append(('Export', f'{type(e).__name__}: {e}\n{traceback.format_exc()}'))
        finally:
            if errors_all:
                try:
                    os.makedirs(target, exist_ok=True)
                    with open(os.path.join(target, '_export_errors.txt'), 'w', encoding='utf-8') as f:
                        for p, msg in errors_all:
                            f.write(f'{p}\n    {msg}\n')
                except OSError:
                    pass
            with self.lock:
                self.state.update(running=False, finished=True, cancelled=self._cancel.is_set(),
                                  errors=len(errors_all), current='',
                                  error_samples=[f'{p}: {m}' for p, m in errors_all[:5]],
                                  elapsed=time.time() - self.state.get('started', time.time()))
            self._publish(True)

    def _account(self, written, errs, errors_all):
        with self.lock:
            self.state['done'] += 1
            self.state['written'] += written
            self.state['errors'] += len(errs)
        errors_all.extend(errs)
        self._publish()
