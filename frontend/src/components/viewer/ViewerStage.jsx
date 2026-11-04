import { useCallback, useMemo, useRef, useState } from "react";
import SplatViewer, { ORIENTATION_COUNT } from "./SplatViewer";
import Icon from "../Icon";

/**
 * Everything around the WebGL canvas: Before/After switch, mode + orientation + quality
 * controls, loading overlay, touch walk pad, and drag-and-drop of a local .splat for testing.
 *
 * `models` = [{ id: "before" | "after" | "local", label, url, cacheKey }]
 * `emptyState` = JSX shown when no model is available (e.g. job progress).
 */
export default function ViewerStage({ viewerRef, models, activeId, onActiveChange, emptyState, afterAvailable, afterPending }) {
  const stageRef = useRef(null);
  const fileInput = useRef(null);
  const [mode, setMode] = useState("orbit");
  const [orientation, setOrientation] = useState(0);
  const [lowPower, setLowPower] = useState(false);
  const [states, setStates] = useState({});
  const [stats, setStats] = useState(null);
  const [fatal, setFatal] = useState(null);
  const [local, setLocal] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  const allModels = useMemo(() => (local ? [local] : models), [local, models]);
  const shownId = local ? "local" : activeId;
  const current = states[shownId];

  const onModelState = useCallback((id, st) => setStates((s) => ({ ...s, [id]: st })), []);

  const openFile = (file) => {
    if (!file) return;
    if (!/\.splat$/i.test(file.name)) {
      setFatal("Only .splat files can be previewed here.");
      return;
    }
    setFatal(null);
    setLocal({ id: "local", label: file.name, url: URL.createObjectURL(file), cacheKey: null });
  };

  const fullscreen = () => {
    const el = stageRef.current;
    if (document.fullscreenElement) document.exitFullscreen();
    else el?.requestFullscreen?.();
  };

  const hasModels = allModels.length > 0;
  const loading = current && (current.status === "loading" || current.status === "preparing");

  return (
    <div
      ref={stageRef}
      className={`stage ${dragOver ? "stage--drop" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        openFile(e.dataTransfer.files?.[0]);
      }}
    >
      <SplatViewer
        ref={viewerRef}
        models={allModels}
        activeId={shownId}
        mode={mode}
        lowPower={lowPower}
        orientation={orientation}
        onModelState={onModelState}
        onStats={setStats}
        onFatal={setFatal}
      />

      {hasModels && (
        <>
          <div className="stage__top">
            <div className="seg" role="tablist" aria-label="Room version">
              <button
                role="tab"
                aria-selected={shownId === "before"}
                className={shownId === "before" ? "is-on" : ""}
                onClick={() => onActiveChange("before")}
                disabled={!!local}
              >
                Before
              </button>
              <button
                role="tab"
                aria-selected={shownId === "after"}
                className={shownId === "after" ? "is-on" : ""}
                onClick={() => onActiveChange("after")}
                disabled={!afterAvailable || !!local}
                title={afterAvailable ? "" : afterPending ? "Your redesign is still being generated" : "Describe a change in Refine to create an After version"}
              >
                {afterPending && !afterAvailable ? "After (building)" : "After"}
              </button>
            </div>

            <div className="toolbar">
              <button
                className={`tool ${mode === "orbit" ? "is-on" : ""}`}
                onClick={() => setMode("orbit")}
                aria-label="Orbit around the room"
                aria-pressed={mode === "orbit"}
                title="Orbit"
              >
                <Icon name="orbit" />
              </button>
              <button
                className={`tool ${mode === "walk" ? "is-on" : ""}`}
                onClick={() => setMode("walk")}
                aria-label="Walk through the room"
                aria-pressed={mode === "walk"}
                title="Walk"
              >
                <Icon name="walk" />
              </button>
              <span className="toolbar__sep" />
              <button className="tool" onClick={() => setOrientation((o) => (o + 1) % ORIENTATION_COUNT)} aria-label="Fix upside-down model" title="Model upside down? Tap to rotate it">
                <Icon name="rotate" />
              </button>
              <button className="tool" onClick={() => viewerRef.current?.resetView()} aria-label="Reset view" title="Reset view">
                <Icon name="home" />
              </button>
              <button className={`tool ${lowPower ? "is-on" : ""}`} onClick={() => setLowPower((v) => !v)} aria-label="Battery saver quality" aria-pressed={lowPower} title="Lower quality for smoother playback">
                <Icon name="bolt" />
              </button>
              <button className="tool" onClick={fullscreen} aria-label="Full screen" title="Full screen">
                <Icon name="expand" />
              </button>
              <button className="tool" onClick={() => fileInput.current?.click()} aria-label="Open a .splat file" title="Open a local .splat file">
                <Icon name="file" />
              </button>
            </div>
          </div>

          <p className="stage__hint">
            {mode === "walk"
              ? "Drag to look around. Move with W A S D or the arrow keys. Hold Shift to go faster."
              : "Drag to turn the room. Scroll to zoom. Right-drag to shift the view."}
          </p>

          {mode === "walk" && <WalkPad viewerRef={viewerRef} />}
          {stats && (
            <span className="stage__fps" aria-hidden="true">
              {stats.fps} fps
            </span>
          )}
        </>
      )}

      {loading && (
        <div className="stage__overlay" role="status">
          <div className="bar">
            <div className="bar__fill" style={{ width: `${Math.round((current.progress || 0) * 100)}%` }} />
          </div>
          <p>{current.status === "preparing" ? "Getting the 3D model ready" : `Downloading your room, ${Math.round((current.progress / 0.85) * 100)}%`}</p>
        </div>
      )}

      {!hasModels && (
        <div className="stage__empty">
          {emptyState}
          <button className="btn btn--ghost" onClick={() => fileInput.current?.click()}>
            Open a local .splat file
          </button>
        </div>
      )}

      {(fatal || current?.status === "error") && (
        <div className="stage__error" role="alert">
          {fatal || current.error}
        </div>
      )}

      {dragOver && <div className="stage__dropmsg">Drop a .splat file to preview it</div>}

      <input ref={fileInput} type="file" accept=".splat" hidden onChange={(e) => openFile(e.target.files?.[0])} />
    </div>
  );
}

function WalkPad({ viewerRef }) {
  const hold = (f, r) => ({
    onPointerDown: (e) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      viewerRef.current?.setMove(f, r);
    },
    onPointerUp: () => viewerRef.current?.setMove(0, 0),
    onPointerCancel: () => viewerRef.current?.setMove(0, 0),
    onContextMenu: (e) => e.preventDefault(),
  });
  return (
    <div className="pad" aria-label="Movement pad">
      <button className="pad__b pad__up" aria-label="Forward" {...hold(1, 0)}><Icon name="up" /></button>
      <button className="pad__b pad__left" aria-label="Left" {...hold(0, -1)}><Icon name="left" /></button>
      <button className="pad__b pad__right" aria-label="Right" {...hold(0, 1)}><Icon name="right" /></button>
      <button className="pad__b pad__down" aria-label="Back" {...hold(-1, 0)}><Icon name="down" /></button>
    </div>
  );
}
