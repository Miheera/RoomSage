import { S3_ORIGIN, S3_PROXY } from "./config";

/**
 * The backend signs URLs for its *internal* storage host (http://minio:9000),
 * which a browser cannot resolve. In dev the Vite server proxies /s3 to the
 * real MinIO and keeps the signed Host header, so we only swap the origin for a
 * same-origin path. The signature stays valid because path + query are unchanged.
 */
export function rewriteAssetUrl(url) {
  if (!url || !S3_PROXY) return url;
  try {
    const u = new URL(url, window.location.origin);
    if (u.origin === new URL(S3_ORIGIN).origin) return `/s3${u.pathname}${u.search}`;
  } catch {
    /* fall through */
  }
  return url;
}
