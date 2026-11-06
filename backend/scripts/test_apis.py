"""Opt-in live smoke test for the configured Gemini and Meshy API keys.

Run from ``backend`` with ``python -m scripts.test_apis``. Meshy image-to-3D
submissions may consume account credits and can take several minutes.
"""

import asyncio
import csv
import logging
import os
from pathlib import Path
import sys
from typing import Any

# Support both ``python -m scripts.test_apis`` from backend and direct execution.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import settings  # noqa: E402
from services.gemini_service import GeminiService  # noqa: E402
from services.meshy_service import MeshyService  # noqa: E402


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("API_Tester")
CATALOG_PATH = BACKEND_DIR / "data" / "furniture_catalog_with_images.csv"


def load_catalog() -> list[dict[str, Any]]:
    with CATALOG_PATH.open("r", encoding="utf-8-sig", newline="") as catalog_file:
        return list(csv.DictReader(catalog_file))


def configured_key(env_name: str, setting_name: str) -> str:
    return os.getenv(env_name) or getattr(settings, setting_name, "") or ""


async def test_gemini_api() -> bool:
    print("\n==========================================")
    print(f"🤖 1. TESTING GEMINI API ({GeminiService.MODEL})")
    print("==========================================")
    try:
        api_key = configured_key("GEMINI_API_KEY", "gemini_api_key")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set in backend/.env")
        catalog = load_catalog()
        service = GeminiService(api_key=api_key)
        print("Testing Stage 5 Budget Planning query (Cap: ₹50,000)...", flush=True)
        print("Waiting for Gemini (request timeout: 120 seconds)...", flush=True)
        result = await asyncio.to_thread(
            service.generate_budget_shopping_list,
            budget_cap_inr=50_000.0,
            theme="Japandi",
            catalog_items=catalog,
            project_id="test_proj_001",
        )
        print("✅ Gemini API Success!")
        print(f"   Selected Items: {len(result.selected_items)}")
        print(
            f"   Total Cost: ₹{result.total_cost_inr:,.2f} "
            f"/ Cap: ₹{result.budget_cap_inr:,.2f}"
        )
        return True
    except Exception as exc:
        logger.exception("Gemini API check failed")
        print(f"❌ Gemini API Failed: {exc}")
        return False


async def test_meshy_api() -> bool:
    print("\n==========================================")
    print("🎨 2. TESTING MESHY API (Image-to-3D)")
    print("==========================================")
    try:
        api_key = configured_key("MESHY_API_KEY", "meshy_api_key")
        if not api_key:
            raise ValueError("MESHY_API_KEY is not set in backend/.env")
        catalog = load_catalog()
        sample = next(
            (
                item
                for item in catalog
                if item.get("theme", "").casefold() == "japandi"
                and item.get("image_url", "").startswith(("https://", "http://"))
            ),
            None,
        )
        if sample is None:
            raise ValueError("No Japandi catalog row with a public image_url was found.")

        service = MeshyService(api_key=api_key)
        sample_image_url = sample["image_url"]
        print(f"Submitting {sample.get('product_id', 'catalog item')} to Meshy:\n   {sample_image_url}")
        if service.mock_meshy:
            print("MOCK_MESHY=true; no Meshy task will be submitted.")
        glb_url = await service.generate_catalog_3d_asset(
            product_id=sample.get("product_id", "sample-sofa"),
            image_url=sample_image_url,
            category=sample.get("category"),
        )
        if glb_url is None:
            print("Skipped 3D generation for this catalog category.")
            return True
        print("✅ Meshy API Success!")
        print(f"   Generated 3D GLB Asset URL: {glb_url}")
        return True
    except Exception as exc:
        logger.exception("Meshy API check failed")
        print(f"❌ Meshy API Failed: {exc}")
        return False


async def main() -> int:
    gemini_ok = await test_gemini_api()
    meshy_ok = await test_meshy_api()
    return 0 if gemini_ok and meshy_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
