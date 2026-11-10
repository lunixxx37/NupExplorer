// App shell: state via server events, tree + search, viewer switching, export, context menu.
import { el, events, formatSize, getJSON, postJSON, TOKEN } from './api.js';
import { hydrate, icon, TYPES as TYPE_INFO } from './icons.js';
import { Tree } from './tree.js';
import * as audio from './viewers/audio.js';
import * as model from './viewers/model.js';
import * as text from './viewers/text.js';
import * as texture from './viewers/texture.js';

const $ = (s) => document.querySelector(s);
const num = (n) => Number(n || 0).toLocaleString('en-US');
let tree = null;
let viewer = null;
let selectSeq = 0;
let loadedNup = null;
let exportDismissed = false;
let state = null;
let typeCounts = null;

const TYPES = [['', 'All types'], ['texture', 'Textures'], ['audio', 'Audio'], ['model', '3D models'],
  ['anim', 'Animations'], ['msbt', 'Texts (MSBT)'], ['byml', 'Data (BYML)'], ['archive', 'Archives'], ['other', 'Other']];
const TILES = [['texture', 'Textures'], ['model', '3D models'], ['audio', 'Music & sound'], ['anim', 'Animations'],
  ['msbt', 'Texts'], ['byml', 'Game data'], ['archive', 'Archives'], ['other', 'Other files']];

function toast(msg, isErr) {
  const t = $('#toast');
  t.replaceChildren(icon(isErr ? 'warn' : 'check'), el('span', {}, msg));
  t.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.className = ''; }, 4000);
}

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
      opts.button ? el('button', { class: 'btn primary', onclick: openNup }, icon('open'), 'Open NUP file…') : null);
    const page = el('div', { class: 'home' }, hero);
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
  const info = $('#nupinfo');
  if (s.nup) {
    const layers = s.nup.layers.slice(1);
    info.replaceChildren(icon('archive'), el('span', { class: 'txt' }, el('b', {}, s.nup.name),
      ` · ${formatSize(s.nup.size)} · ${num(s.nup.file_count)} files · base game${layers.length ? ` + update ${layers.join(', ')}` : ''}`));
    info.classList.remove('hidden');
  } else info.classList.add('hidden');
  if (s.loading) { loading('Loading file index…'); return; }
  if (!s.nup) { loadedNup = null; showHome(); return; }
  if (loadedNup !== s.nup.path) {
    loadedNup = s.nup.path;
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

function header(meta) {
  const chips = [el('span', { class: 'chip' }, meta.type_label || meta.type)];
  if (meta.size !== undefined) chips.push(el('span', { class: 'chip' }, formatSize(meta.size)));
  if (meta.layer) chips.push(meta.layer === 'base' ? el('span', { class: 'chip base' }, 'Base game') : el('span', { class: 'chip upd' }, `Update ${meta.layer}`));
  const copy = el('button', { class: 'btn ghost', title: 'Copy path', onclick: () => { navigator.clipboard.writeText(meta.path); toast('Path copied'); } }, icon('copy'));
  $('#viewhead').replaceChildren(
    typeTile(meta.type),
    el('div', { class: 'head-title' }, el('div', { class: 'head-name' }, meta.name), el('div', { class: 'chips' }, ...chips),
      el('div', { class: 'head-path' }, el('code', {}, meta.path), copy)),
    el('div', { class: 'head-actions' },
      el('button', { class: 'btn primary', onclick: () => exportPaths([meta.path], 'convert') }, icon('export'), 'Export'),
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
    item('export', 'Export (converted)', () => exportPaths([node.path], 'convert')),
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
  });
  // debug handle for automated tests: open a file, resolves when a 3D model has finished loading
  window.__nupxOpen = async (path) => { await select({ path }); await window.__nupxModel?.whenLoaded?.(); };
  $('#btnHome').addEventListener('click', showHome);
  $('#btnOpen').addEventListener('click', openNup);
  $('#btnTarget').addEventListener('click', pickTarget);
  $('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });
  typeSel.addEventListener('change', runSearch);
  document.addEventListener('click', (e) => { if (!e.target.closest('#ctxmenu')) closeMenu(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
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
