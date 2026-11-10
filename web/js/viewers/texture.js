// Texture viewer: zoom/pan, channel toggles, array layers and mip levels.
import { el, url } from '../api.js';
import { icon } from '../icons.js';

export function show(root, meta) {
  const texs = meta.textures || [];
  if (!texs.length) {
    root.append(el('div', { class: 'empty' }, meta.parse_error || 'No texture found.'));
    return { dispose() {} };
  }
  let ti = 0;
  let layer = 0;
  let mip = 0;
  const ch = { r: true, g: true, b: true, a: true };
  let scale = 1;
  let ox = 0;
  let oy = 0;
  let bg = 'checker';
  let img = null;
  let fitted = false;

  const texSel = el('select', { class: 'sel', title: 'Texture in this file' });
  texs.forEach((t, i) => texSel.append(el('option', { value: i }, t.name)));
  const layerSel = el('select', { class: 'sel', title: 'Array layer / cube face' });
  const mipSel = el('select', { class: 'sel', title: 'Mip level' });
  const chanBtns = ['r', 'g', 'b', 'a'].map((c) => el('button', {
    class: `chan chan-${c} on`, title: `Toggle channel ${c.toUpperCase()}`,
    onclick: (e) => { ch[c] = !ch[c]; e.currentTarget.classList.toggle('on', ch[c]); draw(); },
  }, c.toUpperCase()));
  const zoomLbl = el('span', { class: 'muted zoom' }, '100%');
  const info = el('div', { class: 'infoline' });
  const canvas = el('canvas', { class: 'tex-canvas' });
  const stage = el('div', { class: `tex-stage bg-${bg}` }, canvas);
  const spinner = el('div', { class: 'spinner hidden' });
  stage.append(spinner);
  const bgBtn = el('button', {
    class: 'btn', title: 'Switch background', onclick: () => {
      bg = { checker: 'dark', dark: 'light', light: 'checker' }[bg];
      stage.className = `tex-stage bg-${bg}`;
    },
  }, 'Background');
  const bar = el('div', { class: 'toolbar' },
    texs.length > 1 ? texSel : null, layerSel, mipSel, el('span', { class: 'sep' }), ...chanBtns,
    el('span', { class: 'sep' }),
    el('button', { class: 'btn', onclick: () => fit() }, icon('focus'), 'Fit'),
    el('button', { class: 'btn', onclick: () => { setScale(1, stage.clientWidth / 2, stage.clientHeight / 2); } }, '1:1'),
    bgBtn, zoomLbl);
  root.append(bar, info, stage);

  function fillSelectors() {
    const t = texs[ti];
    layerSel.replaceChildren(...Array.from({ length: t.layers }, (_, i) => el('option', { value: i },
      t.dim.startsWith('Cube') ? `Face ${i}` : `Layer ${i}`)));
    layerSel.style.display = t.layers > 1 ? '' : 'none';
    mipSel.replaceChildren(...Array.from({ length: t.mips }, (_, i) => el('option', { value: i },
      `Mip ${i} (${Math.max(1, t.width >> i)}×${Math.max(1, t.height >> i)})`)));
    mipSel.style.display = t.mips > 1 ? '' : 'none';
    layer = 0; mip = 0;
    info.replaceChildren(...[
      el('span', {}, `${t.format}`), el('span', {}, `${t.width} × ${t.height}`),
      el('span', {}, `${t.mips} Mip${t.mips > 1 ? 's' : ''}`),
      t.layers > 1 ? el('span', {}, `${t.layers} ${t.dim.startsWith('Cube') ? 'faces' : 'layers'}`) : null,
      el('span', {}, `Type ${t.dim}`), el('span', { title: 'Channel mapping (swizzle)' }, `Channels ${t.channels}`),
      t.supported ? null : el('span', { class: 'warn' }, `Format ${t.format_id} not supported`),
    ].filter(Boolean));
  }

  function load() {
    const t = texs[ti];
    if (!t.supported) return;
    spinner.classList.remove('hidden');
    const im = new Image();
    im.onload = () => { img = im; spinner.classList.add('hidden'); draw(); if (!fitted) { fit(); fitted = true; } };
    im.onerror = () => { spinner.classList.add('hidden'); info.append(el('span', { class: 'warn' }, 'Decoding failed')); };
    im.src = url('/api/texture.png', { path: meta.path, tex: ti, layer, mip });
  }

  function draw() {
    if (!img) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0);
    if (!(ch.r && ch.g && ch.b && ch.a)) {
      const id = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = id.data;
      const on = [ch.r, ch.g, ch.b];
      const single = [ch.r, ch.g, ch.b, ch.a].filter(Boolean).length === 1;
      const idx = [ch.r, ch.g, ch.b, ch.a].indexOf(true);
      for (let i = 0; i < d.length; i += 4) {
        if (single) {
          const v = d[i + idx];
          d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255;
        } else {
          for (let k = 0; k < 3; k++) if (!on[k]) d[i + k] = 0;
          if (!ch.a) d[i + 3] = 255;
        }
      }
      ctx.putImageData(id, 0, 0);
    }
    applyTransform();
  }

  function applyTransform() {
    canvas.style.transform = `translate(${ox}px, ${oy}px) scale(${scale})`;
    canvas.style.imageRendering = scale >= 2 ? 'pixelated' : 'auto';
    zoomLbl.textContent = `${Math.round(scale * 100)}%`;
  }

  function setScale(s, cx, cy) {
    s = Math.min(64, Math.max(0.02, s));
    ox = cx - (cx - ox) * (s / scale);
    oy = cy - (cy - oy) * (s / scale);
    scale = s;
    applyTransform();
  }

  function fit() {
    if (!img) return;
    const w = stage.clientWidth - 24;
    const h = stage.clientHeight - 24;
    scale = Math.min(w / img.naturalWidth, h / img.naturalHeight, 4);
    ox = (stage.clientWidth - img.naturalWidth * scale) / 2;
    oy = (stage.clientHeight - img.naturalHeight * scale) / 2;
    applyTransform();
  }

  stage.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    setScale(scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let drag = null;
  stage.addEventListener('pointerdown', (e) => { drag = { x: e.clientX - ox, y: e.clientY - oy }; stage.setPointerCapture(e.pointerId); });
  stage.addEventListener('pointermove', (e) => { if (drag) { ox = e.clientX - drag.x; oy = e.clientY - drag.y; applyTransform(); } });
  stage.addEventListener('pointerup', () => { drag = null; });
  stage.addEventListener('dblclick', () => fit());

  texSel.addEventListener('change', () => { ti = Number(texSel.value); fitted = false; fillSelectors(); load(); });
  layerSel.addEventListener('change', () => { layer = Number(layerSel.value); load(); });
  mipSel.addEventListener('change', () => { mip = Number(mipSel.value); fitted = false; load(); });

  fillSelectors();
  load();
  return { dispose() { img = null; } };
}
