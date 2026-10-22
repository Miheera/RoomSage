"""Validate the RoomSage GPU worker runtime before running GPU tasks."""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    details: str
    required: bool = True


def _check_cuda() -> CheckResult:
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return CheckResult("PyTorch / CUDA", "FAIL", f"Could not import torch: {exc}")

    cuda_version = torch.version.cuda or "not built with CUDA"
    try:
        available = bool(torch.cuda.is_available())
    except Exception as exc:
        return CheckResult(
            "PyTorch / CUDA",
            "FAIL",
            f"CUDA probe failed (torch CUDA {cuda_version}): {exc}",
        )
    if not available:
        return CheckResult(
            "PyTorch / CUDA",
            "FAIL",
            f"CUDA unavailable (torch CUDA {cuda_version}); no usable GPU detected",
        )

    try:
        device_index = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(device_index)
        free_bytes, total_bytes = torch.cuda.mem_get_info(device_index)
    except Exception as exc:
        return CheckResult(
            "PyTorch / CUDA",
            "FAIL",
            f"CUDA is available (torch CUDA {cuda_version}), but GPU details failed: {exc}",
        )

    free_gb = free_bytes / (1024**3)
    total_gb = total_bytes / (1024**3)
    return CheckResult(
        "PyTorch / CUDA",
        "PASS",
        (
            f"torch CUDA {cuda_version}; GPU {device_index}: {device_name}; "
            f"VRAM free {free_gb:.2f} GB / total {total_gb:.2f} GB"
        ),
    )


def _check_executable(command: str) -> CheckResult:
    executable = shutil.which(command)
    if executable is None:
        return CheckResult(command, "FAIL", "Executable not found in PATH")
    return CheckResult(command, "PASS", str(Path(executable).resolve()))


def _check_import(module_name: str, label: str | None = None) -> CheckResult:
    display_name = label or module_name
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return CheckResult(display_name, "FAIL", f"Import failed: {exc}")
    version = getattr(module, "__version__", None)
    details = f"imported ({version})" if version else "imported"
    return CheckResult(display_name, "PASS", details)


def _weight_directories() -> list[Path]:
    home = Path.home()
    cache_root = Path(os.getenv("XDG_CACHE_HOME", home / ".cache"))
    torch_home = Path(os.getenv("TORCH_HOME", cache_root / "torch"))
    directories = [
        Path("/app/checkpoints"),
        Path("/models"),
        Path.cwd() / "checkpoints",
        Path.cwd() / "models",
        home / "Downloads",
        cache_root / "sam",
        cache_root / "mobilesam",
        torch_home / "hub" / "checkpoints",
        Path("/root/.cache/torch/hub/checkpoints"),
    ]
    configured_directory = os.getenv("SAM_CHECKPOINT_DIR")
    if configured_directory:
        directories.insert(0, Path(configured_directory).expanduser())
    return list(dict.fromkeys(path.expanduser() for path in directories))


def _find_sam_weights() -> Path | None:
    configured_checkpoint = os.getenv("SAM_CHECKPOINT_PATH")
    if configured_checkpoint:
        checkpoint = Path(configured_checkpoint).expanduser()
        if checkpoint.is_file():
            return checkpoint.resolve()

    known_names = (
        "mobile_sam.pt",
        "mobile_sam.pth",
        "sam_vit_b_01ec64.pth",
        "sam_vit_l_0b3195.pth",
        "sam_vit_h_4b8939.pth",
    )
    for directory in _weight_directories():
        if not directory.is_dir():
            continue
        for name in known_names:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
        for pattern in ("mobile_sam*.pt", "mobile_sam*.pth", "sam*.pth"):
            matches = sorted(path for path in directory.glob(pattern) if path.is_file())
            if matches:
                return matches[0].resolve()
    return None


def _check_sam_weights(required: bool) -> CheckResult:
    checkpoint_path = os.getenv("SAM_CHECKPOINT_PATH")
    if checkpoint_path and not Path(checkpoint_path).expanduser().is_file():
        result_status = "FAIL" if required else "WARN"
        return CheckResult(
            "SAM weights",
            result_status,
            f"SAM_CHECKPOINT_PATH does not point to a file: {checkpoint_path}",
            required=required,
        )

    weights = _find_sam_weights()
    if weights is None:
        result_status = "FAIL" if required else "SKIP"
        details = (
            "No checkpoint found in SAM_CHECKPOINT_PATH, SAM_CHECKPOINT_DIR, "
            "or common cache/download paths"
        )
        if not required:
            details += "; SAM is optional while SAM_ENABLED is false"
        return CheckResult("SAM weights", result_status, details, required=required)
    return CheckResult("SAM weights", "PASS", str(weights), required=required)


def _check_sam_module(required: bool) -> CheckResult:
    try:
        module = importlib.import_module("segment_anything")
    except Exception as exc:
        result_status = "FAIL" if required else "SKIP"
        details = f"segment_anything import failed: {exc}"
        if not required:
            details += "; SAM is optional while SAM_ENABLED is false"
        return CheckResult("SAM Python module", result_status, details, required=required)
    version = getattr(module, "__version__", None)
    return CheckResult(
        "SAM Python module",
        "PASS",
        f"segment_anything imported ({version})" if version else "segment_anything imported",
        required=required,
    )


def _sam_enabled_from_environment() -> bool:
    return os.getenv("SAM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def collect_checks(require_sam: bool = False) -> list[CheckResult]:
    sam_required = require_sam or _sam_enabled_from_environment()
    checks = [_check_cuda()]
    checks.extend(
        _check_executable(command)
        for command in ("colmap", "ns-process-data", "ns-train", "ns-export")
    )
    checks.extend(
        (
            _check_import("gsplat"),
            _check_import("plyfile"),
            _check_import("PIL", "PIL"),
            _check_sam_module(sam_required),
            _check_sam_weights(sam_required),
        )
    )
    return checks


def _print_results(checks: list[CheckResult]) -> bool:
    name_width = max(len("CHECK"), *(len(check.name) for check in checks))
    status_width = len("STATUS")
    details_width = max(len("DETAILS"), 48)
    header = f"{'CHECK':<{name_width}}  {'STATUS':<{status_width}}  DETAILS"
    print(header)
    print(f"{'-' * name_width}  {'-' * status_width}  {'-' * details_width}")
    for check in checks:
        print(f"{check.name:<{name_width}}  {check.status:<{status_width}}  {check.details}")

    failed = any(check.status == "FAIL" and check.required for check in checks)
    print()
    print("RESULT: FAIL" if failed else "RESULT: PASS")
    if not failed and any(check.status == "SKIP" for check in checks):
        print("Optional SAM checks were skipped; set SAM_ENABLED=true or use --require-sam to require them.")
    return not failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-sam",
        action="store_true",
        help="Fail if the segment_anything module or a SAM/MobileSAM checkpoint is missing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return 0 if _print_results(collect_checks(args.require_sam)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
