import * as THREE from "three";
import { ARButton } from "three/addons/webxr/ARButton.js";
import { XRControllerModelFactory } from "three/addons/webxr/XRControllerModelFactory.js";
import { XRHandModelFactory } from "three/addons/webxr/XRHandModelFactory.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const statusEl = document.getElementById("status");
const talkBtn = document.getElementById("talkBtn");
const demoBtn = document.getElementById("demoBtn");
const arButtonHost = document.getElementById("arButton");
const overlayRoot = document.getElementById("overlay");

const SESSION_ID = "default";
const API_BASE = "";

function setStatus(msg, ok = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("ok", !!ok);
  try {
    setXrStatus?.(msg);
  } catch (_) {}
}

// --- Passthrough AR scene (no virtual room) ---
const container = document.getElementById("app");
const renderer = new THREE.WebGLRenderer({
  antialias: true,
  alpha: true,
  preserveDrawingBuffer: false,
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x000000, 0);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType("local-floor");
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = null; // real world shows through

const camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.05, 50);
camera.position.set(0, 1.5, 0.8);

// Strong lighting so metals don't go black in passthrough
scene.add(new THREE.AmbientLight(0xffffff, 0.85));
scene.add(new THREE.HemisphereLight(0xffffff, 0xb0b0b0, 1.2));
const key = new THREE.DirectionalLight(0xffffff, 1.4);
key.position.set(0.8, 2.5, 1.2);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.6);
fill.position.set(-1.5, 1.5, -0.5);
scene.add(fill);

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

// Model root — repositioned in front of your headset when AR starts
const modelRoot = new THREE.Group();
modelRoot.position.set(0, 1.3, -0.7);
scene.add(modelRoot);

const placeholder = new THREE.Mesh(
  new THREE.TorusGeometry(0.07, 0.022, 32, 64),
  new THREE.MeshStandardMaterial({
    color: 0xffcc33,
    roughness: 0.3,
    metalness: 0.05,
    emissive: 0xaa7700,
    emissiveIntensity: 0.35,
  })
);
placeholder.rotation.x = Math.PI / 2;
modelRoot.add(placeholder);

// Soft highlight when hand is near / grabbing
const halo = new THREE.Mesh(
  new THREE.RingGeometry(0.12, 0.14, 48),
  new THREE.MeshBasicMaterial({
    color: 0x4f8cff,
    transparent: true,
    opacity: 0.0,
    side: THREE.DoubleSide,
    depthWrite: false,
  })
);
halo.rotation.x = -Math.PI / 2;
modelRoot.add(halo);

let currentModel = null;
let currentColor = "#FFD700";
let replyAudio = null;
let needsUserPlacement = false;
let placeFrameCount = 0;
const loader = new GLTFLoader();

const _camPos = new THREE.Vector3();
const _camQuat = new THREE.Quaternion();
const _forward = new THREE.Vector3();
let grabbing = false; // declared early for placeModelInFrontOfUser halo timeout

/** Put the model ~arm's length in front of the headset (fixes empty passthrough view). */
function placeModelInFrontOfUser(distance = 0.7) {
  const cam = renderer.xr.isPresenting ? renderer.xr.getCamera() : camera;
  cam.updateMatrixWorld(true);
  cam.getWorldPosition(_camPos);
  cam.getWorldQuaternion(_camQuat);

  _forward.set(0, 0, -1).applyQuaternion(_camQuat).normalize();
  modelRoot.position.copy(_camPos).addScaledVector(_forward, distance);
  // Keep near chest / lower eye height so it's easy to look at
  modelRoot.position.y = _camPos.y - 0.2;

  // Face the user
  modelRoot.quaternion.identity();
  const face = new THREE.Vector3()
    .copy(_camPos)
    .setY(modelRoot.position.y);
  modelRoot.lookAt(face);
  modelRoot.rotateY(Math.PI);

  halo.material.opacity = 0.55;
  setTimeout(() => {
    if (!grabbing) halo.material.opacity = 0.0;
  }, 1800);
}

function prepareVisibleMaterials(root, hex) {
  const color = new THREE.Color(hex || currentColor);
  root.traverse((child) => {
    if (!child.isMesh) return;
    child.frustumCulled = false;
    child.castShadow = false;
    child.receiveShadow = false;
    const neu = new THREE.MeshStandardMaterial({
      color: color.clone(),
      roughness: 0.32,
      metalness: 0.08,
      emissive: color.clone().multiplyScalar(0.18),
      envMapIntensity: 1.0,
    });
    child.material = neu;
  });
}

