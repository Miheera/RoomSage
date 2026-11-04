"""Rebuild the catalog. Downloads each source once, into data/raw/."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.dataset.collector.pipeline import build, write
from app.dataset.validate import summarize


def main() -> int:
    data_dir = get_settings().data_dir
    result = build(data_dir)
    print(json.dumps(summarize(result.products), indent=2))
    print(f"collected={result.collected} duplicates_removed={result.duplicates_removed} "
          f"untagged_removed={result.untagged_removed} warnings={len(result.report.warnings)}")
    if not result.report.ok:
        print("\n".join(result.report.errors[:20]), file=sys.stderr)
        return 1
    write(result, data_dir)
    print(f"Wrote {data_dir / 'furniture' / 'products.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
