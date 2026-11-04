import { MIN_PHOTOS } from "./config";

/** Cheap quality heuristics on a 256px copy: sharpness (Laplacian variance) + brightness. */
function analyse(bitmap) {
  const w = 256;
  const h = Math.max(1, Math.round((bitmap.height / bitmap.width) * w));
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0, w, h);
  const { data } = ctx.getImageData(0, 0, w, h);
  const g = new Float32Array(w * h);
  let sum = 0;
  for (let i = 0; i < g.length; i++) {
    const v = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
    g[i] = v;
    sum += v;
  }
  let lapSum = 0;
  let lapSq = 0;
  let n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const lap = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - w] - g[i + w];
      lapSum += lap;
      lapSq += lap * lap;
      n++;
    }
  }
  const mean = lapSum / n;
  return { sharpness: lapSq / n - mean * mean, brightness: sum / g.length };
}

const toBlob = (canvas, type, q) => new Promise((res) => canvas.toBlob(res, type, q));

/**
 * Decode (respecting EXIF rotation), downscale to <=2048px, re-encode as JPEG
 * (smaller upload, faster COLMAP, no HEIC/rotation surprises) and score quality.
 */
export async function processPhoto(file, { maxSide = 2048, quality = 0.9 } = {}) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new Error(
      `${file.name || "A photo"} can't be read. Use JPEG, PNG or WebP. On iPhone, set Camera > Formats to "Most Compatible".`
    );
  }
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
  const { sharpness, brightness } = analyse(bitmap);
  bitmap.close?.();

  const blob = await toBlob(canvas, "image/jpeg", quality);
  const id = crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2);

  const warnings = [];
  if (sharpness < 40) warnings.push("Looks blurry. Hold steady and retake if you can.");
  if (brightness < 45) warnings.push("Very dark. Turn on a light or open the curtains.");
  if (brightness > 215) warnings.push("Overexposed. Avoid pointing straight at a window.");

  return {
    id,
    file: new File([blob], `photo-${id.slice(0, 8)}.jpg`, { type: "image/jpeg" }),
    url: URL.createObjectURL(blob),
    width,
    height,
    sharpness,
    brightness,
    warnings,
  };
}

/** Small data-URL used as the project cover / "before" image on the share card. */
export async function makeCover(file, maxSide = 960, quality = 0.8) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bitmap.width * scale);
  c.height = Math.round(bitmap.height * scale);
  c.getContext("2d").drawImage(bitmap, 0, 0, c.width, c.height);
  bitmap.close?.();
  return c.toDataURL("image/jpeg", quality);
}

export const photosNeeded = (count) => Math.max(0, MIN_PHOTOS - count);
