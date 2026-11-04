import { API_BASE, USE_MOCK } from "./config";
import { rewriteAssetUrl } from "./urls";
import { mockApi } from "./mockApi";

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailOf(body) {
  if (!body) return null;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(" ");
  return null;
}

async function request(path, { method = "GET", json, signal } = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}/api/v1${path}`, {
      method,
      signal,
      headers: json ? { "Content-Type": "application/json" } : undefined,
      body: json ? JSON.stringify(json) : undefined,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Can't reach the RoomSage server. Check that the backend is running.");
  }
  if (!res.ok) {
    let body = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(detailOf(body) || `Request failed (${res.status}).`, res.status);
  }
  return res.status === 204 ? null : res.json();
}

/** Snake_case JobRead  ->  the shape the UI uses. */
export function normalizeJob(j) {
  return {
    id: j.job_id,
    projectId: j.project_id,
    taskType: j.task_type,
    status: j.status, // QUEUED | PROCESSING | COMPLETED | FAILED
    progress: j.progress ?? 0,
    etaSeconds: j.estimated_time_remaining_seconds ?? null,
    logs: (j.logs || []).map((l) => l.replace(/^\S+:\s/, "")), // strip the ISO timestamp prefix
    error: j.error_message || null,
    outputUrl: rewriteAssetUrl(j.output_urls?.[0] || null),
    depthMapUrl: rewriteAssetUrl(j.depth_map_url || null),
  };
}

function uploadPhotos(projectId, files, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach((f, i) => form.append("files", f, f.name || `photo-${i}.jpg`));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/v1/projects/${projectId}/photos`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(detailOf(body) || `Upload failed (${xhr.status}).`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Upload failed: the server isn't reachable."));
    xhr.send(form);
  });
}

const realApi = {
  async health() {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new ApiError("Backend unhealthy", res.status);
    return true;
  },

  // ---- Existing backend endpoints (Person A) ----------------------------
  createProject: ({ name, roomType }) =>
    request("/projects", { method: "POST", json: { name, room_type: roomType } }),

  uploadPhotos,

  async startReconstruction(projectId) {
    const r = await request("/jobs/reconstruct", { method: "POST", json: { project_id: projectId } });
    return { jobId: r.job_id, status: r.status };
  },

  async startEdit({ projectId, stylePrompt, removeMaskId, insertCatalogImageKey, targetPosition, targetScale }) {
    const body = { project_id: projectId };
    if (stylePrompt) body.style_prompt = stylePrompt;
    if (removeMaskId != null) body.remove_mask_id = removeMaskId;
    if (insertCatalogImageKey) {
      body.insert_catalog_image_key = insertCatalogImageKey;
      body.target_position = targetPosition ?? [0, 0, 0];
      body.target_scale = targetScale ?? 1;
    }
    const r = await request("/jobs/edit", { method: "POST", json: body });
    return { jobId: r.job_id, status: r.status };
  },

  async getJob(jobId) {
    return normalizeJob(await request(`/jobs/${jobId}`));
  },

  // ---- Proposed endpoints (Person B). 404 => "not connected yet". --------
  async getPlan(projectId) {
    try {
      return await request(`/projects/${projectId}/plan`);
    } catch (e) {
      if (e.status === 404 || e.status === 405) return null;
      throw e;
    }
  },

  async refinePlan(projectId, { prompt, budgetCap }) {
    try {
      return await request(`/projects/${projectId}/plan/refine`, {
        method: "POST",
        json: { prompt, budget_cap: budgetCap ?? null },
      });
    } catch (e) {
      if (e.status === 404 || e.status === 405) return null;
      throw e;
    }
  },
};

export const api = USE_MOCK ? mockApi : realApi;
