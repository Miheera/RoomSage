import { useSyncExternalStore } from "react";

/**
 * The backend has no "list projects" / "get project" endpoint yet, so the
 * browser keeps its own registry (ids, job ids, cover photo, edit history).
 * Everything heavy (photos, splats) stays on the server.
 */
const KEY = "roomsage.projects.v1";

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) ?? [];
  } catch {
    return [];
  }
}

let state = load();
const listeners = new Set();

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // Quota exceeded: drop the (large) cover images and retry once.
    state = state.map((p) => ({ ...p, cover: null }));
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      /* give up silently; app keeps working in memory */
    }
  }
}

function emit() {
  persist();
  listeners.forEach((l) => l());
}

export const projectsStore = {
  subscribe(l) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  getSnapshot: () => state,
  add(project) {
    state = [project, ...state.filter((p) => p.id !== project.id)];
    emit();
  },
  update(id, patch) {
    state = state.map((p) =>
      p.id === id ? { ...p, ...(typeof patch === "function" ? patch(p) : patch) } : p
    );
    emit();
  },
  remove(id) {
    state = state.filter((p) => p.id !== id);
    emit();
  },
};

export const useProjects = () => useSyncExternalStore(projectsStore.subscribe, projectsStore.getSnapshot);
export const useProject = (id) => useProjects().find((p) => p.id === id) || null;
