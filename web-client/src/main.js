/**
 * Perception CAD - WebXR Passthrough AR Client
 *
 * Voice-driven CAD on Quest 3 via passthrough AR.
 * Left PTT → STT/Gemini → CadQuery → GLB. Right pinch near model → grab.
 *
 * VoiceState hooks exposed for in-world HUD (design owns visuals).
 */

import * as THREE from "three";
import { ARButton } from "three/addons/webxr/ARButton.js";
import { XRControllerModelFactory } from "three/addons/webxr/XRControllerModelFactory.js";
import { XRHandModelFactory } from "three/addons/webxr/XRHandModelFactory.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

import { PercyAssistant } from "./voice/PercyAssistant.js";
import { voiceState, VoiceStates } from "./voice/VoiceState.js";
import { PhotoPicker } from "./PhotoPicker.js";

// Desktop XR emulation is injected by @iwsdk/vite-plugin-dev (localhost only).
// Quest / LAN IP keep native WebXR — do not manually install IWER here.

const params = new URLSearchParams(window.location.search);
const DEBUG_HUD = params.has("debug") && (params.get("debug") === "hud" || params.get("debug") === "1" || params.get("debug") === "true" || params.get("debug") === "");

const SESSION_ID = "default";
const API_BASE = "";

const statusEl = document.getElementById("status");
const arButtonHost = document.getElementById("arButton");
const overlayRoot = document.getElementById("overlay");
const hudEl = document.getElementById("hud");
const minimalStatusEl = document.getElementById("minimalStatus");

if (DEBUG_HUD) {
  hudEl?.classList.add("debug-visible");
  minimalStatusEl?.classList.add("debug-visible");
}

function setStatus(msg, ok = false) {
  if (statusEl) {
    statusEl.textContent = msg;
    statusEl.classList.toggle("ok", !!ok);
  }
  if (minimalStatusEl) {
    minimalStatusEl.textContent = msg;
    minimalStatusEl.classList.toggle("ok", !!ok);
  }
}

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
scene.background = null;

const camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.05, 50);
camera.position.set(0, 1.5, 0.8);

scene.add(new THREE.AmbientLight(0xffffff, 1.6));
scene.add(new THREE.HemisphereLight(0xffffff, 0xd0d0d0, 1.8));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(0.8, 2.5, 1.2);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 1.1);
fill.position.set(-1.5, 1.5, -0.5);
scene.add(fill);
const rim = new THREE.DirectionalLight(0xffffff, 0.8);
rim.position.set(0, 1.2, -2);
scene.add(rim);

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

const modelRoot = new THREE.Group();
modelRoot.position.set(0, 1.3, -0.7);
scene.add(modelRoot);

const placeholder = new THREE.Mesh(
  new THREE.SphereGeometry(0.03, 32, 16),
  new THREE.MeshStandardMaterial({
    color: 0x4f8cff,
    roughness: 0.4,
    metalness: 0.1,
    emissive: 0x2244aa,
    emissiveIntensity: 0.3,
  })
);
modelRoot.add(placeholder);

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

const voiceIndicatorGroup = new THREE.Group();
voiceIndicatorGroup.visible = false;
scene.add(voiceIndicatorGroup);

const VOICE_COLORS = {
  idle: 0x4f8cff,
  listening: 0xe53935,
  thinking: 0xffc107,
  speaking: 0x4caf50,
  error: 0xff5722,
  muted: 0x9e9e9e,
};

const voiceRing = new THREE.Mesh(
  new THREE.TorusGeometry(0.018, 0.004, 16, 32),
  new THREE.MeshBasicMaterial({
    color: VOICE_COLORS.idle,
    transparent: true,
    opacity: 0.85,
  })
);
voiceRing.rotation.x = Math.PI / 2;
voiceIndicatorGroup.add(voiceRing);

const voiceDot = new THREE.Mesh(
  new THREE.SphereGeometry(0.006, 16, 16),
  new THREE.MeshBasicMaterial({
    color: VOICE_COLORS.idle,
    transparent: true,
    opacity: 0.9,
  })
);
voiceIndicatorGroup.add(voiceDot);

const muteIndicator = new THREE.Mesh(
  new THREE.PlaneGeometry(0.012, 0.003),
  new THREE.MeshBasicMaterial({
    color: 0xff0000,
    transparent: true,
    opacity: 0.0,
    side: THREE.DoubleSide,
  })
);
muteIndicator.position.set(0, 0, 0.02);
voiceIndicatorGroup.add(muteIndicator);

