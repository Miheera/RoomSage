import * as THREE from "three";

/**
 * First-person "walk through the room" controls.
 *  - Desktop: drag to look, W A S D / arrows to move, Q / E down / up, Shift to go faster.
 *  - Touch:   drag to look, on-screen pad calls setMove(forward, right).
 * Movement is horizontal (like walking); only look direction uses pitch.
 */
export class WalkControls {
  constructor(camera, dom) {
    this.camera = camera;
    this.dom = dom;
    this.enabled = false;
    this.speed = 1.2; // metres-ish per second (scaled to the room by the viewer)
    this.keys = new Set();
    this.pad = { f: 0, r: 0 };
    this.euler = new THREE.Euler(0, 0, 0, "YXZ");
    this.dragging = null;
    this._fwd = new THREE.Vector3();
    this._right = new THREE.Vector3();

    this._down = (e) => {
      if (!this.enabled) return;
      this.dragging = { id: e.pointerId, x: e.clientX, y: e.clientY };
      dom.setPointerCapture?.(e.pointerId);
    };
    this._move = (e) => {
      if (!this.enabled || !this.dragging || this.dragging.id !== e.pointerId) return;
      const dx = e.clientX - this.dragging.x;
      const dy = e.clientY - this.dragging.y;
      this.dragging.x = e.clientX;
      this.dragging.y = e.clientY;
      this.euler.y -= dx * 0.0035;
      this.euler.x = THREE.MathUtils.clamp(this.euler.x - dy * 0.0035, -1.35, 1.35);
      this.camera.quaternion.setFromEuler(this.euler);
    };
    this._up = (e) => {
      if (this.dragging?.id === e.pointerId) this.dragging = null;
    };
    this._keydown = (e) => {
      if (!this.enabled || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      this.keys.add(e.code);
      if (e.code.startsWith("Arrow") || e.code === "Space") e.preventDefault();
    };
    this._keyup = (e) => this.keys.delete(e.code);
    this._blur = () => this.keys.clear();

    dom.addEventListener("pointerdown", this._down);
    dom.addEventListener("pointermove", this._move);
    dom.addEventListener("pointerup", this._up);
    dom.addEventListener("pointercancel", this._up);
    window.addEventListener("keydown", this._keydown);
    window.addEventListener("keyup", this._keyup);
    window.addEventListener("blur", this._blur);
  }

  enable() {
    this.euler.setFromQuaternion(this.camera.quaternion, "YXZ");
    this.enabled = true;
  }

  disable() {
    this.enabled = false;
    this.keys.clear();
    this.pad.f = this.pad.r = 0;
    this.dragging = null;
  }

  setMove(f, r) {
    this.pad.f = f;
    this.pad.r = r;
  }

  update(dt) {
    if (!this.enabled) return;
    const k = this.keys;
    let f = this.pad.f + ((k.has("KeyW") || k.has("ArrowUp")) - (k.has("KeyS") || k.has("ArrowDown")));
    let r = this.pad.r + ((k.has("KeyD") || k.has("ArrowRight")) - (k.has("KeyA") || k.has("ArrowLeft")));
    const up = (k.has("KeyE") ? 1 : 0) - (k.has("KeyQ") ? 1 : 0);
    if (!f && !r && !up) return;

    const boost = k.has("ShiftLeft") || k.has("ShiftRight") ? 2.5 : 1;
    this.camera.getWorldDirection(this._fwd);
    this._fwd.y = 0;
    if (this._fwd.lengthSq() < 1e-6) this._fwd.set(0, 0, -1);
    this._fwd.normalize();
    this._right.crossVectors(this._fwd, this.camera.up).normalize();

    const step = this.speed * boost * dt;
    this.camera.position.addScaledVector(this._fwd, THREE.MathUtils.clamp(f, -1, 1) * step);
    this.camera.position.addScaledVector(this._right, THREE.MathUtils.clamp(r, -1, 1) * step);
    this.camera.position.y += up * step;
  }

  dispose() {
    const d = this.dom;
    d.removeEventListener("pointerdown", this._down);
    d.removeEventListener("pointermove", this._move);
    d.removeEventListener("pointerup", this._up);
    d.removeEventListener("pointercancel", this._up);
    window.removeEventListener("keydown", this._keydown);
    window.removeEventListener("keyup", this._keyup);
    window.removeEventListener("blur", this._blur);
  }
}
