/**
 * Download a .splat with progress and keep it in the browser's Cache Storage,
 * keyed by job id (a finished job's file never changes). Re-opening a room is
 * then instant and free, instead of re-downloading tens of MB every time.
 * Presigned URLs change on every request, so the URL itself can't be the key.
 */
const CACHE_NAME = "roomsage-splats-v1";
const MAX_ENTRIES = 6;
const cacheUrl = (key) => `https://roomsage.local/splat/${encodeURIComponent(key)}`;
const hasCache = () => typeof window !== "undefined" && "caches" in window;

async function prune(cache) {
  const keys = await cache.keys();
  for (const req of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) await cache.delete(req);
}

export async function loadSplatBytes(url, cacheKey, { onProgress, signal } = {}) {
  if (cacheKey && hasCache()) {
    try {
      const cache = await caches.open(CACHE_NAME);
      const hit = await cache.match(cacheUrl(cacheKey));
      if (hit) {
        const buf = await hit.arrayBuffer();
        onProgress?.(1);
        return buf;
      }
    } catch {
      /* cache unavailable: fall through to network */
    }
  }

  const res = await fetch(url, { signal });
  if (!res.ok) {
    throw new Error(
      res.status === 403
        ? "The 3D model link has expired. Reload the page to get a fresh one."
        : `Couldn't download the 3D model (error ${res.status}).`
    );
  }
  const total = Number(res.headers.get("Content-Length")) || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (total) onProgress?.(received / total);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const c of chunks) {
    bytes.set(c, offset);
    offset += c.length;
  }
  const buffer = bytes.buffer;

  if (cacheKey && hasCache()) {
    try {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(cacheUrl(cacheKey), new Response(buffer.slice(0)));
      await prune(cache);
    } catch {
      /* quota or private mode: ignore */
    }
  }
  onProgress?.(1);
  return buffer;
}