function updateVoiceIndicator(state) {
  const color = VOICE_COLORS[state] || VOICE_COLORS.idle;
  voiceRing.material.color.setHex(color);
  voiceDot.material.color.setHex(color);
  muteIndicator.material.opacity = state === "muted" ? 0.9 : 0.0;

  if (state === "listening") {
    voiceRing.scale.setScalar(1.2);
    voiceDot.scale.setScalar(1.3);
  } else if (state === "thinking") {
    voiceRing.scale.setScalar(1.0);
  } else if (state === "speaking") {
    voiceRing.scale.setScalar(1.1);
  } else {
    voiceRing.scale.setScalar(1.0);
    voiceDot.scale.setScalar(1.0);
  }
}

let currentModel = null;
let currentColor = "#C0C0C0";
let needsUserPlacement = false;
let placeFrameCount = 0;
const loader = new GLTFLoader();

const _camPos = new THREE.Vector3();
const _camQuat = new THREE.Quaternion();
const _forward = new THREE.Vector3();
let grabbing = false;

const photoPicker = new PhotoPicker(scene, () => camera);

function buildFromPickedPhoto(fileId) {
  photoPicker.keepOnly(fileId);
  photoPicker.busy = true;
  setStatus("Building that from the photo…", true);
  // A sculpt that fails has to hand the picker back, or the photo can never
  // be picked again without reloading the page.
  percy.choosePhoto(fileId).then((result) => {
    if (!result?.rebuilt) photoPicker.busy = false;
  });
}

function placeModelInFrontOfUser(distance = 0.7) {
  const cam = renderer.xr.isPresenting ? renderer.xr.getCamera() : camera;
  cam.updateMatrixWorld(true);
  cam.getWorldPosition(_camPos);
  cam.getWorldQuaternion(_camQuat);

  _forward.set(0, 0, -1).applyQuaternion(_camQuat).normalize();
  modelRoot.position.copy(_camPos).addScaledVector(_forward, distance);
  modelRoot.position.y = _camPos.y - 0.2;

  modelRoot.quaternion.identity();
  const face = new THREE.Vector3().copy(_camPos).setY(modelRoot.position.y);
  modelRoot.lookAt(face);
  modelRoot.rotateY(Math.PI);

  halo.material.opacity = 0.55;
  setTimeout(() => {
    if (!grabbing) halo.material.opacity = 0.0;
  }, 1800);
}

function layoutVoiceIndicator() {
  if (!renderer.xr.isPresenting) {
    voiceIndicatorGroup.visible = false;
    return;
  }
  voiceIndicatorGroup.visible = true;

  const cam = renderer.xr.getCamera();
  cam.updateMatrixWorld(true);
  cam.getWorldPosition(_camPos);
  cam.getWorldQuaternion(_camQuat);

  _forward.set(0, 0, -1).applyQuaternion(_camQuat).normalize();
  voiceIndicatorGroup.position.copy(_camPos).addScaledVector(_forward, 0.45);
  voiceIndicatorGroup.position.y = _camPos.y - 0.35;
  voiceIndicatorGroup.quaternion.copy(_camQuat);
}

function ensureOutwardNormals(geometry) {
  if (!geometry?.attributes?.position) return;
  geometry.computeVertexNormals();
  const pos = geometry.attributes.position;
  const nrm = geometry.attributes.normal;
  if (!nrm) return;

  const c = new THREE.Vector3();
  const p = new THREE.Vector3();
  const n = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    c.add(p.fromBufferAttribute(pos, i));
  }
  c.divideScalar(Math.max(pos.count, 1));

  let votes = 0;
  const step = Math.max(1, Math.floor(pos.count / 250));
  for (let i = 0; i < pos.count; i += step) {
    p.fromBufferAttribute(pos, i);
    n.fromBufferAttribute(nrm, i);
    votes += n.dot(p.sub(c)) >= 0 ? 1 : -1;
  }
  if (votes >= 0) return;

  for (let i = 0; i < nrm.count; i++) {
    nrm.setXYZ(i, -nrm.getX(i), -nrm.getY(i), -nrm.getZ(i));
  }
  nrm.needsUpdate = true;
  if (geometry.index) {
    const a = geometry.index.array;
    for (let i = 0; i < a.length; i += 3) {
      const tmp = a[i + 1];
      a[i + 1] = a[i + 2];
      a[i + 2] = tmp;
    }
    geometry.index.needsUpdate = true;
  }
}

