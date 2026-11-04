// A fake backend so the whole UI can be built and demoed without a GPU.
// Enabled with `npm run dev:mock`. Jobs "run" for a few seconds, then complete.
import { DEMO_SPLAT_URL } from "./config";
import { samplePlan } from "../data/samplePlan";

const jobs = new Map(); // jobId -> { start, duration, taskType, projectId }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));

const STAGES = [
  [0, "Queued for a GPU worker."],
  [10, "Preparing your photos."],
  [30, "Estimating camera positions with COLMAP."],
  [55, "Training the Gaussian Splatting model."],
  [90, "Converting to a web-ready .splat file."],
  [99, "Uploading the finished model."],
];

function makeJob(projectId, taskType, duration) {
  const id = uid();
  jobs.set(id, { start: Date.now(), duration, taskType, projectId });
  return id;
}

export const mockApi = {
  async health() {
    return true;
  },
  async createProject({ name, roomType }) {
    await sleep(300);
    return { id: uid(), name, room_type: roomType, photo_keys: [] };
  },
  async uploadPhotos(_id, files, onProgress) {
    for (let i = 1; i <= 10; i++) {
      await sleep(80);
      onProgress?.(i / 10);
    }
    return { photo_keys: files.map((_, i) => `mock/${i}`) };
  },
  async startReconstruction(projectId) {
    return { jobId: makeJob(projectId, "GAUSSIAN_SPLATTING_TRAIN", 14000), status: "QUEUED" };
  },
  async startEdit({ projectId }) {
    return { jobId: makeJob(projectId, "INSTRUCT_GS2GS_EDIT", 9000), status: "QUEUED" };
  },
  async getJob(jobId) {
    await sleep(120);
    const j = jobs.get(jobId) || { start: 0, duration: 1, taskType: "GAUSSIAN_SPLATTING_TRAIN", projectId: "mock" };
    const t = Math.min(1, (Date.now() - j.start) / j.duration);
    const progress = Math.round(t * 100);
    const done = t >= 1;
    const logs = STAGES.filter(([p]) => p <= progress).map(([, m]) => m);
    return {
      id: jobId,
      projectId: j.projectId,
      taskType: j.taskType,
      status: done ? "COMPLETED" : progress < 5 ? "QUEUED" : "PROCESSING",
      progress,
      etaSeconds: done ? null : Math.round((j.duration * (1 - t)) / 1000),
      logs,
      error: null,
      outputUrl: done ? DEMO_SPLAT_URL || null : null,
      depthMapUrl: null,
    };
  },
  async getPlan() {
    await sleep(250);
    return samplePlan;
  },
  async refinePlan(_id, { budgetCap }) {
    await sleep(500);
    return { ...samplePlan, budget: { cap: budgetCap ?? samplePlan.budget.cap, currency: "INR" } };
  },
};
