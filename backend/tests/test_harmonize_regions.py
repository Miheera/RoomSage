import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image


def test_harmonized_room_regions_preserve_surroundings_and_drift_guard():
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    env = os.environ.copy()
    env.update({"IMAGE_PROVIDER":"huggingface", "HF_HOME":r"D:\huggingface_cache",
                "HF_HUB_OFFLINE":"1", "U2NET_HOME":r"D:\huggingface_cache\rembg",
                "HF_INPAINT_MODEL_ID":r"D:\huggingface_cache\local\stable-diffusion-v1-5-inpainting",
                "TRACE_PIPELINE":"1", "RENDER_OUTPUT_DIR":str(root / "out")})
    subprocess.run([sys.executable, "out/run_surface_object_followup.py"], cwd=backend, env=env, check=True)
    reports = json.loads((root / "out" / "object_run_reports.json").read_text())
    assert {record["photo"] for record in reports} == {2,5,6}
    for record in reports:
        trace = Path(record["trace_dir"])
        composite = np.asarray(Image.open(trace / "06_composite.png").convert("RGB"))
        final = np.asarray(Image.open(trace / "10_final.png").convert("RGB"))
        allowed = np.asarray(Image.open(trace / "harmonize_region_mask.png").convert("L")) > 0
        assert np.array_equal(composite[~allowed], final[~allowed])
        data = json.loads((trace / "harmonize.json").read_text())
        assert data["seconds"] < 120
        assert all(item["ssim"] >= data["ssim_threshold"] or item["fallback"] for item in data["items"])
        assert (root / "out" / f"composite_{record['photo']:02}.png").is_file()
        assert (root / "out" / f"harmonized_{record['photo']:02}.png").is_file()
