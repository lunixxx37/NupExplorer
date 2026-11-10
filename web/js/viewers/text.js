// MSBT text table (with language compare), BYML as YAML, hex view, folder/archive info.
import { el, formatSize, getJSON, url } from '../api.js';
import { icon } from '../icons.js';

export function showMsbt(root, meta) {
  let disposed = false;
  let rows = [];
  const filter = el('input', { class: 'inp', placeholder: 'Filter (label or text)…' });
  const cmpSel = el('select', { class: 'sel', title: 'Compare with another language' },
    el('option', { value: '' }, '— no comparison —'),
    ...(meta.languages || []).filter((l) => l !== meta.lang).map((l) => el('option', { value: l }, l)));
  const count = el('span', { class: 'muted' });
  const table = el('table', { class: 'msbt' });
  const dl = (fmt) => { location.href = url('/api/convert', { path: meta.path, fmt }); };
  root.append(
    el('div', { class: 'toolbar' }, filter, meta.languages?.length ? cmpSel : null, count, el('span', { class: 'sep' }),
      el('button', { class: 'btn', onclick: () => dl('json') }, icon('export'), 'JSON'),
      el('button', { class: 'btn', onclick: () => dl('csv') }, icon('export'), 'CSV')),
    el('div', { class: 'infoline' }, meta.lang ? el('span', {}, `Language ${meta.lang}`) : null, el('span', { class: 'enc' }, '')),
    el('div', { class: 'scroll' }, table));

  function render() {
    const q = filter.value.trim().toLowerCase();
    const cmp = cmpSel.value;
    const shown = q ? rows.filter((r) => r.label.toLowerCase().includes(q) || r.text.toLowerCase().includes(q)
      || (r.compare || '').toLowerCase().includes(q)) : rows;
    count.textContent = `${shown.length} / ${rows.length} entries`;
    const head = el('tr', {}, el('th', {}, 'Label'), el('th', {}, meta.lang || 'Text'), cmp ? el('th', {}, cmp) : null);
    const body = shown.slice(0, 5000).map((r) => el('tr', {},
      el('td', { class: 'lbl' }, r.label), el('td', {}, fmtText(r.text)), cmp ? el('td', {}, fmtText(r.compare || '')) : null));
    table.replaceChildren(el('thead', {}, head), el('tbody', {}, ...body));
  }

  function fmtText(t) {
    const frag = document.createDocumentFragment();
    for (const part of t.split(/(\{[^}]*\})/)) {
      if (!part) continue;
      if (part.startsWith('{') && part.endsWith('}')) frag.append(el('span', { class: 'tag', title: 'Control tag' }, part));
      else frag.append(part);
    }
    return frag;
  }

  async function load() {
    try {
      const res = await getJSON('/api/msbt', { path: meta.path, compare: cmpSel.value });
      if (disposed) return;
      rows = res.rows;
      root.querySelector('.enc').textContent = `Encoding ${res.encoding}`;
      if (res.compare && res.compare_found === false) count.after(el('span', { class: 'warn' }, ` (no ${res.compare} version found)`));
      render();
    } catch (e) { table.replaceChildren(el('tr', {}, el('td', { class: 'warn' }, `Error: ${e.message}`))); }
  }
  let t = 0;
  filter.addEventListener('input', () => { clearTimeout(t); t = setTimeout(render, 150); });
  cmpSel.addEventListener('change', load);
  load();
  return { dispose() { disposed = true; } };
}

export function showByml(root, meta) {
  let disposed = false;
  let text = '';
  const search = el('input', { class: 'inp', placeholder: 'Search…' });
  const onlyCb = el('input', { type: 'checkbox' });
  const pre = el('pre', { class: 'code' }, 'Loading…');
  const count = el('span', { class: 'muted' });
  const dl = (fmt) => { location.href = url('/api/convert', { path: meta.path, fmt }); };
  root.append(el('div', { class: 'toolbar' }, search, el('label', { class: 'check' }, onlyCb, 'Matches only'), count,
    el('span', { class: 'sep' }),
    el('button', { class: 'btn', onclick: () => dl('yaml') }, icon('export'), 'YAML'),
    el('button', { class: 'btn', onclick: () => dl('json') }, icon('export'), 'JSON')),
  el('div', { class: 'infoline' }, el('span', {}, `BYML version ${meta.byml_version ?? '?'}`)),
  el('div', { class: 'scroll' }, pre));

  function render() {
    const q = search.value.trim().toLowerCase();
    if (!q) { pre.textContent = text; count.textContent = ''; return; }
    const lines = text.split('\n');
    const hits = lines.filter((l) => l.toLowerCase().includes(q));
    count.textContent = `${hits.length} matches`;
    if (onlyCb.checked) { pre.textContent = hits.join('\n'); return; }
    const frag = document.createDocumentFragment();
    let first = null;
    for (const l of lines) {
      if (l.toLowerCase().includes(q)) {
        const m = el('mark', {}, l);
        if (!first) first = m;
        frag.append(m, '\n');
      } else frag.append(l + '\n');
    }
    pre.replaceChildren(frag);
    if (first) first.scrollIntoView({ block: 'center' });
  }
  let t = 0;
  search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(render, 200); });
  onlyCb.addEventListener('change', render);
  getJSON('/api/byml', { path: meta.path }).then((res) => { if (!disposed) { text = res.yaml; render(); } })
    .catch((e) => { pre.textContent = `Error: ${e.message}`; });
  return { dispose() { disposed = true; } };
}

export function showHex(root, meta) {
  let offset = 0;
  let size = 0;
  const pre = el('pre', { class: 'code hex' });
  const more = el('button', { class: 'btn' }, 'Load more');
  const info = el('span', { class: 'muted' });
  root.append(el('div', { class: 'toolbar' }, info, el('span', { class: 'sep' }),
    el('span', { class: 'muted' }, 'No preview for this type – hex view of the unpacked data')),
  el('div', { class: 'scroll' }, pre, more));
  async function load() {
    const res = await getJSON('/api/hex', { path: meta.path, offset, length: 8192 });
    size = res.size;
    pre.append(res.lines.join('\n') + '\n');
    offset += 8192;
    info.textContent = `${formatSize(size)} unpacked`;
    more.style.display = offset < size ? '' : 'none';
  }
  more.addEventListener('click', load);
  load().catch((e) => { pre.textContent = `Error: ${e.message}`; });
  return { dispose() {} };
}

export function showFolder(root, meta, ctx) {
  root.append(el('div', { class: 'folder-info' },
    ctx.typeTile ? ctx.typeTile(meta.type === 'archive' ? 'archive' : 'dir') : null,
    el('h3', {}, meta.name),
    el('p', {}, meta.type === 'archive'
      ? `Archive with ${meta.count ?? '?'} files. Expand it in the tree to browse the contents.`
      : `${(meta.count ?? 0).toLocaleString('en-US')} entries`),
    el('p', { class: 'muted' }, meta.type === 'archive'
      ? '“Export” unpacks the archive into a folder of the same name and converts its contents.'
      : '“Export” converts every file inside (textures → PNG, audio → WAV, models → GLB, texts → JSON, data → YAML).'),
    el('div', { class: 'row' },
      el('button', { class: 'btn primary', onclick: () => ctx.exportPaths([meta.path], 'convert') }, icon('export'), 'Export'),
      el('button', { class: 'btn', onclick: () => ctx.exportPaths([meta.path], 'raw') }, icon('raw'), 'Export raw'))));
  return { dispose() {} };
}
