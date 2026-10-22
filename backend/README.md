# RoomSage backend: Phase 1

This service provides project/photo APIs, persistent job metadata, Redis-backed
polling state, and a Celery reconstruction task entry point.

## Run locally

From this directory, start the local dependencies and services:

```powershell
docker compose up --build
```

The API uses the lightweight `Dockerfile`; the Celery worker uses
`Dockerfile.gpu`, based on CUDA 12.1 / Ubuntu 22.04 with Python 3.10,
PyTorch 2.2.2, gsplat 1.0.0, Nerfstudio 1.0.2, and COLMAP. Running the worker
requires Docker with NVIDIA Container Toolkit/GPU passthrough configured. GPU
tasks are routed to the `gpu_queue`. The TripoSR runner is not bundled; set
`TRIPOSR_RUN_SCRIPT` to a path available inside the worker container if asset
insertion is enabled.

The API is at `http://localhost:8000` and its OpenAPI UI is at
`http://localhost:8000/docs`. MinIO's console is at `http://localhost:9001`
(`minioadmin` / `minioadmin`). Compose creates the `roomsage` bucket automatically.
For non-container development, copy `.env.example` to
`.env`, create the bucket, install `requirements.txt`, and run
`uvicorn main:app --reload`; start a separate worker with
`celery -A tasks.celery_app.celery_app worker --loglevel=INFO`.

## API flow

1. `POST /api/v1/projects` with `{"name":"Living room","room_type":"living_room"}`.
2. `POST /api/v1/projects/{project_id}/photos` as multipart form data with a
   `files` field repeated for 5-15 JPEG, PNG, or WebP images.
3. `POST /api/v1/jobs/reconstruct` with `{"project_id":"<uuid>"}`.
4. Poll `GET /api/v1/jobs/{job_id}` for state, progress, logs, ETA, and signed
   output URLs.
5. `POST /api/v1/jobs/edit` with a project ID and one or more edit operations
   to queue Stage 4. The response includes a `polling_url` for the same job
   endpoint.

Uploads are limited to 25 MiB per photo by default. The API validates both the
filename extension and image signature before uploading. Redis keeps recent job
state and up to 200 log entries per job; PostgreSQL remains the durable source
of job and project metadata.

The reconstruction worker downloads the project's 5-15 supported images,
executes `ns-process-data`, `ns-train splatfacto`, and `ns-export gaussian-splat`,
converts the exported Gaussian PLY to the common 32-byte-per-splat `.splat`
layout, and uploads both files under `{project_id}/outputs/`. It
requires Nerfstudio's `ns-process-data`, `ns-train`, and `ns-export` commands
plus a compatible CUDA/PyTorch GPU runtime on the worker image; install and
validate those GPU-specific components in the worker image rather than the
lightweight API image. Temporary project workspaces are removed after each run.
Only the durable `.splat` object key is stored on the job; the API generates a
fresh, expiring signed URL when polling. After training, the worker selects the
camera pose nearest the median capture position, renders its raw depth tensor,
normalizes valid depths between the 1st and 99th percentiles, and stores a
near-bright grayscale PNG at `{project_id}/outputs/depth_map.png`. Depth
rendering is optional: failures leave the 3D reconstruction completed and
report `depth_map_available: false`; successful job polling includes
`depth_map_url`. Replace the startup `create_all` call
with Alembic migrations before deploying to a shared or production environment.

The edit task consumes `outputs/edit_assets/manifest.json` and the referenced
checkpoint/config/transforms bundle, which the reconstruction task now uploads.
Projects reconstructed before this bundle was added must be reconstructed again
before Stage 4 edits are available.
It supports Instruct-GS2GS style editing, mask pruning when the Gaussian PLY
contains `mask_id`, `sam_mask_id`, or `cluster_id`, and TripoSR mesh insertion
by sampling the generated mesh into Gaussian primitives. Configure
`TRIPOSR_RUN_SCRIPT` in the worker environment to enable insertion, and provide
`target_position` (three scene-space coordinates) plus positive `target_scale`
with an insertion request. Applying the additive depth metadata SQL migration
above remains required for databases created before that field was added.
For an existing database, apply `migrations/0002_depth_map_metadata.sql` before
deploying this version so project depth-map keys and per-job availability are
persisted.

The Compose file provisions MinIO as the local S3-compatible store in addition
to the requested Redis and PostgreSQL services.

## End-to-end pipeline smoke test

With the API, GPU worker, Redis, PostgreSQL, and S3/MinIO running, configure the
test process with the same `DATABASE_URL`, `REDIS_URL`, `S3_BUCKET`,
`S3_ENDPOINT_URL`, and AWS credentials as the services. Ensure the S3 bucket
contains a non-empty catalog image at `catalog/sample_chair.png`, and that the
worker has a configured TripoSR runner. Run:

```powershell
python scripts/test_pipeline_e2e.py
```

The script creates a disposable test project and five synthetic JPEG captures,
then waits up to two hours per GPU job by default. Use `--help` for options such
as alternate API URL, catalog image key, timeouts, and insertion scale. Stage 4
is dispatched through `POST /api/v1/jobs/edit`; job status and output
verification use the API. The script streams only a small byte range when
checking artifact URLs so it does not load full `.splat` files into memory.

SAM Gaussian labeling is optional and disabled by default. To enable it on the
GPU worker, install the `segment-anything` package, make a compatible SAM
checkpoint available inside the container, and set `SAM_ENABLED=true`,
`SAM_CHECKPOINT_PATH`, and optionally `SAM_MODEL_TYPE` (`vit_b`, `vit_l`, or
`vit_h`). The worker samples up to `SAM_MAX_KEYFRAMES` views from Nerfstudio's
`transforms.json`, associates per-view masks by overlap of projected Gaussian
centers, and writes integer `mask_id` labels (zero means unassigned) to
`outputs/before_model_labeled.ply`. A successful labeled PLY is also published
as `outputs/before_model.ply` so Stage 4 mask pruning can consume it directly.
SAM/model/checkpoint/inference errors are logged and do not fail reconstruction;
in that case the ordinary unlabelled PLY is published. Automatic masks are
class-agnostic segments, so review labels before using them to remove furniture.
