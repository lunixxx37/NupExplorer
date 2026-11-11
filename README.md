<p align="center"><img src="web/logo.svg" width="112" alt="NUP Explorer logo"></p>

<h1 align="center">NUP Explorer</h1>

<p align="center">
Browse, preview and extract the assets of <b>NUP</b> game containers (Nintendo Switch 2 titles, base game + update in one file):
textures, music and voice, 3D models with skeletal animations, videos, texts and game data – Nintendo's formats
and Koei Tecmo's Katana Engine (e.g. Hyrule Warriors: Age of Imprisonment).
</p>

![NUP Explorer home screen](docs/screenshot-home.png)

## Features

- **File tree** of the whole container – base game and update merged (the update wins), lazily loaded and
  virtualized (folders with 20,000+ entries stay smooth), search (all words must match) and type filter.
  SARC/pack archives expand like folders.
- **Textures** (BNTX with the Switch 2 block-linear swizzle): BC1–BC7, BC6H, ASTC 4×4 … 12×12, R8G8, R16,
  R16G16, R11G11B10F, RGBA16F. Zoom/pan, single channels, mip levels, array layers and cube faces.
- **Audio** (BWAV): DSP-ADPCM and Opus, waveform, seeking, loop points from the file, up to 6 channels
  (the preview is mixed down to stereo, the export keeps all channels). Koei Tecmo sound banks: see below.
- **3D models** (BFRES v10): meshes and LOD levels, textures and normal maps, skeleton, **skeletal
  animations** including mesh visibility tracks, and the game's **compressed meshlet geometry** used by most
  map parts. Material colours are approximated from the shader parameters (gems, glass, liquids, hair, eyes);
  emission maps make lights and glowing parts glow. Animation files are matched to their models by name
  (including shared character animations for costume models); files with only material animations are marked.