function loadGlb(url) {
  return new Promise((resolve, reject) => {
    loader.load(url, (gltf) => resolve(gltf.scene), undefined, reject);
  });
}

function applyColorToObject(obj, hex) {
  const color = new THREE.Color(hex);
  obj.traverse((child) => {
    if (!child.isMesh || !child.material) return;
    const mats = Array.isArray(child.material) ? child.material : [child.material];
    for (const m of mats) {
      if (m.color) m.color.copy(color);
      if (m.emissive) m.emissive.copy(color).multiplyScalar(0.18);
      m.needsUpdate = true;
    }
  });
}

async function setModelFromResponse(data) {
  if (data.color) {
    currentColor = data.color;
    if (currentModel && !data.rebuilt) applyColorToObject(currentModel, currentColor);
  }

  if (data.glb_url && data.rebuilt) {
    const bust = `${data.glb_url}${data.glb_url.includes("?") ? "&" : "?"}t=${Date.now()}`;
    const sceneObj = await loadGlb(bust);
    // Fit to ~22cm so it's obvious in passthrough
    const box = new THREE.Box3().setFromObject(sceneObj);
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    sceneObj.scale.setScalar(0.22 / maxDim);
    box.setFromObject(sceneObj);
    sceneObj.position.sub(box.getCenter(new THREE.Vector3()));

    prepareVisibleMaterials(sceneObj, currentColor);

    if (currentModel) modelRoot.remove(currentModel);
    placeholder.visible = false;
    currentModel = sceneObj;
    modelRoot.add(currentModel);

    if (renderer.xr.isPresenting) placeModelInFrontOfUser();
  }

  if (data.reply_audio_url) {
    try {
      if (replyAudio) {
        try { replyAudio.pause(); } catch (_) {}
      }
      replyAudio = new Audio(data.reply_audio_url);
      replyAudio.play().catch(() => {});
    } catch (_) {}
  }
}

// --- Enter AR (passthrough) + DOM overlay for UI ---
const arBtn = ARButton.createButton(renderer, {
  // Don't require local-floor — some Quest AR sessions reject the session if it's required
  optionalFeatures: ["local-floor", "hand-tracking", "dom-overlay"],
  domOverlay: { root: overlayRoot },
});
arButtonHost.replaceWith(arBtn);
arBtn.id = "arButton";
arBtn.classList.add("hit");

renderer.xr.addEventListener("sessionstart", () => {
  scene.background = null;
  renderer.setClearColor(0x000000, 0);
  needsUserPlacement = true;
  placeFrameCount = 0;
  // Keep placeholder visible until a model exists so you always see *something*
  if (!currentModel) placeholder.visible = true;
  setStatus("Look forward — placing model in front of you…", true);
});
renderer.xr.addEventListener("sessionend", () => {
  renderer.setClearColor(0x0a0c12, 1);
  needsUserPlacement = false;
  setStatus("AR ended — tap Enter AR again for passthrough.");
});

// Desktop preview clear (not passthrough)
renderer.setClearColor(0x0a0c12, 1);

// --- Controllers + hands ---
const controllerModelFactory = new XRControllerModelFactory();
const handModelFactory = new XRHandModelFactory();

function setupHand(index) {
  const controller = renderer.xr.getController(index);
  scene.add(controller);
  // Pointer ray for clicking 3D UI
  const ray = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, -1.2),
    ]),
    new THREE.LineBasicMaterial({ color: 0x88aaff, transparent: true, opacity: 0.65 })
  );
  controller.add(ray);

  const grip = renderer.xr.getControllerGrip(index);
  grip.add(controllerModelFactory.createControllerModel(grip));
  scene.add(grip);

  const hand = renderer.xr.getHand(index);
  hand.add(handModelFactory.createHandModel(hand, "mesh"));
  scene.add(hand);

  // Pinch anchor: hand ROOT stays at origin in WebXR — joints move. We track pinch in world space.
  const pinchAnchor = new THREE.Object3D();
  scene.add(pinchAnchor);

  return { controller, grip, hand, pinchAnchor, index, ray };
}

const left = setupHand(0);
const right = setupHand(1);

// --- In-headset 3D UI (DOM overlay often disappears in immersive) ---
const xrUi = new THREE.Group();
xrUi.visible = false;
scene.add(xrUi);

