// Inline SVG icon set (24 x 24, stroke based), drawn for NUP Explorer.
const NS = 'http://www.w3.org/2000/svg';
const F = 'fill="currentColor" stroke="none"';

const PATHS = {
  dir: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.4l2.1 2.4h7.5A2.5 2.5 0 0 1 21 9.9v7.6a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1.6"/><path d="M5 9v9.4A1.6 1.6 0 0 0 6.6 20h10.8a1.6 1.6 0 0 0 1.6-1.6V9"/><path d="M10 13h4"/>',
  texture: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="9.5" r="1.8"/><path d="m4 17.5 5-4.8 3.2 3 2.8-2.6L20 17.5"/>',
  audio: '<path d="M9 17.5V6.2L19 4v11.3"/><circle cx="6.6" cy="17.5" r="2.4"/><circle cx="16.6" cy="15.3" r="2.4"/>',
  model: '<path d="M12 2.8 20.2 7.4v9.2L12 21.2l-8.2-4.6V7.4z"/><path d="M3.8 7.4 12 12l8.2-4.6M12 12v9.2"/>',
  anim: '<path d="M3 18c3.6 0 4.4-11.5 9-11.5s5.4 11.5 9 11.5"/><path d="m12 3.6 2.2 2.2L12 8 9.8 5.8z" ' + F + '/>',
  msbt: '<path d="M4 6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-6.5L6 20v-4h1a3 3 0 0 1-3-3z" transform="translate(0 .5)"/><path d="M8.5 8.5h7M8.5 12h4.5"/>',
  byml: '<path d="M8.5 4H7.6A2.1 2.1 0 0 0 5.5 6.1v3.3L3.6 12l1.9 2.6v3.3A2.1 2.1 0 0 0 7.6 20h.9"/><path d="M15.5 4h.9a2.1 2.1 0 0 1 2.1 2.1v3.3l1.9 2.6-1.9 2.6v3.3a2.1 2.1 0 0 1-2.1 2.1h-.9"/><circle cx="12" cy="12" r="1.2" ' + F + '/>',
  other: '<path d="M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8z"/><path d="M14 3v5h5"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  open: '<path d="M3 17.5V7.5A2.5 2.5 0 0 1 5.5 5h3.4l2.1 2.4h6A2.5 2.5 0 0 1 19.5 9.9V11"/><path d="M3 17.5 5.6 12a2 2 0 0 1 1.8-1.1H20a1 1 0 0 1 .9 1.4l-2.6 6.2a2 2 0 0 1-1.8 1.2H5.5A2.5 2.5 0 0 1 3 17.5z"/>',
  target: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.4l2.1 2.4h7.5A2.5 2.5 0 0 1 21 9.9v7.6a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M12 10.5v6M9.3 13.9 12 16.6l2.7-2.7"/>',
  export: '<path d="M12 4v10.5M7.6 10.2 12 14.6l4.4-4.4"/><path d="M4.5 16.5v1.8A2.7 2.7 0 0 0 7.2 21h9.6a2.7 2.7 0 0 0 2.7-2.7v-1.8"/>',
  raw: '<path d="M12 3.2 3.5 7.6 12 12l8.5-4.4z"/><path d="m3.5 12 8.5 4.4 8.5-4.4M3.5 16.4 12 20.8l8.5-4.4"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.6"/><path d="M15.5 8.5V6.1A2.6 2.6 0 0 0 12.9 3.5H6.1A2.6 2.6 0 0 0 3.5 6.1v6.8a2.6 2.6 0 0 0 2.6 2.6h2.4"/>',
  reveal: '<path d="M4 6h7M4 12h4M4 18h7"/><path d="M15 6.5v11"/><path d="m12.2 14.8 2.8 2.8 2.8-2.8"/><circle cx="18.8" cy="6.5" r="2.2"/>',
  play: '<path d="M8 5.6v12.8a1 1 0 0 0 1.5.86l10.3-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6z" ' + F + '/>',
  pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.3" ' + F + '/><rect x="13.5" y="5" width="4" height="14" rx="1.3" ' + F + '/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2.6" ' + F + '/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  warn: '<path d="M10.3 4.4 2.9 17.5A2 2 0 0 0 4.6 20.5h14.8a2 2 0 0 0 1.7-3L13.7 4.4a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4.2M12 16.9v.1"/>',
  chevron: '<path d="m9.5 6 6 6-6 6"/>',
  home: '<path d="M4 10.8 12 4l8 6.8V19a1.5 1.5 0 0 1-1.5 1.5h-4V15h-5v5.5h-4A1.5 1.5 0 0 1 4 19z"/>',
  focus: '<path d="M4 9V6.5A2.5 2.5 0 0 1 6.5 4H9M15 4h2.5A2.5 2.5 0 0 1 20 6.5V9M20 15v2.5a2.5 2.5 0 0 1-2.5 2.5H15M9 20H6.5A2.5 2.5 0 0 1 4 17.5V15"/><circle cx="12" cy="12" r="2.4"/>',
  volume: '<path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z"/><path d="M15.5 9.2a4 4 0 0 1 0 5.6M18.2 6.6a7.6 7.6 0 0 1 0 10.8"/>',
  keyboard: '<rect x="2.8" y="6" width="18.4" height="12" rx="2.6"/><path d="M6.5 9.5h.1M10 9.5h.1M13.5 9.5h.1M17 9.5h.1M7.5 14.5h9"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.6 2.6M15.8 15.8l2.6 2.6M18.4 5.6l-2.6 2.6M8.2 15.8l-2.6 2.6"/>',
  video: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9.2 4.8 2.8-4.8 2.8z" ' + F + '/>',
  sliders: '<path d="M4 7h8.5M17.5 7H20M4 17h2.5M11.5 17H20"/><circle cx="15" cy="7" r="2.5"/><circle cx="9" cy="17" r="2.5"/>',
  update: '<circle cx="12" cy="12" r="8.6"/><path d="M12 16.4V7.8M8.3 11.3 12 7.6l3.7 3.7"/>',
  external: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18.5 13.5v4.6a1.9 1.9 0 0 1-1.9 1.9H5.9A1.9 1.9 0 0 1 4 18.1V7.4a1.9 1.9 0 0 1 1.9-1.9h4.6"/>',
};

