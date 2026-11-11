import json
import os
from pathlib import Path
import subprocess
import sys


def test_photo_02_and_05_flat_rug_and_support_lamp():
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    env = os.environ.copy()
    env.update({
        "IMAGE_PROVIDER": "huggingface",
        "HF_HOME": r"D:\huggingface_cache",
        "HF_HUB_OFFLINE": "1",
        "U2NET_HOME": r"D:\huggingface_cache\rembg",
        "TRACE_PIPELINE": "1",
        "RENDER_OUTPUT_DIR": "../out",
    })
    subprocess.run(
        [sys.executable, "out/run_surface_object_followup.py"],
        cwd=backend, env=env, check=True,
    )
    reports = json.loads((root / "out" / "object_run_reports.json").read_text())
    assert {entry["photo"] for entry in reports} == {2, 5}
    for entry in reports:
        trace = Path(entry["trace_dir"])
        placements = entry["placements"]["items"]
        rug = next(item for item in placements if item["catalog_item_id"] == "RUG-JAP-006")
        assert rug["visible"] is True
        areas = json.loads((trace / "placement_footprints.json").read_text())
        assert areas["RUG-JAP-006"]["area"] >= 2000
        lamp = next(item for item in placements if item["catalog_item_id"] == "LIGHT-JAP-007")
        if lamp["visible"]:
            table = next(item for item in placements if item["catalog_item_id"] == "FURN-JAP-004")
            assert lamp["y"] < table["y"]
        else:
            assert lamp["visibility_reason"] == "needs a table to sit on"
        nonflat = [(item_id, data["bbox"]) for item_id, data in areas.items()
                   if not data["flat"] and item_id != "LIGHT-JAP-007"]
        for index, (first_id, first) in enumerate(nonflat):
            for second_id, second in nonflat[index + 1:]:
                intersection = max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(0, min(first[3], second[3]) - max(first[1], second[1]))
                first_area = (first[2]-first[0]) * (first[3]-first[1])
                second_area = (second[2]-second[0]) * (second[3]-second[1])
                assert intersection / max(1, min(first_area, second_area)) <= 0.20, (first_id, second_id)
        assert (root / "out" / f"fix1_photo{entry['photo']:02}.png").is_file()
