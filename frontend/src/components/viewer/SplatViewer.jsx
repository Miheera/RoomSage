import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import * as Spark from "@sparkjsdev/spark";
import { WalkControls } from "../../lib/walkControls";
import { loadSplatBytes } from "../../lib/splatCache";

const { SplatMesh, SparkRenderer, SplatFileType } = Spark;

/**
 * Nerfstudio exports can come out with any "up" axis, so the user can cycle
 * through 4 presets. Quaternions are (x, y, z, w).
 *   0: 180deg about X  (the usual .splat convention)   1: none
 *   2: +90deg about X                                   3: -90deg about X
 */
const ORIENTATIONS = [
  [1, 0, 0, 0],
  [0, 0, 0, 1],
  [Math.SQRT1_2, 0, 0, Math.SQRT1_2],
  [-Math.SQRT1_2, 0, 0, Math.SQRT1_2],
];
export const ORIENTATION_COUNT = ORIENTATIONS.length;

const isTouchDevice = () => window.matchMedia?.("(pointer: coarse)").matches;

/**
 * Props
 *  models        [{ id, url, cacheKey }]  - every model is loaded once and kept in GPU memory
 *  activeId      which model is visible. Switching Before/After is instant (just a visibility flip)
 *  mode          "orbit" | "walk"
 *  lowPower      cap resolution at 1x for weak GPUs / phones
 *  orientation   0..3 (see above)
 *  onModelState  (id, { status: loading|preparing|ready|error, progress, error })
 *  onStats       ({ fps, dpr }) once a second
 *  onFatal       (message)  e.g. WebGL not available
 * Ref API: snapshot(), getPlacement(), resetView(), setMove(f, r)
 */
