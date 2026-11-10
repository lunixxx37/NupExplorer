# Third-party components

## Included in this repository

| Component | Location | License |
|---|---|---|
| [three.js](https://threejs.org/) r170 (core, OrbitControls, GLTFLoader, BufferGeometryUtils) | `web/vendor/three/` | MIT – Copyright © 2010-2024 three.js authors |

## Bundled in the Windows release (`NupExplorer.exe`)

The executable is built with [PyInstaller](https://pyinstaller.org/) and contains the Python runtime and
these packages:

| Package | License |
|---|---|
| Python 3.14 | PSF License |
| Flask, Werkzeug, Jinja2, ItsDangerous, Click, MarkupSafe | BSD-3-Clause |
| Blinker | MIT |
| NumPy | BSD-3-Clause (and bundled components under 0BSD, MIT, Zlib, CC0-1.0) |
| Pillow | MIT-CMU (HPND) |
| texture2ddecoder | MIT |
| soundfile (python-soundfile), cffi, pycparser | BSD-3-Clause / MIT |
| libsndfile (shipped with soundfile, used for Opus) | LGPL-2.1-or-later |

libsndfile is used as an unmodified, separately loadable library; to use a different build, run NUP Explorer
from source (`python app.py`) with your own `soundfile`/libsndfile installation.
