# API guide for the frontend

Base URL: `http://127.0.0.1:8100/api/v1/design-engine`. Interactive docs are at `/docs` and the
schema is at `/openapi.json`. `GET /health` (outside the prefix) reports which modes are active.

Person A's backend runs separately, on port 8000 by default, and owns `/api/v1/projects` and
`/api/v1/jobs`. This service does not repeat those routes, and it sits under its own prefix.

## Errors

Every error has the same shape:

```json
{"error": "not_found", "message": "Design ... does not exist.", "details": {}}
```

| Status | `error` | Meaning |
|---|---|---|
| 404 | `not_found` | Unknown design, product, upload, or Person A resource |
| 409 | `conflict` | The request is valid, but not in this state |
| 422 | `invalid_request` | Bad input. `details.problems` lists each field |
| 502 | `upstream_error` | Person A's service failed |
| 503 | `unavailable` | A dependency is not installed or not ready |

## Typical flow

```text
GET  /catalog/themes                      theme picker
POST /uploads                             store a room photo for local analysis
POST /designs/full                        spec + recommendations + budget + plan
POST /designs/{id}/preview                dispatch Person A's 2D redesign Celery job
GET  /previews/{job_id}                   poll Person A's PostgreSQL-backed job
GET  /previews/{job_id}/image             redirect to the completed S3 image
POST /designs/{id}/refine                 change the design in plain language
```

Use `POST /designs`, then `/recommendations`, `/budget` and `/plan` if you want the steps one at a
time. Each step computes what it needs and stores it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/catalog/themes` | The 8 themes with palettes and materials |
| GET | `/catalog/categories` | Category ids |
| GET | `/catalog/stats` | Catalog counts by source and category |
| GET | `/catalog/products` | Filter by `category`, `theme`, `room_type`, `priced_only`, `max_price_inr`, `small_room`, `q`, with `limit` and `offset` |
| GET | `/catalog/products/{id}` | One product |
| POST | `/scene/normalize` | Turn partial scene data into a `SceneInfo`, listing `missing_fields` |
| POST | `/uploads` | Upload a JPEG, PNG or WebP photo (multipart field `file`) |
| POST | `/uploads/{id}/analysis` | Detect furniture classes in a photo. Empty in mock mode |
| POST | `/designs` | Create a design spec |
| POST | `/designs/full` | Spec, recommendations, budget and plan together |
| GET | `/designs/{id}` | The stored design specification and plan |
| POST | `/designs/{id}/recommendations` | Ranked products per slot, with reasons and warnings |
| POST | `/designs/{id}/budget` | Optimise. Optional body `{"budget_inr": 30000}` |
| POST | `/budget/calculate` | Total a fixed list of `product_ids` |
| POST | `/designs/{id}/plan` | Ordered transformation plan |
| POST | `/designs/{id}/refine` | Body `{"message": "Use a darker sofa."}` |
| POST | `/designs/{id}/preview` | Body `{"project_id"}`; requires Person A Stage 2 with `outputs/depth_map.png` |
| GET | `/previews/{job_id}` | Person A job status, progress and output URL |
| GET | `/previews/{job_id}/image` | Redirect to the S3 preview, or 409 if not finished |
| POST | `/designs/{id}/edit-3d/plan` | What would go to Person A. Calls nothing |
| POST | `/designs/{id}/edit-3d` | Send the edit to Person A |
| GET | `/edit-3d/jobs/{job_id}` | Poll Person A's job |

## Example: create a full design

```json
POST /designs/full
{
  "theme": "japandi",
  "budget_inr": 60000,
  "room_type": "living_room",
  "retain": ["curtain"],
  "small_room": true,
  "scene": {
    "room_type": "living_room",
    "dimensions": {"length_m": 4.2, "width_m": 3.4, "height_m": 2.8}
  }
}
```

The response is a `Design`, trimmed here:

```json
{
  "design_id": "1fb5cfc2ae32485fb861130db374306b",
  "spec": {"theme": "japandi", "color_palette": ["beige", "sand"], "assumptions": ["..."]},
  "recommendations": [{
    "slot": "sofa_seating", "priority": "essential",
    "options": [{"product": {"name": "...", "price_inr": 19890},
                 "score": 58.0, "reasons": ["..."], "warnings": ["..."]}]
  }],
  "budget": {
    "total_inr": 26945, "budget_inr": 60000, "within_budget": true, "is_partial": true,
    "items": [{"slot": "rug", "unit_price_inr": 4100, "price_as_of": "2016-01-07"}],
    "skipped_slots": [{"slot": "table", "reason": "No option has a known price."}],
    "price_notes": ["8 price(s) come from listings dated ... and are not current. Confirm before buying."]
  },
  "plan": {"summary": "...", "steps": [{"order": 1, "title": "Clear the room"}]}
}
```

## Example: refine

```json
POST /designs/{id}/refine
{"message": "Keep everything under ₹20,000."}
```

```json
{
  "outcome": {
    "understood": true,
    "stages_rerun": ["budget", "plan"],
    "stages_skipped": ["spec", "recommendations", "preview"],
    "preview_stale": false,
    "message": "Updated: budget, plan."
  },
  "spec": {}, "budget": {}, "plan": {}
}
```

If `understood` is false the design did not change, so show `message` to the user.

## What the UI needs to show

These are part of the contract, not edge cases:

- **`price_inr` can be null.** Show "Price unknown". Never show ₹0. IKEA records all have null
  prices, because their source prices are in Saudi riyals.
- **`is_partial` with `skipped_slots`.** The budget may cover only part of the room, even with money
  left over, when a slot has no priced product. `missing_essentials` lists the serious cases.
- **`price_notes`.** Catalog prices are from 2015-16. Show this note wherever totals appear.
- **`verification_status`.** `source_dataset` is a real but dated listing, `verified_live` was checked
  by hand, and `demo` is a placeholder. Mark `demo` clearly.
- **`preview.is_mock`.** A mock preview is a tinted copy of the photo, and must not be shown as a
  redesign.
- **`preview.stale`.** The design has moved on since this image was made. Offer to regenerate.
- **`preview.limitations`.** Show these near the image. The preview is illustrative and does not
  depict the recommended products.
- **Previews take time.** About 20 seconds per image on an RTX 4050, plus model loading on the
  first request, and they run one at a time. Poll every few seconds.
