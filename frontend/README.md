# RoomSage frontend (Person C)

React + Vite PWA. Guided multi-photo capture, a fast Gaussian-splat 3D viewer
(Before / After / walkthrough), the costed plan, natural-language refinement and
a shareable before/after card. Talks to the FastAPI backend in `../backend`.

## Run it

Requirements: Node 18+ (Node 20/22 recommended).

```bash
cd frontend
npm install
cp .env.example .env        # defaults already match backend/docker-compose.yml
```

**A. Full stack (needs the GPU worker)**
```bash
# terminal 1: backend (from /backend)
docker compose up --build
# terminal 2: frontend
npm run dev                 # http://localhost:5173
```

**B. UI only, no GPU / no backend (use this for most of your dev work)**
```bash
# optional: drop any .splat at public/demo/room.splat
npm run dev:mock
```
Mock mode fakes projects, uploads, job progress, plan and refinement. If there is
no demo splat you can still drag-and-drop a `.splat` onto the viewer.

**C. Test the camera on your phone** (phones only allow the camera on https)
```bash
npm run dev:https           # then open https://<your-pc-ip>:5173 on the phone
```
Accept the self-signed certificate warning once. Phone and PC must be on the same Wi-Fi.

**Production build / PWA**
```bash
npm run build && npm run preview     # service worker + installable app
```

## How it talks to the backend

The dev server proxies everything, so the browser only ever sees one origin:

| Browser path | Goes to | Why |
|---|---|---|
| `/api/*`, `/health` | `DEV_API_TARGET` (FastAPI :8000) | no CORS issues, no mixed content on https |
| `/s3/*` | `DEV_S3_TARGET` (MinIO :9000) | presigned URLs contain `http://minio:9000`, which a browser can't resolve. `src/lib/urls.js` rewrites them to `/s3/...` and `vite.config.js` keeps the signed `Host` header so the signature stays valid |

For real deployment: put the API behind the same domain (or set `VITE_API_URL`),
use a public S3/MinIO URL, set `VITE_S3_PROXY=false`, and enable CORS on the bucket
(`GET`, `HEAD`, `Range`, origin = your site). MinIO allows all origins by default.

## Project structure

```
frontend/
├─ index.html  vite.config.js  package.json  .env.example  .env.mock
├─ public/                       PWA icons, favicon, demo/ (optional room.splat)
└─ src/
   ├─ main.jsx  App.jsx          entry + routes (workspace is lazy-loaded)
   ├─ styles/app.css            design tokens + all component styles
   ├─ pages/
   │  ├─ ProjectsPage.jsx        list of rooms on this device
   │  ├─ CapturePage.jsx         name -> capture/upload 5-15 photos -> start reconstruction
   │  └─ WorkspacePage.jsx       viewer + Plan / Refine / Share tabs (the main screen)
   ├─ components/
   │  ├─ Layout.jsx  Icon.jsx  JobProgress.jsx
   │  ├─ capture/   CameraCapture, CoverageDial, PhotoGrid
   │  ├─ viewer/    SplatViewer (Three.js + Spark engine), ViewerStage (UI around it)
   │  └─ workspace/ PlanPanel, RefinePanel, SharePanel
   ├─ hooks/        useJob (polling), usePlan
   ├─ lib/
   │  ├─ api.js            real client for the FastAPI endpoints
   │  ├─ mockApi.js        fake backend for UI work
   │  ├─ splatCache.js     streaming download + Cache Storage
   │  ├─ walkControls.js   WASD / touch first-person movement
   │  ├─ image.js          resize, EXIF fix, blur + brightness checks
   │  ├─ storage.js        local project registry
   │  ├─ refine.js         "warmer, under 30k" -> style prompt + budget cap
   │  ├─ budget.js  shareCard.js  urls.js  format.js  config.js
   └─ data/samplePlan.js   the plan JSON shape (contract for Person B)
```

## Why the 3D viewer stays smooth

