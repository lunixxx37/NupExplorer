// App shell: state via server events, tree + search, viewer switching, export, context menu.
import { el, events, formatSize, getJSON, postJSON, TOKEN } from './api.js';
import { hydrate, icon, TYPES as TYPE_INFO } from './icons.js';
import { Tree } from './tree.js';
import * as audio from './viewers/audio.js';
import * as model from './viewers/model.js';
import * as text from './viewers/text.js';
import * as texture from './viewers/texture.js';
import * as video from './viewers/video.js';

const $ = (s) => document.querySelector(s);
const num = (n) => Number(n || 0).toLocaleString('en-US');
let tree = null;
let viewer = null;
let selectSeq = 0;
let loadedNup = null;
let exportDismissed = false;
let state = null;
let typeCounts = null;
let currentMeta = null;
let homePage = null;
let labelsShown = false;

// Koei Tecmo index ready: real names, descriptions and the files inside the packs are now known
async function onLabels() {
  try {
    await tree.updateLabels();
  } catch (e) { console.error(e); }
  if ($('#search').value.trim() || $('#typefilter').value) runSearch();
  if (homePage && homePage.isConnected) { typeCounts = null; showHome(); }
}
let formatOptions = null;

// "Export" (converted) writes these formats; chosen in the Formats menu of the top bar
const FORMAT_TYPES = [['audio', 'Music & sound'], ['texture', 'Textures'], ['model', '3D models'], ['msbt', 'Texts'], ['byml', 'Game data']];
const FORMAT_SHORT = { wav: 'WAV', flac: 'FLAC', mp3: 'MP3', ogg: 'OGG', png: 'PNG', tga: 'TGA', dds: 'DDS', glb: 'GLB',
  gltf: 'glTF', obj: 'OBJ', json: 'JSON', csv: 'CSV', yml: 'YAML' };

const TYPES = [['', 'All types'], ['texture', 'Textures'], ['audio', 'Audio'], ['model', '3D models'],
  ['anim', 'Animations'], ['msbt', 'Texts (MSBT)'], ['byml', 'Data (BYML)'], ['video', 'Videos'], ['archive', 'Archives'], ['other', 'Other']];
const TILES = [['texture', 'Textures'], ['model', '3D models'], ['audio', 'Music & sound'], ['anim', 'Animations'],
  ['msbt', 'Texts'], ['byml', 'Game data'], ['video', 'Videos'], ['archive', 'Archives'], ['other', 'Other files']];

function toast(msg, isErr, ms = 4000, ic = null) {
  const t = $('#toast');
  t.replaceChildren(icon(ic || (isErr ? 'warn' : 'check')), el('span', {}, msg));
  t.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.className = ''; }, ms);
}

// ------------------------------------------------------------ version and updates
// The server asks GitHub for the latest release (at start, then daily); a newer one shows the "Update" pill in the
// top bar, a note on the home screen and once a toast. The version pill opens the menu with details and settings.
let updateNotified = null;

function renderUpdate() {
  const u = state && state.update;
  const ver = $('#btnVersion');
  if (state && state.version) { ver.textContent = `v${state.version}`; ver.classList.remove('hidden'); }
  const pill = $('#btnUpdate');
  const avail = !!(u && u.available && u.latest);
  pill.classList.toggle('hidden', !avail);
  if (avail) {
    pill.querySelector('.lbl').textContent = `Update ${u.latest.version}`;
    pill.title = `NUP Explorer ${u.latest.version} is available – click for what’s new and the download`;
    if (updateNotified !== u.latest.version) {
      updateNotified = u.latest.version;
      toast(`NUP Explorer ${u.latest.version} is available – see “Update” in the top bar`, false, 9000, 'update');
    }
  }
  const slot = homePage && homePage.isConnected ? homePage.querySelector('.update-slot') : null;
  if (slot) slot.replaceChildren(...[updateCallout()].filter(Boolean));
  if (!$('#aboutmenu').classList.contains('hidden')) fillAbout();
}

