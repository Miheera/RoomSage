# Integration with Person A's backend

Based on a read-only review of [Miheera/RoomSage](https://github.com/Miheera/RoomSage) at commit
`23a2fd8`. Nothing here has been run against a live instance: the adapter is tested with mocked HTTP
against schemas mirrored in `app/integration/person_a_schemas.py`. Live calls happen only with
`PERSON_A_MODE=live`.

## What is used

| Route | Used for |
|---|---|
| `POST /api/v1/projects` | Create a project |
| `POST /api/v1/projects/{id}/photos` | Upload 5-15 photos in one request |
| `POST /api/v1/jobs/reconstruct` | Start reconstruction |
| `POST /api/v1/jobs/edit` | Send the design's 3D edit |
| `GET /api/v1/jobs/{id}` | Poll a job |

Project creation, photo upload and reconstruction stay with the frontend and Person A's API.
This service only needs the resulting `project_id`.

## Constraints found in the code

**A design has to fit in one edit job.** Every job starts from `before_model.ply` and overwrites
`after_model.ply` (`run_3d_edit_task`), so a second job discards the first. One job takes a style
prompt, one mask removal and one insertion. `build_edit_plan` therefore produces exactly one
request, and lists every other selected product in `not_sent`.

**Only one 3D job can run per project.** Both job types take the same Redis lock, and a second job
fails rather than waits. Callers should wait for `COMPLETED` before submitting again.

**Inserting a product needs data this service cannot derive.**
- `insert_catalog_image_key` is an S3 key the worker can read. There is no upload route for catalog
  images, so someone has to place the image in the bucket and record the key in the product's
  `catalog_image_key`. No shipped product has one yet.
- `target_position` is in Nerfstudio scene coordinates and `target_scale` sets the mesh's largest
  extent in scene units. These are not metres, and no API returns scene bounds. The caller supplies
  them; the adapter never guesses a placement.

**Removal needs a reviewed mask id.** Mask ids are class-agnostic SAM clusters, written only when
`SAM_ENABLED=true`, and no API lists them. Person A's README warns to review labels before removing
furniture. The adapter sends `remove_mask_id` only when the caller provides one.

**No scene data comes back.** There is no `GET /projects/{id}`, and no route returns room dimensions
or detected objects. `room_type` is known only because the frontend set it. Dimensions come from the
user, objects from the user or `/uploads/{id}/analysis`, and anything else stays unknown.

**The depth map is not used for 2D previews.** It is rendered from the pose nearest the median capture
position, and the API does not say which photo that is. ControlNet needs depth aligned with the image
being edited, so the 2D pipeline estimates depth from the same photo instead.

## Requests that would help

These are suggestions for Person A, in order of value:

1. A way to accumulate edits, such as an option to start from the latest `after_model.ply`.
2. A route that lists SAM clusters with a preview image for each.
3. A route that returns scene bounds and scale, so metres can map to scene units.
4. A catalog image upload route.
5. `GET /projects/{id}`, and the image name behind the depth map.

## Going live

1. Start Person A's stack as its README describes.
2. Set `PERSON_A_MODE=live` and `PERSON_A_BASE_URL`.
3. Create a project, upload photos and reconstruct through Person A's API, then wait for `COMPLETED`.
4. Check `POST /designs/{id}/edit-3d/plan`, then send `POST /designs/{id}/edit-3d`.

## Keeping in sync

`person_a_schemas.py` is a copy of Person A's contract at one commit. When their schemas change,
update it and `tests/test_person_a.py` together.
