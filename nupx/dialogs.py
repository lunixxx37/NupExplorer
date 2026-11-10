"""Native file/folder dialogs on one dedicated Tk thread (Tk must stay on a single thread)."""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future


class Dialogs:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def _run(self):
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes('-topmost', True)
        except tk.TclError:
            pass
        while True:
            kind, kw, fut = self._q.get()
            if kind is None:
                break
            try:
                root.lift()
                root.update()
                if kind == 'file':
                    r = filedialog.askopenfilename(parent=root, **kw)
                else:
                    r = filedialog.askdirectory(parent=root, **kw)
                fut.set_result(r or None)
            except Exception as e:  # noqa: BLE001
                fut.set_exception(e)
        root.destroy()

    def ask(self, kind: str, **kw):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='dialogs', daemon=True)
                self._thread.start()
        fut: Future = Future()
        self._q.put((kind, kw, fut))
        return fut.result()

    def pick_file(self, title='Open NUP file', initialdir=None):
        return self.ask('file', title=title, initialdir=initialdir,
                        filetypes=[('NUP container', '*.nup'), ('All files', '*.*')])

    def pick_folder(self, title='Choose export folder', initialdir=None):
        return self.ask('folder', title=title, initialdir=initialdir, mustexist=False)