const cache = new Map();

/** SVG icon element; cls is added to the 'ic' class (e.g. a type tint 't-model'). */
export function icon(name, cls = '') {
  const key = name in PATHS ? name : 'other';
  let tpl = cache.get(key);
  if (!tpl) {
    tpl = document.createElementNS(NS, 'svg');
    tpl.setAttribute('viewBox', '0 0 24 24');
    tpl.setAttribute('aria-hidden', 'true');
    tpl.innerHTML = PATHS[key];
    cache.set(key, tpl);
  }
  const s = tpl.cloneNode(true);
  s.setAttribute('class', `ic ${cls}`.trim());
  return s;
}

/** Replace <span data-icon="name"> placeholders in static markup. */
export function hydrate(root = document) {
  for (const e of root.querySelectorAll('[data-icon]')) e.replaceWith(icon(e.dataset.icon, e.className));
}

/** Icon name and display label per file type. */
export const TYPES = {
  dir: { icon: 'dir', label: 'Folder' },
  archive: { icon: 'archive', label: 'Archive' },
  texture: { icon: 'texture', label: 'Texture' },
  audio: { icon: 'audio', label: 'Audio' },
  model: { icon: 'model', label: '3D model' },
  anim: { icon: 'anim', label: 'Animation' },
  msbt: { icon: 'msbt', label: 'Text (MSBT)' },
  byml: { icon: 'byml', label: 'Data (BYML)' },
  video: { icon: 'video', label: 'Video' },
  other: { icon: 'other', label: 'File' },
};
