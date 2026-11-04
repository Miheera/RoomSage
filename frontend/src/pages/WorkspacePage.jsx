import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { parseRefinement } from "../lib/refine";
import { projectsStore, useProject } from "../lib/storage";
import { samplePlan } from "../data/samplePlan";
import { isRunning, useJob } from "../hooks/useJob";
import { usePlan } from "../hooks/usePlan";
import ViewerStage from "../components/viewer/ViewerStage";
import JobProgress from "../components/JobProgress";
import PlanPanel from "../components/workspace/PlanPanel";
import RefinePanel from "../components/workspace/RefinePanel";
import SharePanel from "../components/workspace/SharePanel";
import { roomLabel } from "../lib/format";

const TABS = [
  { id: "plan", label: "Plan" },
  { id: "refine", label: "Refine" },
  { id: "share", label: "Share" },
];

export default function WorkspacePage() {
  const { id } = useParams();
  const project = useProject(id);
  const viewerRef = useRef(null);
  const [tab, setTab] = useState("plan");
  const [view, setView] = useState("before");
  const [toast, setToast] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [placing, setPlacing] = useState(null);
  const [sample, setSample] = useState(false);
  const planState = usePlan(id);

  const edits = project?.edits ?? [];
  const activeEdit = edits.find((e) => e.id === project?.activeEditId) || null;
  const latestEdit = edits[edits.length - 1] || null;

  const { job: beforeJob, error: beforeErr } = useJob(project?.beforeJobId);
  const { job: afterJob } = useJob(activeEdit?.id);
  const { job: latestJob } = useJob(latestEdit && latestEdit.id !== activeEdit?.id ? latestEdit.id : null);
  const editRunning = isRunning(afterJob) || isRunning(latestJob);

  // Each finished job's file never changes, so keying by job id is safe and lets the viewer cache it.
  const models = useMemo(() => {
    const m = [];
    if (beforeJob?.status === "COMPLETED" && beforeJob.outputUrl)
      m.push({ id: "before", label: "Before", url: beforeJob.outputUrl, cacheKey: beforeJob.id });
    if (afterJob?.status === "COMPLETED" && afterJob.outputUrl)
      m.push({ id: "after", label: "After", url: afterJob.outputUrl, cacheKey: afterJob.id });
    return m;
  }, [beforeJob?.id, beforeJob?.status, beforeJob?.outputUrl, afterJob?.id, afterJob?.status, afterJob?.outputUrl]);

  const afterAvailable = models.some((m) => m.id === "after");
  const shown = models.find((m) => m.id === view)?.id ?? models[0]?.id ?? "before";

  // Jump to the redesign as soon as it finishes.
  useEffect(() => {
    if (afterJob?.status === "COMPLETED") setView("after");
  }, [afterJob?.id, afterJob?.status]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  if (!project) {
    return (
      <main className="page page--narrow">
        <h1>Room not found on this device</h1>
        <p className="lede">Rooms are listed per browser. If you scanned it on another device, open it there.</p>
        <Link to="/" className="btn btn--primary">Back to your rooms</Link>
      </main>
    );
  }

  const addEdit = (edit) =>
    projectsStore.update(project.id, (p) => ({ edits: [...p.edits, { ...edit, createdAt: new Date().toISOString() }], activeEditId: edit.id }));

  const retryReconstruction = async () => {
    try {
      const j = await api.startReconstruction(project.id);
      projectsStore.update(project.id, { beforeJobId: j.jobId });
    } catch (e) {
      setActionError(e.message);
    }
  };

  const submitRefinement = async (text, buildOn) => {
    setActionError(null);
    const { stylePrompt, budgetCap } = parseRefinement(text);
    setBusy(true);
    try {
      if (budgetCap) projectsStore.update(project.id, { budgetCap });
      if (stylePrompt) {
        const prev = buildOn ? activeEdit?.fullPrompt : null;
        const full = prev ? `${prev}, ${stylePrompt}` : stylePrompt;
        const r = await api.startEdit({ projectId: project.id, stylePrompt: full });
        addEdit({ id: r.jobId, kind: "style", label: stylePrompt, fullPrompt: full });
        setToast("Redesign started. The plan is updating now; the 3D room follows in a few minutes.");
      }
      // The plan refreshes immediately; the 3D edit continues in the background.
      await planState.refine({ prompt: text, budgetCap: budgetCap ?? project.budgetCap });
      if (!stylePrompt && budgetCap) setToast(`Budget set to ₹${budgetCap.toLocaleString("en-IN")}.`);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const placeItem = async (item) => {
    setActionError(null);
    const pos = viewerRef.current?.getPlacement();
    if (!pos) return setToast("Open the 3D room first, then aim at the floor spot.");
    setPlacing(item.id);
    try {
      const r = await api.startEdit({
        projectId: project.id,
        insertCatalogImageKey: item.catalog_image_key,
        targetPosition: pos,
        targetScale: 1,
      });
      addEdit({ id: r.jobId, kind: "insert", label: `Place ${item.name}`, fullPrompt: null });
      setTab("refine");
      setToast(`Placing ${item.name} in your room.`);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setPlacing(null);
    }
  };

  const removeObject = async (maskId) => {
    setActionError(null);
    try {
      const r = await api.startEdit({ projectId: project.id, removeMaskId: maskId });
      addEdit({ id: r.jobId, kind: "remove", label: `Remove object ${maskId}`, fullPrompt: null });
    } catch (e) {
      setActionError(e.message);
    }
  };

  const beforeDone = beforeJob?.status === "COMPLETED";
  const emptyState = !project.beforeJobId ? (
    <div className="job">
      <h3>This room hasn't been built yet</h3>
      <button className="btn btn--primary" onClick={retryReconstruction}>Start the 3D build</button>
    </div>
  ) : beforeDone ? (
    <div className="job">
      <h3>The 3D model is ready but has no download link</h3>
      <p>Drop a .splat file here to preview a model locally.</p>
    </div>
  ) : (
    <JobProgress job={beforeJob} error={beforeErr} kind="reconstruct" onRetry={retryReconstruction} />
  );

  const plan = planState.plan === null && sample ? samplePlan : planState.plan;

  return (
    <div className="ws">
      <header className="ws__head">
        <div>
          <h1>{project.name}</h1>
          <p>{roomLabel(project.roomType)}</p>
        </div>
        <Link to="/" className="btn btn--ghost btn--sm">All rooms</Link>
      </header>

      <div className="ws__viewer">
        {editRunning && afterJob && (
          <div className="banner" role="status">
            Redesign in progress, {afterJob.progress}%. You can keep exploring the original room.
          </div>
        )}
        <ViewerStage
          viewerRef={viewerRef}
          models={models}
          activeId={shown}
          onActiveChange={setView}
          afterAvailable={afterAvailable}
          afterPending={editRunning}
          emptyState={emptyState}
        />
      </div>

      <aside className="ws__panel">
        <div className="tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "is-on" : ""} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="ws__body">
          {tab === "plan" && (
            <PlanPanel
              plan={plan}
              isSample={sample && planState.plan === null}
              onUseSample={() => setSample(true)}
              budgetCap={project.budgetCap}
              onBudgetCap={(n) => projectsStore.update(project.id, { budgetCap: n })}
              onReplan={(n) => planState.refine({ prompt: `Keep the total under ${n}`, budgetCap: n })}
              replanning={planState.busy}
              checked={project.checkedSteps}
              onToggleStep={(sid) => projectsStore.update(project.id, (p) => ({ checkedSteps: { ...p.checkedSteps, [sid]: !p.checkedSteps?.[sid] } }))}
              onPlace={placeItem}
              placing={placing}
              canPlace={beforeDone && !editRunning}
              error={planState.error}
            />
          )}
          {tab === "refine" && (
            <RefinePanel
              project={project}
              activeEditId={project.activeEditId}
              activeJob={afterJob}
              onSubmit={submitRefinement}
              onSelect={(eid) => {
                projectsStore.update(project.id, { activeEditId: eid });
                setView("after");
              }}
              onRemoveObject={removeObject}
              busy={busy || !beforeDone}
              error={actionError}
            />
          )}
          {tab === "share" && (
            <SharePanel project={project} plan={plan} viewerRef={viewerRef} viewingAfter={shown === "after"} afterAvailable={afterAvailable} />
          )}
        </div>
      </aside>

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
