"""NUP Explorer – entry point.

Starts the local API server (127.0.0.1, random port + token) and opens the UI in its own
Edge app window (falls back to the default browser). The server ends when the window is
closed (no live SSE connection for 15 s) unless an export is still running.

Usage: python app.py [path\\to\\file.nup] [--port N] [--browser app|default|none] [--keep-running]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

APP = 'NupExplorer'


def base_dir() -> str:
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


def data_dirs() -> tuple[str, str]:
    roaming = os.environ.get('APPDATA') or os.path.expanduser('~')
    local = os.environ.get('LOCALAPPDATA') or roaming
    return os.path.join(roaming, APP), os.path.join(local, APP)


def load_settings(path: str) -> dict:
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def make_saver(path: str):
    lock = threading.Lock()

    def save(settings: dict):
        with lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
    return save


def find_browser() -> str | None:
    pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    pf = os.environ.get('ProgramFiles', r'C:\Program Files')
    local = os.environ.get('LOCALAPPDATA', '')
    for p in (os.path.join(pf86, r'Microsoft\Edge\Application\msedge.exe'),
              os.path.join(pf, r'Microsoft\Edge\Application\msedge.exe'),
              os.path.join(local, r'Microsoft\Edge\Application\msedge.exe'),
              os.path.join(pf, r'Google\Chrome\Application\chrome.exe'),
              os.path.join(pf86, r'Google\Chrome\Application\chrome.exe'),
              os.path.join(local, r'Google\Chrome\Application\chrome.exe')):
        if p and os.path.isfile(p):
            return p
    return None


def open_window(url: str, mode: str, profile_dir: str, log) -> subprocess.Popen | None:
    if mode == 'none':
        log(f'UI: {url}')
        return None
    exe = find_browser() if mode == 'app' else None
    if exe:
        os.makedirs(profile_dir, exist_ok=True)
        args = [exe, f'--app={url}', f'--user-data-dir={profile_dir}', '--no-first-run',
                '--no-default-browser-check', '--disable-features=Translate', '--window-size=1600,960']
        try:
            return subprocess.Popen(args, close_fds=True)
        except OSError as e:
            log(f'Could not start the browser: {e}')
    webbrowser.open(url)
    return None


def free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    ap = argparse.ArgumentParser(description='NUP Explorer')
    ap.add_argument('nup', nargs='?', help='path to the .nup file')
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--browser', choices=['app', 'default', 'none'], default='app')
    ap.add_argument('--keep-running', action='store_true', help='keep the server running when the window is closed')
    ap.add_argument('--token', help=argparse.SUPPRESS)  # fixed token for development/testing
    args = ap.parse_args()

    roaming, local = data_dirs()
    os.makedirs(local, exist_ok=True)
    log_file = None
    if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
        log_file = open(os.path.join(local, 'log.txt'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log_file

    def log(*a):
        print(time.strftime('%H:%M:%S'), *a, flush=True)

    from werkzeug.serving import make_server

    from nupx.server import State, create_app

    settings_path = os.path.join(roaming, 'settings.json')
    settings = load_settings(settings_path)
    state = State(os.path.join(base_dir(), 'web'), os.path.join(local, 'cache'), settings, make_saver(settings_path), log)
    if args.token:
        state.token = args.token
    app = create_app(state)
    port = args.port or free_port()
    srv = make_server('127.0.0.1', port, app, threaded=True)
    state.port = srv.server_port
    threading.Thread(target=srv.serve_forever, name='http', daemon=True).start()

    nup = args.nup or settings.get('last_nup')
    if nup and os.path.isfile(nup):
        state.open_nup(nup)
    elif args.nup:
        log(f'File not found: {args.nup}')

    url = f'http://127.0.0.1:{state.port}/?t={state.token}'
    log(f'NUP Explorer is running on http://127.0.0.1:{state.port}/')
    open_window(url, args.browser, os.path.join(local, 'browser-profile'), log)

    started = time.time()
    try:
        while True:
            time.sleep(1)
            if args.keep_running or args.browser == 'none' or state.export.running:
                continue
            if state.client_count():
                continue
            idle = time.time() - state.last_disconnect
            if state.ever_connected and idle > 15:
                log('Window closed – exiting.')
                break
            if not state.ever_connected and time.time() - started > 180:
                log('No connection to the window – exiting.')
                break
    except KeyboardInterrupt:
        pass
    finally:
        state.export.cancel()
        srv.shutdown()
        if state.vfs:
            state.vfs.close()
        if log_file:
            log_file.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
