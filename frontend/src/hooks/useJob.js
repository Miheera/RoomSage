import { useEffect, useState } from "react";
import { api } from "../lib/api";

/**
 * Polls GET /jobs/{id} until it COMPLETES or FAILS.
 *  - 2.5 s interval, relaxing to 5 s after two minutes (GPU jobs take minutes)
 *  - pauses while the tab is hidden, resumes instantly when it's visible again
 *  - backs off on network errors instead of hammering a struggling server
 */
export function useJob(jobId) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setJob(null);
    setError(null);
    if (!jobId) return undefined;

    let cancelled = false;
    let busy = false;
    let timer;
    let failures = 0;
    const startedAt = Date.now();

    const tick = async () => {
      if (cancelled || busy) return;
      if (document.hidden) {
        timer = setTimeout(tick, 3000);
        return;
      }
      busy = true;
      try {
        const j = await api.getJob(jobId);
        if (cancelled) return;
        failures = 0;
        setError(null);
        setJob(j);
        if (j.status === "COMPLETED" || j.status === "FAILED") return;
      } catch (e) {
        if (cancelled) return;
        failures += 1;
        setError(e);
        if (e.status === 404) return; // unknown job: stop polling
      } finally {
        busy = false;
      }
      const base = Date.now() - startedAt > 120000 ? 5000 : 2500;
      timer = setTimeout(tick, Math.min(15000, base * (1 + failures)));
    };

    const onVisible = () => {
      if (!document.hidden) {
        clearTimeout(timer);
        tick();
      }
    };
    tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [jobId]);

  return { job, error };
}

export const isRunning = (job) => job && (job.status === "QUEUED" || job.status === "PROCESSING");
