"""Flask API for the NUP Explorer UI. Bound to 127.0.0.1 and protected by a random token."""
from __future__ import annotations

import io
import json
import mimetypes
import os
import queue
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import zipfile

import numpy as np
from flask import Flask, Response, abort, jsonify, request

from . import anim as A
from . import audio, bfres, byml, gltf, msbt, textures
from .convert import (base_name, convert_file, eye_available, eye_composite, ramp_color, raw_name,
                      texture_png_for_model)
from .dialogs import Dialogs
from .export import ExportManager
from .util import ByteLRU
from .vfs import VFS, classify

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('image/svg+xml', '.svg')

VERSION = '1.0.0'


class State:
    def __init__(self, web_dir: str, cache_dir: str, settings: dict, save_settings, log=print):
        self.web_dir = web_dir
        self.cache_dir = cache_dir
        self.settings = settings
        self.save_settings = save_settings
        self.log = log
        self.vfs: VFS | None = None
        self.loading = False
        self.error = None
        self.token = secrets.token_urlsafe(24)
        self.port = None
        self.png = ByteLRU(512 << 20)
        self.wav = ByteLRU(768 << 20)
        self.glb = ByteLRU(384 << 20)
        self.models = ByteLRU(512 << 20)
        self.anims = ByteLRU(256 << 20)
        self.clips = ByteLRU(256 << 20)
        self.sub_lock = threading.Lock()
        self.subscribers: list[queue.Queue] = []
        self.ever_connected = False
        self.last_disconnect = time.time()
        self.dialogs = Dialogs()
        self.export = ExportManager(self.emit)

    # ---------------------------------------------------------------- events
    def emit(self, event: str, data):
        with self.sub_lock:
            subs = list(self.subscribers)
        for q in subs:
            q.put((event, data))

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.sub_lock:
            self.subscribers.append(q)
            self.ever_connected = True
        return q

    def unsubscribe(self, q):
        with self.sub_lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            self.last_disconnect = time.time()

    def client_count(self) -> int:
        with self.sub_lock:
            return len(self.subscribers)

    # ---------------------------------------------------------------- container
    def state_dict(self) -> dict:
        return {'loading': self.loading, 'error': self.error, 'version': VERSION,
                'nup': self.vfs.c.info() if self.vfs else None,
                'export_dir': self.settings.get('export_dir') or '',
                'export': self.export.status()}

    def open_nup(self, path: str):
        if self.loading:
            return

        def work():
            self.loading = True
            self.error = None
            self.emit('state', self.state_dict())
            try:
                v = VFS(path, cache_dir=self.cache_dir, log=self.log)
                old = self.vfs
                self.vfs = v
                for c in (self.png, self.wav, self.glb, self.models, self.anims, self.clips):
                    c.clear()
                if old is not None:
                    old.close()
                self.settings['last_nup'] = os.path.abspath(path)
                if not self.settings.get('export_dir'):
                    self.settings['export_dir'] = os.path.join(os.path.dirname(os.path.abspath(path)), 'NUP_Export')
                self.save_settings(self.settings)
            except Exception as e:  # noqa: BLE001
                self.error = f'{type(e).__name__}: {e}'
                self.log(traceback.format_exc())
            finally:
                self.loading = False
                self.emit('state', self.state_dict())

        threading.Thread(target=work, name='open-nup', daemon=True).start()

    # ---------------------------------------------------------------- cached parsing
    def get_models(self, path: str):
        m = self.models.get(path)
        if m is None:
            raw = self.vfs.read(path)
            m = bfres.parse_models(raw)
            self.models.put(path, m, len(raw))
        return m

    def get_anims(self, path: str):
        return self._get_anim_data(path)[0]

    def get_vis_anims(self, path: str):
        return self._get_anim_data(path)[1]

    def _get_anim_data(self, path: str):
        a = self.anims.get(path)
        if a is None:
            raw = self.vfs.read(path)
            a = (A.parse_anims(raw), A.parse_vis_anims(raw))
            self.anims.put(path, a, len(raw))
        return a

    def tex_uri(self, name: str, kind: str):
        if kind == 'eye':
            if not eye_available(self.vfs, name):
                return None
            q = urllib.parse.urlencode({'eye': name, 'maxdim': 1024, 't': self.token})
            return f'api/texture.png?{q}'
        if not self.vfs.find_texture(name):
            return None
        q = urllib.parse.urlencode({'name': name, 'mode': kind, 'maxdim': 1024, 't': self.token})
        return f'api/texture.png?{q}'


