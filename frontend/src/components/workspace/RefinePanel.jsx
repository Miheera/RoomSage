import { useState } from "react";
import { isRunning } from "../../hooks/useJob";
import Icon from "../Icon";
import JobProgress from "../JobProgress";

const CHIPS = ["Warmer and cosier", "More minimal", "Darker and moodier", "Brighter and airier", "Keep it under ₹30,000"];

export default function RefinePanel({ project, activeEditId, activeJob, onSubmit, onSelect, onRemoveObject, busy, error }) {
  const [text, setText] = useState("");
  const [buildOn, setBuildOn] = useState(true);
  const [maskId, setMaskId] = useState("");
  const running = isRunning(activeJob) || busy;

  const send = (value) => {
    const v = value.trim();
    if (!v || running) return;
    onSubmit(v, buildOn);
    setText("");
  };

  return (
    <div className="refine">
      <p className="muted">Describe what you'd like changed. The 3D room and the plan both update. A style change takes a few minutes in the background; the plan updates right away.</p>

      <ul className="chips">
        {CHIPS.map((c) => (
          <li key={c}>
            <button className="chip" onClick={() => send(c)} disabled={running}>
              {c}
            </button>
          </li>
        ))}
      </ul>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Scandinavian, lighter wood, under ₹40,000" aria-label="Describe a change" disabled={running} />
        <button className="btn btn--primary" disabled={!text.trim() || running} aria-label="Send">
          <Icon name="send" />
        </button>
      </form>
      <label className="check">
        <input type="checkbox" checked={buildOn} onChange={(e) => setBuildOn(e.target.checked)} />
        Build on my previous style changes
      </label>

      {error && <p className="notice notice--error" role="alert">{error}</p>}
      {isRunning(activeJob) && <JobProgress job={activeJob} kind="edit" />}
      {activeJob?.status === "FAILED" && <JobProgress job={activeJob} kind="edit" />}

      <section>
        <h3>Versions</h3>
        {project.edits.length === 0 ? (
          <p className="muted">No redesigns yet. Your original room is the only version.</p>
        ) : (
          <ol className="versions">
            {[...project.edits].reverse().map((e, i) => (
              <li key={e.id}>
                <button className={e.id === activeEditId ? "is-on" : ""} onClick={() => onSelect(e.id)}>
                  <strong>Version {project.edits.length - i}</strong>
                  <span>{e.label}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>

      <details className="adv">
        <summary>Remove an object</summary>
        <p className="muted">Enter the object's number from the segmentation step. Object picking in the viewer is coming later.</p>
        <form
          className="capform"
          onSubmit={(e) => {
            e.preventDefault();
            onRemoveObject(Number(maskId));
            setMaskId("");
          }}
        >
          <label>
            Object number
            <input inputMode="numeric" value={maskId} onChange={(e) => setMaskId(e.target.value.replace(/\D/g, ""))} placeholder="e.g. 3" />
          </label>
          <button className="btn btn--ghost" disabled={!Number(maskId) || running}>Remove</button>
        </form>
      </details>
    </div>
  );
}