function makeCanvasButton(label, w = 512, h = 160, bg = "#4f8cff") {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  const paint = (text, color) => {
    ctx.clearRect(0, 0, w, h);
    // rounded rect
    const r = 36;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.arcTo(w, 0, w, h, r);
    ctx.arcTo(w, h, 0, h, r);
    ctx.arcTo(0, h, 0, 0, r);
    ctx.arcTo(0, 0, w, 0, r);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 64px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, w / 2, h / 2);
  };
  paint(label, bg);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(0.28, 0.09),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, depthTest: false })
  );
  mesh.renderOrder = 10;
  mesh.userData.paint = (text, color) => {
    paint(text, color);
    tex.needsUpdate = true;
  };
  mesh.userData.isTalkBtn = label.toLowerCase().includes("talk");
  mesh.userData.isDemoBtn = label.toLowerCase().includes("demo");
  return mesh;
}

const talk3d = makeCanvasButton("HOLD TO TALK", 512, 160, "#4f8cff");
talk3d.position.set(0, 0.06, 0);
xrUi.add(talk3d);

const demo3d = makeCanvasButton("DEMO RING", 512, 160, "#3a4664");
demo3d.position.set(0, -0.05, 0);
xrUi.add(demo3d);

const status3dCanvas = document.createElement("canvas");
status3dCanvas.width = 768;
status3dCanvas.height = 128;
const status3dCtx = status3dCanvas.getContext("2d");
const status3dTex = new THREE.CanvasTexture(status3dCanvas);
status3dTex.colorSpace = THREE.SRGBColorSpace;
const status3d = new THREE.Mesh(
  new THREE.PlaneGeometry(0.42, 0.07),
  new THREE.MeshBasicMaterial({ map: status3dTex, transparent: true, side: THREE.DoubleSide, depthTest: false })
);
status3d.position.set(0, 0.15, 0);
status3d.renderOrder = 10;
xrUi.add(status3d);

function setXrStatus(msg) {
  const ctx = status3dCtx;
  const w = status3dCanvas.width;
  const h = status3dCanvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#e8eefc";
  ctx.font = "28px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const line = msg.length > 54 ? `${msg.slice(0, 51)}…` : msg;
  ctx.fillText(line, w / 2, h / 2);
  status3dTex.needsUpdate = true;
}

function layoutXrUi() {
  if (!renderer.xr.isPresenting) {
    xrUi.visible = false;
    return;
  }
  xrUi.visible = true;
  const cam = renderer.xr.getCamera();
  cam.updateMatrixWorld(true);
  cam.getWorldPosition(_camPos);
  cam.getWorldQuaternion(_camQuat);
  _forward.set(0, 0, -1).applyQuaternion(_camQuat).normalize();
  // Dock UI lower-center in view so it doesn't cover the model
  xrUi.position.copy(_camPos).addScaledVector(_forward, 0.55);
  xrUi.position.y = _camPos.y - 0.28;
  xrUi.quaternion.copy(_camQuat);
}

// Interaction state
let grabSource = null; // pinchAnchor we follow
let grabHandKey = -1;
const grabOffset = new THREE.Matrix4();
const tempMatrix = new THREE.Matrix4();
const _thumb = new THREE.Vector3();
const _indexTip = new THREE.Vector3();
const _pinch = new THREE.Vector3();
const _modelCenter = new THREE.Vector3();
const _wristQuat = new THREE.Quaternion();
let wasPinching = { 0: false, 1: false };
let pokeTalking = false;
let demoPoked = false;

function modelInteractTarget() {
  return currentModel || (placeholder.visible ? placeholder : null);
}

function getModelCenter(out) {
  const target = modelInteractTarget();
  if (!target) {
    out.copy(modelRoot.position);
    return out;
  }
  return new THREE.Box3().setFromObject(target).getCenter(out);
}

function pinchGapAndPos(hand, outPos) {
  const joints = hand.joints;
  if (!joints?.["thumb-tip"] || !joints?.["index-finger-tip"]) return null;
  joints["thumb-tip"].getWorldPosition(_thumb);
  joints["index-finger-tip"].getWorldPosition(_indexTip);
  outPos.copy(_thumb).add(_indexTip).multiplyScalar(0.5);
  return _thumb.distanceTo(_indexTip);
}