def _languages(vfs: VFS) -> list[str]:
    langs = set()
    for f in vfs.c.dirs.get('romfs/Mals', ([], []))[1]:
        if f.endswith('.sarc.zs'):
            langs.add(f.split('.', 1)[0])
    return sorted(langs)


def _msbt_lang_path(path: str, lang: str, vfs: VFS) -> str | None:
    arc, sep, inner = path.partition('::')
    if not sep or not arc.startswith('romfs/Mals/'):
        return None
    fname = arc.rpartition('/')[2]
    rest = fname.split('.', 1)[1]
    cand = f'romfs/Mals/{lang}.{rest}'
    if cand in vfs.c.files:
        return f'{cand}::{inner}'
    return None


def create_app(state: State) -> Flask:
    app = Flask(__name__, static_folder=state.web_dir, static_url_path='')
    app.config['JSON_SORT_KEYS'] = False
    import logging
    logging.getLogger('werkzeug').setLevel(logging.WARNING)

    @app.before_request
    def guard():
        if state.port is not None and request.host not in (f'127.0.0.1:{state.port}', f'localhost:{state.port}'):
            abort(403)
        if request.path.startswith('/api/'):
            tok = request.headers.get('X-Token') or request.args.get('t') or ''
            if not secrets.compare_digest(tok, state.token):
                abort(403)
            if request.path not in ('/api/state', '/api/events', '/api/open', '/api/settings', '/api/pick-folder') \
                    and state.vfs is None:
                return jsonify({'error': 'No NUP file loaded'}), 409

    @app.errorhandler(Exception)
    def on_error(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return jsonify({'error': e.description or e.name}), e.code
        state.log(traceback.format_exc())
        return jsonify({'error': f'{type(e).__name__}: {e}'}), 500

    def arg(name, default=None, typ=str):
        v = request.args.get(name)
        if v is None or v == '':
            return default
        return typ(v)

    def binary(data: bytes, mime: str, cache=True, filename: str | None = None):
        r = Response(data, mimetype=mime)
        if cache:
            r.headers['Cache-Control'] = 'private, max-age=86400'
        if filename:
            r.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + urllib.parse.quote(filename)
        return r

    @app.get('/')
    def index():
        r = app.send_static_file('index.html')
        r.headers['Cache-Control'] = 'no-cache'
        return r

    # ---------------------------------------------------------------- state & events
    @app.get('/api/state')
    def api_state():
        return jsonify(state.state_dict())

    @app.get('/api/events')
    def api_events():
        q = state.subscribe()

        def gen():
            try:
                yield 'retry: 2000\n\n'
                yield f'event: state\ndata: {json.dumps(state.state_dict())}\n\n'
                while True:
                    try:
                        ev, data = q.get(timeout=5)
                        yield f'event: {ev}\ndata: {json.dumps(data)}\n\n'
                    except queue.Empty:
                        yield ': ping\n\n'
            finally:
                state.unsubscribe(q)

        return Response(gen(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.post('/api/open')
    def api_open():
        body = request.get_json(silent=True) or {}
        path = body.get('path')
        if not path:
            last = state.settings.get('last_nup')
            path = state.dialogs.pick_file(initialdir=os.path.dirname(last) if last else None)
            if not path:
                return jsonify({'ok': False})
        if not os.path.isfile(path):
            return jsonify({'error': f'File not found: {path}'}), 404
        state.open_nup(path)
        return jsonify({'ok': True, 'path': path})

    @app.route('/api/settings', methods=['GET', 'POST'])
    def api_settings():
        if request.method == 'POST':
            body = request.get_json(silent=True) or {}
            if 'export_dir' in body and body['export_dir']:
                state.settings['export_dir'] = body['export_dir']
                state.save_settings(state.settings)
        return jsonify({'export_dir': state.settings.get('export_dir') or ''})

    @app.post('/api/pick-folder')
    def api_pick_folder():
        cur = state.settings.get('export_dir')
        p = state.dialogs.pick_folder(initialdir=cur if cur and os.path.isdir(cur) else None)
        if p:
            state.settings['export_dir'] = os.path.normpath(p)
            state.save_settings(state.settings)
        return jsonify({'path': state.settings.get('export_dir') or '', 'picked': bool(p)})

    @app.post('/api/open-folder')
    def api_open_folder():
        body = request.get_json(silent=True) or {}
        p = body.get('path') or state.settings.get('export_dir')
        if p and os.path.isdir(p) and hasattr(os, 'startfile'):
            os.startfile(p)  # noqa: S606 - only existing directories (opens Explorer)
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'Folder does not exist'}), 404

    # ---------------------------------------------------------------- browsing
    @app.get('/api/list')
    def api_list():
        path = arg('path', '')
        return jsonify({'path': path, 'items': state.vfs.list_dir(path)})

    @app.get('/api/search')
    def api_search():
        return jsonify(state.vfs.search(arg('q', ''), arg('type'), arg('limit', 5000, int)))

    @app.get('/api/meta')
    def api_meta():
        v = state.vfs
        path = arg('path', '')
        if v.is_dir(path):
            name = path.rstrip('/').rpartition('::')[2].rpartition('/')[2] or 'Root'
            return jsonify({'path': path, 'name': name, 'type': 'dir', 'type_label': 'Folder',
                            'count': len(v.list_dir(path))})
        m = v.meta(path)
        t = m['type']
        try:
            if t == 'texture':
                m['textures'] = [x.to_dict() for x in textures.parse_bntx(v.read(path))]
            elif t == 'audio':
                m['audio'] = audio.BwavInfo(v.read(path)).to_dict()
            elif t == 'model':
                models = state.get_models(path)
                m['model'] = bfres.model_summary(models)
                m['anim_suggestions'] = v.anim_candidates(path)
            elif t == 'anim':
                m['clips'] = A.clip_list(state.get_anims(path))
                m['model_suggestions'] = v.model_candidates(path)
            elif t == 'msbt':
                m['languages'] = _languages(v)
                arc = path.partition('::')[0]
                m['lang'] = arc.rpartition('/')[2].split('.', 1)[0] if arc.startswith('romfs/Mals/') else None
            elif t == 'byml':
                d = v.read(path)
                m['byml_version'] = int.from_bytes(d[2:4], 'little' if d[:2] == b'YB' else 'big')
            elif t == 'archive':
                m['count'] = len(v.open_archive(path).sarc.entries)
        except Exception as e:  # noqa: BLE001
            m['parse_error'] = f'{type(e).__name__}: {e}'
        return jsonify(m)

    # ---------------------------------------------------------------- textures
    @app.get('/api/texture.png')
    def api_texture():
        v = state.vfs
        path = arg('path')
        name = arg('name')
        eye = arg('eye')
        if eye:  # eye composite (iris|sclera|mask) for 3D models
            key = ('eye', eye, arg('maxdim', None, int))
            png = state.png.get(key)
            if png is None:
                img = eye_composite(v, *eye.split('|'), maxdim=arg('maxdim', None, int))
                if img is None:
                    abort(404)
                png = state.png.put(key, textures.png_bytes(img, 1))
            return binary(png, 'image/png')
        if not path and name:
            path = v.find_texture(name)
            if not path:
                abort(404)
        ti = arg('tex', 0, int)
        layer = arg('layer', 0, int)
        mip = arg('mip', 0, int)
        mode = arg('mode', 'color')
        maxdim = arg('maxdim', None, int)
        key = (path, ti, layer, mip, mode, maxdim)
        png = state.png.get(key)
        if png is None:
            raw = v.read(path)
            texs = textures.parse_bntx(raw)
            t = texs[min(ti, len(texs) - 1)]
            if maxdim:
                mip = max(mip, textures.choose_mip(t, maxdim))
            img = textures.decode(raw, t, mip, layer, swizzle=(mode != 'raw'))
            if mode == 'normal':
                img = textures.to_normal_map(img, t)
            png = state.png.put(key, textures.png_bytes(img, 1))
        return binary(png, 'image/png')

    # ---------------------------------------------------------------- audio
    @app.get('/api/audio.wav')
    def api_audio():
        path = arg('path')
        mix = arg('mix', '')
        key = (path, mix)
        wav = state.wav.get(key)
        if wav is None:
            pcm, rate, info = audio.decode(state.vfs.read(path))
            if mix == 'stereo' and pcm.shape[1] > 2:
                f = pcm.astype(np.float32)
                left = f[:, 0::2].mean(1)
                right = f[:, 1::2].mean(1)
                pcm = np.clip(np.stack([left, right], 1), -32768, 32767).astype(np.int16)
            wav = state.wav.put(key, audio.wav_bytes(pcm, rate))
        return binary(wav, 'audio/wav')

    # ---------------------------------------------------------------- models & animations
    @app.get('/api/model.glb')
    def api_model():
        path = arg('path')
        lod = arg('lod', 0, int)
        key = (path, lod)
        glb = state.glb.get(key)
        if glb is None:
            models = state.get_models(path)
            glb = gltf.build_glb(models, lod, texture_uri=state.tex_uri,
                                 extras={'source': path, 'lod': lod},
                                 fallback_color=lambda mt: ramp_color(state.vfs, mt))
            state.glb.put(key, glb)
        return binary(glb, 'model/gltf-binary')

    @app.get('/api/anim/clips')
    def api_anim_clips():
        return jsonify({'path': arg('path'), 'clips': A.clip_list(state.get_anims(arg('path')))})

    @app.get('/api/anim/clip.bin')
    def api_anim_clip():
        path = arg('path')
        ci = arg('clip', 0, int)
        model = arg('model')
        key = (path, ci, model)
        data = state.clips.get(key)
        if data is None:
            anims = state.get_anims(path)
            skel = None
            if model:
                ms = state.get_models(model)
                skel = ms[0].skeleton if ms else None
            header, arr = A.bake(anims[ci], skel)
            # bone visibility animation of the same name (switches meshes on/off, e.g. Goron ball form)
            vis = next((va for va in state.get_vis_anims(path) if va.name == anims[ci].name), None)
            if vis is not None:
                header['vis'] = vis.track()
            data = state.clips.put(key, A.pack_clip(header, arr))
        return binary(data, 'application/octet-stream')

    @app.get('/api/anim/suggest')
    def api_anim_suggest():
        v = state.vfs
        return jsonify({'suggested': v.anim_candidates(arg('model')), 'all': v.all_anims()})

    # ---------------------------------------------------------------- text & data
    @app.get('/api/msbt')
    def api_msbt():
        v = state.vfs
        path = arg('path')
        parsed = msbt.parse(v.read(path))
        rows = parsed['rows']
        cmp_lang = arg('compare')
        compare_found = None
        if cmp_lang:
            cp = _msbt_lang_path(path, cmp_lang, v)
            if cp and v.exists(cp):
                other = {r['label']: r['text'] for r in msbt.parse(v.read(cp))['rows']}
                for r in rows:
                    r['compare'] = other.get(r['label'], '')
                compare_found = True
            else:
                compare_found = False
        return jsonify({'encoding': parsed['encoding'], 'rows': rows, 'compare': cmp_lang,
                        'compare_found': compare_found})

    @app.get('/api/byml')
    def api_byml():
        obj = byml.parse(state.vfs.read(arg('path')))
        max_lines = arg('max_lines', 20000, int)
        text = byml.to_yaml(obj, max_lines=max_lines)
        return jsonify({'yaml': text})

    @app.get('/api/hex')
    def api_hex():
        v = state.vfs
        path = arg('path')
        data = v.read(path)
        off = max(0, arg('offset', 0, int))
        length = min(arg('length', 4096, int), 65536)
        chunk = data[off:off + length]
        lines = []
        for i in range(0, len(chunk), 16):
            row = chunk[i:i + 16]
            hx = ' '.join(f'{b:02x}' for b in row)
            asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
            lines.append(f'{off + i:08x}  {hx:<47}  {asc}')
        return jsonify({'size': len(data), 'offset': off, 'lines': lines})

    # ---------------------------------------------------------------- downloads & export
    @app.get('/api/raw')
    def api_raw():
        path = arg('path')
        name = path.rpartition('::')[2].rpartition('/')[2]
        return binary(state.vfs.read(path), 'application/octet-stream', cache=False, filename=raw_name(name))

    @app.get('/api/convert')
    def api_convert():
        path = arg('path')
        items = convert_file(state.vfs, path, arg('fmt'))
        if len(items) == 1:
            n, d = items[0]
            return binary(d, mimetypes.guess_type(n)[0] or 'application/octet-stream', cache=False, filename=n)
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, 'w', zipfile.ZIP_DEFLATED) as z:
            for n, d in items:
                z.writestr(n, d)
        stem = path.rpartition('/')[2].split('.')[0]
        return binary(bio.getvalue(), 'application/zip', cache=False, filename=stem + '.zip')

    @app.post('/api/export')
    def api_export():
        body = request.get_json(silent=True) or {}
        paths = body.get('paths') or []
        mode = 'raw' if body.get('mode') == 'raw' else 'convert'
        target = body.get('target') or state.settings.get('export_dir')
        if not target:
            target = state.dialogs.pick_folder()
            if not target:
                return jsonify({'ok': False})
            state.settings['export_dir'] = target
            state.save_settings(state.settings)
        os.makedirs(target, exist_ok=True)
        st = state.export.start(state.vfs, state.cache_dir, paths, mode, target, bool(body.get('skip_existing')))
        return jsonify({'ok': True, 'status': st})

    @app.post('/api/export/cancel')
    def api_export_cancel():
        state.export.cancel()
        return jsonify({'ok': True})

    @app.post('/api/export/model-glb')
    def api_export_model_glb():
        """GLB with embedded textures plus baked animations (one clip or all clips of a file)."""
        body = request.get_json(silent=True) or {}
        model = body.get('model')
        anim_path = body.get('anim')
        clip = body.get('clip')
        target = state.settings.get('export_dir')
        if not target:
            target = state.dialogs.pick_folder()
            if not target:
                return jsonify({'ok': False})
            state.settings['export_dir'] = target
            state.save_settings(state.settings)
        models = state.get_models(model)
        if not models:
            return jsonify({'error': 'This file contains no model'}), 400
        clips = []
        suffix = ''
        if anim_path:
            anims = state.get_anims(anim_path)
            idxs = [int(clip)] if clip is not None else range(len(anims))
            skel = models[0].skeleton
            clips = [A.bake(anims[i], skel) for i in idxs if anims[i].rot_supported]
            suffix = '.' + (anims[int(clip)].name if clip is not None else
                            base_name(anim_path.rpartition('/')[2]).replace('.anim', ''))
        glb = gltf.build_glb(models, 0, texture_png=lambda n, k: texture_png_for_model(state.vfs, n, k, None, 6),
                             animations=clips, extras={'source': model, 'animations_from': anim_path},
                             fallback_color=lambda mt: ramp_color(state.vfs, mt))
        fname = base_name(model.rpartition('::')[2].rpartition('/')[2]) + suffix + '.glb'
        fname = re.sub(r'[<>:"/\\|?*]', '_', fname)
        os.makedirs(target, exist_ok=True)
        out = os.path.join(target, fname)
        with open(out, 'wb') as f:
            f.write(glb)
        return jsonify({'ok': True, 'path': out, 'clips': len(clips), 'size': len(glb)})

    return app
