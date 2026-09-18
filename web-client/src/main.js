/**
 * Perception CAD - WebXR Passthrough AR Client
 *
 * Voice-driven CAD on Quest 3 via passthrough AR.
 * Percy wake word → VAD listen → STT/Gemini → CadQuery → GLB render
 *
 * Chrome stripped: No tutorial UI, no PTT buttons.
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
  wake_tentative: 0xffab00, // Amber for tentative wake detection
  wake_miss: 0xff6d00,      // Orange flash for missed wake / retry
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

let wakeTentativeFlashPhase = 0;
let wakeMissFlashActive = false;

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
  } else if (state === "wake_tentative") {
    wakeTentativeFlashPhase = 0;
    voiceRing.scale.setScalar(1.15);
    voiceDot.scale.setScalar(1.2);
  } else if (state === "wake_miss") {
    wakeMissFlashActive = true;
    voiceRing.scale.setScalar(1.3);
    voiceDot.scale.setScalar(1.4);
  } else {
    voiceRing.scale.setScalar(1.0);
    voiceDot.scale.setScalar(1.0);
    wakeMissFlashActive = false;
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

let lastLoadedGlbUrl = null;
let lastModelId = null;

async function setModelFromResponse(data) {
  const newColor = data.color || currentColor;
  const newGlbUrl = data.glb_url;
  const newModelId = data.model_id;
  
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
      const maxDim = Math.max(size.x, size.y, size.z) || 1;
      sceneObj.scale.setScalar(0.22 / maxDim);
      box.setFromObject(sceneObj);
      sceneObj.position.sub(box.getCenter(new THREE.Vector3()));

      prepareVisibleMaterials(sceneObj, currentColor);

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
  } else if (data.color && currentModel) {
    // Color-only update (no new model) - apply directly to existing mesh
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
    if (wasPinching[key] && grabHandKey === key) endGrab();
    wasPinching[key] = false;
    pinchStartTime[key] = 0;
    return;
  }

  const now = performance.now();
  const pos = handEntry.pinchAnchor.position;

  const isPinching = gap < PINCH_THRESHOLD_START;
  const wasOpen = gap > PINCH_THRESHOLD_END;

  if (!grabbing) {
    getModelCenter(_modelCenter);
    const dist = pos.distanceTo(_modelCenter);
    if (dist < GRAB_RANGE) {
      halo.material.opacity = Math.max(halo.material.opacity, 0.4 + (1 - dist / GRAB_RANGE) * 0.3);
    }
  }

  if (isPinching && !wasPinching[key]) {
    pinchStartTime[key] = now;
  }

  if (isPinching && wasPinching[key] && !grabbing) {
    const duration = now - pinchStartTime[key];
    if (duration >= PINCH_MIN_DURATION_MS && nearModel(pos, GRAB_RANGE)) {
      beginGrab(handEntry.pinchAnchor, key);
    }
  }

  if (wasOpen && wasPinching[key] && grabHandKey === key) {
    endGrab();
  }

  wasPinching[key] = isPinching;
}

for (const entry of [left, right]) {
  entry.controller.addEventListener("selectstart", () => {
    entry.controller.getWorldPosition(_pinch);
    if (nearModel(_pinch, GRAB_RANGE)) beginGrab(entry.controller, -1);
  });
  entry.controller.addEventListener("selectend", () => {
    if (grabSource === entry.controller) endGrab();
  });
}

const percy = new PercyAssistant({
  onModelUpdate: (data) => setModelFromResponse(data),
  onStatusMessage: (msg, ok) => setStatus(msg, ok),
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

renderer.setAnimationLoop(() => {
  if (needsUserPlacement && renderer.xr.isPresenting) {
    placeFrameCount += 1;
    if (placeFrameCount >= 3) {
      placeModelInFrontOfUser(0.7);
      needsUserPlacement = false;
      setStatus("Say 'Hey Percy' to give a command. Pinch model to grab.", true);
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
    } else if (state === "wake_tentative") {
      wakeTentativeFlashPhase += 0.15;
      const pulse = 1.1 + 0.2 * Math.sin(wakeTentativeFlashPhase * 4);
      const opacity = 0.6 + 0.35 * Math.sin(wakeTentativeFlashPhase * 4);
      voiceRing.scale.setScalar(pulse);
      voiceDot.scale.setScalar(pulse);
      voiceRing.material.opacity = opacity;
      voiceDot.material.opacity = opacity;
    } else if (state === "wake_miss") {
      const flashPulse = 1.2 + 0.25 * Math.sin(pulsePhase * 6);
      voiceRing.scale.setScalar(flashPulse);
      voiceDot.scale.setScalar(flashPulse);
      voiceRing.material.opacity = 0.9;
      voiceDot.material.opacity = 0.95;
    } else {
      voiceRing.material.opacity = 0.85;
      voiceDot.material.opacity = 0.9;
    }
  }

  pollHand(left, 0);
  pollHand(right, 1);
  updateGrab();

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
  }
});

fetch(`${API_BASE}/api/health`)
  .then((r) => r.json())
  .then((h) => {
    const status = `CadQuery ${h.cadquery ? "✓" : "✗"} · Voice ${h.stt && h.tts ? "✓" : "partial"}`;
    setStatus(`Percy ready. Say 'Hey Percy' to activate. ${status}`, true);
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
    else if (state === VoiceStates.WAKE_TENTATIVE) voiceState.toWakeTentative();
    else if (state === VoiceStates.WAKE_MISS) voiceState.toWakeMiss();
  },
  isMuted: () => voiceState.isMuted,
  toggleMute: () => percy.toggleMute(),
  startListening: () => percy._onWakeWord(),
  stopListening: () => {},
  sendCommand: (text) => percy.sendTextCommand(text),
  onVoiceStateChange: (callback) => voiceState.subscribe(callback),
  
  isGrabbing: () => grabbing,
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