function updatePinchAnchor(handEntry) {
  const gap = pinchGapAndPos(handEntry.hand, _pinch);
  if (gap === null) return null;
  handEntry.pinchAnchor.position.copy(_pinch);
  const wrist = handEntry.hand.joints?.wrist;
  if (wrist) {
    wrist.getWorldQuaternion(_wristQuat);
    handEntry.pinchAnchor.quaternion.copy(_wristQuat);
  }
  handEntry.pinchAnchor.updateMatrixWorld(true);
  return gap;
}

function nearModel(worldPos, pad = 0.16) {
  const target = modelInteractTarget();
  if (!target) return false;
  const box = new THREE.Box3().setFromObject(target);
  box.expandByScalar(pad);
  return box.containsPoint(worldPos);
}

function beginGrab(anchor, key) {
  grabbing = true;
  grabSource = anchor;
  grabHandKey = key;
  grabSource.updateMatrixWorld(true);
  modelRoot.updateMatrixWorld(true);
  tempMatrix.copy(grabSource.matrixWorld).invert();
  grabOffset.multiplyMatrices(tempMatrix, modelRoot.matrixWorld);
  halo.material.opacity = 0.9;
  setStatus("Grabbing — move hand to move, twist to spin.", true);
}

function endGrab() {
  grabbing = false;
  grabSource = null;
  grabHandKey = -1;
  halo.material.opacity = 0.0;
}

function updateGrab() {
  if (!grabbing || !grabSource) return;
  grabSource.updateMatrixWorld(true);
  tempMatrix.multiplyMatrices(grabSource.matrixWorld, grabOffset);
  const s = new THREE.Vector3();
  tempMatrix.decompose(modelRoot.position, modelRoot.quaternion, s);
  modelRoot.scale.set(1, 1, 1);
}

function pollHand(handEntry, key) {
  const gap = updatePinchAnchor(handEntry);
  if (gap === null) {
    if (wasPinching[key] && grabHandKey === key) endGrab();
    wasPinching[key] = false;
    return;
  }

  const pinching = gap < 0.035;
  const pos = handEntry.pinchAnchor.position;

  if (!grabbing) {
    getModelCenter(_modelCenter);
    const dist = pos.distanceTo(_modelCenter);
    if (dist < 0.22) halo.material.opacity = Math.max(halo.material.opacity, 0.4);
  }

  // Pinch near model → grab+spin (either hand)
  if (pinching && !wasPinching[key]) {
    if (nearModel(pos, 0.18)) {
      beginGrab(handEntry.pinchAnchor, key);
    }
  }
  if (!pinching && wasPinching[key] && grabHandKey === key) {
    endGrab();
  }

  // Index fingertip poke on 3D talk / demo buttons
  const indexJoint = handEntry.hand.joints?.["index-finger-tip"];
  if (indexJoint && xrUi.visible) {
    indexJoint.getWorldPosition(_indexTip);
    const talkDist = _indexTip.distanceTo(talk3d.getWorldPosition(_modelCenter));
    // reuse vectors carefully
    talk3d.getWorldPosition(_modelCenter);
    const dTalk = _indexTip.distanceTo(_modelCenter);
    demo3d.getWorldPosition(_camPos); // temp
    const dDemo = _indexTip.distanceTo(_camPos);

    if (dTalk < 0.06) {
      if (!pokeTalking && !grabbing) {
        pokeTalking = true;
        startTalk();
        talk3d.userData.paint("LISTENING…", "#e53935");
      }
    } else if (pokeTalking && key === 0) {
      // only end poke-talk when left index leaves (or both checked below)
    }

    if (dDemo < 0.06 && !demoPoked) {
      demoPoked = true;
      sendCommand("build me a ring").catch((e) => setStatus(e.message));
    } else if (dDemo >= 0.08) {
      demoPoked = false;
    }
  }

  wasPinching[key] = pinching;
}

function pollPokeRelease() {
  if (!pokeTalking) return;
  // End talk when BOTH index tips are away from talk button
  let near = false;
  for (const entry of [left, right]) {
    const tip = entry.hand.joints?.["index-finger-tip"];
    if (!tip) continue;
    tip.getWorldPosition(_indexTip);
    talk3d.getWorldPosition(_modelCenter);
    if (_indexTip.distanceTo(_modelCenter) < 0.07) near = true;
  }
  if (!near) {
    pokeTalking = false;
    stopTalk();
    talk3d.userData.paint("HOLD TO TALK", "#4f8cff");
  }
}

// Controller: squeeze = talk, select ray on UI or grab model
const xrRaycaster = new THREE.Raycaster();
const _rayOrig = new THREE.Vector3();
const _rayDir = new THREE.Vector3();

