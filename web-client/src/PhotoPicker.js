/**
 * Floating photo carousel in AR.
 * Pinch-drag sideways to swipe. Pinch-release without moving to pick.
 */

import * as THREE from "three";

const CARD_W = 0.28;
const CARD_H = 0.28;
const GAP = 0.06;
const SWIPE_M = 0.03;
const PLACE_DIST = 0.75;

export class PhotoPicker {
  constructor(scene, getCamera) {
    this.scene = scene;
    this.getCamera = getCamera;
    this.group = new THREE.Group();
    this.group.visible = false;
    scene.add(this.group);

    this.candidates = [];
    this.cards = [];
    this.offset = 0;
    this.velocity = 0;
    this._pinchStart = null;
    this._startOffset = 0;
    this._moved = 0;
    this._startLateral = 0;
    this.busy = false;
  }

  get isOpen() {
    return this.group.visible && this.cards.length > 0 && !this.busy;
  }

  async show(candidates, renderer) {
    this.clear();
    this.candidates = candidates || [];
    if (!this.candidates.length) return;

    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");

    for (let i = 0; i < this.candidates.length; i++) {
      const c = this.candidates[i];
      const tex = await new Promise((resolve, reject) => {
        loader.load(c.image_url || c.preview_url, resolve, undefined, reject);
      }).catch(() => null);
      if (tex) {
        tex.colorSpace = THREE.SRGBColorSpace;
      }
      const mat = new THREE.MeshBasicMaterial({
        map: tex,
        color: tex ? 0xffffff : 0x88aaff,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.95,
      });
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(CARD_W, CARD_H), mat);
      mesh.userData.fileId = c.id;
      mesh.userData.index = i;
      this.group.add(mesh);
      this.cards.push(mesh);

      const rim = new THREE.Mesh(
        new THREE.PlaneGeometry(CARD_W + 0.012, CARD_H + 0.012),
        new THREE.MeshBasicMaterial({
          color: 0x66ccff,
          transparent: true,
          opacity: 0.35,
          side: THREE.DoubleSide,
        })
      );
      rim.position.z = -0.002;
      mesh.add(rim);
    }

    this.offset = 0;
    this.velocity = 0;
    this._layout();
    this._placeInFront(renderer);
    this.group.visible = true;
  }

  keepOnly(fileId) {
    for (const card of this.cards) {
      if (card.userData.fileId !== fileId) {
        card.visible = false;
      }
    }
    this.offset = 0;
    this.velocity = 0;
    this._layout();
  }

  hide() {
    this.clear();
    this.group.visible = false;
  }

  clear() {
    for (const card of this.cards) {
      card.geometry.dispose();
      const mats = [card.material];
      card.traverse((ch) => {
        if (ch.material && ch !== card) mats.push(ch.material);
        if (ch.geometry && ch !== card) ch.geometry.dispose();
      });
      mats.forEach((m) => {
        m.map?.dispose();
        m.dispose();
      });
      this.group.remove(card);
    }
    this.cards = [];
    this.candidates = [];
    this.offset = 0;
    this.velocity = 0;
    this._pinchStart = null;
    this.busy = false;
  }

  beginPinch(worldPos) {
    this._pinchStart = worldPos.clone();
    this._startOffset = this.offset;
    this._startLateral = this._lateral(worldPos);
    this._moved = 0;
    this.velocity = 0;
  }

  movePinch(worldPos) {
    if (!this._pinchStart) return;
    const dx = this._lateral(worldPos) - this._startLateral;
    this._moved = Math.max(this._moved, Math.abs(dx));
    this.offset = this._startOffset + dx;
    this._layout();
  }

  endPinch(worldPos) {
    if (!this._pinchStart) return null;
    const dx = this._lateral(worldPos) - this._startLateral;
    this._moved = Math.max(this._moved, Math.abs(dx));
    this._pinchStart = null;
    if (this._moved < SWIPE_M) {
      return this._nearest(worldPos);
    }
    this.velocity = dx * 4;
    return null;
  }

  _lateral(worldPos) {
    const cam = this.getCamera();
    const q = new THREE.Quaternion();
    cam.getWorldQuaternion(q);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(q);
    right.y = 0;
    if (right.lengthSq() < 1e-6) return worldPos.x;
    right.normalize();
    return worldPos.dot(right);
  }

  tick(dt) {
    if (!this.isOpen) return;
    if (this._pinchStart) return;
    if (Math.abs(this.velocity) > 0.001) {
      this.offset += this.velocity * dt;
      this.velocity *= 0.92;
      this._clamp();
      this._layout();
    }
  }

  _nearest(worldPos) {
    let best = null;
    let bestD = Infinity;
    for (const card of this.cards) {
      if (!card.visible) continue;
      const p = new THREE.Vector3();
      card.getWorldPosition(p);
      const d = p.distanceTo(worldPos);
      if (d < bestD) {
        bestD = d;
        best = card;
      }
    }
    if (best && bestD < 0.45) return best.userData.fileId;
    if (this.cards.length === 1) return this.cards[0].userData.fileId;
    // One card in view: pick the most centered.
    let centered = this.cards[0];
    let minAbs = Infinity;
    for (const card of this.cards) {
      const a = Math.abs(card.position.x);
      if (a < minAbs) {
        minAbs = a;
        centered = card;
      }
    }
    return centered.userData.fileId;
  }

  _layout() {
    const step = CARD_W + GAP;
    const n = this.cards.length;
    const vis = this.cards.filter((c) => c.visible);
    vis.forEach((card, i) => {
      const x = (i - (vis.length - 1) / 2) * step + this.offset;
      card.position.set(x, 0, 0);
      const focus = 1 - Math.min(1, Math.abs(x) / (step * 1.4));
      card.scale.setScalar(0.92 + 0.12 * focus);
    });
    void n;
  }

  _clamp() {
    const vis = this.cards.filter((c) => c.visible).length;
    if (vis <= 1) {
      this.offset = 0;
      return;
    }
    const span = ((vis - 1) / 2) * (CARD_W + GAP);
    this.offset = Math.max(-span, Math.min(span, this.offset));
  }

  _placeInFront(renderer) {
    const cam = renderer?.xr?.isPresenting ? renderer.xr.getCamera() : this.getCamera();
    cam.updateMatrixWorld(true);
    const pos = new THREE.Vector3();
    const quat = new THREE.Quaternion();
    cam.getWorldPosition(pos);
    cam.getWorldQuaternion(quat);
    const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(quat).normalize();
    this.group.position.copy(pos).addScaledVector(forward, PLACE_DIST);
    this.group.position.y = pos.y - 0.05;
    this.group.quaternion.copy(quat);
  }
}
