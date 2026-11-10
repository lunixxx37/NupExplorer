// 3D model viewer (three.js) with mesh/LOD controls and skeletal animation playback.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { el, formatSize, getBinary, getJSON, postJSON } from '../api.js';
import { icon } from '../icons.js';

let renderer = null;
function getRenderer() {
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.NeutralToneMapping;
  }
  return renderer;
}

function baseName(p) { return p.split('::').pop().split('/').pop(); }

function imageHasAlpha(image) {
  try {
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(image, 0, 0, 64, 64);
    const d = g.getImageData(0, 0, 64, 64).data;
    for (let i = 3; i < d.length; i += 4) if (d[i] < 250) return true;
  } catch { /* tainted or unsupported */ }
  return false;
}

// Textures that failed to load (e.g. a connection dropped while dozens of textures load at once,
// net::ERR_NO_BUFFER_SPACE on Windows) would leave the material white: retry them once.
async function retryFailedTextures(gltf) {
  const json = gltf.parser.json;
  const jobs = [];
  gltf.parser.associations.forEach((ref, obj) => {
    if (!obj || !obj.isMaterial || !ref || ref.materials === undefined) return;
    const def = json.materials?.[ref.materials] || {};
    const slots = [['map', def.pbrMetallicRoughness?.baseColorTexture, THREE.SRGBColorSpace],
      ['normalMap', def.normalTexture, THREE.NoColorSpace]];
    for (const [key, info, colorSpace] of slots) {
      if (!info || obj[key]) continue;
      const uri = json.images?.[json.textures?.[info.index]?.source]?.uri;
      if (!uri) continue;
      jobs.push(new THREE.TextureLoader().loadAsync(uri).then((t) => {
        t.flipY = false; t.colorSpace = colorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping;
        obj[key] = t; obj.needsUpdate = true;
      }).catch(() => { /* stays without texture */ }));
    }
  });
  await Promise.all(jobs);
}

function disposeObject(root) {
  root.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    for (const m of mats) {
      for (const k of ['map', 'normalMap', 'aoMap', 'emissiveMap', 'roughnessMap', 'metalnessMap', 'alphaMap']) if (m[k]) m[k].dispose();
      if (m.userData.origMap) m.userData.origMap.dispose();
      if (m.userData.origNormal) m.userData.origNormal.dispose();
      m.dispose();
    }
    if (o.skeleton) o.skeleton.dispose();
  });
}

