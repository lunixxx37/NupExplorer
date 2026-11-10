// Audio player (WebAudio): waveform, seeking, loop points from the BWAV header.
import { el, formatTime, getBinary } from '../api.js';
import { icon } from '../icons.js';

export function show(root, meta) {
  const a = meta.audio;
  if (!a) {
    root.append(el('div', { class: 'empty' }, meta.parse_error || 'No audio data.'));
    return { dispose() {} };
  }
  let ctx = null;
  let buffer = null;
  let src = null;
  let gain = null;
  let playing = false;
  let startedAt = 0;   // ctx time when playback (re)started
  let startOffset = 0; // seconds into the buffer at startedAt
  let raf = 0;
  let disposed = false;
  const hasLoop = a.loop && a.loop_end > a.loop_start;
  const loopStart = hasLoop ? a.loop_start / a.rate : 0;
  const loopEnd = hasLoop ? a.loop_end / a.rate : a.duration;

  const playBtn = el('button', { class: 'btn play', disabled: true, title: 'Play / pause (space)' }, icon('play'));
  const stopBtn = el('button', { class: 'btn round', disabled: true, title: 'Stop' }, icon('stop'));
  const time = el('span', { class: 'time' }, `0:00 / ${formatTime(a.duration)}`);
  const loopCb = el('input', { type: 'checkbox', checked: hasLoop });
  const vol = el('input', { type: 'range', min: 0, max: 1, step: 0.01, value: 0.8, class: 'vol', title: 'Volume' });
  const canvas = el('canvas', { class: 'wave' });
  const status = el('div', { class: 'muted' }, 'Loading and decoding…');
  root.append(
    el('div', { class: 'infoline' },
      el('span', {}, a.codec), el('span', {}, `${a.rate} Hz`),
      el('span', {}, a.channels === 1 ? 'Mono' : a.channels === 2 ? 'Stereo' : `${a.channels} channels`),
      el('span', {}, formatTime(a.duration)),
      hasLoop ? el('span', { title: 'Loop points from the file' }, `Loop ${formatTime(loopStart)} – ${formatTime(loopEnd)}`) : el('span', { class: 'muted' }, 'no loop points'),
      a.channels > 2 ? el('span', { class: 'muted' }, 'Preview as stereo mix, export keeps all channels') : null),
    el('div', { class: 'player' }, playBtn, stopBtn, time,
      el('label', { class: 'check', title: hasLoop ? 'Repeat using the loop points' : 'Repeat the whole file' }, loopCb, hasLoop ? 'Loop' : 'Repeat'),
      el('span', { class: 'vol-ic' }, icon('volume')), vol),
    canvas, status);

  function position() {
    if (!buffer) return 0;
    if (!playing) return startOffset;
    let p = startOffset + (ctx.currentTime - startedAt);
    if (src && src.loop) {
      const ls = src.loopStart || 0;
      const le = src.loopEnd || buffer.duration;
      if (p > le) p = ls + ((p - ls) % (le - ls));
    }
    return Math.min(p, buffer.duration);
  }

  function play(from = position()) {
    if (!buffer) return;
    stopSource();
    if (ctx.state === 'suspended') ctx.resume();
    src = ctx.createBufferSource();
    src.buffer = buffer;
    src.loop = loopCb.checked;
    if (hasLoop && loopCb.checked) { src.loopStart = loopStart; src.loopEnd = Math.min(loopEnd, buffer.duration); }
    src.connect(gain);
    src.onended = () => { if (src && !src.loop && playing) { playing = false; startOffset = 0; ui(); } };
    if (from >= buffer.duration) from = 0;
    src.start(0, from);
    startedAt = ctx.currentTime;
    startOffset = from;
    playing = true;
    ui();
    tick();
  }

  function stopSource() {
    if (src) { src.onended = null; try { src.stop(); } catch { /* not started */ } src.disconnect(); src = null; }
  }

  function pause() { startOffset = position(); playing = false; stopSource(); ui(); }
  function stop() { playing = false; stopSource(); startOffset = 0; ui(); drawWave(); }
  function ui() { playBtn.replaceChildren(icon(playing ? 'pause' : 'play')); time.textContent = `${formatTime(position())} / ${formatTime(a.duration)}`; }

  let peaks = null;
  function computePeaks() {
    const w = Math.max(200, canvas.clientWidth * devicePixelRatio | 0);
    const chs = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) chs.push(buffer.getChannelData(c));
    const n = buffer.length;
    const step = n / w;
    peaks = new Float32Array(w * 2);
    for (let x = 0; x < w; x++) {
      let mn = 1, mx = -1;
      const s = Math.floor(x * step), e = Math.min(n, Math.floor((x + 1) * step));
      for (const d of chs) for (let i = s; i < e; i += 1) { const v = d[i]; if (v < mn) mn = v; if (v > mx) mx = v; }
      peaks[2 * x] = mn; peaks[2 * x + 1] = mx;
    }
  }

  function drawWave() {
    const w = canvas.width = Math.max(200, canvas.clientWidth * devicePixelRatio | 0);
    const h = canvas.height = Math.max(80, canvas.clientHeight * devicePixelRatio | 0);
    const g = canvas.getContext('2d');
    const css = getComputedStyle(document.documentElement);
    g.fillStyle = css.getPropertyValue('--wave-bg').trim() || '#111';
    g.fillRect(0, 0, w, h);
    if (!peaks || peaks.length !== w * 2) { if (buffer) computePeaks(); else return; }
    if (hasLoop) {
      g.fillStyle = css.getPropertyValue('--loop-bg').trim() || 'rgba(80,140,255,.15)';
      const x0 = loopStart / buffer.duration * w, x1 = loopEnd / buffer.duration * w;
      g.fillRect(x0, 0, x1 - x0, h);
    }
    g.fillStyle = css.getPropertyValue('--wave').trim() || '#6cf';
    const mid = h / 2;
    for (let x = 0; x < w; x++) {
      const y0 = mid - peaks[2 * x + 1] * mid * 0.95, y1 = mid - peaks[2 * x] * mid * 0.95;
      g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
    }
    const px = position() / buffer.duration * w;
    g.fillStyle = css.getPropertyValue('--playhead').trim() || '#fff';
    g.fillRect(px - 1, 0, 2, h);
  }

  function tick() {
    cancelAnimationFrame(raf);
    const loop = () => {
      if (disposed) return;
      drawWave(); ui();
      if (playing) raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  }

  canvas.addEventListener('pointerdown', (e) => {
    if (!buffer) return;
    const r = canvas.getBoundingClientRect();
    const t = (e.clientX - r.left) / r.width * buffer.duration;
    if (playing) play(t); else { startOffset = t; drawWave(); ui(); }
  });
  playBtn.addEventListener('click', () => (playing ? pause() : play()));
  stopBtn.addEventListener('click', stop);
  loopCb.addEventListener('change', () => { if (playing) play(position()); });
  vol.addEventListener('input', () => { if (gain) gain.gain.value = Number(vol.value); });
  const onKey = (e) => {
    if (e.code === 'Space' && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLSelectElement)) {
      e.preventDefault(); playing ? pause() : play();
    }
  };
  document.addEventListener('keydown', onKey);
  const ro = new ResizeObserver(() => { peaks = null; if (buffer) drawWave(); });
  ro.observe(canvas);

  (async () => {
    try {
      const data = await getBinary('/api/audio.wav', { path: meta.path, mix: a.channels > 2 ? 'stereo' : '' });
      if (disposed) return;
      ctx = new AudioContext({ sampleRate: a.rate || 48000 });
      gain = ctx.createGain();
      gain.gain.value = Number(vol.value);
      gain.connect(ctx.destination);
      buffer = await ctx.decodeAudioData(data);
      status.textContent = '';
      playBtn.disabled = false; stopBtn.disabled = false;
      drawWave(); ui();
    } catch (e) {
      status.textContent = `Error: ${e.message}`;
      status.className = 'warn';
    }
  })();

  return {
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      stopSource();
      document.removeEventListener('keydown', onKey);
      ro.disconnect();
      if (ctx) ctx.close();
    },
  };
}
