# Third-party components

## Included in this repository

| Component | Location | License |
|---|---|---|
| [three.js](https://threejs.org/) r170 (core, OrbitControls, GLTFLoader, BufferGeometryUtils) | `web/vendor/three/` | MIT – Copyright © 2010-2024 three.js authors |
| KA1A audio decoder: Python/numpy port of [vgmstream](https://github.com/vgmstream/vgmstream)'s KA1A decoder (`src/coding/libs/ka1a_dec.c`, `ka1a_dec_data.h`); KTSR details cross-checked with its `src/meta/ktsr.c` and `ka1a.c` | `nupx/ka1a.py`, `nupx/ktsr.py` | ISC – see below |

### vgmstream

The KA1A codec was reverse engineered by bnnm for vgmstream, with help from Kelebek1 and AceKombat.
`nupx/ka1a.py` is a port of that decoder (frame layout, tables, transform and overlap window); the KTSR sound bank
reader in `nupx/ktsr.py` follows vgmstream's documentation of the format (name obfuscation, Blowfish-scrambled
stream data, sub-header fields). License of vgmstream:

```
Copyright (c) 2008-2025 Adam Gashlin, Fastelbja, Ronny Elfert, bnnm,
                        Christopher Snowhill, NicknineTheEagle, bxaimc,
                        Thealexbarney, CyberBotX, EdnessP, et al

Permission to use, copy, modify, and distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

### Format documentation used as reference (no code included)

The G1M chunk layouts were cross-checked with the community documentation in
[Project G1M](https://github.com/Joschuka/Project-G1M) (Joschuka) and
[gust_stuff](https://github.com/eArmada8/gust_stuff) (eArmada8). No code from these projects is included; the G1M
reader in `nupx/g1m.py` is an independent implementation verified against the game data.

The Koei Tecmo name hash (KTID) and the `R_<EXT>［name］` / `<Class>［name］［scene］` name formats in
`nupx/katana.py` follow the [Cethleann](https://github.com/yretenai/Cethleann) tools (yretenai, MIT). The hash
function is a short independent re-implementation of that algorithm; no code from Cethleann is included.

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
| libsndfile (shipped with soundfile: Opus decoding, FLAC/MP3/OGG export) | LGPL-2.1-or-later |
| codecs inside that libsndfile build: LAME, mpg123 | LGPL-2.0-or-later / LGPL-2.1 |
| codecs inside that libsndfile build: FLAC, Ogg, Vorbis, Opus | BSD-3-Clause |

libsndfile is used as an unmodified, separately loadable library; to use a different build, run NUP Explorer
from source (`python app.py`) with your own `soundfile`/libsndfile installation.
