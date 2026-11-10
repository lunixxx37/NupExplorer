// API helpers: token handling, JSON/binary fetch, URL building, server events.
const params = new URLSearchParams(location.search);
let token = params.get('t');
try {
  if (token) sessionStorage.setItem('nupx-token', token);
  else token = sessionStorage.getItem('nupx-token');
} catch { /* storage may be unavailable */ }
if (params.has('t')) history.replaceState(null, '', location.pathname);

export const TOKEN = token || '';

export function url(path, query = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
  q.set('t', TOKEN);
  return `${path}?${q.toString()}`;
}

async function check(r) {
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j.error) msg = j.error; } catch { /* not json */ }
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return r;
}

export async function getJSON(path, query = {}, signal) {
  const r = await fetch(url(path, query), { headers: { 'X-Token': TOKEN }, signal });
  return (await check(r)).json();
}

export async function getBinary(path, query = {}, signal) {
  const r = await fetch(url(path, query), { headers: { 'X-Token': TOKEN }, signal });
  return (await check(r)).arrayBuffer();
}

export async function postJSON(path, body = {}) {
  const r = await fetch(url(path), {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN }, body: JSON.stringify(body),
  });
  return (await check(r)).json();
}

export function events(handlers) {
  const es = new EventSource(url('/api/events'));
  for (const [name, fn] of Object.entries(handlers)) {
    es.addEventListener(name, (ev) => { try { fn(JSON.parse(ev.data)); } catch (e) { console.error(e); } });
  }
  es.onerror = () => handlers.onerror && handlers.onerror();
  es.onopen = () => handlers.onopen && handlers.onopen();
  return es;
}

export function formatSize(n) {
  if (n === undefined || n === null) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return i === 0 ? `${n} B` : `${n.toFixed(n < 10 ? 2 : 1)} ${u[i]}`;
}

export function formatTime(s) {
  if (!isFinite(s)) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}