const SplatViewer = forwardRef(function SplatViewer(
  { models, activeId, mode, lowPower, orientation, onModelState, onStats, onFatal },
  ref
) {
  const mountRef = useRef(null);
  const S = useRef(null); // three.js objects
  const live = useRef({});
  live.current = { activeId, lowPower, orientation, onModelState, onStats, onFatal, mode };

  // ---------- one-time scene setup ----------
  useEffect(() => {
    const mount = mountRef.current;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: false, // splats don't benefit from MSAA; this is a big mobile win
        powerPreference: "high-performance",
        stencil: false,
      });
    } catch {
      live.current.onFatal?.("This browser can't run 3D graphics (WebGL 2 is unavailable). Try Chrome, Edge, Safari 16+ or Firefox.");
      return undefined;
    }
    renderer.setClearColor(0x14221f, 1);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(65, 1, 0.02, 500);
    camera.position.set(0, 0.5, 3);
    const spark = new SparkRenderer({ renderer });
    scene.add(spark);

    const orbit = new OrbitControls(camera, renderer.domElement);
    orbit.enableDamping = true;
    orbit.dampingFactor = 0.08;
    orbit.screenSpacePanning = true;
    const walk = new WalkControls(camera, renderer.domElement);

    const s = {
      renderer, scene, camera, orbit, walk, spark,
      meshes: new Map(),
      orbitDistance: 2,
      dpr: 1,
      maxDpr: 1,
      raf: 0,
    };
    S.current = s;

    const computeMaxDpr = () =>
      live.current.lowPower ? 1 : Math.min(window.devicePixelRatio || 1, isTouchDevice() ? 1.5 : 2);
    s.applySize = () => {
      const w = mount.clientWidth || 1;
      const h = mount.clientHeight || 1;
      renderer.setPixelRatio(s.dpr);
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    s.maxDpr = computeMaxDpr();
    s.dpr = s.maxDpr;
    s.refreshDprCap = () => {
      s.maxDpr = computeMaxDpr();
      s.dpr = Math.min(s.dpr, s.maxDpr);
      s.applySize();
    };
    s.applySize();

    // Fit the camera to a model. Uses the splat centres' bounding box.
    s.frame = (mesh) => {
      mesh.updateMatrixWorld(true);
      let box = null;
      try {
        box = mesh.getBoundingBox?.(true)?.clone();
      } catch {
        /* older builds: fall back to defaults */
      }
      if (!box || box.isEmpty()) {
        camera.position.set(0, 0.5, 3);
        orbit.target.set(0, 0, 0);
        s.orbitDistance = 3;
        walk.speed = 1.2;
        orbit.update();
        return;
      }
      box.applyMatrix4(mesh.matrixWorld);
      const c = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const r = Math.max(size.x, size.z) / 2 || 1;
      camera.position.set(c.x, c.y, c.z + r * 0.55);
      camera.lookAt(c);
      orbit.target.copy(c);
      orbit.minDistance = Math.max(0.05, r * 0.02);
      orbit.maxDistance = r * 8;
      s.orbitDistance = r * 0.55;
      walk.speed = Math.max(0.4, size.length() * 0.07);
      camera.near = Math.max(0.01, r * 0.002);
      camera.far = r * 40;
      camera.updateProjectionMatrix();
      orbit.update();
      if (live.current.mode === "walk") walk.enable();
    };

    const ro = new ResizeObserver(() => s.applySize());
    ro.observe(mount);

    // Adaptive resolution: drop pixel ratio when frames get slow, restore it when there's headroom.
    let last = performance.now();
    let ema = 16;
    let frames = 0;
    let statClock = last;
    let statFrames = 0;
    let cooldown = 0;
    const tick = (now) => {
      s.raf = requestAnimationFrame(tick);
      const dtMs = now - last;
      last = now;
      if (dtMs > 500) return; // tab was backgrounded
      const dt = Math.min(0.1, dtMs / 1000);
      ema = ema * 0.92 + dtMs * 0.08;

      if (live.current.mode === "walk") walk.update(dt);
      else orbit.update();
      renderer.render(scene, camera);

      frames += 1;
      statFrames += 1;
      if (cooldown > 0) cooldown -= 1;
      if (frames % 45 === 0 && cooldown === 0) {
        if (ema > 26 && s.dpr > 0.6) {
          s.dpr = Math.max(0.6, s.dpr - 0.25);
          s.applySize();
          cooldown = 120;
        } else if (ema < 12 && s.dpr < s.maxDpr) {
          s.dpr = Math.min(s.maxDpr, s.dpr + 0.25);
          s.applySize();
          cooldown = 240;
        }
      }
      if (now - statClock >= 1000) {
        live.current.onStats?.({ fps: Math.round((statFrames * 1000) / (now - statClock)), dpr: s.dpr });
        statClock = now;
        statFrames = 0;
      }
    };
    s.raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(s.raf);
      ro.disconnect();
      orbit.dispose();
      walk.dispose();
      s.meshes.forEach((e) => {
        e.aborted = true;
        e.abort?.abort();
        e.mesh?.dispose?.();
      });
      s.meshes.clear();
      renderer.dispose();
      renderer.domElement.remove();
      S.current = null;
    };
  }, []);

  // ---------- load models (active first, others in the background) ----------
  const signature = models.map((m) => `${m.id}:${m.cacheKey ?? m.url}`).join("|");
  useEffect(() => {
    const s = S.current;
    if (!s) return undefined;
    let cancelled = false;
    const emit = (id, st) => live.current.onModelState?.(id, st);

    const applyOrientation = (mesh) => mesh.quaternion.set(...ORIENTATIONS[live.current.orientation % 4]);

    const ensure = (m) => {
      const key = m.cacheKey ?? m.url;
      const existing = s.meshes.get(m.id);
      if (existing && existing.key === key) return existing.promise;
      if (existing) {
        existing.aborted = true;
        existing.abort?.abort();
        if (existing.mesh) {
          s.scene.remove(existing.mesh);
          existing.mesh.dispose?.();
        }
        s.meshes.delete(m.id);
      }
      const entry = { key, mesh: null, aborted: false, abort: new AbortController() };
      s.meshes.set(m.id, entry);
      entry.promise = (async () => {
        emit(m.id, { status: "loading", progress: 0 });
        try {
          const bytes = await loadSplatBytes(m.url, m.cacheKey, {
            signal: entry.abort.signal,
            onProgress: (p) => emit(m.id, { status: "loading", progress: p * 0.85 }),
          });
          if (entry.aborted) return;
          emit(m.id, { status: "preparing", progress: 0.9 });
          const mesh = new SplatMesh({
            fileBytes: bytes,
            fileName: "model.splat",
            ...(SplatFileType ? { fileType: SplatFileType.SPLAT } : {}),
          });
          await mesh.initialized;
          if (entry.aborted) {
            mesh.dispose?.();
            return;
          }
          applyOrientation(mesh);
          mesh.visible = m.id === live.current.activeId;
          s.scene.add(mesh);
          entry.mesh = mesh;
          if (s.meshes.size === 1 || m.id === live.current.activeId) s.frame(mesh);
          emit(m.id, { status: "ready", progress: 1 });
        } catch (e) {
          if (entry.aborted || e.name === "AbortError") return;
          s.meshes.delete(m.id);
          emit(m.id, { status: "error", error: e.message || "Couldn't load the 3D model." });
        }
      })();
      return entry.promise;
    };

    (async () => {
      const ordered = [...models].sort((a, b) => Number(b.id === live.current.activeId) - Number(a.id === live.current.activeId));
      for (const m of ordered) {
        if (cancelled) return;
        await ensure(m);
      }
    })();

    // drop meshes for models that are no longer listed
    const ids = new Set(models.map((m) => m.id));
    s.meshes.forEach((entry, id) => {
      if (!ids.has(id)) {
        entry.aborted = true;
        entry.abort?.abort();
        if (entry.mesh) {
          s.scene.remove(entry.mesh);
          entry.mesh.dispose?.();
        }
        s.meshes.delete(id);
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // ---------- visibility, mode, orientation, quality ----------
  useEffect(() => {
    const s = S.current;
    if (!s) return;
    s.meshes.forEach((e, id) => e.mesh && (e.mesh.visible = id === activeId));
  }, [activeId]);

  useEffect(() => {
    const s = S.current;
    if (!s) return;
    if (mode === "walk") {
      s.orbit.enabled = false;
      s.walk.enable();
    } else {
      s.walk.disable();
      const dir = new THREE.Vector3();
      s.camera.getWorldDirection(dir);
      s.orbit.target.copy(s.camera.position).addScaledVector(dir, s.orbitDistance);
      s.orbit.enabled = true;
      s.orbit.update();
    }
  }, [mode]);

  useEffect(() => {
    const s = S.current;
    if (!s) return;
    let first = null;
    s.meshes.forEach((e) => {
      if (e.mesh) {
        e.mesh.quaternion.set(...ORIENTATIONS[orientation % 4]);
        first = first || e.mesh;
      }
    });
    const active = s.meshes.get(live.current.activeId)?.mesh || first;
    if (active) s.frame(active);
  }, [orientation]);

  useEffect(() => {
    S.current?.refreshDprCap();
  }, [lowPower]);

  // ---------- imperative API ----------
  useImperativeHandle(ref, () => ({
    /** JPEG data URL of the current view (for the share card). */
    snapshot() {
      const s = S.current;
      if (!s) return null;
      s.renderer.render(s.scene, s.camera); // must render in the same tick as toDataURL
      return s.renderer.domElement.toDataURL("image/jpeg", 0.9);
    },
    /** A point ~1.5 units in front of the camera, in the model's own coordinates (what the backend expects). */
    getPlacement() {
      const s = S.current;
      const mesh = s?.meshes.get(live.current.activeId)?.mesh || [...(s?.meshes.values() || [])].find((e) => e.mesh)?.mesh;
      if (!s || !mesh) return null;
      const dir = new THREE.Vector3();
      s.camera.getWorldDirection(dir);
      const p = s.camera.position.clone().addScaledVector(dir, Math.max(0.8, s.orbitDistance * 0.6));
      mesh.updateMatrixWorld(true);
      return mesh.worldToLocal(p).toArray();
    },
    resetView() {
      const s = S.current;
      const mesh = s?.meshes.get(live.current.activeId)?.mesh;
      if (mesh) s.frame(mesh);
    },
    setMove(f, r) {
      S.current?.walk.setMove(f, r);
    },
  }));

  return <div ref={mountRef} className="viewer-canvas" />;
});

export default SplatViewer;