function hologramMaterialFromColor(color) {
  const c = color.clone();
  return new THREE.MeshLambertMaterial({
    color: c,
    emissive: c.clone(),
    emissiveIntensity: 0.65,
    side: THREE.DoubleSide,
    vertexColors: false,
  });
}

function hologramMaterial(hex) {
  return hologramMaterialFromColor(new THREE.Color(hex || currentColor));
}

function meshAlbedo(child, fallbackHex) {
  const attr = child.geometry?.attributes?.color;
  if (attr && attr.count) {
    let r = attr.getX(0);
    let g = attr.getY(0);
    let b = attr.getZ(0);
    if (r > 1 || g > 1 || b > 1) {
      r /= 255;
      g /= 255;
      b /= 255;
    }
    if (r + g + b > 0.04) return new THREE.Color(r, g, b);
  }
  const mat = Array.isArray(child.material) ? child.material[0] : child.material;
  if (mat?.color) {
    const c = mat.color;
    if (c.r + c.g + c.b > 0.04) return c.clone();
  }
  return new THREE.Color(fallbackHex || currentColor);
}

function prepareVisibleMaterials(root, hex, textured = false) {
  const meshes = [];
  root.traverse((child) => {
    if (child.isMesh) meshes.push(child);
  });
  if (textured) {
    meshes.forEach((child) => {
      child.frustumCulled = false;
      child.castShadow = false;
      child.receiveShadow = false;
      if (child.geometry) ensureOutwardNormals(child.geometry);
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      mats.forEach((m) => {
        if (!m) return;
        m.side = THREE.DoubleSide;
        m.needsUpdate = true;
      });
    });
    return;
  }
  const colors = meshes.map((m) => meshAlbedo(m, hex));
  const unique = new Set(colors.map((c) => c.getHexString()));
  const keepParts = unique.size > 1;
  meshes.forEach((child, i) => {
    child.frustumCulled = false;
    child.castShadow = false;
    child.receiveShadow = false;
    const col = keepParts ? colors[i] : new THREE.Color(hex || currentColor);
    if (child.geometry) {
      child.geometry.deleteAttribute("color");
      ensureOutwardNormals(child.geometry);
    }
    if (child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      mats.forEach((m) => m.dispose?.());
    }
    child.material = hologramMaterialFromColor(col);
  });
}

function loadGlb(url) {
  return new Promise((resolve, reject) => {
    loader.load(url, (gltf) => resolve(gltf.scene), undefined, reject);
  });
}

function applyColorToObject(obj, hex) {
  obj.traverse((child) => {
    if (!child.isMesh) return;
    if (child.geometry?.attributes?.color) child.geometry.deleteAttribute("color");
    if (child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      mats.forEach((m) => m.dispose());
    }
    child.material = hologramMaterial(hex);
  });
}

let lastLoadedGlbUrl = null;
let lastModelId = null;
let lastTextured = false;
// Longest edge the model should occupy, in metres. The backend decides it:
// CAD parts arrive life size, sculpts get an estimate, and "make it bigger"
// just moves this number.
const DEFAULT_DISPLAY_SIZE_M = 0.22;
let modelBaseMaxDim = 1;

function applyDisplaySize(obj, displaySizeM) {
  obj.scale.setScalar(displaySizeM / modelBaseMaxDim);
  const box = new THREE.Box3().setFromObject(obj);
  obj.position.sub(box.getCenter(new THREE.Vector3()));
}

