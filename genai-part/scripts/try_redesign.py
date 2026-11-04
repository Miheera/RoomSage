"""Run the real 2D pipeline once on a photo and report memory use.

    python scripts/try_redesign.py photo.jpg --theme japandi --scope furniture
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.models import Theme
from app.dataset.catalog import Catalog
from app.llm import rules
from app.llm.schemas import DesignRequest
from app.redesign.base import EditScope, RenderRequest
from app.redesign.diffusion import DiffusionRenderer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--theme", default="modern", choices=[t.value for t in Theme])
    parser.add_argument("--scope", default="furniture", choices=[s.value for s in EditScope])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    theme = Theme(args.theme)
    spec = rules.build_spec(DesignRequest(theme=theme, budget_inr=50000),
                            Catalog.load(settings).theme(theme))
    output = args.output or settings.outputs_dir / "redesigns" / f"try_{args.theme}_{args.scope}.png"
    renderer = DiffusionRenderer(settings)

    print("Detected:", [o.label for o in renderer.analyze(args.image)])
    started = time.perf_counter()
    result = renderer.render(RenderRequest(
        image_path=args.image, output_path=output, prompt=spec.redesign_prompt,
        negative_prompt=spec.negative_prompt, scope=EditScope(args.scope),
    ))
    print(f"Done in {time.perf_counter() - started:.0f}s: {result.model_dump_json(indent=2)}")


if __name__ == "__main__":
    main()