- **Right format**: the backend already exports `.splat` (32 bytes/splat). The viewer
  uses [Spark](https://sparkjs.dev) (Three.js based), which sorts splats in a Web Worker so
  the main thread never stalls.
- **Cached**: finished models are stored in the browser Cache Storage keyed by job id,
  so reopening a room is instant and doesn't re-download tens of MB.
- **Before and After both stay in GPU memory**, so toggling is a visibility flip, not a reload.
  The active model loads first; the other loads in the background.
- **Adaptive resolution**: the render scale drops automatically when frames take over
  ~26 ms and climbs back when there is headroom. The lightning-bolt button caps it at 1x.
- **No MSAA, no depth/stencil extras, pixel ratio capped** (2x desktop, 1.5x phones).
- **Code-split**: three.js + Spark load only when a room is opened.
- A small fps counter shows in the viewer corner.

## What this frontend expects from teammates

### Existing backend (Person A): used as-is
`POST /projects`, `POST /projects/{id}/photos`, `POST /jobs/reconstruct`,
`POST /jobs/edit`, `GET /jobs/{id}`, `GET /health`.

Requests for Person A (nice to have, frontend works around each):
1. `GET /projects` and `GET /projects/{id}`: today the browser keeps its own list in localStorage,
   so a room can't be opened on a second device.
2. **Edits are not cumulative**: `run_3d_edit_task` always starts from `before_model.ply`, so
   "warmer" then "darker" does not stack. The UI works around this by sending the combined prompt
   ("warmer, darker") when "Build on my previous style changes" is ticked. Better fix: allow
   a `base_job_id` so an edit can start from a previous `after_model.ply`.
3. Splat coordinates: "Place in room" sends a point in the splat's own coordinate system
   (viewer position converted with `worldToLocal`). Please confirm the `.splat` is not re-centred
   or re-scaled relative to the PLY used for insertion.
4. Run the migration `0002_depth_map_metadata.sql` on existing DBs.

### Planner (Person B): proposed endpoints (the UI shows "plan isn't ready" on 404)
```
GET  /api/v1/projects/{id}/plan
POST /api/v1/projects/{id}/plan/refine    body: { "prompt": "...", "budget_cap": 30000 | null }
```
Both return the same JSON (see `src/data/samplePlan.js`):
```jsonc
{
  "summary": "string",
  "palette":  [{ "name": "Sage", "hex": "#8FA68F", "role": "Accent wall" }],
  "steps":    [{ "id": "s1", "title": "…", "detail": "…" }],
  "shopping": [{
    "id": "i1", "name": "Oat linen sofa", "category": "Seating", "qty": 1,
    "price": { "kind": "catalog",  "amount": 24999 },      // grounded, real price (INR)
    //  or    { "kind": "estimate", "min": 3000, "max": 4500 }  // LLM range, no catalog match
    "url": "https://… or null",
    "catalog_image_key": "catalog/sofa.png or null"        // enables the "Place in room" button
  }],
  "budget": { "cap": 50000, "currency": "INR" },
  "after_image_url": "optional 2D ControlNet+SDXL preview, used on the share card"
}
```
Catalog prices render with a green tick; estimates render as dashed amber ranges, exactly
as the project brief requires. The totals bar is display-only: the server stays the
authority on budget compliance.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Can't reach the RoomSage server" | backend not running, or `DEV_API_TARGET` wrong in `.env` |
| Model download fails / 403 | the presigned link lasts 1 h; reload the page for a fresh one |
| Model loads but is upside down or sideways | tap the rotate button in the viewer toolbar (cycles 4 orientations) |
| Camera button says "needs https" on phone | use `npm run dev:https` |
| `npm install` peer-dependency error for three | install the three version Spark asks for, or `npm i --legacy-peer-deps` |
| Slow on a laptop | toggle the lightning-bolt button; close other GPU-heavy tabs |
| Blank viewer, console shows Spark API error | check the installed `@sparkjsdev/spark` version against its docs; `SplatMesh({ fileBytes, fileName })` and `SparkRenderer({ renderer })` are the two calls used in `SplatViewer.jsx` |
