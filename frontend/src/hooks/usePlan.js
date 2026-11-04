import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

/** Loads the transformation plan. `plan === null` means the planner isn't connected yet. */
export function usePlan(projectId) {
  const [plan, setPlan] = useState(undefined); // undefined = loading
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setPlan(undefined);
    api
      .getPlan(projectId)
      .then((p) => !cancelled && setPlan(p))
      .catch((e) => {
        if (!cancelled) {
          setError(e);
          setPlan(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const refine = useCallback(
    async ({ prompt, budgetCap }) => {
      setBusy(true);
      setError(null);
      try {
        const next = await api.refinePlan(projectId, { prompt, budgetCap });
        if (next) setPlan(next);
        return next;
      } catch (e) {
        setError(e);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [projectId]
  );

  return { plan, setPlan, refine, busy, error };
}
