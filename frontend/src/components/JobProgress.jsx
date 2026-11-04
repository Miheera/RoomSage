import { formatDuration } from "../lib/format";

const PHASES = [
  { from: 0, label: "Preparing your photos" },
  { from: 12, label: "Working out where each photo was taken" },
  { from: 35, label: "Building the 3D model" },
  { from: 90, label: "Packaging the model for your browser" },
];

const EDIT_PHASES = [
  { from: 0, label: "Loading your room" },
  { from: 15, label: "Applying your changes to the 3D model" },
  { from: 85, label: "Saving the new version" },
];

export default function JobProgress({ job, kind = "reconstruct", error, onRetry }) {
  const phases = kind === "edit" ? EDIT_PHASES : PHASES;
  const progress = job?.progress ?? 0;
  const failed = job?.status === "FAILED";
  const phase = [...phases].reverse().find((p) => progress >= p.from) || phases[0];
  const lastLog = job?.logs?.[job.logs.length - 1];

  if (failed) {
    return (
      <div className="job job--failed" role="alert">
        <h3>This didn't finish</h3>
        <p>{job.error || "The 3D job failed on the server."}</p>
        {lastLog && <p className="job__log">Last step: {lastLog}</p>}
        {onRetry && (
          <button className="btn btn--primary" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="job" role="status" aria-live="polite">
      <h3>{job?.status === "QUEUED" ? "Waiting for a GPU" : phase.label}</h3>
      <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
        <div className="bar__fill" style={{ width: `${Math.max(3, progress)}%` }} />
      </div>
      <p className="job__meta">
        {progress}% done
        {job?.etaSeconds != null && job.status !== "COMPLETED" ? `, ${formatDuration(job.etaSeconds)} left` : ""}
      </p>
      {lastLog && <p className="job__log">{lastLog}</p>}
      {error && <p className="job__warn">Connection problem. Retrying. {error.message}</p>}
      <p className="job__hint">You can leave this page. Your room keeps building and will be here when you're back.</p>
    </div>
  );
}
