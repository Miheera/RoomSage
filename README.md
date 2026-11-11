# RoomSage

RoomSage helps people plan room interiors from room photos. It combines room
photos, a style and palette, a budget, and catalog products to create and manage
room design concepts.

## Features

- **Private projects and rooms** — Create an account, keep projects organized,
  and add rooms such as living rooms, bedrooms, and dining rooms.
- **Room photo setup** — Upload room photos and review photo coverage. One photo
  is enough to start a 2D design; additional angles improve room coverage.
- **Style and palette choices** — Choose from supported interior styles and
  palettes. The app shows required catalog roles and a minimum budget for each
  supported style.
- **Budget-aware catalog selection** — Generate a design using catalog-listed
  furniture and decor, plus available paint and flooring finishes. Selected
  items stay attached to the design with their prices and product details.
- **2D room redesigns** — The renderer analyzes room structure, recolors detected
  wall areas, applies an available floor texture, and places catalog product
  images in the room. The default mask-based path preserves the room photo's
  aspect ratio. A configured remote image service can refine the placed-item
  regions; if that service is unavailable, RoomSage labels the result as a
  composite fallback.
- **Design review and revisions** — View item hotspots, see which products were
  placed, swap or remove items, and create a new version or re-render after
  changes.
- **Shopping list and 3D workspace** — Room pages include shopping-list and 3D
  workspace areas. 3D reconstruction is a separate job from the 2D redesign;
  having enough photos makes a room eligible for coverage, but does not mean a
  3D model has already been generated.

## Tech stack

- **Frontend:** React 19, Vite, TypeScript for API types, and Three.js with
  React Three Fiber/Drei for 3D views.
- **API:** Python, FastAPI, Pydantic Settings, and SQLAlchemy.
- **Database and jobs:** PostgreSQL for persistent records; Celery and Redis
  for background jobs and progress tracking.
- **Artifact storage:** MinIO with the S3 API for room photos, design images,
  catalog assets, and generated outputs.
- **2D image pipeline:** PyTorch with CUDA, Hugging Face Transformers and
  Diffusers, OpenCV, and Pillow. The default room parsing uses ADE20K UPerNet;
  remote image refinement can run in a Kaggle or Colab notebook.
- **Language and vision services:** Google Gemini for supported project
  refinement and room-analysis tasks.
- **3D and deployment components:** NVIDIA CUDA worker containers, COLMAP and
  Nerfstudio for Gaussian reconstruction, and Meshy for supported catalog 3D
  assets.
- **Local orchestration:** Docker Compose.

## How a 2D design is made

1. Create a project and room, upload at least one room photo, and set a budget.
2. Choose an available style and palette.
3. RoomSage selects products from the catalog and queues a render job.
4. The worker analyzes room surfaces, applies the selected finishes, places
   catalog objects, and saves the result with its item list and hotspots.
5. Review the design, change products if needed, and regenerate or re-render.

The renderer needs local catalog images for furniture and flooring textures for
floor finishes. Some catalog options may be unavailable if their image asset is
missing; RoomSage should exclude those options from selection.

## Running locally

Start the services from the repository root:

```powershell
docker compose -f backend\docker-compose.yml up -d
```

Open the app at [http://localhost:5173](http://localhost:5173). The API and
worker need to be running for design jobs. GPU rendering requires a compatible
NVIDIA Docker runtime and the configured model weights in the shared Hugging
Face cache. Remote rendering also requires a reachable notebook server and
matching `REMOTE_RENDER_URL` and `REMOTE_RENDER_TOKEN` settings.

For backend configuration, API details, worker setup, and model-cache commands,
see [backend/README.md](backend/README.md). For the remote Kaggle/Colab renderer,
see [notebooks/README.md](notebooks/README.md).