- **Texts** (MSBT) as a table with a second language side by side, download as JSON/CSV.
- **Game data** (BYML/GYML) as YAML with search, download as YAML/JSON.
- **Videos** (WebM, also when a game stores them as `.bin`) with seeking; export as `.webm`.
- **Koei Tecmo (Katana Engine) packs** – `.fdata` / `.file`: browsable like archives; the entries are unpacked
  and named `0x<hash>.<type>` (`g1t` textures, `g1m` models, `g2a` animations …). The games only store name
  hashes, and their complete name lists are not part of the game data, so NUP Explorer recovers **real names
  wherever the game data allows it** – each one checked against the hash: UI textures (`still_tutorial_064`),
  stage textures, models (`set-mi-gen-stable-01`), sound banks (`CE_Ganondorf`), databases and all sounds
  (exports use them too). Everything else gets a **description of the content** (`62 sound banks`,
  `102 sounds`, `564 bones · 642 motions`, `1.6 s · 310 bones` …). The search and the category tiles include the
  models, textures, animations and sounds inside the packs. **G1T textures** are shown like BNTX textures (BC1–BC7 tiled and linear, RGBA8, float formats, arrays,
  cube maps, mip levels) and exported as PNG/TGA/DDS. **G1M models** are shown in the 3D viewer with skeleton,
  skinning and their textures (colour and normal maps, found through the game's resource database) and exported
  like the other models (GLB, glTF, OBJ; converted from centimetres to metres). Their **G2A/G1A animations** play in
  the viewer: the motions the game assigns to the character come first, followed by every animation that fits the
  skeleton (most bones first; the lists show length and bone count, as the files only have hash names), and they
  are exported together with the model as GLB. **Sound banks** (KTSR `.srsa`) open like folders and list
  their sounds by name (voices, effects, music with loop points, 5.1 cutscene audio); the KA1A codec is decoded,
  also for sounds whose data sits in a separate stream file (found automatically), and the sounds play like any
  other audio and export as WAV/FLAC/MP3/OGG (raw export: `.ka1a` files, which vgmstream plays). Other entries
  are shown as hex and exported unpacked, stream files as `.ktsl2stbin`.
- **Export** – single files, folders or archives:
  *converted* or *raw* (just unpacked); archives become folders. Formats of the converted export
  (button *Formats* in the top bar):

  | Type | Formats |
  |---|---|
  | Audio | WAV, FLAC, MP3 (320 kbps), OGG Vorbis |
  | Textures | PNG, TGA, DDS (original data with all mipmaps; BC1–BC7 stay compressed) |
  | 3D models | GLB (textures embedded), glTF + .bin + PNG textures, OBJ + MTL (static) |
  | Texts | JSON, CSV |
  | Game data | YAML, JSON |

  Folders are exported in parallel; models can be exported
  as GLB with embedded textures together with one clip, all clips of an animation file or all animation
  files of the model at once (standard glTF 2.0: opens in Blender and other glTF tools).
- Runs completely **locally**: a small server bound to `127.0.0.1` with a random access token, shown in its
  own app window (Microsoft Edge in app mode). Nothing is uploaded anywhere; the only request to the internet is
  the update check below, and it can be switched off.
- **Version and updates**: the version number is shown in the top bar. When a newer release is out, an
  **Update** button appears next to it (and a note on the home screen) with the release notes and the download.
  To find out, NUP Explorer asks GitHub's API for the latest release a few seconds after the start and then
  once a day while it runs – only that request is sent, nothing about you or your files. Switch it off in the
  version menu (click the version number).

## Screenshots

**3D models** – textures and normal maps, detail levels (LOD), mesh list with visibility toggles, skeleton,
export as GLB:

![3D model viewer](docs/screenshot-model.png)

**Skeletal animations** – matching animation files are suggested for every model; clips play with timeline,
speed and loop while the camera follows the model, and model + animations can be exported together as GLB:

![Skeletal animation playback](docs/animation.gif)

## Download

Windows: download `NupExplorer.exe` from the [latest release](../../releases/latest) – no installation and no
Python needed. Microsoft Edge (preinstalled on Windows 10/11) is used for the window.

Start it, pick your `.nup` file, done. The last file is remembered. Closing the window ends the program after
a few seconds (unless an export is still running). From version 1.2.0 on, the program tells you when a new
version is available (*Update* button in the top bar); replace the old `NupExplorer.exe` with the new one.

## Usage

| What | How |
|---|---|
| View a file | click it in the tree (arrow keys work too) |
| Search | search box at the top left; several words = all must match; type filter next to it |
| Jump from a search result to its folder | right-click → *Show in tree* |
| Open an archive | expand it like a folder |
| Export | right-click a file, folder or archive – or use the buttons in the viewer header |
| Change the export folder | button at the top right |
| Choose export formats | *Formats* button in the top bar (audio, textures, 3D models, texts, game data) |
| Play / pause audio | <kbd>Space</kbd> |
| Animations | choose a 3D model → pick an animation file (suggestions are shown) → click a clip |
| Model + animations as GLB | 3D viewer → *Animation*: *GLB + clip*, *GLB + all clips* (this file) or *GLB + all files* (every file in the list) |
| Version, updates | click the version number in the top bar: what's new and the download of a newer release, *Check now*, automatic update check on/off |

Command line:

```
NupExplorer.exe [file.nup] [--browser app|default|none] [--port N] [--keep-running]
```

- `--browser default` opens the UI in your default browser, `none` opens nothing (the URL is printed/logged).
- `--keep-running` keeps the server alive when the window is closed.

Settings are stored in `%APPDATA%\NupExplorer\settings.json`; the file index cache, the browser profile of the
app window and the log (`log.txt`, EXE only) live in `%LOCALAPPDATA%\NupExplorer\`.

## Run from source

Requires Python 3.14 (Windows; tested with 3.14.0).

```
pip install -r requirements.txt
python app.py [path\to\file.nup]
```

or simply `start.bat`.

### Build the EXE

```
pip install pyinstaller
build_exe.bat
```

The result is `dist\NupExplorer.exe` (single file, no console window).

### Tests

They need a NUP file – pass it as argument or set `NUP_FILE`:

```
python tests\smoke.py path\to\file.nup        # random samples of every file type + regressions
python tests\model_qa.py path\to\file.nup     # quality check of all 3D models (missing textures, materials, geometry)
python tests\curve_check.py path\to\file.nup  # animation curve decoder acceptance test
```

## Format notes

Everything below was reverse engineered from the data; details are in the module docstrings.

- **NUP container** (`nupx/container.py`): header `NUP\x01`, TOC with sections (base game, update),
  directory and file tables, string pool, SHA-256 per file. Files are stored unencrypted; newer NUP files
  store some files as a single zstd frame (file record: compression 1, stored size, original size).
- **Zstd**: dictionary chosen by the ID in the frame header (1 `zs`, 2 `bkprb`, 3 `bcett.byml`, 4 `pack`,
  taken from `romfs/Pack/ZsDic.pack.zs`).
- **BNTX** (`nupx/textures.py`): platform block `Ounc`; Switch 2 GOB bit order `x0..x3, y0, y1, x4, y2, x5`.
- **BWAV** (`nupx/audio.py`): DSP-ADPCM, or Nintendo Opus packets re-muxed into Ogg.
- **BFRES v10.2 / v10.4** (`nupx/bfres.py`, `nupx/anim.py`): skeletal animation curves are cubic Hermite curves
  with (value, slope) per key, quantized (8/16-bit keys after a slope header; v10.4 also sets flag 0x0800) or
  float; 8/16-bit frame numbers are multiplied by the curve's frame scale when flag 0x0400 is set; quaternion
  curves (flag 0x80) use slerp; Euler rotations compose as R = Rz·Ry·Rx and the rotation type is chosen
  **per bone** (flag 0x00100000).
- **Texts**: one SARC per language (`romfs/Mals/<Lang>.Product.<ver>.sarc.zs`) or one folder per language
  (`romfs/Mals/<Lang>/*.msarc`); the language compare looks up the same MSBT in the other language.
- **Materials**: render-info and shader-parameter names are only stored as 64-bit hashes. Identified by
  comparing all materials: render info `c449e543484e03dc` = "shadow only", `9637173a98b4d882` = blend mode,
  colour parameters `ad91cd2b…`, `d8c1e846…`, `6e1f54c6…` carry the surface colour of untextured materials.
- **Koei Tecmo packs** (`nupx/ktpack.py`): `.fdata` = `PDRK0000` + `IDRK0000` entries (16-byte aligned), `.file` = one
  entry. Entry: size, stored size, unpacked size, name hash (KTID), type hash, flags; flag 0x400000 = data in
  chunks `[u16 zlib size][8 bytes][zlib stream]`. The payload magics are stored reversed (`GT1G` = G1T).
- **G1T** (`nupx/g1t.py`, version 0060-0062, platform 0x18): formats 0x59-0x5F / 0x60-0x66 = BC1-BC7 linear /
  tiled (0x06-0x08 / 0x10-0x12 = BC1-BC3), 0x00-0x04 / 0x09-0x0D = RGBA8, R32F, RGBA16F, RGBA32F linear / tiled.
  Tiled = the Switch block-linear layout of BNTX with the block height derived from the height; tiled arrays
  store each layer with its mip chain, linear arrays mip by mip.
- **G1M** (`nupx/g1m.py`): chunks G1MS (skeleton: parent-relative joints, quaternions, a global-id table) and G1MG
  (materials with texture slots, vertex buffers – several can share one data block –, vertex layouts, joint
  palettes, index buffers, submeshes, LOD groups); blend indices are three times the palette slot. The chunk
  layouts follow the community documentation of the format (Project G1M, gust_stuff); the implementation is
  independent and was checked against all 8,989 models of the game.
- **G2A / G1A** (`nupx/g2a.py`): one clip per file, keyed by the global bone ids of the G1MS table. G2A (60 fps)
  stores per track (rotation / position / scale) u16 key frames and one 32-byte record per key: four 64-bit rows
  with a 4-bit exponent and three signed 20-bit mantissas each – the coefficients of a cubic in the segment's
  normalised time; rotations are rotation vectors (axis × angle). G1A stores float cubic coefficients and key times
  in seconds, rotations as quaternions. The rotation convention was confirmed by skinning the meshes with the
  animations: the right one keeps the limbs rigid.
- **Koei Tecmo resource links** (`nupx/katana.py`): a G1M names its textures only by slot. The kidsobjdb databases
  (`.kod`) link a model object (G1M file) to a texture set, the set to a texture table (slot → texture object) and
  each texture object to its G1T file. NUP Explorer reads these links once per NUP (about 20 seconds in the
  background; the names and descriptions appear when it is done) and caches them.
- **Koei Tecmo names** (`nupx/katana.py`): every id is a 32-bit hash of a name (the algorithm of the
  Cethleann tools): files are `R_<EXT>［name］` with fullwidth brackets (`R_SRSA［CE_Ganondorf］`) or the plain
  file name (`Field_Common.kidsobjdb`), objects `<Class>［name］［scene］`. The game's name databases
  (`_RNK0000`: id, name, type, property names) would name everything, but only 52 small ones are shipped – the main
  list `root.rdb.name` and about 3,900 others are referenced but missing. NUP Explorer therefore uses the names it
  can prove: UI texture objects (`0x79991E79`) pair a G1T file with a name file (u32 1 + string); the shipped name
  databases name texture objects (→ G1T, kept when `R_G1T［name］` hashes to the file's id), model and visual
  objects (→ the G1M they use) and some files directly; sister textures are found by varying verified names
  (suffixes `_kidsalb`/`_kidsnmh`/…, neighbouring numbers) and kept when their hash exists; a sound bank is named by
  the prefix of its sound names whose `R_SRSA［prefix］` hash is the bank's id. In Hyrule Warriors: Age of
  Imprisonment this names 5,443 of 19,515 textures (5,038 of them UI), 229 of 8,981 models, 53 of 139 sound banks,
  14 object databases, 41 name databases and all 85,619 sounds; motions are not in the shipped lists.
- **KTSR sound banks** (`nupx/ktsr.py`): `.srsa` asset banks hold the sound headers and short sounds, `.srst` stream
  files (inside the language folders' `.file` packs) the long ones. Sound names are XOR-ed with the C `rand()`
  sequence seeded with the bank's audio id; stream data is Blowfish (ECB) scrambled with a key stored in the stream
  file's own header. A bank does not name its stream file: candidates are matched by the chunk in front of each
  sound's data (sound id + size), sounds the update moved are found by their id.
- **KA1A** (`nupx/ka1a.py`, port of vgmstream's decoder): mono transform codec, 512 samples per frame, constant
  frame size per bitrate mode (−5…5); multichannel sounds store one frame per channel in turn.
- **Compressed meshlet geometry** (`nupx/clusters.py`): shapes with flag 0x10 and an empty FVTX store their
  geometry as meshlets (≤ 64 vertices / 124 triangles) for up to 16 LOD levels. Each meshlet record
  (32 bytes + 8 per UV set or colour) holds origin and extent (3 × 21 bit on a 0.5 mm grid), triangle and
  vertex count, data offset, bits per vertex and UV offsets; the vertex data is an LSB-first bit stream
  (position, octahedral normal, tangent, UV sets as `(offset + value) · 2^(exponent − 16)`, colours), followed
  by the triangle indices.

## Limitations

- Materials are approximated with a base colour and a normal map. Effects that only exist in the game's
  shaders (e.g. the dark look of shadow characters, terrain blending) are not reproduced.
- A few materials reference textures that do not exist in the game files; they show a shader colour or grey.
  Untextured materials whose colour parameters are not known yet are shown grey as well.
- Material animations (texture scrolling, colour changes) are recognised but not played.
- Export formats: MP3 is limited to stereo (sounds with more channels are mixed down), OBJ has no skeleton
  or animations, DDS keeps BC1–BC7 data as stored (the texture's channel mapping is not applied).
  AC3 and AAC are not offered – there is no encoder for them without bundling ffmpeg.
- Terrain (`TerrainArc`), effects and physics data are only available as hex view / raw export.
- G1M meshes that the game's cloth simulation positions (a few capes and hair parts) are not shown; G1M materials
  are shown with their colour and normal maps only. Koei Tecmo games ship only part of their name lists: files
  whose name cannot be recovered keep their hash name (`0x<hash>.<type>`, in Age of Imprisonment most textures and
  models and all animations) and show a description instead. Most Koei Tecmo characters do not name their
  animations in the game data: they get every animation that fits their skeleton, which includes animations of
  other characters with the same bones. Sound banks with other codecs than KA1A (other Koei Tecmo games) list
  their sounds, but only export them raw.

## Legal

NUP Explorer is an independent, fan-made tool. It is **not affiliated with, endorsed or sponsored by
Nintendo**. The repository and the release contain **no game data, keys or copyrighted assets**, and the tool
does not circumvent console encryption or copy protection – it only reads unencrypted NUP files you provide.
(Koei Tecmo scrambles some sound data and sound names with a key stored in the same file; like vgmstream, NUP
Explorer reads that key from the file to play them.)
Use it only with files you are legally allowed to use. Extracted assets remain the property of their
respective owners; do not redistribute them. The screenshots show game assets for illustration only – all
rights to them belong to their owners. All trademarks belong to their respective owners.

## License

[MIT](LICENSE). Third-party components and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
