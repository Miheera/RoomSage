"""Download and load every configured Hugging Face render model."""
from __future__ import annotations

import gc
import os
from pathlib import Path

from config import settings


def _unique_size(path: Path) -> int:
    seen: set[str] = set()
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            resolved = str(file.resolve())
            if resolved not in seen:
                seen.add(resolved)
                total += file.stat().st_size
    return total


def main() -> None:
    from huggingface_hub import HfApi, snapshot_download
    from transformers import AutoImageProcessor, UperNetForSemanticSegmentation

    os.environ.setdefault("U2NET_HOME", str(Path(settings.hf_home) / "rembg"))

    api = HfApi(token=settings.hf_token or None)
    models = settings.image_model_ids
    paths: dict[str, str] = {}
    for name, model_id in models.items():
        info = None
        try:
            path = Path(snapshot_download(
                model_id, token=settings.hf_token or None, local_files_only=True,
                cache_dir=settings.hf_home,
            ))
        except Exception:
            try:
                info = api.model_info(model_id, timeout=10)
                path = Path(snapshot_download(
                    model_id, token=settings.hf_token or None,
                    cache_dir=settings.hf_home,
                ))
            except Exception as hub_error:
                raise RuntimeError(
                    f"Model {model_id} is not available in the shared Hugging Face cache "
                    f"and the Hub request failed: {hub_error}"
                ) from hub_error
        paths[name] = str(path)
        license_name = getattr(info.card_data, "license", None) if info else None
        if not license_name:
            readme = path / "README.md"
            if readme.exists():
                content = readme.read_text(encoding="utf-8", errors="replace")
                if content.startswith("---"):
                    frontmatter = content.split("---", 2)[1]
                    for line in frontmatter.splitlines():
                        if line.strip().startswith("license:"):
                            license_name = line.split(":", 1)[1].strip().strip("'\"")
                            break
        license_name = license_name or "unknown (cached model card has no license field)"
        print(f"{name}: {model_id} | size={_unique_size(path)} bytes | license={license_name}", flush=True)

    token = settings.hf_token or None
    p = paths["segmentation"]
    AutoImageProcessor.from_pretrained(p, token=token, local_files_only=True)
    model = UperNetForSemanticSegmentation.from_pretrained(
        p, token=token, local_files_only=True
    ).eval()
    expected = {"wall", "floor", "ceiling", "windowpane", "door", "curtain"}
    labels = {str(label).casefold() for label in model.config.id2label.values()}
    missing = sorted(expected - labels)
    if missing:
        raise RuntimeError(f"ADE20K model is missing room labels: {', '.join(missing)}")
    del model; gc.collect()
    if settings.image_provider == "huggingface":
        from rembg import new_session

        session = new_session("u2net")
        rembg_weights = Path(os.environ["U2NET_HOME"]) / "models" / "u2net" / "u2net.onnx"
        if not rembg_weights.is_file():
            raise RuntimeError(f"rembg did not create expected U-2-Net weights at {rembg_weights}")
        print(
            "background_removal: rembg/u2net | "
            f"size={rembg_weights.stat().st_size} bytes | "
            "license=Apache-2.0 (https://github.com/xuebinqin/U-2-Net/blob/master/LICENSE)",
            flush=True,
        )
        del session; gc.collect()
    print(f"Verified loads for {len(models)} configured model entries.", flush=True)


if __name__ == "__main__":
    main()
