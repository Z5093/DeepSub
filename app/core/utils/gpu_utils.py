from __future__ import annotations

import re
import subprocess


def detect_cuda() -> dict[str, object]:
    default: dict[str, object] = {
        "available": False,
        "driver_version": "",
        "cuda_version": "",
        "gpu_name": "",
        "vram_total_mb": 0,
        "vram_free_mb": 0,
    }
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version,cuda_version,name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return default
        line = result.stdout.strip().split("\n")[0].strip()
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            return default
        return {
            "available": True,
            "driver_version": parts[0],
            "cuda_version": parts[1],
            "gpu_name": parts[2],
            "vram_total_mb": int(float(parts[3])),
            "vram_free_mb": int(float(parts[4])),
        }
    except (FileNotFoundError, ValueError, IndexError):
        return default


def get_recommended_mode(vram_mb: int, model_size_mb: int) -> str:
    if vram_mb > model_size_mb * 1.5:
        return "gpu"
    return "cpu"


def get_gpu_vram() -> int:
    info = detect_cuda()
    if info["available"]:
        return int(info["vram_free_mb"])
    return 0