export function show(root, meta, ctx = {}) {
  const isAnimFile = meta.type === 'anim';
  let modelPath = isAnimFile ? (ctx.modelPath || (meta.model_suggestions || [])[0] || null) : meta.path;
  let animPath = isAnimFile ? meta.path : (ctx.animPath || null);
  let disposed = false;
  let gltf = null;
  let modelRoot = null;
  let skelHelper = null;
  let grid = null;
  let mixer = null;
  let action = null;
  let clipDuration = 0;
  let playing = false;
  let lod = 0;
  let boneByName = new Map();
  let framed = false;
  let shapeStates = [];       // per mesh: default visibility (bone flag), animation track, user override
  let animVis = null;         // {boneName: [[frame, visible], ...]} of the playing clip
  let animVisFps = 30;
  let lastVisFrame = -1;
  let trackObj = null;        // bone the camera follows during animations (usually Skl_Root)
  let lastTrack = null;
  let recenter = false;
  const tmpV = new THREE.Vector3();
  const clock = new THREE.Clock();

  // ------------------------------------------------------------ layout
  const r = getRenderer();
  const stage = el('div', { class: 'model-stage' });
  const overlay = el('div', { class: 'overlay' }, '');
  stage.append(r.domElement, overlay);
  const panel = el('aside', { class: 'model-panel' });
  root.append(el('div', { class: 'model-wrap' }, stage, panel));

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1014);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
  camera.position.set(3, 2, 4);
  scene.add(camera);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x323844, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(2, 3, 4);
  camera.add(sun);
  const controls = new OrbitControls(camera, r.domElement);
  controls.enableDamping = true;
  // debug handle for automated tests (read-only use)
  let loadDone = false;
  let loadPromise = Promise.resolve();
  window.__nupxModel = {
    scene, camera, controls, renderer: r, get root() { return modelRoot; }, get track() { return trackObj; },
    get loaded() { return loadDone; }, get overlay() { return overlay.textContent; }, whenLoaded: () => loadPromise,
  };

  // ------------------------------------------------------------ panel controls
  const cbWire = el('input', { type: 'checkbox' });
  const cbTex = el('input', { type: 'checkbox', checked: true });
  const cbNrm = el('input', { type: 'checkbox', checked: true });
  const cbSkel = el('input', { type: 'checkbox' });
  const cbGrid = el('input', { type: 'checkbox', checked: true });
  const cbLight = el('input', { type: 'checkbox' });
  const lodSel = el('select', { class: 'sel' });
  const meshList = el('div', { class: 'mesh-list' });
  const infoBox = el('div', { class: 'small' });

  const animSel = el('select', { class: 'sel wide' });
  const animSearch = el('input', { class: 'inp wide', placeholder: 'Search other animation files…', list: 'anim-all' });
  const animList = el('datalist', { id: 'anim-all' });
  const clipSel = el('select', { class: 'sel wide clips', size: 7 });
  const playBtn = el('button', { class: 'btn round', disabled: true, title: 'Play / pause' }, icon('play'));
  const stopBtn = el('button', { class: 'btn round', disabled: true, title: 'Stop' }, icon('stop'));
  const slider = el('input', { type: 'range', min: 0, max: 1, step: 0.001, value: 0, class: 'wide', disabled: true });
  const tLabel = el('span', { class: 'muted small' }, '');
  const speedSel = el('select', { class: 'sel' }, ...[0.25, 0.5, 1, 1.5, 2].map((s) => el('option', { value: s, selected: s === 1 }, `${s}×`)));
  const cbLoop = el('input', { type: 'checkbox', checked: true });
  const cbFollow = el('input', { type: 'checkbox', checked: true });
  const animMsg = el('div', { class: 'small muted' });
  const expMsg = el('div', { class: 'small muted' });
  const expClipBtn = el('button', { class: 'btn', disabled: true, title: 'Model + selected animation as .glb into the export folder' }, icon('export'), 'GLB + clip');
  const expAllBtn = el('button', { class: 'btn', disabled: true, title: 'Model + all animations of the file as .glb into the export folder' }, icon('export'), 'GLB + all clips');

  const modelSel = el('select', { class: 'sel wide' });
  const modelSearch = el('input', { class: 'inp wide', placeholder: 'Search model…', list: 'model-all' });
  const modelList = el('datalist', { id: 'model-all' });

  if (isAnimFile) panel.append(el('section', {}, el('h4', {}, 'Model'), modelSel, modelSearch, modelList));
  panel.append(
    el('section', {}, el('h4', {}, 'View'),
      el('label', { class: 'check' }, cbTex, 'Textures'), el('label', { class: 'check' }, cbNrm, 'Normal maps'),
      el('label', { class: 'check' }, cbWire, 'Wireframe'), el('label', { class: 'check' }, cbSkel, 'Skeleton'),
      el('label', { class: 'check' }, cbGrid, 'Grid'), el('label', { class: 'check' }, cbLight, 'Light background'),
      el('button', { class: 'btn', onclick: () => frame() }, icon('focus'), 'Reset camera')),
    el('section', {}, el('h4', {}, 'Meshes'), el('div', { class: 'row' }, el('span', { class: 'small' }, 'Detail level (LOD)'), lodSel), meshList),
    el('section', {}, el('h4', {}, 'Animation'), animSel, animSearch, animList, clipSel,
      el('div', { class: 'row' }, playBtn, stopBtn, speedSel, el('label', { class: 'check' }, cbLoop, 'Loop')),
      el('label', { class: 'check small', title: 'Cutscenes are often recorded far away from the origin – the camera follows the model' },
        cbFollow, 'Camera follows the model'),
      slider, tLabel, animMsg,
      el('div', { class: 'row' }, expClipBtn, expAllBtn), expMsg),
    el('section', {}, el('h4', {}, 'Info'), infoBox),
  );

  // ------------------------------------------------------------ helpers
  function setOverlay(t, isErr) { overlay.textContent = t || ''; overlay.className = 'overlay' + (t ? ' show' : '') + (isErr ? ' err' : ''); }

  function resize() {
    const w = stage.clientWidth || 1, h = stage.clientHeight || 1;
    r.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(stage);

  function frame() {
    if (!modelRoot) return;
    // only what is shown: hidden variants and shadow proxies can be far larger than the visible model
    modelRoot.updateMatrixWorld(true);
    const box = new THREE.Box3();
    modelRoot.traverseVisible((o) => {
      if (!o.isMesh || !o.geometry) return;
      if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld));
    });
    if (box.isEmpty()) box.setFromObject(modelRoot);
    if (box.isEmpty()) for (const b of boneByName.values()) box.expandByPoint(b.getWorldPosition(new THREE.Vector3()));
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    if (action && trackObj) {
      // animated: the bind-pose box may be far away from where the model currently is
      const bindTrack = trackObj.userData.bindWorld;
      trackObj.getWorldPosition(tmpV);
      if (bindTrack) center.add(tmpV.clone().sub(bindTrack));
    }
    const d = Math.max(size.length(), 0.01);
    camera.near = d / 1000;
    camera.far = d * 200;
    // fit the bounding sphere into the narrower of the two fields of view
    const vfov = THREE.MathUtils.degToRad(camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = (d / 2) / Math.sin(Math.min(vfov, hfov) / 2) * 1.05;
    camera.position.copy(center).add(new THREE.Vector3(0.6, 0.35, 1).normalize().multiplyScalar(dist));
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
    if (grid) scene.remove(grid);
    const gs = Math.pow(10, Math.ceil(Math.log10(Math.max(size.x, size.z) * 1.5 || 1)));
    grid = new THREE.GridHelper(gs, 20, 0x1f7f9e, 0x262c36);
    grid.position.y = box.min.y;
    grid.visible = cbGrid.checked;
    scene.add(grid);
  }

  function applyView() {
    if (!modelRoot) return;
    modelRoot.traverse((o) => {
      if (!o.material) return;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        if (m.userData.origMap === undefined) m.userData.origMap = m.map || null;
        if (m.userData.origNormal === undefined) m.userData.origNormal = m.normalMap || null;
        m.map = cbTex.checked ? m.userData.origMap : null;
        m.normalMap = cbNrm.checked ? m.userData.origNormal : null;
        m.wireframe = cbWire.checked;
        m.needsUpdate = true;
      }
    });
    if (skelHelper) skelHelper.visible = cbSkel.checked;
    if (grid) grid.visible = cbGrid.checked;
    scene.background = new THREE.Color(cbLight.checked ? 0xdfe4ea : 0x0d1014);
  }
  for (const cb of [cbWire, cbTex, cbNrm, cbSkel, cbGrid, cbLight]) cb.addEventListener('change', applyView);

  function visAt(seq, f) {
    let v = seq[0][1];
    for (const [fr, val] of seq) { if (fr <= f) v = val; else break; }
    return v;
  }

  function applyVisibility(force) {
    const f = animVis && action ? Math.round(action.time * animVisFps) : 0;
    if (!force && f === lastVisFrame) return;
    lastVisFrame = f;
    for (const st of shapeStates) {
      let v = st.def;
      if (!st.shadowOnly && animVis && action && st.visBone && animVis[st.visBone]) v = !!visAt(animVis[st.visBone], f);
      if (st.override !== null) v = st.override;
      if (st.obj) st.obj.visible = v;
      if (st.cb) st.cb.checked = v;
    }
  }

  function clearModel() {
    stopAnim(true);
    shapeStates = [];
    if (modelRoot) { scene.remove(modelRoot); disposeObject(modelRoot); modelRoot = null; }
    if (skelHelper) { scene.remove(skelHelper); skelHelper.dispose?.(); skelHelper = null; }
    gltf = null;
    mixer = null;
  }

  function loadModel() {
    loadPromise = loadModelImpl();
    return loadPromise;
  }

  async function loadModelImpl() {
    clearModel();
    meshList.replaceChildren();
    infoBox.replaceChildren();
    if (!modelPath) { setOverlay(isAnimFile ? 'Choose a model on the right to play the animation.' : 'No model.'); loadDone = true; return; }
    loadDone = false;
    setOverlay('Loading model…');
    try {
      const buf = await getBinary('/api/model.glb', { path: modelPath, lod });
      if (disposed) return;
      const loader = new GLTFLoader();
      gltf = await loader.parseAsync(buf, location.origin + '/');
      if (disposed) return;
      await retryFailedTextures(gltf);
      if (disposed) return;
      modelRoot = gltf.scene;
      scene.add(modelRoot);
      const ex = gltf.parser.json.scenes?.[0]?.extras || {};
      const nodeObj = [];
      gltf.parser.associations.forEach((v, k) => { if (k && k.isObject3D && v && v.nodes !== undefined) nodeObj[v.nodes] = k; });
      boneByName = new Map();
      const m0 = (ex.models || [])[0];
      if (m0) (ex.bones || []).forEach((name, i) => { const o = nodeObj[m0.bone_nodes[i]]; if (o) boneByName.set(name, o); });
      // alpha-tested materials (hair, lashes, leaves): detect from the base colour texture
      modelRoot.traverse((o) => {
        // skinned meshes are culled by their bind-pose bounds, which animations can move far away from
        if (o.isSkinnedMesh) o.frustumCulled = false;
        if (!o.material) return;
        for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
          // only guess from the texture when the material carries no render state (opaque/mask/translucent)
          if (m.userData.render_mode == null && m.map && m.map.image && imageHasAlpha(m.map.image)) {
            m.alphaTest = 0.5; m.needsUpdate = true;
          }
        }
      });
      const bones = [...boneByName.values()];
      const tops = bones.filter((b) => !b.parent || !b.parent.isBone);
      trackObj = boneByName.get('Skl_Root') || bones.find((b) => b.parent && tops.includes(b.parent)) || tops[0] || null;
      if (trackObj) { modelRoot.updateMatrixWorld(true); trackObj.userData.bindWorld = trackObj.getWorldPosition(new THREE.Vector3()); }
      lastTrack = null;
      if (boneByName.size) {
        skelHelper = new THREE.SkeletonHelper(modelRoot);
        skelHelper.material.depthTest = false;
        skelHelper.renderOrder = 10;
        scene.add(skelHelper);
      }
      // mesh list; visibility = user override ?? animation track ?? model default (bone flag)
      let tris = 0;
      let verts = 0;
      shapeStates = [];
      const allShapes = (ex.models || []).flatMap((m) => m.shapes);
      const useDefaults = allShapes.some((s) => s.visible !== false); // never hide everything
      for (const m of ex.models || []) {
        if ((ex.models || []).length > 1) meshList.append(el('div', { class: 'small muted' }, m.name));
        for (const s of m.shapes) {
          tris += s.triangles; verts += s.vertices;
          const st = { obj: nodeObj[s.node], visBone: s.vis_bone, def: useDefaults ? s.visible !== false : true,
            shadowOnly: useDefaults && !!s.shadow_only, override: null, cb: null };
          const cb = el('input', { type: 'checkbox', onchange: () => { st.override = cb.checked; applyVisibility(true); } });
          st.cb = cb;
          shapeStates.push(st);
          const why = st.shadowOnly ? ' · invisible in the game (only casts shadows)'
            : st.def ? '' : ' · hidden by default (e.g. another form or variant)';
          meshList.append(el('label', {
            class: 'check mesh' + (st.def ? '' : ' hidden-default'),
            title: `${s.material} · ${s.triangles} triangles${why}`,
          }, cb, s.name));
        }
      }
      applyVisibility(true);
      const lc = Math.max(1, ...(ex.models || []).map((m) => m.lod_count || 1));
      lodSel.replaceChildren(...Array.from({ length: lc }, (_, i) => el('option', { value: i, selected: i === lod }, `${i}`)));
      lodSel.disabled = lc <= 1;
      const mats = gltf.parser.json.materials || [];
      infoBox.replaceChildren(
        el('div', {}, `${(ex.models || []).map((m) => m.name).join(', ') || baseName(modelPath)}`),
        el('div', { class: 'info-big' }, `${tris.toLocaleString('en-US')} triangles · ${verts.toLocaleString('en-US')} vertices · ${boneByName.size} bones`),
        el('details', {}, el('summary', {}, `${mats.length} materials`),
          ...mats.map((mt) => el('div', { class: 'mat' }, el('b', {}, mt.name), el('div', { class: 'muted' }, (mt.extras?.textures || []).join(', '))))),
      );
      applyView();
      if (!framed) { frame(); framed = true; }
      setOverlay(ex.models && ex.models.length ? '' : 'This file contains no model.');
      if (ex.models && ex.models.length && !ex.models.some((m) => m.shapes.length)) {
        const errs = ex.models.flatMap((m) => m.cluster_errors || []);
        if (errs.length) {
          setOverlay(`The compressed geometry of this model could not be decoded (${errs[0]}). Raw export is still possible.`, true);
        } else if (boneByName.size) {  // skeleton files: show the bones
          infoBox.prepend(el('div', { class: 'warn' }, 'Skeleton only, no meshes.'));
          cbSkel.checked = true;
          applyView();
          frame();
        } else {
          setOverlay('This file contains no displayable model.');
        }
      }
      mixer = new THREE.AnimationMixer(modelRoot);
      updateExportButtons();
      if (animPath && clipSel.value !== '') playClip(Number(clipSel.value));
    } catch (e) {
      setOverlay(`Error: ${e.message}`, true);
    }
    loadDone = true;
  }

  // ------------------------------------------------------------ animations
  async function loadAnimList() {
    try {
      const sug = await getJSON('/api/anim/suggest', { model: modelPath || '' });
      const sugList = isAnimFile ? [meta.path] : sug.suggested;
      animSel.replaceChildren(el('option', { value: '' }, sugList.length ? '— choose an animation file —' : '— no matching file found —'),
        ...sugList.map((p) => el('option', { value: p, selected: p === animPath }, baseName(p))));
      animList.replaceChildren(...sug.all.map((p) => el('option', { value: baseName(p) })));
      animList._map = new Map(sug.all.map((p) => [baseName(p), p]));
      if (!animPath && sug.suggested.length) { animPath = sug.suggested[0]; animSel.value = animPath; }
      if (animPath) await loadClips();
    } catch (e) { animMsg.textContent = `Error: ${e.message}`; }
  }

  async function loadClips() {
    clipSel.replaceChildren();
    animMsg.textContent = '';
    if (!animPath) return;
    const res = await getJSON('/api/anim/clips', { path: animPath });
    clipSel.replaceChildren(...res.clips.map((c) => el('option', { value: c.index, title: c.supported ? '' : 'Rotation mode not supported' },
      `${c.name}  (${c.frames} F${c.loop ? ', Loop' : ''})${c.supported ? '' : ' ⚠'}`)));
    animMsg.textContent = `${res.clips.length} animations in ${baseName(animPath)}`;
    updateExportButtons();
  }

  async function playClip(idx) {
    if (!modelRoot || !mixer) { animMsg.textContent = 'Load a model first.'; return; }
    stopAnim(false);
    animMsg.textContent = 'Loading animation…';
    try {
      const buf = await getBinary('/api/anim/clip.bin', { path: animPath, clip: idx, model: modelPath });
      if (disposed) return;
      const hlen = new DataView(buf).getUint32(0, true);
      const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
      const data = new Float32Array(buf, 4 + hlen);
      const nf = header.frames;
      const fps = header.fps || 30;
      const times = Float32Array.from({ length: nf }, (_, i) => i / fps);
      const tracks = [];
      let missing = 0;
      header.bones.forEach((b, bi) => {
        const o = boneByName.get(b.name);
        if (!o) { missing++; return; }
        const base = bi * nf * 10;
        const pos = new Float32Array(nf * 3), q = new Float32Array(nf * 4), s = new Float32Array(nf * 3);
        for (let f = 0; f < nf; f++) {
          const k = base + f * 10;
          pos.set(data.subarray(k, k + 3), f * 3);
          q.set(data.subarray(k + 3, k + 7), f * 4);
          s.set(data.subarray(k + 7, k + 10), f * 3);
        }
        tracks.push(new THREE.VectorKeyframeTrack(`${o.uuid}.position`, times, pos));
        tracks.push(new THREE.QuaternionKeyframeTrack(`${o.uuid}.quaternion`, times, q));
        tracks.push(new THREE.VectorKeyframeTrack(`${o.uuid}.scale`, times, s));
      });
      const clip = new THREE.AnimationClip(header.name, Math.max(1, nf - 1) / fps, tracks).optimize();
      clipDuration = clip.duration;
      animVis = header.vis || null;
      animVisFps = fps;
      action = mixer.clipAction(clip);
      action.setLoop(cbLoop.checked ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
      action.clampWhenFinished = true;
      action.timeScale = Number(speedSel.value);
      action.play();
      playing = true;
      lastTrack = null;
      recenter = cbFollow.checked;
      applyVisibility(true);
      slider.max = clipDuration; slider.disabled = false; playBtn.disabled = false; stopBtn.disabled = false;
      playBtn.replaceChildren(icon('pause'));
      const warn = [...(header.warnings || [])];
      if (missing) warn.push(`${missing} bones not found in the model`);
      animMsg.replaceChildren(el('span', {}, `${header.name}: ${header.frame_count} Frames @ ${fps} fps`),
        ...warn.map((w) => el('div', { class: 'warn' }, w)));
    } catch (e) { animMsg.textContent = `Error: ${e.message}`; }
  }

  function stopAnim(reset) {
    if (mixer) mixer.stopAllAction();
    action = null;
    animVis = null;
    applyVisibility(true);
    playing = false;
    playBtn.replaceChildren(icon('play'));
    playBtn.disabled = true; stopBtn.disabled = true; slider.disabled = true; slider.value = 0; tLabel.textContent = '';
    if (reset && modelRoot) modelRoot.traverse((o) => { if (o.isSkinnedMesh) o.skeleton.pose(); });
  }

  animSel.addEventListener('change', async () => { animPath = animSel.value || null; stopAnim(true); await loadClips(); });
  animSearch.addEventListener('change', async () => {
    const p = animList._map && animList._map.get(animSearch.value);
    if (!p) return;
    animPath = p;
    if (![...animSel.options].some((o) => o.value === p)) animSel.append(el('option', { value: p }, baseName(p)));
    animSel.value = p;
    stopAnim(true);
    await loadClips();
  });
  clipSel.addEventListener('change', () => playClip(Number(clipSel.value)));

  async function exportGlb(all) {
    if (!modelPath || !animPath) return;
    const body = { model: modelPath, anim: animPath };
    if (!all) body.clip = Number(clipSel.value);
    expMsg.textContent = 'Exporting GLB…';
    expClipBtn.disabled = true; expAllBtn.disabled = true;
    try {
      const r = await postJSON('/api/export/model-glb', body);
      expMsg.textContent = r.ok ? `Saved: ${r.path} (${r.clips} animation${r.clips === 1 ? '' : 's'}, ${formatSize(r.size)})` : 'Cancelled';
    } catch (e) { expMsg.textContent = `Error: ${e.message}`; }
    updateExportButtons();
  }
  function updateExportButtons() {
    expAllBtn.disabled = !(modelRoot && animPath);
    expClipBtn.disabled = !(modelRoot && animPath && clipSel.value !== '');
  }
  expClipBtn.addEventListener('click', () => exportGlb(false));
  expAllBtn.addEventListener('click', () => exportGlb(true));
  clipSel.addEventListener('change', updateExportButtons);
  animSel.addEventListener('change', updateExportButtons);
  playBtn.addEventListener('click', () => {
    if (!action) return;
    playing = !playing;
    action.paused = !playing;
    if (playing && !action.isRunning()) { action.reset(); action.play(); }
    playBtn.replaceChildren(icon(playing ? 'pause' : 'play'));
  });
  stopBtn.addEventListener('click', () => { stopAnim(true); clipSel.value = ''; });
  speedSel.addEventListener('change', () => { if (action) action.timeScale = Number(speedSel.value); });
  cbLoop.addEventListener('change', () => { if (action) action.setLoop(cbLoop.checked ? THREE.LoopRepeat : THREE.LoopOnce, Infinity); });
  cbFollow.addEventListener('change', () => { lastTrack = null; recenter = cbFollow.checked; });
  slider.addEventListener('input', () => {
    if (!action) return;
    playing = false; action.paused = true; playBtn.replaceChildren(icon('play'));
    action.time = Number(slider.value);
    mixer.update(0);
  });
  lodSel.addEventListener('change', () => { lod = Number(lodSel.value); loadModel(); });

  // model chooser for animation files
  async function loadModelChoices() {
    const sug = meta.model_suggestions || [];
    modelSel.replaceChildren(el('option', { value: '' }, sug.length ? '— choose a model —' : '— no matching model found —'),
      ...sug.map((p) => el('option', { value: p, selected: p === modelPath }, baseName(p))));
    try {
      const res = await getJSON('/api/search', { q: 'romfs/Model/', type: 'model', limit: 20000 });
      modelList.replaceChildren(...res.items.map((i) => el('option', { value: i.name })));
      modelList._map = new Map(res.items.map((i) => [i.name, i.path]));
    } catch { /* optional */ }
  }
  modelSel.addEventListener('change', () => { modelPath = modelSel.value || null; framed = false; loadModel(); });
  modelSearch.addEventListener('change', () => {
    const p = modelList._map && modelList._map.get(modelSearch.value);
    if (!p) return;
    modelPath = p;
    if (![...modelSel.options].some((o) => o.value === p)) modelSel.append(el('option', { value: p }, baseName(p)));
    modelSel.value = p;
    framed = false;
    loadModel();
  });

  // ------------------------------------------------------------ render loop
  r.setAnimationLoop(() => {
    const dt = clock.getDelta();
    controls.update();
    if (mixer && action && playing) {
      mixer.update(dt);
      slider.value = action.time;
      tLabel.textContent = `${action.time.toFixed(2)} s / ${clipDuration.toFixed(2)} s`;
    }
    if (animVis && action) applyVisibility(false);
    if (action && trackObj && cbFollow.checked) {
      modelRoot.updateMatrixWorld(true);
      trackObj.getWorldPosition(tmpV);
      if (recenter) {
        // first frame of a clip: move the view to the model, keep angle and distance
        const offset = camera.position.clone().sub(controls.target);
        controls.target.copy(tmpV);
        camera.position.copy(tmpV).add(offset);
        recenter = false;
      } else if (lastTrack) {
        const d = tmpV.clone().sub(lastTrack);
        camera.position.add(d);
        controls.target.add(d);
      }
      lastTrack = (lastTrack || new THREE.Vector3()).copy(tmpV);
    }
    r.render(scene, camera);
  });
  resize();
  if (isAnimFile) loadModelChoices();
  loadModel().then(() => loadAnimList());

  return {
    dispose() {
      disposed = true;
      r.setAnimationLoop(null);
      ro.disconnect();
      controls.dispose();
      clearModel();
      if (grid) scene.remove(grid);
      r.domElement.remove();
      r.renderLists.dispose();
    },
  };
}
