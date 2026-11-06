> **Reading as an AI agent?** This is the Markdown version of https://docs.meshy.ai/api/image-to-3d.
>
> - Full docs index: https://docs.meshy.ai/llms.txt
> - Single-fetch full content: https://docs.meshy.ai/llms-full.txt
> - Tool-calling access via MCP server: https://docs.meshy.ai/api/ai

---
# Image to 3D API

Turn an image into a 3D model, textured or untextured, in the formats you choose. Send the image, or use the output of a Text to Image or Image to Image task.

---

## POST /openapi/v1/image-to-3d -- Create an Image to 3D task

Creates an Image to 3D task. The task runs in the background. For how long a task usually takes, see [Processing times](/api/task-lifecycle#processing-times).

### Parameters

  - `input_task_id` · *string* · **required**

  Required unless you send `image_url`. The ID of a Text to Image or Image to Image task whose image you want to use as the input. The task must be one you created with the API, have the status `SUCCEEDED`, and have produced exactly one image.

  - `image_url` · *string* · **required**

  Required unless you send `input_task_id`. The image to turn into a 3D model, as a public URL or a base64-encoded data URI, such as `data:image/jpeg;base64,<your base64-encoded image data>`. If you send both, Meshy uses `input_task_id`.

  The image must be a JPG, JPEG, PNG, or WebP file of at most 20 MB, at least 32 pixels on each side, and at most 178,956,970 pixels (width × height).

  - `model_type` · *string* · default: `standard`

  The kind of 3D mesh to generate.

  Available values:
  * `standard`: a high-detail mesh
  * `smart-topology`: a mesh from a Smart Topology model, which you pick with `ai_model` (`meshy-t2` or `meshy-t2.5`)

  With `smart-topology`, Meshy ignores `should_remesh`, `save_pre_remeshed_model`, and `decimation_mode`, and accepts only `topology: triangle`.

  - `ai_model` · *string* · default: `latest`

  The AI model that generates the 3D model. The available values depend on `model_type`.

  Standard models (`model_type: standard`):
  * `latest`: Meshy 7.1
  * `meshy-7.1`
  * `meshy-6`
  * `meshy-6-lite`
  * `meshy-7` (deprecated): use `meshy-7.1` instead

  Smart Topology models (`model_type: smart-topology`) produce triangles, at a face count you can set with `target_polycount`:
  * `meshy-t2`: cleaner topology and natively separated parts
  * `meshy-t2.5` (default): sharper detail for game assets

  See [Models](/api/models#choose-by-project) for which model fits your project, such as a game character or a 3D print.

  - `geometry_resolution` · *string* · default: `standard`

  The geometry generation pass.

  Available values:
  * `standard`
  * `2k`: runs the Ultra pass at 2048³
  * `4k`: runs the Ultra pass at 4096³, for the most surface detail

  `2k` needs `meshy-7.1`, `latest`, or the deprecated `meshy-7`. `4k` needs `meshy-7.1` or `latest`.

  - `ultra_mode` · *boolean* · default: `false` · **deprecated**

  Use `geometry_resolution` instead. `ultra_mode: true` is the same as `geometry_resolution: "2k"`.

  - `should_texture` · *boolean* · default: `true`

  Whether to generate textures. `true` returns a textured model, and `false` skips texturing and returns an untextured mesh.

**Only when `should_texture` is set:**
  - `texture_model` · *string* · default: `depends on ai_model`

  The AI model that textures the 3D model, in the same task. Leave it out to use the default texture model for your `ai_model`. It doesn't change the credit cost.

  Available values:
  * `meshy-7`: Meshy 7, with PBR maps and textures up to 8K
  * `meshy-n1`: Meshy N1, a 2K base color texture from reference images only
  * `meshy-6-lite`: Meshy 6 Lite, a 2K texture at the lowest cost
  * `meshy-6`: Meshy 6, only as the default for `ai_model: meshy-6`

  Every `ai_model` takes `meshy-7`, `meshy-n1`, and `meshy-6-lite`. An option that the chosen texture model doesn't support returns `400`. With `meshy-n1`, a generated model over 7,999,999 triangles fails the task, and Meshy refunds its credits. With `should_texture: false`, Meshy ignores `texture_model`, but an unknown value still returns `400`.

  See [Models](/api/models#texture-models) for what each texture model does and which to pick.

  - `enable_pbr` · *boolean* · default: `false`

  Whether to also generate PBR maps (metallic, roughness, and normal) besides the base color. `true` needs `should_texture: true`, and returns `400` with `should_texture: false`. With `texture_model: meshy-n1`, `true` returns `400`.

  The Meshy 6 texture model also generates an emission map, except at `texture_resolution: 8k`.

  - `texture_resolution` · *string* · default: `2k`

  The size of the base color texture. A larger texture captures more surface detail.

  Available values:
  * `2k`: 2048×2048
  * `4k`: 4096×4096
  * `8k`: 8192×8192

  `4k` and `8k` need the Meshy 7 or Meshy 6 texture model, and return `400` with the others. At `8k`, Meshy doesn't produce an emission map.

  - `hd_texture` · *boolean* · default: `false` · **deprecated**

  Use `texture_resolution` instead. `hd_texture: true` is the same as `texture_resolution: "4k"`. If you send both, Meshy uses `texture_resolution`.

  - `texture_prompt` · *string*

  Text that guides the texture, up to 800 characters. Longer text returns `400`. With `texture_model: meshy-n1`, sending it returns `400`.

  - `texture_image_url` · *string*

  An image that guides the texture, as a public URL or a base64-encoded data URI, such as `data:image/jpeg;base64,<your base64-encoded image data>`. The image must be a JPG, JPEG, PNG, or WebP file of at most 20 MB, at least 32 pixels on each side, and at most 178,956,970 pixels (width × height).

  Meshy guides the texture with only one of `texture_prompt` and `texture_image_url`. If you send both, Meshy uses `texture_prompt`. For the credit cost of texturing, see [Pricing](/api/pricing).

  With `texture_model: meshy-n1`, Meshy textures from this image as the front view, or from the input image when you leave it out.

  > **Note:** Texturing from an image might not work well when the image's shape differs a lot from the 3D model's geometry.

  - `should_remesh` · *boolean* · default: `true (meshy-6-lite), false (other models)`

  Whether to run the remesh phase. For the highest-quality 3D model, set it to `false`.

**Only when `should_remesh` is set:**
  - `topology` · *string* · default: `triangle`

  The face type of the remeshed model.

  Available values:
  * `quad`: a quad-dominant mesh
  * `triangle`: a decimated triangle mesh

  Smart Topology output is triangle-only, so `quad` with `meshy-t2` or `meshy-t2.5` returns `400`. With `texture_model: meshy-n1`, `quad` returns `400` when the model is remeshed, which `meshy-6-lite` does by default.

  - `decimation_mode` · *integer*

  Adaptive face count, by level. When you set it, Meshy ignores `target_polycount`.

  Available values:
  * `1`: ultra
  * `2`: high
  * `3`: medium
  * `4`: low

  - `save_pre_remeshed_model` · *boolean* · default: `false`

  Whether to also store a GLB of the model from before the remesh phase. Meshy returns it in `model_urls.pre_remeshed_glb`.

  - `target_polycount` · *integer*

  The number of faces to aim for. The result can differ from the target, depending on the model's geometry.

  It applies in two separate cases:
  * **Remesh**: When `should_remesh` is `true` on a `standard` model, Meshy decimates the mesh to about this count. If you set `decimation_mode`, Meshy uses it and ignores `target_polycount`.
  * **Smart Topology**: When `model_type` is `smart-topology` and `ai_model` is `meshy-t2` or `meshy-t2.5`, Meshy generates the model at this face count. No remesh runs, and you don't need `should_remesh`.

  The range and default depend on the case:

  | Case | Range | Default |
  | --- | --- | --- |
  | Remesh | `100` to `300000` | `30000` |
  | `meshy-t2` | `100` to `15000` | `4000` |
  | `meshy-t2.5` | `100` to `30000` | `15000` |

  A value outside the range returns `400`.

  For a low-poly game asset, use a Smart Topology model instead of remeshing a standard model. See [Models](/api/models#choose-by-project).

  - `symmetry_mode` · *string* · default: `auto` · **deprecated**

  Leave it out, because it doesn't affect the output. Its values controlled symmetry during generation:
  * `off`: no symmetry
  * `auto`: symmetry chosen from the input geometry
  * `on`: enforced symmetry

  - `pose_mode` · *string* · default: `""`

  The pose for the generated model.

  Available values:
  * `a-pose`: an A pose
  * `t-pose`: a T pose
  * `""` (empty string): no specific pose

  - `is_a_t_pose` · *boolean* · default: `false` · **deprecated**

  Use `pose_mode` instead. Whether to generate the model in an A or T pose.

  - `surface_mode` · *string* · **deprecated**

  Use `ai_model` instead. It doesn't affect the output, because the model generation it selected is retired.

  Accepted values are `organic` and `hard`. Any other value returns `400`.

  - `image_enhancement` · *boolean* · default: `true`

  Whether to optimize the input image before generating. `false` keeps the exact look of the input image, with no style processing.

  It works only with `meshy-6`, `meshy-7.1`, or `latest`. With `meshy-6-lite` or `model_type: smart-topology`, it returns `400`.

  - `remove_lighting` · *boolean* · default: `true`

  Whether to remove highlights and shadows from the base color texture. The result is cleaner and works better under your own lighting.

  It takes effect only with the Meshy 6 texture model, and a texture model that doesn't support it returns `400` for `true`. With `model_type: smart-topology`, or with `ai_model: meshy-6-lite` and its default texture model, sending it returns `400`.

  - `moderation` · *boolean* · default: `false`

  Whether to screen these inputs for harmful content before generating:
  * `image_url`
  * `texture_image_url`
  * `texture_prompt`

  If Meshy detects harmful content, the request returns `400` and Meshy doesn't create a task.

  - `target_formats` · *string[]*

  The file formats to generate. Meshy generates and returns only the formats you list, which can shorten the task. If you leave it out, Meshy generates every format except `3mf`, which you get only when you list it.

  Available values:
  * `glb`
  * `obj`
  * `fbx`
  * `stl`
  * `usdz`
  * `3mf`

  - `auto_size` · *boolean* · default: `false`

  Whether to estimate the object's real-world height with AI vision and resize the model to it. Meshy puts the origin at the bottom unless you set `origin_at`.

  - `alpha_thumbnail` · *boolean* · default: `false`

  Whether to also render a preview with a transparent background (RGBA), returned in `alpha_thumbnail_url` when you get the task. `thumbnail_url` doesn't change.

  - `multi_view_thumbnails` · *boolean* · default: `false`

  Whether to also render front, right, back, and left previews, returned in `thumbnail_urls` when you get the task. `thumbnail_url` doesn't change and still shows the front view. This adds about 5 seconds to the task.

**Only when `auto_size` is set:**
  - `origin_at` · *string* · default: `bottom`

  Where to put the model's origin, `bottom` or `center`.

### Returns

The `result` field of the response holds the ID of the new Image to 3D task.

### Failure modes

  - `400 - Bad Request`

  The request can't run. The error message says which problem it found. For example:
  * **Missing parameter**: Neither `image_url` nor `input_task_id` was sent.
  * **Invalid input task**: `input_task_id` isn't a `SUCCEEDED` Text to Image or Image to Image task that produced exactly one image.
  * **Invalid image format**: The `image_url` image isn't a JPG, JPEG, PNG, or WebP file.
  * **Unreachable URL**: Meshy can't download the `image_url` image, because the URL returns `404` or times out.
  * **Image too large**: The `image_url` or `texture_image_url` image is over 20 MB.
  * **Invalid image size**: The `image_url` or `texture_image_url` image has a side shorter than 32 pixels, or more than 178,956,970 pixels (width × height).
  * **Invalid data URI**: The base64 string is malformed.
  * **Invalid parameter combination**: `enable_pbr` is `true`, and `should_texture` is `false`.
  * **Unsupported model for Ultra**: `geometry_resolution` is `2k` or `4k`, with an `ai_model` that can't run it.
  * **Unsupported model for image options**: one of these:
* `image_enhancement` was sent with `ai_model: "meshy-6-lite"` or `model_type: "smart-topology"`.
* `remove_lighting` was sent with `model_type: "smart-topology"`, or with `ai_model: "meshy-6-lite"` and its default texture model.
  * **Invalid texture model**: `texture_model` isn't one of the available values.
  * **Texture model not available**: `texture_model` is `meshy-6` with an `ai_model` other than `meshy-6`. Send `meshy-7`, `meshy-n1`, or `meshy-6-lite`, or leave it out.
  * **Unsupported option for the texture model**: The request asks the texture model for an option it doesn't support, such as `enable_pbr: true` with `meshy-n1` or a `4k` texture with `meshy-6-lite`. See [Models](/api/models#texture-models).

  Fix the request, then send it again. See [Errors](/api/errors#request-errors).

  - `401 - Unauthorized`

  The API key is missing or invalid. Check the `Authorization` header.

  - `402 - Payment Required`

  For example:
  * **Insufficient credits**: The account doesn't have enough credits for this task, or the account reached a credit limit. Check your credit balance, then retry.
  * **Free plan**: Creating a task requires a paid plan. Upgrade your plan in your [subscription settings](https://www.meshy.ai/settings/subscription), then retry.

  - `403 - Forbidden`

  Content moderation blocked the prompt or an input image. Change the input, then retry.

  - `404 - Not Found`

  `input_task_id` doesn't match any of your tasks. Check the task ID, then send the request again.

  - `429 - Too Many Requests`

  You exceeded your rate limit. Wait, then retry. See [Rate limits](/api/rate-limits).

**cURL**

```bash
# Simple request with required params
curl https://api.meshy.ai/openapi/v1/image-to-3d \
  -X POST \
  -H "Authorization: Bearer ${YOUR_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{
    "image_url": "<your publicly accessible image url or base64-encoded data URI>"
  }'

# With Ultra 4K geometry, remesh, PBR, and A-pose
curl https://api.meshy.ai/openapi/v1/image-to-3d \
  -X POST \
  -H "Authorization: Bearer ${YOUR_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{
    "image_url": "<your publicly accessible image url or base64-encoded data URI>",
    "ai_model": "meshy-7.1",
    "geometry_resolution": "4k",
    "enable_pbr": true,
    "should_remesh": true,
    "target_polycount": 100000,
    "should_texture": true,
    "pose_mode": "a-pose",
    "target_formats": ["glb"]
  }'

# Textured with Meshy N1 from the input image
curl https://api.meshy.ai/openapi/v1/image-to-3d \
  -X POST \
  -H "Authorization: Bearer ${YOUR_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{
    "image_url": "<your publicly accessible image url or base64-encoded data URI>",
    "ai_model": "meshy-7.1",
    "texture_model": "meshy-n1"
  }'
```

```javascript
import axios from 'axios'

const headers = { Authorization: `Bearer ${YOUR_API_KEY}` };

// Simple request with required params
const payload = {
  image_url: "<your publicly accessible image url or base64-encoded data URI>",
};

try {
  const response = await axios.post(
    'https://api.meshy.ai/openapi/v1/image-to-3d',
    payload,
    { headers }
  );
  console.log(response.data);
} catch (error) {
  console.error(error);
}

// With Ultra 4K geometry, remesh, PBR, and A-pose
const advancedPayload = {
  image_url: "<your publicly accessible image url or base64-encoded data URI>",
  ai_model: "meshy-7.1",
  geometry_resolution: "4k",
  enable_pbr: true,
  should_remesh: true,
  target_polycount: 100000,
  should_texture: true,
  pose_mode: "a-pose",
  target_formats: ["glb"],
};

try {
  const response = await axios.post(
    'https://api.meshy.ai/openapi/v1/image-to-3d',
    advancedPayload,
    { headers }
  );
  console.log(response.data);
} catch (error) {
  console.error(error);
}

// Textured with Meshy N1 from the input image
const meshyN1Payload = {
  image_url: "<your publicly accessible image url or base64-encoded data URI>",
  ai_model: "meshy-7.1",
  texture_model: "meshy-n1",
};

try {
  const response = await axios.post(
    'https://api.meshy.ai/openapi/v1/image-to-3d',
    meshyN1Payload,
    { headers }
  );
  console.log(response.data);
} catch (error) {
  console.error(error);
}
```

```python
import requests

headers = {
    "Authorization": f"Bearer {YOUR_API_KEY}"
}

# Simple request with required params
payload = {
    "image_url": "<your publicly accessible image url or base64-encoded data URI>",
}

response = requests.post(
    "https://api.meshy.ai/openapi/v1/image-to-3d",
    headers=headers,
    json=payload,
)
response.raise_for_status()
print(response.json())

# With Ultra 4K geometry, remesh, PBR, and A-pose
advanced_payload = {
    "image_url": "<your publicly accessible image url or base64-encoded data URI>",
    "ai_model": "meshy-7.1",
    "geometry_resolution": "4k",
    "enable_pbr": True,
    "should_remesh": True,
    "target_polycount": 100000,
    "should_texture": True,
    "pose_mode": "a-pose",
    "target_formats": ["glb"],
}

response = requests.post(
    "https://api.meshy.ai/openapi/v1/image-to-3d",
    headers=headers,
    json=advanced_payload,
)
response.raise_for_status()
print(response.json())

# Textured with Meshy N1 from the input image
meshy_n1_payload = {
    "image_url": "<your publicly accessible image url or base64-encoded data URI>",
    "ai_model": "meshy-7.1",
    "texture_model": "meshy-n1",
}

response = requests.post(
    "https://api.meshy.ai/openapi/v1/image-to-3d",
    headers=headers,
    json=meshy_n1_payload,
)
response.raise_for_status()
print(response.json())
```

**Response**

```json
{
  "result": "018a210d-8ba4-705c-b111-1f1776f7f578"
}
```

---

## GET /openapi/v1/image-to-3d/:id -- Get an Image to 3D task

Returns an Image to 3D task by its ID.

### Parameters

  - `id` · *path*

  The task ID from the create response.

### Returns

The [Image to 3D task object](#the-image-to-3d-task-object).

**cURL**

```bash
curl https://api.meshy.ai/openapi/v1/image-to-3d/018a210d-8ba4-705c-b111-1f1776f7f578 \
  -H "Authorization: Bearer ${YOUR_API_KEY}"
```

```javascript
import axios from 'axios'

const taskId = '018a210d-8ba4-705c-b111-1f1776f7f578';
const headers = { Authorization: `Bearer ${YOUR_API_KEY}` };

try {
  const response = await axios.get(
    `https://api.meshy.ai/openapi/v1/image-to-3d/${taskId}`,
    { headers }
  );
  console.log(response.data);
} catch (error) {
  console.error(error);
}
```

```python
import requests

task_id = "018a210d-8ba4-705c-b111-1f1776f7f578"
headers = {
    "Authorization": f"Bearer {YOUR_API_KEY}"
}

response = requests.get(
    f"https://api.meshy.ai/openapi/v1/image-to-3d/{task_id}",
    headers=headers,
)
response.raise_for_status()
print(response.json())
```

**Response**

```json
{
  "id": "018a210d-8ba4-705c-b111-1f1776f7f578",
  "type": "image-to-3d",
  "model_urls": {
    "glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.glb?Expires=***",
    "fbx": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.fbx?Expires=***",
    "obj": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.obj?Expires=***",
    "usdz": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.usdz?Expires=***",
    "stl": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.stl?Expires=***",
    "pre_remeshed_glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/pre_remeshed_model.glb?Expires=***"
  },
  "thumbnail_url": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview.png?Expires=***",
  "thumbnail_urls": {
    "front": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_front.png?Expires=***",
    "right": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_right.png?Expires=***",
    "back": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_back.png?Expires=***",
    "left": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_left.png?Expires=***"
  },
  "texture_prompt": "",
  "progress": 100,
  "started_at": 1692771667037,
  "created_at": 1692771650657,
  "expires_at": 1692771679037,
  "finished_at": 1692771669037,
  "status": "SUCCEEDED",
  "texture_urls": [
    {
      "base_color": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0.png?Expires=***",
      "metallic": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_metallic.png?Expires=XXX",
      "normal": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_normal.png?Expires=XXX",
      "roughness": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_roughness.png?Expires=XXX",
      "emission": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_emission.png?Expires=XXX"
    }
  ],
  "preceding_tasks": 0,
  "task_error": null,
  "consumed_credits": 30
}
```

---

## DELETE /openapi/v1/image-to-3d/:id -- Delete an Image to 3D task

Permanently deletes an Image to 3D task, including its models and data. You can't undo a delete.

### Path parameters

  - `id` · *path*

  The ID of the task to delete.

### Task status

Deleting a `PENDING` task refunds the credits it used when you created it.

An `IN_PROGRESS` task can't be deleted. The request returns `409 Conflict`,
and the task keeps running. Meshy doesn't refund a task that has started,
so deleting it would cost you both the credits and the result. Wait for it
to reach `SUCCEEDED`, `FAILED`, or `CANCELED`, then delete it.

Deleting a task in a final status (`SUCCEEDED`, `FAILED`, or `CANCELED`)
doesn't refund its credits.

### Returns

`200 OK` when the task is deleted, or `409 Conflict` when the task is
`IN_PROGRESS`.

  **cURL**

  ```bash
  curl --request DELETE \
    --url https://api.meshy.ai/openapi/v1/image-to-3d/018a210d-8ba4-705c-b111-1f1776f7f578 \
    -H "Authorization: Bearer ${YOUR_API_KEY}"
  ```

  ```javascript
  import axios from 'axios'

  const taskId = '018a210d-8ba4-705c-b111-1f1776f7f578'
  const headers = { Authorization: `Bearer ${YOUR_API_KEY}` }

  try {
    await axios.delete(
      `https://api.meshy.ai/openapi/v1/image-to-3d/${taskId}`,
      { headers }
    )
  } catch (error) {
    console.error(error)
  }
  ```

  ```python
  import requests

  task_id = "018a210d-8ba4-705c-b111-1f1776f7f578"
  headers = {
      "Authorization": f"Bearer {YOUR_API_KEY}"
  }

  response = requests.delete(
      f"https://api.meshy.ai/openapi/v1/image-to-3d/{task_id}",
      headers=headers,
  )
  response.raise_for_status()
  ```

**Response**

```json
// 200 OK on success, with an empty body.
//
// 409 Conflict when the task is IN_PROGRESS. The task keeps running:
{
  "message": "Task is IN_PROGRESS and cannot be deleted. Credits for a task the worker has already started are not refundable; wait for it to reach SUCCEEDED, FAILED or CANCELED, then delete it."
}
```

---

## GET /openapi/v1/image-to-3d -- List Image to 3D tasks

Returns a list of your Image to 3D tasks.

### Parameters

  - `page_num` · *integer* · default: `1`

  The page number to return, up to `255`. A larger value returns `400`.

  - `page_size` · *integer* · default: `10`

  The number of tasks per page, up to `100`.

  - `sort_by` · *string*

  The sort order.

  Available values:
  * `+created_at`: oldest first
  * `-created_at`: newest first

### Returns

A paginated list of [Image to 3D task objects](#the-image-to-3d-task-object).

  **cURL**

  ```bash
  curl https://api.meshy.ai/openapi/v1/image-to-3d?page_size=10 \
  -H "Authorization: Bearer ${YOUR_API_KEY}"
  ```

  ```javascript
  import axios from 'axios'

  const headers = { Authorization: `Bearer ${YOUR_API_KEY}` };

  try {
   const response = await axios.get(
     `https://api.meshy.ai/openapi/v1/image-to-3d?page_size=10`,
  { headers }
    );
   console.log(response.data);
  } catch (error) {
   console.error(error);
  }
  ```

  ```python
  import requests

  headers = {
      "Authorization": f"Bearer {YOUR_API_KEY}"
  }

  response = requests.get(
      "https://api.meshy.ai/openapi/v1/image-to-3d",
      headers=headers,
      params={"page_size": 10}
  )
  response.raise_for_status()
  print(response.json())
  ```

**Response**

```json
[
  {
    "id": "018a210d-8ba4-705c-b111-1f1776f7f578",
    "type": "image-to-3d",
    "model_urls": {
      "glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.glb?Expires=***",
      "fbx": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.fbx?Expires=***",
      "obj": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.obj?Expires=***",
      "usdz": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.usdz?Expires=***",
      "pre_remeshed_glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/pre_remeshed_model.glb?Expires=***"
    },
    "thumbnail_url": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview.png?Expires=***",
    "thumbnail_urls": {
      "front": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_front.png?Expires=***",
      "right": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_right.png?Expires=***",
      "back": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_back.png?Expires=***",
      "left": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_left.png?Expires=***"
    },
    "texture_prompt": "",
    "progress": 100,
    "started_at": 1692771667037,
    "created_at": 1692771650657,
    "expires_at": 1692771679037,
    "finished_at": 1692771669037,
    "status": "SUCCEEDED",
    "texture_urls": [
      {
        "base_color": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0.png?Expires=***",
        "metallic": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_metallic.png?Expires=XXX",
        "normal": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_normal.png?Expires=XXX",
        "roughness": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_roughness.png?Expires=XXX",
        "emission": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_emission.png?Expires=XXX"
      }
    ],
    "preceding_tasks": 0,
    "task_error": null,
    "consumed_credits": 30
  }
]
```

---

## GET /openapi/v1/image-to-3d/:id/stream -- Stream an Image to 3D task

Streams an Image to 3D task's updates as Server-Sent Events (SSE).

### Parameters

  - `id` · *path*

  The task ID from the create response.

### Returns

A stream of [Image to 3D task objects](#the-image-to-3d-task-object) as Server-Sent Events.

Each event holds the full task object, in the same shape the Get endpoint returns. While the task is `PENDING` or `IN_PROGRESS`, its result fields don't have values yet.

  **cURL**

  ```bash
  curl -N https://api.meshy.ai/openapi/v1/image-to-3d/018a210d-8ba4-705c-b111-1f1776f7f578/stream \
  -H "Authorization: Bearer ${YOUR_API_KEY}"
  ```

  ```javascript
  const response = await fetch(
    'https://api.meshy.ai/openapi/v1/image-to-3d/018a210d-8ba4-705c-b111-1f1776f7f578/stream',
    {
      headers: { Authorization: `Bearer ${YOUR_API_KEY}` }
    }
  );

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (line.startsWith('data:')) {
        const data = JSON.parse(line.slice(5));
        console.log(data);

        if (['SUCCEEDED', 'FAILED', 'CANCELED'].includes(data.status)) {
          reader.cancel();
        }
      }
    }
  }
  ```

  ```python
  import requests
  import json

  headers = {
      "Authorization": f"Bearer {YOUR_API_KEY}",
      "Accept": "text/event-stream"
  }

  response = requests.get(
      'https://api.meshy.ai/openapi/v1/image-to-3d/018a210d-8ba4-705c-b111-1f1776f7f578/stream',
      headers=headers,
      stream=True
  )

  for line in response.iter_lines():
      if line:
          if line.startswith(b'data:'):
              data = json.loads(line.decode('utf-8')[5:])
              print(data)

              if data['status'] in ['SUCCEEDED', 'FAILED', 'CANCELED']:
                  break

  response.close()
  ```

  **Response Stream**

  ```javascript
  // Error event example
  event: error
  data: {
    "status_code": 404,
    "message": "Task not found"
  }

  // Message event examples illustrate task progress.
  // These examples are shortened. Each event holds the full task object.
  event: message
  data: {
    "id": "018a210d-8ba4-705c-b111-1f1776f7f578",
    "progress": 0,
    "status": "PENDING"
  }

  event: message
  data: {
    "id": "018a210d-8ba4-705c-b111-1f1776f7f578",
    "type": "image-to-3d",
    "model_urls": {
    "glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.glb?Expires=***",
    "fbx": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.fbx?Expires=***",
    "obj": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.obj?Expires=***",
    "usdz": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.usdz?Expires=***",
    "stl": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.stl?Expires=***",
    "pre_remeshed_glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/pre_remeshed_model.glb?Expires=***"
    },
    "thumbnail_url": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview.png?Expires=***",
    "thumbnail_urls": {
      "front": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_front.png?Expires=***",
      "right": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_right.png?Expires=***",
      "back": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_back.png?Expires=***",
      "left": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_left.png?Expires=***"
    },
    "texture_prompt": "",
    "progress": 100,
    "started_at": 1692771667037,
    "created_at": 1692771650657,
    "expires_at": 1692771679037,
    "finished_at": 1692771669037,
    "status": "SUCCEEDED",
    "texture_urls": [
      {
        "base_color": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0.png?Expires=***",
        "metallic": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_metallic.png?Expires=XXX",
        "normal": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_normal.png?Expires=XXX",
        "roughness": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_roughness.png?Expires=XXX",
        "emission": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_emission.png?Expires=XXX"
      }
    ],
    "preceding_tasks": 0,
    "task_error": null,
    "consumed_credits": 30
  }
```

---

## The Image to 3D task object
An Image to 3D task object describes one generation of a 3D model from an image. It holds the status, the progress, and the model files when the task succeeds.

### Properties

- `id` · *string*

  The task ID. Treat it as an opaque string, and don't rely on its format.

- `type` · *string*

  The task type, always `image-to-3d`.

- `model_urls` · *object*

  Download URLs for the generated 3D model, one per file format. A format the task didn't generate has no key, rather than an empty string.

  - `glb` · *string*

  The GLB file.

  - `fbx` · *string*

  The FBX file.

  - `obj` · *string*

  The OBJ file.

  - `usdz` · *string*

  The USDZ file.

  - `mtl` · *string*

  The MTL file, returned with an OBJ file when the model has textures.

  - `stl` · *string*

  The STL file.

  - `3mf` · *string*

  The 3MF file. Present only when `target_formats` includes `3mf`.

  - `pre_remeshed_glb` · *string*

  The GLB file from before the remesh. Present only when you created the task with both `should_remesh: true` and `save_pre_remeshed_model: true`.

- `thumbnail_url` · *string*

  Download URL for a preview image of the 3D model. When `thumbnail_urls` is present, it's the same image as `thumbnail_urls.front`. Meshy keeps it for backward compatibility.

- `alpha_thumbnail_url` · *string*

  Download URL for a version of `thumbnail_url` with a transparent background (RGBA). Present only when you created the task with `alpha_thumbnail: true` and Meshy rendered the transparent preview.

- `thumbnail_urls` · *object*

  Download URLs for previews of the 3D model from four sides. Each value is a signed URL to a 512×512 PNG, rendered with the same materials and lighting as `thumbnail_url`. Use them to preview the model from several angles, for example in a batch pipeline, without downloading the GLB.

  Present only when you created the task with `multi_view_thumbnails: true` and the task reached `SUCCEEDED`. Tasks created without `multi_view_thumbnails: true` don't include this field.

  - `front` · *string*

  The front view, at 0° around the vertical axis. It matches `thumbnail_url`.

  - `right` · *string*

  The right view, at 90°.

  - `back` · *string*

  The back view, at 180°.

  - `left` · *string*

  The left view, at 270°.

- `texture_prompt` · *string*

  The text prompt that guided the texture.

- `texture_image_url` · *string*

  Download URL for the image that guided the texture.

- `ultra_mode` · *boolean* · **deprecated**

  Read `geometry_resolution` instead.

- `geometry_resolution` · *string*

  The Ultra tier the task ran, `2k` or `4k`. A `standard` task doesn't include this field.

- `progress` · *integer*

  How much of the task is done, as a percentage. It's `0` until the task starts and `100` when it succeeds.

- `started_at` · *timestamp*

  Time the task started, in Unix milliseconds. It's `0` until the task starts.

- `created_at` · *timestamp*

  Time the task was created, in Unix milliseconds.

- `expires_at` · *timestamp*

  Time the task's result expires, in Unix milliseconds.

- `finished_at` · *timestamp*

  Time the task finished, in Unix milliseconds. It's `0` until the task finishes.

- `status` · *string*

  The task status, one of `PENDING`, `IN_PROGRESS`, `SUCCEEDED`, `FAILED`, or `CANCELED`.

- `texture_urls` · *array*

  The texture maps the task generated, as an array of objects. It usually holds one object, with these fields:

  - `base_color` · *string*

  Download URL for the base color map.

  - `metallic` · *string*

  Download URL for the metallic map. The field is absent when you created the task with `enable_pbr: false`.

  - `normal` · *string*

  Download URL for the normal map. The field is absent when you created the task with `enable_pbr: false`.

  - `roughness` · *string*

  Download URL for the roughness map. The field is absent when you created the task with `enable_pbr: false`.

  - `emission` · *string*

  Download URL for the emission map. Meshy includes it only when you created the task with all of these:
* `ai_model: meshy-6`, with its default texture model
* `enable_pbr: true`
* a `texture_resolution` of `2k` or `4k`

- `preceding_tasks` · *integer*

  The number of tasks ahead of this one in the queue. The value is meaningful only while the task is `PENDING`.

- `task_error` · *object*

  The error details when the task fails. It's `null` unless the status is `FAILED`. See [Errors](/api/errors#task-errors).

- `consumed_credits` · *integer*

  The number of credits the task used. Present when the status is `PENDING`, `IN_PROGRESS`, or `SUCCEEDED`. It's `0` for a `FAILED` task, because Meshy refunds the credits when a task fails.

<span id="example-image-to-3d-task-object" />

**Example Image to 3D Task Object**

```json
{
  "id": "018a210d-8ba4-705c-b111-1f1776f7f578",
  "type": "image-to-3d",
  "model_urls": {
    "glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.glb?Expires=***",
    "fbx": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.fbx?Expires=***",
    "obj": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.obj?Expires=***",
    "usdz": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.usdz?Expires=***",
    "stl": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/model.stl?Expires=***",
    "pre_remeshed_glb": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/pre_remeshed_model.glb?Expires=***"
  },
  "thumbnail_url": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview.png?Expires=***",
  "thumbnail_urls": {
    "front": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_front.png?Expires=***",
    "right": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_right.png?Expires=***",
    "back": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_back.png?Expires=***",
    "left": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/preview_left.png?Expires=***"
  },
  "texture_prompt": "",
  "texture_image_url": "",
  "progress": 100,
  "started_at": 1692771667037,
  "created_at": 1692771650657,
  "expires_at": 1692771679037,
  "finished_at": 1692771669037,
  "status": "SUCCEEDED",
  "texture_urls": [
    {
      "base_color": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0.png?Expires=***",
      "metallic": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_metallic.png?Expires=XXX",
      "normal": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_normal.png?Expires=XXX",
      "roughness": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_roughness.png?Expires=XXX",
      "emission": "https://assets.meshy.ai/***/tasks/018a210d-8ba4-705c-b111-1f1776f7f578/output/texture_0_emission.png?Expires=XXX"
    }
  ],
  "preceding_tasks": 0,
  "task_error": null,
  "consumed_credits": 30
}
```