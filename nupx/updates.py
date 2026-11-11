"""Update check: asks GitHub for the latest release of NUP Explorer – a few seconds after the start, then once a day
while the program runs, or when "Check now" is clicked.

Only the request itself goes out (user agent "NupExplorer/<version>"); nothing about the user or the opened files.
It can be switched off in the version menu of the top bar (setting "check_updates"). Links from the answer are only
used when they point into the project's own GitHub pages, because they are opened in the browser.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

REPO = 'lunixxx37/NupExplorer'
REPO_URL = f'https://github.com/{REPO}'
API_URL = f'https://api.github.com/repos/{REPO}/releases/latest'
FIRST_DELAY = 3.0          # seconds after the start
INTERVAL = 24 * 3600.0     # then once a day
_URL_OK = re.compile(r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """'v1.2.0', '1.2' or 'NUP Explorer 1.2.1' -> (major, minor, patch); None without a version number."""
    m = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text or '')
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _link(url, prefix: str) -> str | None:
    """A link from the answer, only if it stays inside the project's GitHub pages."""
    if isinstance(url, str) and url.startswith(prefix) and _URL_OK.fullmatch(url):
        return url
    return None


def fetch_latest(current: str, timeout: float = 10.0) -> dict:
    """The latest published release (drafts and pre-releases are not included by GitHub)."""
    req = urllib.request.Request(API_URL, headers={
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': f'NupExplorer/{current}'})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
        rel = json.loads(r.read(4 << 20))
    tag = str(rel.get('tag_name') or '')
    ver = parse_version(tag)
    if ver is None:
        raise ValueError(f'unexpected release tag {tag[:40]!r}')
    exe = next((a for a in rel.get('assets') or () if str(a.get('name', '')).lower().endswith('.exe')), None) or {}
    download = _link(exe.get('browser_download_url'), f'{REPO_URL}/releases/download/')
    return {'version': '.'.join(map(str, ver)), 'tag': tag[:40], 'name': str(rel.get('name') or tag)[:200],
            'notes': str(rel.get('body') or '')[:8000], 'published': str(rel.get('published_at') or '')[:40],
            'page': _link(rel.get('html_url'), f'{REPO_URL}/releases/') or f'{REPO_URL}/releases/latest',
            'download': download, 'file': str(exe.get('name') or '')[:120] if download else '',
            'size': int(exe.get('size') or 0) if download else 0}


class UpdateChecker:
    """Background update check; status() goes into the UI state, on_change() is called after every check."""

    def __init__(self, current: str, settings: dict, on_change=None, log=print):
        self.current = current
        self.settings = settings
        self.on_change = on_change
        self.log = log
        self.latest: dict | None = None
        self.error: str | None = None
        self.checked: float | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.get('check_updates', True) is not False

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name='update-check', daemon=True)
            self._thread.start()

    def wake(self):
        """Check now if a check is due (e.g. after the automatic check was switched on again)."""
        self._wake.set()

    def _loop(self):
        self._wake.wait(FIRST_DELAY)
        while True:
            self._wake.clear()
            if self.enabled and (self.checked is None or time.time() - self.checked >= INTERVAL - 60):
                self.check()
            self._wake.wait(INTERVAL)

    def check(self) -> dict:
        with self._lock:  # one request at a time
            try:
                self.latest = fetch_latest(self.current)
                self.error = None
            except Exception as e:  # noqa: BLE001 - offline, rate limit ...: try again later
                self.error = f'{type(e).__name__}: {e}'[:300]
                self.log(f'Update check failed: {self.error}')
            self.checked = time.time()
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass
        return self.status()

    def status(self) -> dict:
        latest = self.latest
        cur = parse_version(self.current)
        new = parse_version(latest['version']) if latest else None
        return {'current': self.current, 'enabled': self.enabled, 'checked': self.checked, 'error': self.error,
                'available': bool(cur and new and new > cur), 'latest': latest, 'project': REPO_URL}