function controllerSelectUi(controller) {
  controller.getWorldPosition(_rayOrig);
  _rayDir.set(0, 0, -1).applyQuaternion(controller.getWorldQuaternion(_camQuat)).normalize();
  xrRaycaster.set(_rayOrig, _rayDir);
  const hits = xrRaycaster.intersectObjects([talk3d, demo3d], false);
  if (!hits.length) return null;
  return hits[0].object;
}

function onControllerSelectStart(controller) {
  const ui = controllerSelectUi(controller);
  if (ui?.userData.isTalkBtn) {
    startTalk();
    talk3d.userData.paint("LISTENING…", "#e53935");
    controller.userData.talking = true;
    return;
  }
  if (ui?.userData.isDemoBtn) {
    sendCommand("build me a ring").catch((e) => setStatus(e.message));
    return;
  }
  controller.getWorldPosition(_pinch);
  if (nearModel(_pinch, 0.22)) beginGrab(controller, -1);
}

function onControllerSelectEnd(controller) {
  if (controller.userData.talking) {
    controller.userData.talking = false;
    stopTalk();
    talk3d.userData.paint("HOLD TO TALK", "#4f8cff");
  }
  if (grabSource === controller) endGrab();
}

for (const entry of [left, right]) {
  entry.controller.addEventListener("selectstart", () => onControllerSelectStart(entry.controller));
  entry.controller.addEventListener("selectend", () => onControllerSelectEnd(entry.controller));
  entry.controller.addEventListener("squeezestart", () => {
    startTalk();
    talk3d.userData.paint("LISTENING…", "#e53935");
    entry.controller.userData.squeezeTalk = true;
  });
  entry.controller.addEventListener("squeezeend", () => {
    if (entry.controller.userData.squeezeTalk) {
      entry.controller.userData.squeezeTalk = false;
      stopTalk();
      talk3d.userData.paint("HOLD TO TALK", "#4f8cff");
    }
  });
}

// --- API ---
async function sendCommand(text) {
  setStatus(`Thinking: “${text}”…`);
  const res = await fetch(`${API_BASE}/api/command`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, session_id: SESSION_ID }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  await setModelFromResponse(data);
  const ms = data.latency_ms?.total_ms ? ` (${Math.round(data.latency_ms.total_ms)}ms)` : "";
  setStatus(`${data.reply}${ms}`, data.ok);
  return data;
}

async function sendVoice(blob, attempt = 1) {
  setStatus("Got it — working…");
  const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
  const form = new FormData();
  form.append("audio", blob, `utterance.${ext}`);
  form.append("session_id", SESSION_ID);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const res = await fetch(`${API_BASE}/api/voice`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    await setModelFromResponse(data);
    const heard = data.transcript ? `Heard: “${data.transcript}”. ` : "";
    const ms = data.latency_ms?.total_ms ? ` (${Math.round(data.latency_ms.total_ms)}ms)` : "";
    setStatus(`${heard}${data.reply}${ms}`, data.ok);
    return data;
  } catch (err) {
    if (attempt < 2 && err.name !== "AbortError") {
      setStatus("Retrying voice…");
      return sendVoice(blob, attempt + 1);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// --- Hold to talk → release auto-sends ---
let mediaStream = null;
let mediaRecorder = null;
let chunks = [];
let recording = false;
let voiceBusy = false;
let pendingBlob = null;

function pickMime() {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]) {
    if (window.MediaRecorder?.isTypeSupported?.(t)) return t;
  }
  return "";
}

async function getMicStream() {
  if (mediaStream?.active) return mediaStream;
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
  });
  return mediaStream;
}

async function flushVoice(blob) {
  voiceBusy = true;
  talkBtn.disabled = true;
  try {
    await sendVoice(blob);
  } catch (err) {
    const msg = err.name === "AbortError" ? "timed out" : err.message;
    setStatus(`Voice failed: ${msg}. Hold to talk and try again.`);
  } finally {
    voiceBusy = false;
    talkBtn.disabled = false;
    talk3d.userData.paint("HOLD TO TALK", "#4f8cff");
    if (pendingBlob) {
      const next = pendingBlob;
      pendingBlob = null;
      flushVoice(next);
    }
  }
}