async function setModelFromResponse(data) {
  if (data.action === "find_photos") {
    return;
  }
  const newColor = data.color || currentColor;
  const newGlbUrl = data.glb_url;
  const newModelId = data.model_id;
  const displaySizeM = data.display_size_m || DEFAULT_DISPLAY_SIZE_M;
  
  // Contract: if rebuilt && model_id && glb_url → load new GLB + swap mesh
  // Color path now always returns fresh model_id/glb_url with rebuilt:true
  const needsNewModel = data.rebuilt && newModelId && newGlbUrl;
  
  // Fallback: also load if glb_url changed (defensive)
  const glbUrlChanged = newGlbUrl && newGlbUrl !== lastLoadedGlbUrl;

  if (data.color) {
    currentColor = data.color;
  }

  if (needsNewModel || glbUrlChanged) {
    const bust = `${newGlbUrl}${newGlbUrl.includes("?") ? "&" : "?"}t=${Date.now()}`;
    console.log("[Percy] Loading new GLB:", bust, "model_id:", newModelId);

    try {
      const sceneObj = await loadGlb(bust);
      const box = new THREE.Box3().setFromObject(sceneObj);
      const size = box.getSize(new THREE.Vector3());
      modelBaseMaxDim = Math.max(size.x, size.y, size.z) || 1;
      applyDisplaySize(sceneObj, displaySizeM);

      const textured = Boolean(data.textured || data.backend === "mesh");
      lastTextured = textured;
      prepareVisibleMaterials(sceneObj, currentColor, textured);

      const wasGrabbing = grabbing;
      const savedSource = grabSource;
      const savedKey = grabHandKey;
      const savedOffset = grabbing ? grabOffset.clone() : null;
      const savedPos = modelRoot.position.clone();
      const savedQuat = modelRoot.quaternion.clone();

      if (currentModel) {
        modelRoot.remove(currentModel);
        currentModel.traverse((child) => {
          if (child.isMesh) {
            child.geometry?.dispose();
            if (child.material) {
              const mats = Array.isArray(child.material) ? child.material : [child.material];
              mats.forEach((m) => m.dispose());
            }
          }
        });
      }

      clearStaleGrabRefs();

      placeholder.visible = false;
      currentModel = sceneObj;
      modelRoot.add(currentModel);
      lastLoadedGlbUrl = newGlbUrl;
      lastModelId = newModelId;
      photoPicker.hide();

      if (wasGrabbing && savedOffset && savedSource) {
        grabOffset.copy(savedOffset);
        modelRoot.position.copy(savedPos);
        modelRoot.quaternion.copy(savedQuat);
        beginGrab(savedSource, savedKey);
        console.log("[Percy] Grab state rebound after mesh swap");
      } else if (renderer.xr.isPresenting) {
        placeModelInFrontOfUser();
      }

      console.log("[Percy] GLB loaded and applied successfully, model_id:", newModelId);
    } catch (err) {
      console.error("[Percy] Failed to load GLB:", err);
      clearStaleGrabRefs();
    }
  } else if (data.action === "set_scale" && currentModel) {
    // Resizing a sculpt ships no new GLB — rescale what is already loaded.
    applyDisplaySize(currentModel, displaySizeM);
    console.log("[Percy] Resized to", displaySizeM.toFixed(3), "m");
  } else if (data.color && currentModel && !lastTextured) {
    // Color-only update (no new model) - apply hologram paint on CAD only
    applyColorToObject(currentModel, currentColor);
    console.log("[Percy] Applied color update:", currentColor);
  }
}

const arBtn = ARButton.createButton(renderer, {
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
  if (!currentModel) placeholder.visible = true;
  setStatus("Entering passthrough AR…", true);
});

renderer.xr.addEventListener("sessionend", () => {
  renderer.setClearColor(0x0a0c12, 1);
  needsUserPlacement = false;
  setStatus("AR ended — tap Enter AR again.");
  voiceIndicatorGroup.visible = false;
});

renderer.setClearColor(0x0a0c12, 1);

const controllerModelFactory = new XRControllerModelFactory();
const handModelFactory = new XRHandModelFactory();

function setupHand(index) {
  const controller = renderer.xr.getController(index);
  scene.add(controller);

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

  const pinchAnchor = new THREE.Object3D();
  scene.add(pinchAnchor);

  return { controller, grip, hand, pinchAnchor, index, ray };
}

const left = setupHand(0);
const right = setupHand(1);

let grabSource = null;
let grabHandKey = -1;
const grabOffset = new THREE.Matrix4();
const tempMatrix = new THREE.Matrix4();
const _thumb = new THREE.Vector3();
const _indexTip = new THREE.Vector3();
const _pinch = new THREE.Vector3();
const _modelCenter = new THREE.Vector3();
const _wristQuat = new THREE.Quaternion();
let wasPinching = { 0: false, 1: false };
let pinchStartTime = { 0: 0, 1: 0 };

const PINCH_THRESHOLD_START = 0.032;
const PINCH_THRESHOLD_END = 0.045;
const PINCH_MIN_DURATION_MS = 50;
const GRAB_RANGE = 0.20;

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

function nearModel(worldPos, pad = GRAB_RANGE) {
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
  halo.material.color.setHex(0x00ff88);
}

function endGrab() {
  grabbing = false;
  grabSource = null;
  grabHandKey = -1;
  halo.material.opacity = 0.0;
  halo.material.color.setHex(0x4f8cff);
}

