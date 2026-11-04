"""Validate data/furniture/products.json without rebuilding it."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.models import Product
from app.dataset.validate import summarize, validate_products


def main() -> int:
    path = get_settings().data_dir / "furniture" / "products.json"
    products = [Product.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    report = validate_products(products)
    print(json.dumps(summarize(products), indent=2))
    print(f"errors={len(report.errors)} warnings={len(report.warnings)}")
    for line in report.errors[:50]:
        print(line, file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