function updateCallout() {
  const u = state && state.update;
  if (!u || !u.available || !u.latest) return null;
  return el('button', { class: 'update-callout', onclick: toggleAbout }, icon('update'),
    el('span', {}, el('b', {}, `NUP Explorer ${u.latest.version} is available`), ' – what’s new and download'));
}

const dateText = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }); };
const clockText = (sec) => new Date(sec * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

// release notes (Markdown) as plain text: headings, paragraphs and bullet lists – never as HTML
function releaseNotes(md) {
  const box = el('div', { class: 'notes' });
  const plain = (s) => s.replace(/\*\*(.+?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  let list = null;
  for (const raw of md.replace(/\r/g, '').split('\n')) {
    const line = raw.trim();
    if (!line) { list = null; continue; }
    const bullet = /^[-*]\s+/.test(line);
    const text = plain(line.replace(/^#+\s*/, '').replace(/^[-*]\s+/, ''));
    if (bullet) {
      if (!list) { list = el('ul'); box.append(list); }
      list.append(el('li', {}, text));
    } else {
      list = null;
      box.append(el(line.startsWith('#') ? 'h5' : 'p', {}, text));
    }
  }
  return box;
}

async function openLink(what) {
  try {
    await postJSON('/api/update/open', { what });
    toast(what === 'download' ? 'The download opens in your browser' : 'Opened in your browser', false, 4000, 'external');
  } catch (e) { toast(e.message, true); }
}

async function checkNow(btn) {
  btn.disabled = true;
  btn.textContent = 'Checking…';
  try {
    const u = await postJSON('/api/update/check');
    if (state) state.update = u;
    renderUpdate();
    if (u.error) toast('Could not reach GitHub – try again later', true);
    else if (!u.available) toast(`NUP Explorer ${u.current} is up to date`);
  } catch (e) { toast(e.message, true); }
  fillAbout();
}

async function saveUpdateSetting(on) {
  try {
    await postJSON('/api/settings', { check_updates: on });
    if (state && state.update) state.update.enabled = on;
    toast(on ? 'Automatic update check on' : 'Automatic update check off');
  } catch (e) { toast(e.message, true); }
}

function fillAbout() {
  const m = $('#aboutmenu');
  const u = (state && state.update) || {};
  const latest = u.latest;
  const rows = [el('div', { class: 'about-head' }, el('img', { src: 'logo.svg', alt: '' }),
    el('div', {}, el('div', { class: 'about-name' }, el('b', {}, 'NUP'), ' Explorer'), el('div', { class: 'muted small' }, `Version ${(state && state.version) || u.current || ''}`)))];
  if (u.available && latest) {
    rows.push(el('div', { class: 'about-update' },
      el('div', { class: 'au-title' }, icon('update'), `Version ${latest.version} is available`),
      latest.published ? el('div', { class: 'muted small' }, `Released ${dateText(latest.published)}`) : null,
      latest.notes ? releaseNotes(latest.notes) : null,
      el('div', { class: 'row' },
        latest.download ? el('button', { class: 'btn primary', title: `${latest.file || 'Download'} – ${latest.download}`, onclick: () => openLink('download') },
          icon('export'), `Download${latest.size ? ` (${formatSize(latest.size)})` : ''}`) : null,
        el('button', { class: 'btn', title: latest.page, onclick: () => openLink('release') }, icon('external'), 'Release page'))));
  } else if (latest && !u.error) {
    rows.push(el('div', { class: 'about-ok' }, icon('check'), 'You have the latest version'));
  }
  if (u.error) rows.push(el('div', { class: 'about-err small' }, icon('warn'), 'Could not check for updates (offline?)'));
  const toggle = el('input', { type: 'checkbox', checked: u.enabled !== false });
  toggle.addEventListener('change', () => saveUpdateSetting(toggle.checked));
  const btn = el('button', { class: 'btn ghost small' }, 'Check now');
  btn.addEventListener('click', (e) => { e.stopPropagation(); checkNow(btn); });
  rows.push(el('div', { class: 'msep' }),
    el('label', { class: 'check about-row' }, toggle, el('span', {}, 'Check for updates automatically')),
    el('div', { class: 'about-row' }, el('span', { class: 'muted small' }, u.checked ? `Last checked ${clockText(u.checked)}` : 'Not checked yet'), btn),
    el('div', { class: 'msep' }),
    el('div', { class: 'mi', title: u.project || '', onclick: () => openLink('project') }, icon('external'), 'Project page on GitHub'));
  m.replaceChildren(...rows.filter(Boolean));
}

function toggleAbout(ev) {
  ev.stopPropagation();
  const m = $('#aboutmenu');
  if (!m.classList.contains('hidden')) { closeAbout(); return; }
  closeFormats();
  fillAbout();
  m.className = 'menu about';
  const r = $('#btnVersion').getBoundingClientRect();
  m.style.top = `${r.bottom + 8}px`;
  m.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - m.offsetWidth - 8))}px`;
}

function closeAbout() { $('#aboutmenu').className = 'menu hidden'; }

function setViewer(fn) {
  if (viewer) { try { viewer.dispose(); } catch (e) { console.error(e); } viewer = null; }
  const v = $('#viewer');
  v.replaceChildren();
  if (fn) viewer = fn(v);
}

// ------------------------------------------------------------ home screen
function loading(msg) {
  $('#viewhead').replaceChildren();
  setViewer((v) => { v.append(el('div', { class: 'loading-state' }, el('div', { class: 'spinner' }), el('div', {}, msg))); return { dispose() {} }; });
}

function home(msg, opts = {}) {
  $('#viewhead').replaceChildren();
  setViewer((v) => {
    const tiles = el('div', { class: 'tiles' });
    const hero = el('div', { class: 'hero' },
      el('img', { src: 'logo.svg', alt: '' }),
      el('h1', {}, 'NUP ', el('span', {}, 'Explorer')),
      el('p', { class: opts.err ? 'warn' : '' }, msg),
      opts.button ? el('button', { class: 'btn primary', onclick: openNup }, icon('open'), 'Open NUP file…') : null,
      el('div', { class: 'update-slot' }, updateCallout()));
    const page = el('div', { class: 'home' }, hero);
    homePage = page;
    if (opts.tiles) {
      for (const [type, label] of TILES) {
        // texts live inside the language archives (romfs/Mals): the tile lists those archives
        const isText = type === 'msbt';
        const unit = isText ? 'language archives' : 'files';
        const count = el('span', { class: 'tc' }, typeCounts ? `${num(typeCounts[type])} ${unit}` : '…');
        tiles.append(el('button', {
          class: `tile t-${type}`, title: isText ? 'Show the language archives with the game texts' : `Show all: ${label}`,
          onclick: () => {
            $('#search').value = isText ? 'romfs/Mals/' : '';
            $('#typefilter').value = isText ? 'archive' : type;
            runSearch(); $('#tree').focus();
          },
        }, icon(TYPE_INFO[type].icon), el('span', { class: 'tl' }, label), count));
      }
      page.append(tiles, el('div', { class: 'tips' },
        el('span', {}, icon('search'), 'Search: several words must all match'),
        el('span', {}, el('kbd', {}, 'Ctrl'), '+', el('kbd', {}, 'F'), 'search'),
        el('span', {}, el('kbd', {}, '↑'), el('kbd', {}, '↓'), 'browse the tree'),
        el('span', {}, el('kbd', {}, 'Space'), 'play / pause audio'),
        el('span', {}, icon('export'), 'Right-click: export as PNG / WAV / GLB / JSON / YAML or raw')));
      if (!typeCounts) loadTypeCounts().then(() => { if (page.isConnected) home(msg, opts); });
    }
    v.append(page);
    return { dispose() {} };
  });
}

async function loadTypeCounts() {
  const nup = state && state.nup && state.nup.path;
  const res = await Promise.all(TILES.map(([type]) => getJSON('/api/search',
    type === 'msbt' ? { q: 'romfs/Mals/', type: 'archive', limit: 1 } : { q: '', type, limit: 1 }).then((r) => r.total).catch(() => 0)));
  if (!state || !state.nup || state.nup.path !== nup) return;
  typeCounts = Object.fromEntries(TILES.map(([type], i) => [type, res[i]]));
}

function showHome() {
  if (!state || state.loading) return;
  if (!state.nup) home(state.error ? `Could not open the file: ${state.error}` : 'No NUP file loaded.', { button: true, err: !!state.error });
  else home('Pick a file in the tree – or start with a category:', { tiles: true });
}

// ------------------------------------------------------------ top bar / state
async function openNup() {
  try { await postJSON('/api/open', {}); } catch (e) { toast(e.message, true); }
}

async function pickTarget() {
  try {
    const r = await postJSON('/api/pick-folder', {});
    if (r.picked) toast(`Export folder: ${r.path}`);
    updateTarget(r.path);
  } catch (e) { toast(e.message, true); }
}

function updateTarget(p) {
  const lbl = $('#targetLabel');
  lbl.textContent = p ? (p.length > 42 ? '…' + p.slice(-40) : p) : 'Choose export folder';
  $('#btnTarget').title = p ? `Export folder: ${p} (click to change)` : 'Choose export folder';
}

function onState(s) {
  state = s;
  updateTarget(s.export_dir);
  renderUpdate();
  const info = $('#nupinfo');
  if (s.nup) {
    const layers = s.nup.layers.slice(1);  // update sections; their names may be empty
    const upd = layers.length ? ` + update${layers.some(Boolean) ? ` ${layers.filter(Boolean).join(', ')}` : ''}` : '';
    info.replaceChildren(icon('archive'), el('span', { class: 'txt' }, el('b', {}, s.nup.name),
      ` · ${formatSize(s.nup.size)} · ${num(s.nup.file_count)} files · base game${upd}`));
    info.classList.remove('hidden');
  } else info.classList.add('hidden');
  if (s.loading) { loading('Loading file index…'); return; }
  if (!s.nup) { loadedNup = null; showHome(); return; }
  if (loadedNup === s.nup.path && s.labels && !labelsShown) {
    labelsShown = true;  // the Koei Tecmo index finished after the tree was listed
    onLabels();
  }
  if (loadedNup !== s.nup.path) {
    loadedNup = s.nup.path;
    labelsShown = !!s.labels;
    typeCounts = null;
    tree.setRoot('').then(() => {
      const romfs = tree.roots.find((n) => n.path === 'romfs');
      if (romfs) tree.toggle(romfs, true);
    });
    showHome();
  }
  if (s.export) onExport(s.export);
}

function onExport(st) {
  const bar = $('#statusbar');
  if (!st || (!st.running && !st.finished)) { bar.className = ''; bar.replaceChildren(); return; }
  if (st.running) exportDismissed = false;
  if (!st.running && exportDismissed) { bar.className = ''; return; }
  bar.className = 'show';
  const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
  if (st.running) {
    bar.replaceChildren(
      el('span', { class: 'spinner', style: 'position:static;width:18px;height:18px;border-width:2px' }),
      el('span', {}, `Export (${st.mode === 'raw' ? 'raw' : 'converted'}): ${num(st.done)} / ${num(st.total)}`),
      el('div', { class: 'progress' }, el('div', { style: `width:${pct}%` })),
      el('span', { class: 'muted' }, `${num(st.written)} files written${st.errors ? ` · ${st.errors} errors` : ''}`),
      el('span', { class: 'muted cur' }, st.current || ''),
      el('button', { class: 'btn', onclick: () => postJSON('/api/export/cancel') }, icon('stop'), 'Cancel'));
  } else {
    bar.replaceChildren(...[
      el('span', { class: 'ok' }, icon(st.cancelled ? 'stop' : 'check'), st.cancelled ? 'Export cancelled' : 'Export finished'),
      el('span', { class: 'muted cur' }, `${num(st.written)} files in ${(st.elapsed || 0).toFixed(1)} s → ${st.target}`),
      st.errors ? el('span', { class: 'warn', title: (st.error_samples || []).join('\n') }, `${st.errors} errors (see _export_errors.txt)`) : null,
      el('button', { class: 'btn', onclick: () => postJSON('/api/open-folder', { path: st.target }).catch((e) => toast(e.message, true)) }, icon('open'), 'Open folder'),
      el('button', { class: 'btn ghost', title: 'Close', onclick: () => { exportDismissed = true; bar.className = ''; } }, icon('close')),
    ].filter(Boolean));
  }
}

async function exportPaths(paths, mode) {
  try {
    const r = await postJSON('/api/export', { paths, mode });
    if (!r.ok) toast('Export cancelled (no folder chosen)');
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------ viewer header
function typeTile(type) {
  const t = TYPE_INFO[type] ? type : 'other';
  return el('div', { class: `type-tile t-${t}` }, icon(TYPE_INFO[t].icon));
}

function exportLabel(type) {
  if (type === 'video') return 'Export WebM';
  const f = state && state.formats && state.formats[type];
  return f ? `Export ${FORMAT_SHORT[f] || f.toUpperCase()}` : 'Export';
}

function header(meta) {
  currentMeta = meta;
  const chips = [el('span', { class: 'chip' }, meta.type_label || meta.type)];
  if (meta.size !== undefined) chips.push(el('span', { class: 'chip' }, formatSize(meta.size)));
  if (meta.layer_count > 1) {
    chips.push(meta.layer_index === 0 ? el('span', { class: 'chip base' }, 'Base game')
      : el('span', { class: 'chip upd' }, meta.layer ? `Update ${meta.layer}` : 'Update'));
  }
  const copy = el('button', { class: 'btn ghost', title: 'Copy path', onclick: () => { navigator.clipboard.writeText(meta.path); toast('Path copied'); } }, icon('copy'));
  $('#viewhead').replaceChildren(
    typeTile(meta.type),
    el('div', { class: 'head-title' },
      el('div', { class: 'head-name', title: meta.desc || '' }, meta.title || meta.name,
        (meta.title || meta.desc) ? el('span', { class: 'desc' }, meta.title ? meta.name : meta.desc) : null),
      el('div', { class: 'chips' }, ...chips),
      el('div', { class: 'head-path' }, el('code', {}, meta.path), copy)),
    el('div', { class: 'head-actions' },
      el('button', { class: 'btn primary', title: 'Converted export – formats can be changed under “Formats” in the top bar',
        onclick: () => exportPaths([meta.path], 'convert') }, icon('export'), exportLabel(meta.type)),
      el('button', { class: 'btn', title: 'Unpacked original file(s), no conversion', onclick: () => exportPaths([meta.path], 'raw') }, icon('raw'), 'Export raw')));
}

async function select(node) {
  const seq = ++selectSeq;
  let meta;
  try {
    meta = await getJSON('/api/meta', { path: node.path });
  } catch (e) {
    if (seq === selectSeq) { $('#viewhead').replaceChildren(); setViewer((v) => { v.append(el('div', { class: 'empty warn' }, `Error: ${e.message}`)); return { dispose() {} }; }); }
    return;
  }
  if (seq !== selectSeq) return;
  header(meta);
  const ctx = { exportPaths, typeTile };
  setViewer((v) => {
    if (meta.parse_error && !['other', 'dir', 'archive'].includes(meta.type)) {
      v.append(el('div', { class: 'warn small' }, `Note: ${meta.parse_error}`));
    }
    switch (meta.type) {
      case 'dir': case 'archive': return text.showFolder(v, meta, ctx);
      case 'texture': return texture.show(v, meta, ctx);
      case 'audio': return audio.show(v, meta, ctx);
      case 'model': case 'anim': return model.show(v, meta, ctx);
      case 'msbt': return text.showMsbt(v, meta, ctx);
      case 'byml': return text.showByml(v, meta, ctx);
      case 'video': return video.show(v, meta, ctx);
      default: return text.showHex(v, meta, ctx);
    }
  });
}

// ------------------------------------------------------------ context menu
function openMenu(node, ev) {
  const m = $('#ctxmenu');
  const item = (ic, label, fn) => el('div', { class: 'mi', onclick: () => { closeMenu(); fn(); } }, icon(ic), label);
  m.replaceChildren(...[
    el('div', { class: 'mh' }, node.name),
    item('export', node.kind === 'file' && state && state.formats && state.formats[node.type]
      ? `${exportLabel(node.type)} (converted)` : 'Export (converted)', () => exportPaths([node.path], 'convert')),
    item('raw', 'Export raw (unpacked)', () => exportPaths([node.path], 'raw')),
    el('div', { class: 'msep' }),
    item('copy', 'Copy path', () => { navigator.clipboard.writeText(node.path); toast('Path copied'); }),
    node.showPath ? item('reveal', 'Show in tree', () => { $('#search').value = ''; $('#typefilter').value = ''; updateSearchInfo(); tree.reveal(node.path); }) : null,
  ].filter(Boolean));
  m.className = 'menu';
  const x = Math.min(ev.clientX, window.innerWidth - 250);
  const y = Math.min(ev.clientY, window.innerHeight - m.offsetHeight - 10);
  m.style.left = `${x}px`;
  m.style.top = `${y}px`;
}
function closeMenu() { $('#ctxmenu').className = 'menu hidden'; }

// ------------------------------------------------------------ export formats
async function toggleFormats(ev) {
  ev.stopPropagation();
  const m = $('#fmtmenu');
  if (!m.classList.contains('hidden')) { closeFormats(); return; }
  closeAbout();
  try {
    if (!formatOptions) formatOptions = (await getJSON('/api/settings')).format_options;
  } catch (e) { toast(e.message, true); return; }
  const cur = (state && state.formats) || {};
  m.replaceChildren(el('div', { class: 'mh' }, 'Formats for “Export” (converted)'),
    ...FORMAT_TYPES.map(([t, label]) => {
      const sel = el('select', { class: 'sel' },
        ...Object.entries(formatOptions[t] || {}).map(([k, l]) => el('option', { value: k, selected: k === cur[t] }, l)));
      sel.addEventListener('change', () => saveFormat(t, sel.value, label));
      return el('label', { class: 'fmt-row' }, el('span', {}, label), sel);
    }),
    el('div', { class: 'msep' }),
    el('div', { class: 'mh fmt-note' }, '“Export raw” always writes the original files.'));
  const r = $('#btnFormats').getBoundingClientRect();
  m.className = 'menu fmt';
  m.style.top = `${r.bottom + 8}px`;
  m.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - m.offsetWidth - 8))}px`;
}

function closeFormats() { $('#fmtmenu').className = 'menu hidden'; }

async function saveFormat(type, value, label) {
  try {
    const r = await postJSON('/api/settings', { formats: { [type]: value } });
    if (state) state.formats = r.formats;
    if (currentMeta) header(currentMeta);
    toast(`${label}: export as ${FORMAT_SHORT[value] || value}`);
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------ search
let searchCtl = null;
let searchTimer = 0;
function updateSearchInfo(t) { $('#treeinfo').textContent = t || ''; }
async function runSearch() {
  const q = $('#search').value.trim();
  const type = $('#typefilter').value;
  if (searchCtl) searchCtl.abort();
  if (!q && !type) { tree.clearFlat(); updateSearchInfo(); return; }
  searchCtl = new AbortController();
  try {
    const res = await getJSON('/api/search', { q, type, limit: 5000 }, searchCtl.signal);
    tree.setFlat(res.items);
    updateSearchInfo(`${num(res.total)} results${res.total > res.items.length ? ` (first ${num(res.items.length)} shown)` : ''} · right-click → “Show in tree”`);
  } catch (e) { if (e.name !== 'AbortError') updateSearchInfo(`Error: ${e.message}`); }
}

// ------------------------------------------------------------ init
function init() {
  hydrate();
  if (!TOKEN) {
    document.body.replaceChildren(el('div', { class: 'home' }, el('div', { class: 'hero' }, el('img', { src: 'logo.svg', alt: '' }),
      el('h1', {}, 'NUP ', el('span', {}, 'Explorer')), el('p', {}, 'Please start the program with NupExplorer.exe or start.bat.'))));
    return;
  }
  const typeSel = $('#typefilter');
  for (const [v, l] of TYPES) typeSel.append(el('option', { value: v }, l));
  tree = new Tree($('#tree'), {
    load: (p) => getJSON('/api/list', { path: p }).then((r) => r.items),
    onSelect: select,
    onContext: openMenu,
    onOpenInTree: async (n) => {
      $('#search').value = '';
      $('#typefilter').value = '';
      updateSearchInfo();
      await tree.reveal(n.path);
      if (tree.selected?.path === n.path) await tree.toggle(tree.selected, true);
    },
  });
  // debug handle for automated tests: open a file, resolves when a 3D model has finished loading
  window.__nupxOpen = async (path) => { await select({ path }); await window.__nupxModel?.whenLoaded?.(); };
  $('#btnHome').addEventListener('click', showHome);
  $('#btnOpen').addEventListener('click', openNup);
  $('#btnTarget').addEventListener('click', pickTarget);
  $('#btnFormats').addEventListener('click', toggleFormats);
  $('#btnVersion').addEventListener('click', toggleAbout);
  $('#btnUpdate').addEventListener('click', toggleAbout);
  $('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });
  typeSel.addEventListener('change', runSearch);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#ctxmenu')) closeMenu();
    if (!e.target.closest('#fmtmenu')) closeFormats();
    if (!e.target.closest('#aboutmenu')) closeAbout();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeMenu(); closeFormats(); closeAbout(); }
    if (e.ctrlKey && e.key.toLowerCase() === 'f') { e.preventDefault(); $('#search').focus(); $('#search').select(); }
  });
  // resizable sidebar
  const side = $('#sidebar');
  try { const w = Number(localStorage.getItem('nupx-side')); if (w > 150) side.style.width = `${w}px`; } catch { /* ignore */ }
  $('#splitter').addEventListener('pointerdown', (e) => {
    const sp = e.currentTarget;
    sp.setPointerCapture(e.pointerId);
    const move = (ev) => { side.style.width = `${Math.max(220, Math.min(window.innerWidth - 420, ev.clientX - 16))}px`; };
    const up = () => {
      sp.removeEventListener('pointermove', move); sp.removeEventListener('pointerup', up);
      try { localStorage.setItem('nupx-side', parseInt(side.style.width, 10)); } catch { /* ignore */ }
    };
    sp.addEventListener('pointermove', move);
    sp.addEventListener('pointerup', up);
  });
  loading('Connecting…');
  const conn = $('#conn');
  events({
    state: onState,
    export: onExport,
    onerror: () => { conn.classList.add('off'); conn.querySelector('.lbl').textContent = 'Offline'; },
    onopen: () => { conn.classList.remove('off'); conn.querySelector('.lbl').textContent = 'Connected'; },
  });
}

init();