function clearStaleGrabRefs() {
  if (grabbing && !modelInteractTarget()) {
    endGrab();
  }
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
    if (wasPinching[key]) {
      if (key === 0) percy.endTalk();
      if (grabHandKey === key) endGrab();
    }
    wasPinching[key] = false;
    pinchStartTime[key] = 0;
    return;
  }

  const now = performance.now();
  const pos = handEntry.pinchAnchor.position;

  const isPinching = gap < PINCH_THRESHOLD_START;
  const wasOpen = gap > PINCH_THRESHOLD_END;

  if (key === 1 && photoPicker.isOpen) {
    if (isPinching && !wasPinching[key]) {
      photoPicker.beginPinch(pos);
    } else if (isPinching && wasPinching[key]) {
      photoPicker.movePinch(pos);
    }
    if (wasOpen && wasPinching[key]) {
      const picked = photoPicker.endPinch(pos);
      if (picked) buildFromPickedPhoto(picked);
    }
    wasPinching[key] = isPinching;
    return;
  }

  if (key === 1 && !grabbing) {
    getModelCenter(_modelCenter);
    const dist = pos.distanceTo(_modelCenter);
    if (dist < GRAB_RANGE) {
      halo.material.opacity = Math.max(halo.material.opacity, 0.4 + (1 - dist / GRAB_RANGE) * 0.3);
    }
  }

  if (isPinching && !wasPinching[key]) {
    pinchStartTime[key] = now;
    if (key === 0) percy.beginTalk();
  }

  if (key === 1 && isPinching && wasPinching[key] && !grabbing) {
    const duration = now - pinchStartTime[key];
    if (duration >= PINCH_MIN_DURATION_MS && nearModel(pos, GRAB_RANGE)) {
      beginGrab(handEntry.pinchAnchor, key);
    }
  }

  if (wasOpen && wasPinching[key]) {
    if (key === 0) percy.endTalk();
    if (grabHandKey === key) endGrab();
  }

  wasPinching[key] = isPinching;
}

left.controller.addEventListener("selectstart", () => {
  percy.beginTalk();
});
left.controller.addEventListener("selectend", () => {
  percy.endTalk();
});

right.controller.addEventListener("selectstart", () => {
  right.controller.getWorldPosition(_pinch);
  if (photoPicker.isOpen) {
    photoPicker.beginPinch(_pinch);
    return;
  }
  if (nearModel(_pinch, GRAB_RANGE)) beginGrab(right.controller, -1);
});
right.controller.addEventListener("selectend", () => {
  if (photoPicker.isOpen) {
    right.controller.getWorldPosition(_pinch);
    const picked = photoPicker.endPinch(_pinch);
    if (picked) buildFromPickedPhoto(picked);
    return;
  }
  if (grabSource === right.controller) endGrab();
});

const percy = new PercyAssistant({
  onModelUpdate: (data) => setModelFromResponse(data),
  onStatusMessage: (msg, ok) => setStatus(msg, ok),
  onPhotoCandidates: (cands) => {
    if (!cands || !cands.length) {
      photoPicker.hide();
      return;
    }
    photoPicker.show(cands, renderer).then(() => {
      setStatus("Pinch to pick a photo. Drag sideways to swipe.", true);
    }).catch((err) => {
      console.error("[Percy] Photo picker failed:", err);
      setStatus("Found the photo but couldn't show it.", false);
    });
  },
  onTalkingChange: (talking) => {
    const mic = document.getElementById("micButton");
    mic?.classList.toggle("talking", talking);
  },
});

