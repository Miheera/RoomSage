export const API_BASE = import.meta.env.VITE_API_URL ?? "";
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";
export const DEMO_SPLAT_URL = import.meta.env.VITE_DEMO_SPLAT_URL || "";
export const S3_ORIGIN = import.meta.env.VITE_S3_ORIGIN || "http://minio:9000";
export const S3_PROXY = import.meta.env.VITE_S3_PROXY !== "false";

// Mirrors the backend rule: 5-15 photos per project.
export const MIN_PHOTOS = 5;
export const MAX_PHOTOS = 15;

export const ROOM_TYPES = [
  { value: "living_room", label: "Living room" },
  { value: "bedroom", label: "Bedroom" },
  { value: "study", label: "Study" },
  { value: "kitchen", label: "Kitchen" },
  { value: "dining_room", label: "Dining room" },
  { value: "other", label: "Other" },
];
