// Video player (WebM): streamed from the server with range requests, so seeking works in large files.
import { el, formatSize, url } from '../api.js';

export function show(root, meta) {
  const video = el('video', { class: 'video', controls: true, preload: 'metadata', src: url('/api/video', { path: meta.path }) });
  const info = el('div', { class: 'infoline' }, el('span', {}, 'WebM'), el('span', {}, formatSize(meta.size)));
  video.addEventListener('loadedmetadata', () => {
    const d = video.duration;
    info.append(el('span', {}, `${video.videoWidth} × ${video.videoHeight}`),
      el('span', {}, Number.isFinite(d) ? `${Math.floor(d / 60)}:${String(Math.floor(d % 60)).padStart(2, '0')} min` : 'stream'));
  });
  video.addEventListener('error', () => {
    info.append(el('span', { class: 'warn' }, 'This video cannot be played here (codec not supported by the browser) – export it as .webm.'));
  });
  root.append(info, el('div', { class: 'video-stage' }, video));
  return {
    dispose() {
      video.pause();
      video.removeAttribute('src');
      video.load();  // stops the download
    },
  };
}