voiceState.subscribe((snapshot) => {
  updateVoiceIndicator(snapshot.state);
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

let pulsePhase = 0;
let lastTick = performance.now();

renderer.setAnimationLoop(() => {
  if (needsUserPlacement && renderer.xr.isPresenting) {
    placeFrameCount += 1;
    if (placeFrameCount >= 3) {
      placeModelInFrontOfUser(0.7);
      needsUserPlacement = false;
      setStatus("Hold left trigger to talk. Right pinch the model to move it.", true);
    }
  }

  layoutVoiceIndicator();

  if (voiceIndicatorGroup.visible) {
    pulsePhase += 0.08;
    const state = voiceState.state;
    if (state === "listening") {
      const pulse = 1.0 + 0.15 * Math.sin(pulsePhase * 2);
      voiceRing.scale.setScalar(pulse);
      voiceDot.scale.setScalar(pulse);
    } else if (state === "thinking") {
      const spin = pulsePhase * 0.5;
      voiceRing.rotation.z = spin;
    } else if (state === "speaking") {
      const pulse = 1.0 + 0.1 * Math.sin(pulsePhase * 3);
      voiceDot.scale.setScalar(pulse);
    } else {
      voiceRing.material.opacity = 0.85;
      voiceDot.material.opacity = 0.9;
    }
  }

  pollHand(left, 0);
  pollHand(right, 1);
  updateGrab();
  const nowTick = performance.now();
  photoPicker.tick((nowTick - lastTick) / 1000);
  lastTick = nowTick;

  if (!grabbing) {
    const decay = 0.92;
    halo.material.opacity *= decay;
    if (halo.material.opacity < 0.01) halo.material.opacity = 0;
  }

  renderer.render(scene, camera);
});

let desktopMode = null;
const pointer = new THREE.Vector2();
const raycaster = new THREE.Raycaster();
const lastPtr = new THREE.Vector2();

renderer.domElement.addEventListener("pointerdown", (e) => {
  if (renderer.xr.isPresenting) return;
  pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  if (photoPicker.isOpen) {
    const hits = raycaster.intersectObjects(photoPicker.cards, true);
    const card = hits[0]?.object;
    const fileId = card?.userData?.fileId || card?.parent?.userData?.fileId;
    if (fileId) buildFromPickedPhoto(fileId);
    return;
  }
  const target = modelInteractTarget();
  if (target && raycaster.intersectObject(target, true).length) {
    desktopMode = e.shiftKey ? "spin" : "move";
    lastPtr.set(e.clientX, e.clientY);
  }
});
window.addEventListener("pointerup", () => {
  desktopMode = null;
});
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

window.addEventListener("keydown", (e) => {
  if (e.key === "m" || e.key === "M") {
    percy.toggleMute();
    return;
  }
  if (e.code === "Space" && !e.repeat) {
    e.preventDefault();
    percy.beginTalk();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    percy.endTalk();
  }
});

const micButton = document.getElementById("micButton");
if (micButton) {
  const holdStart = (e) => {
    e.preventDefault();
    percy.beginTalk();
  };
  const holdEnd = (e) => {
    e.preventDefault();
    percy.endTalk();
  };
  micButton.addEventListener("pointerdown", holdStart);
  micButton.addEventListener("pointerup", holdEnd);
  micButton.addEventListener("pointercancel", holdEnd);
  micButton.addEventListener("pointerleave", (e) => {
    if (e.buttons) holdEnd(e);
  });
}

fetch(`${API_BASE}/api/health`)
  .then((r) => r.json())
  .then((h) => {
    const status = `CadQuery ${h.cadquery ? "✓" : "✗"} · Voice ${h.stt && h.tts ? "✓" : "partial"}`;
    setStatus(`Percy ready. Hold mic / left trigger to talk. ${status}`, true);
    percy.start();
  })
  .catch(() => {
    setStatus("API offline — start backend on :8000");
  });

window.percyAssistant = percy;
window.voiceState = voiceState;
window.VoiceStates = VoiceStates;

window.PerceptionCAD = {
  VoiceState: VoiceStates,
  getVoiceState: () => voiceState.state,
  setVoiceState: (state) => {
    if (state === VoiceStates.IDLE) voiceState.toIdle();
    else if (state === VoiceStates.LISTENING) voiceState.toListening();
    else if (state === VoiceStates.THINKING) voiceState.toThinking();
    else if (state === VoiceStates.SPEAKING) voiceState.toSpeaking();
    else if (state === VoiceStates.ERROR) voiceState.toError();
  },
  isMuted: () => voiceState.isMuted,
  toggleMute: () => percy.toggleMute(),
  startListening: () => percy.beginTalk(),
  stopListening: () => percy.endTalk(),
  beginTalk: () => percy.beginTalk(),
  endTalk: () => percy.endTalk(),
  sendCommand: (text) => percy.sendTextCommand(text),
  onVoiceStateChange: (callback) => voiceState.subscribe(callback),
  
  isGrabbing: () => grabbing,
  placeModelInFrontOfUser,
  clearStaleGrabRefs: clearStaleGrabRefs,
  rebindGrabAfterMeshSwap: () => {
    if (grabbing && grabSource) {
      const wasSource = grabSource;
      const wasKey = grabHandKey;
      endGrab();
      beginGrab(wasSource, wasKey);
    }
  },
};
