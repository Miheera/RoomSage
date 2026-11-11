from __future__ import annotations

import io
import json
import os

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from PIL import Image, ImageOps

app = FastAPI()


def encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/render")
async def render(request: Request):
    if request.headers.get("Authorization") != "Bearer test-secret":
        raise HTTPException(status_code=401, detail="bad token")
    form = await request.form()
    assert form.get("style") == "Japandi"
    assert "mask_image_field" in str(form.get("items", ""))
    original_bytes = await form["room_photo"].read()
    composite_bytes = await form["composite_image"].read()
    mode = os.getenv("FAKE_REMOTE_MODE", "invert")
    if mode == "input":
        body = original_bytes
    elif mode == "mismatch":
        body = encode(Image.new("RGB", (32, 24), "purple"))
    elif mode == "composite":
        with Image.open(io.BytesIO(original_bytes)) as original, Image.open(io.BytesIO(composite_bytes)) as composite:
            body = encode(composite.convert("RGB").resize(original.size, Image.Resampling.NEAREST))
    else:
        with Image.open(io.BytesIO(original_bytes)) as original, Image.open(io.BytesIO(composite_bytes)) as composite:
            target_size = original.size
            changed = ImageOps.invert(composite.convert("RGB").resize(target_size, Image.Resampling.NEAREST))
            body = encode(changed)
    return Response(body, media_type="image/png", headers={"X-Render-Model-Id": "fake-local-editor"})
