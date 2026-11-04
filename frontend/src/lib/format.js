const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const plain = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const formatINR = (n) => inr.format(n ?? 0);
export const formatNumber = (n) => plain.format(n ?? 0);

export function formatDuration(seconds) {
  if (seconds == null) return "";
  const m = Math.round(seconds / 60);
  if (m < 1) return "under a minute";
  if (m < 60) return `about ${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `about ${h} h ${r} min` : `about ${h} h`;
}

export function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  } catch {
    return "";
  }
}

export const roomLabel = (v) =>
  (v || "room").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
