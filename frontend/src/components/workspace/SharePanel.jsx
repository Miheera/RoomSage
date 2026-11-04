import { useEffect, useRef, useState } from "react";
import { renderShareCard } from "../../lib/shareCard";
import { formatINR } from "../../lib/format";
import { totals } from "../../lib/budget";
import Icon from "../Icon";

function Compare({ before, after }) {
  const [pos, setPos] = useState(50);
  return (
    <div className="compare">
      <img src={after} alt="After" />
      <img src={before} alt="Before" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }} />
      <span className="compare__line" style={{ left: `${pos}%` }} />
      <input type="range" min="0" max="100" value={pos} onChange={(e) => setPos(+e.target.value)} aria-label="Slide to compare before and after" />
    </div>
  );
}

export default function SharePanel({ project, plan, viewerRef, viewingAfter, afterAvailable }) {
  const [afterShot, setAfterShot] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const blobRef = useRef(null);

  // Prefer the 2D redesign preview from the planner service when it exists.
  const remoteAfter = plan?.after_image_url || null;
  const after = afterShot || remoteAfter;

  useEffect(() => {
    blobRef.current = null;
  }, [after, project.cover]);

  const capture = () => {
    const url = viewerRef.current?.snapshot();
    if (!url) return setMsg("The 3D view isn't ready yet.");
    setAfterShot(url);
    setMsg(null);
  };

  const build = async () => {
    if (blobRef.current) return blobRef.current;
    const t = plan ? totals(plan.shopping) : null;
    const blob = await renderShareCard({
      before: project.cover,
      after,
      title: project.name,
      palette: plan?.palette || [],
      costLine: t ? (t.estMax ? `${formatINR(t.low)} to ${formatINR(t.high)}` : formatINR(t.low)) : "",
    });
    blobRef.current = blob;
    return blob;
  };

  const download = async () => {
    setBusy(true);
    try {
      const blob = await build();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${project.name.replace(/\W+/g, "-").toLowerCase()}-before-after.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch {
      setMsg("Couldn't create the card. Try capturing the view again.");
    } finally {
      setBusy(false);
    }
  };

  const share = async () => {
    setBusy(true);
    try {
      const blob = await build();
      const file = new File([blob], "roomsage-before-after.png", { type: "image/png" });
      if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file], title: project.name, text: "My room, redesigned with RoomSage" });
      else await download();
    } catch (e) {
      if (e.name !== "AbortError") setMsg("Sharing failed. Use Download instead.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="share">
      <p className="muted">Make a before and after picture to post or send. The "after" image is a snapshot of your redesigned 3D room.</p>

      {!project.cover ? (
        <p className="notice">This room has no cover photo saved on this device, so a card can't be made.</p>
      ) : after ? (
        <Compare before={project.cover} after={after} />
      ) : (
        <div className="share__empty">
          <img src={project.cover} alt="Your room before" />
          <p>No "after" image yet.</p>
        </div>
      )}

      <div className="share__actions">
        <button className="btn btn--ghost" onClick={capture} disabled={!afterAvailable || !viewingAfter}>
          <Icon name="camera" /> Use current 3D view
        </button>
        <button className="btn btn--primary" onClick={download} disabled={!after || !project.cover || busy}>
          <Icon name="download" /> Download card
        </button>
        <button className="btn btn--primary" onClick={share} disabled={!after || !project.cover || busy}>
          <Icon name="share" /> Share
        </button>
      </div>
      {afterAvailable && !viewingAfter && <p className="muted">Switch the viewer to After, point it at your favourite angle, then press "Use current 3D view".</p>}
      {!afterAvailable && <p className="muted">Create a redesign in Refine first. Then you can capture the After view here.</p>}
      {msg && <p className="notice notice--error" role="alert">{msg}</p>}
    </div>
  );
}