async function startTalk() {
  if (recording) return;
  try {
    if (replyAudio) {
      try { replyAudio.pause(); } catch (_) {}
      replyAudio = null;
    }
    const stream = await getMicStream();
    const mime = pickMime();
    chunks = [];
    mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    const usedMime = mediaRecorder.mimeType || mime || "audio/webm";

    mediaRecorder.ondataavailable = (e) => {
      if (e.data?.size > 0) chunks.push(e.data);
    };
    mediaRecorder.onstop = async () => {
      recording = false;
      talkBtn.classList.remove("recording");
      talkBtn.textContent = "Hold to talk";
      talk3d.userData.paint("HOLD TO TALK", "#4f8cff");
      const blob = new Blob(chunks, { type: usedMime });
      chunks = [];
      if (blob.size < 400) {
        setStatus("Hold a little longer, then release.");
        return;
      }
      if (voiceBusy) {
        pendingBlob = blob;
        setStatus("Queued — sending when ready…");
        return;
      }
      await flushVoice(blob);
    };

    mediaRecorder.start(100);
    recording = true;
    talkBtn.classList.add("recording");
    talkBtn.textContent = "Listening…";
    talk3d.userData.paint("LISTENING…", "#e53935");
    setStatus("Listening… release to send.");
  } catch (err) {
    recording = false;
    setStatus(`Mic error: ${err.message}`);
  }
}

function stopTalk() {
  if (!recording || !mediaRecorder) return;
  if (mediaRecorder.state === "recording" || mediaRecorder.state === "paused") {
    try { mediaRecorder.requestData(); } catch (_) {}
    try { mediaRecorder.stop(); } catch (_) {}
  } else {
    recording = false;
    talkBtn.classList.remove("recording");
    talkBtn.textContent = "Hold to talk";
    talk3d.userData.paint("HOLD TO TALK", "#4f8cff");
  }
}

// HTML button (desktop / before immersive)
talkBtn.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  e.stopPropagation();
  talkBtn.setPointerCapture?.(e.pointerId);
  startTalk();
});
talkBtn.addEventListener("pointerup", (e) => {
  e.preventDefault();
  e.stopPropagation();
  stopTalk();
});
talkBtn.addEventListener("pointercancel", () => stopTalk());
talkBtn.addEventListener("lostpointercapture", () => stopTalk());

demoBtn.addEventListener("click", () => {
  sendCommand("build me a ring").catch((err) => setStatus(err.message));
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => {
  if (needsUserPlacement && renderer.xr.isPresenting) {
    placeFrameCount += 1;
    if (placeFrameCount >= 3) {
      placeModelInFrontOfUser(0.7);
      needsUserPlacement = false;
      setStatus(
        currentModel
          ? "Pinch the model to grab/spin. Squeeze or poke HOLD TO TALK."
          : "Poke DEMO RING or squeeze to talk.",
        true
      );
    }
  }

  layoutXrUi();
  pollHand(left, 0);
  pollHand(right, 1);
  pollPokeRelease();
  updateGrab();
  renderer.render(scene, camera);
});

// Desktop preview: drag to move, drag+shift to spin
let desktopMode = null;
const pointer = new THREE.Vector2();
const raycaster = new THREE.Raycaster();
const lastPtr = new THREE.Vector2();

renderer.domElement.addEventListener("pointerdown", (e) => {
  if (renderer.xr.isPresenting) return;
  pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const target = modelInteractTarget();
  if (target && raycaster.intersectObject(target, true).length) {
    desktopMode = e.shiftKey ? "spin" : "move";
    lastPtr.set(e.clientX, e.clientY);
  }
});
window.addEventListener("pointerup", () => { desktopMode = null; });
window.addEventListener("pointermove", (e) => {
  if (!desktopMode || renderer.xr.isPresenting) return;
  const dx = e.clientX - lastPtr.x;
  const dy = e.clientY - lastPtr.y;
  lastPtr.set(e.clientX, e.clientY);
  if (desktopMode === "move") {
    modelRoot.position.x += dx * 0.002;
    modelRoot.position.y -= dy * 0.002;
  } else {
    modelRoot.rotation.y += dx * 0.01;
    modelRoot.rotation.x += dy * 0.01;
  }
});

fetch(`${API_BASE}/api/health`)
  .then((r) => r.json())
  .then((h) => {
    setStatus(
      `Ready · CadQuery ${h.cadquery ? "on" : "off"} · voice ${h.stt && h.tts ? "on" : "partial"}`,
      true
    );
  })
  .catch(() => setStatus("API offline — start backend on :8000"));
