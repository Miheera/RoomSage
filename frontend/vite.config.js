import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import basicSsl from "@vitejs/plugin-basic-ssl";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.DEV_API_TARGET || "http://localhost:8000";
  const s3Target = env.DEV_S3_TARGET || "http://localhost:9000";
  // Presigned URLs are signed for this Host header, so the proxy must send it
  // unchanged or MinIO/S3 rejects the signature.
  const s3SignedHost = new URL(env.VITE_S3_ORIGIN || "http://minio:9000").host;

  const proxy = {
    "/api": { target: apiTarget, changeOrigin: true },
    "/health": { target: apiTarget, changeOrigin: true },
    "/s3": {
      target: s3Target,
      changeOrigin: false,
      rewrite: (path) => path.replace(/^\/s3/, ""),
      configure: (p) => p.on("proxyReq", (req) => req.setHeader("Host", s3SignedHost)),
    },
  };

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["favicon.svg", "apple-touch-icon.png"],
        manifest: {
          name: "RoomSage",
          short_name: "RoomSage",
          description: "Redesign your real room in 3D and get a costed shopping plan.",
          theme_color: "#17302B",
          background_color: "#F2F4F0",
          display: "standalone",
          start_url: "/",
          icons: [
            { src: "pwa-192.png", sizes: "192x192", type: "image/png" },
            { src: "pwa-512.png", sizes: "512x512", type: "image/png" },
            { src: "pwa-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
          ],
        },
        workbox: {
          globPatterns: ["**/*.{js,css,html,svg,png,woff2,wasm}"],
          maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
          navigateFallbackDenylist: [/^\/api/, /^\/s3/, /^\/health/],
        },
      }),
      ...(mode === "https" ? [basicSsl()] : []),
    ],
    resolve: { dedupe: ["three"] },
    optimizeDeps: { exclude: ["@sparkjsdev/spark"] },
    worker: { format: "es" },
    server: { host: true, port: 5173, proxy },
    preview: { host: true, port: 4173, proxy },
    build: { target: "es2022", chunkSizeWarningLimit: 2000 },
  };
});
