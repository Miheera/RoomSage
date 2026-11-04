# Reused components

Checked on 2026-11-01. Model and dataset licenses were read from Hugging Face and Kaggle metadata.
Review weights' terms before any commercial use; OpenRAIL licenses carry use restrictions.

## In use

| Component | Source | License | Role | How it is used |
|---|---|---|---|---|
| diffusers | [huggingface/diffusers](https://github.com/huggingface/diffusers) | Apache-2.0 | ControlNet inpainting | Library, unmodified |
| transformers | [huggingface/transformers](https://github.com/huggingface/transformers) | Apache-2.0 | Segmentation and depth inference | Library, unmodified |
| SD 1.5 inpainting | [stable-diffusion-v1-5/stable-diffusion-inpainting](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-inpainting) | CreativeML OpenRAIL-M | Image generation | Weights, fp16 |
| ControlNet depth | [lllyasviel/control_v11f1p_sd15_depth](https://huggingface.co/lllyasviel/control_v11f1p_sd15_depth) | OpenRAIL | Preserves room structure | Weights, fp16 |
| Depth Anything V2 Small | [depth-anything/Depth-Anything-V2-Small-hf](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) | Apache-2.0 (Small only) | Depth from the photo | Weights |
| UperNet ConvNeXt-tiny | [openmmlab/upernet-convnext-tiny](https://huggingface.co/openmmlab/upernet-convnext-tiny) | MIT | ADE20K room segmentation | Weights |
| PuLP with CBC | [coin-or/PuLP](https://github.com/coin-or/PuLP) | MIT (CBC is EPL-2.0) | Budget optimisation | Library, unmodified |
| instructor | [jxnl/instructor](https://github.com/jxnl/instructor) | MIT | Validated output from OpenAI-compatible LLMs | Library, unmodified |
| anthropic | [anthropics/anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python) | MIT | Claude API client | Library, unmodified |
| RapidFuzz | [rapidfuzz/RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | MIT | Near-duplicate detection | Library, unmodified |
| FastAPI, Pydantic, httpx, Pillow | Their PyPI projects | MIT or BSD style | Service and validation | Libraries, unmodified |

## Data

| Dataset | Source | License | Changes |
|---|---|---|---|
| Flipkart Products | [PromptCloudHQ on Kaggle](https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products) | CC BY-SA 4.0 | Filtered, mapped, parsed, deduplicated, theme tagged |
| IKEA Saudi Arabia | [TidyTuesday 2020-11-03](https://github.com/rfordatascience/tidytuesday/tree/main/data/2020/2020-11-03) | CC0-1.0 repository, Kaggle origin "Other" | Factual fields only, as above |
| Sample room photo | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Living_room_(Unsplash).jpg), by Jarosław Ceborski | CC0 | Resized for docs |

## Followed, not copied

[ml6team/controlnet-interior-design](https://huggingface.co/spaces/ml6team/controlnet-interior-design)
(OpenRAIL) showed that segmenting a room and then inpainting with ControlNet works well. No code
was taken from it. This project differs in using depth instead of its segmentation ControlNet, and
adds keep-masks and composites the original pixels back.

## Evaluated and not used

| Candidate | Why not |
|---|---|
| [RoomGPT](https://github.com/Nutlope/roomGPT) | A web app around a paid hosted API, with no pipeline to reuse |
| [adirik/interior-design](https://replicate.com/adirik/interior-design) | A hosted model rather than a local pipeline |
| Base, Large and Giant Depth Anything V2 | CC BY-NC 4.0, and unnecessary at 512 px |
| SegFormer | Its license could not be confirmed in this review, and UperNet is MIT |
| OneFormer Swin-tiny | MIT, but ships no safetensors weights |
| Paid IKEA and retailer scrapers | Paid services, and scraping was out of scope |
| [apx103/ikea](https://github.com/apx103/ikea) | Images are limited to non-commercial use |

## Custom code

Written for RoomSage because nothing suitable exists: the source adapters and theme tagging, the spec
builder, recommendation scoring, the budget model, the planner, refinement orchestration, mask rules,
the Person A adapter, and the API.
