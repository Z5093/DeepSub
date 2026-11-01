from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache

# CREATE_NO_WINDOW：打包成 GUI 程序后调用控制台程序不会闪黑框
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TIMEOUT = 8

# 注意：字段要按「从多到少」排列，逐个降级重试。
# nvidia-smi 只要遇到一个不认识的字段就整个查询失败（exit code 2），
# 所以不能把 cuda_version 这类非标准字段放在唯一的一次查询里。
_QUERY_FIELD_SETS = (
    "driver_version,name,memory.total,memory.free",
    "driver_version,name,memory.total",
    "name",
)

_GPU_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _empty(reason: str = "") -> dict[str, object]:
    return {
        "available": False,
        "driver_version": "",
        "cuda_version": "",
        "gpu_name": "",
        "vram_total_mb": 0,
        "vram_free_mb": 0,
        "gpu_count": 0,
        "smi_path": "",
        "nvidia_devices": [],
        "reason": reason,
    }


@lru_cache(maxsize=1)
def find_nvidia_smi() -> str:
    """定位 nvidia-smi：优先 PATH，其次常见安装目录。"""
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform != "win32":
        return ""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        os.path.join(system_root, "System32", "nvidia-smi.exe"),
        os.path.join(system_root, "SysWOW64", "nvidia-smi.exe"),
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVIDIA App\nvidia-smi.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _to_int(text: str) -> int:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _query_gpu(exe: str) -> tuple[list[str], list[list[str]]]:
    """按降级顺序尝试查询，返回 (实际生效的字段列表, 每行单元格)。

    nvidia-smi 遇到任意一个不认识的字段就整条查询失败，所以字段集要逐个降级重试。
    """
    for fields in _QUERY_FIELD_SETS:
        result = _run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
        if result is None or result.returncode != 0 or not result.stdout.strip():
            continue
        rows: list[list[str]] = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                rows.append([cell.strip() for cell in line.split(",")])
        if rows:
            return [f.strip() for f in fields.split(",")], rows
    return [], []


def _cuda_version_from_banner(exe: str) -> str:
    """cuda_version 不是 --query-gpu 的标准字段，从 nvidia-smi 头部横幅里取。"""
    result = _run([exe])
    if result is None or not result.stdout:
        return ""
    match = re.search(r"CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)", result.stdout)
    return match.group(1) if match else ""


def _nvidia_devices_from_registry() -> list[str]:
    """只看注册表也能知道「机器上确实插着 NVIDIA 卡」，用于区分「没显卡」和「驱动/工具不可用」。"""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _GPU_KEY) as root:
            index = 0
            while True:
                try:
                    sub_key = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if not sub_key.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub_key) as device:
                        desc = str(winreg.QueryValueEx(device, "DriverDesc")[0])
                except OSError:
                    continue
                if "nvidia" in desc.lower():
                    names.append(desc)
    except OSError:
        return []
    return names


def detect_cuda() -> dict[str, object]:
    """检测 NVIDIA 显卡 / CUDA 可用性。

    返回字段：
      available        是否可走 GPU（找到 nvidia-smi 且拿到有效显存信息）
      driver_version   驱动版本
      cuda_version     驱动支持的最高 CUDA 版本（可能为空，仅用于展示）
      gpu_name         显卡名称
      vram_total_mb    显存总量
      vram_free_mb     当前可用显存
      gpu_count        显卡数量（多卡时取显存最大的那张）
      smi_path         nvidia-smi 路径
      nvidia_devices   注册表里发现的 NVIDIA 设备（诊断用）
      reason           失败原因：ok / smi_not_found / smi_failed / no_gpu
    """
    devices = _nvidia_devices_from_registry()
    exe = find_nvidia_smi()

    if not exe:
        info = _empty("smi_not_found")
        info["nvidia_devices"] = devices
        return info

    fields, rows = _query_gpu(exe)
    if not rows:
        info = _empty("smi_failed")
        info["smi_path"] = exe
        info["nvidia_devices"] = devices
        # 查询失败时仍尝试拿显卡名，便于界面给出准确提示
        if devices:
            info["gpu_name"] = devices[0]
        return info

    def cell(row: list[str], field: str) -> str:
        try:
            return row[fields.index(field)]
        except (ValueError, IndexError):
            return ""

    # 多卡（笔记本核显 + 独显场景）：取显存最大的那张
    best: dict[str, object] | None = None
    for row in rows:
        total = _to_int(cell(row, "memory.total"))
        free = _to_int(cell(row, "memory.free"))
        candidate = {
            "gpu_name": cell(row, "name"),
            "vram_total_mb": total,
            # 拿不到空闲显存时用总量兜底，避免被误判为「显存不足」
            "vram_free_mb": free or total,
            "driver_version": cell(row, "driver_version"),
        }
        if best is None or int(candidate["vram_total_mb"]) > int(best["vram_total_mb"]):
            best = candidate

    if best is None or not str(best["gpu_name"]).strip():
        info = _empty("no_gpu")
        info["smi_path"] = exe
        info["nvidia_devices"] = devices
        return info

    info = _empty("ok")
    info.update(
        {
            "available": True,
            "driver_version": best["driver_version"],
            "cuda_version": _cuda_version_from_banner(exe),
            "gpu_name": best["gpu_name"],
            "vram_total_mb": best["vram_total_mb"],
            "vram_free_mb": best["vram_free_mb"],
            "gpu_count": len(rows),
            "smi_path": exe,
            "nvidia_devices": devices,
        }
    )
    return info


def get_recommended_mode(vram_mb: int, model_size_mb: int) -> str:
    if vram_mb > model_size_mb * 1.5:
        return "gpu"
    return "cpu"


def get_gpu_vram() -> int:
    info = detect_cuda()
    if info["available"]:
        return int(info["vram_free_mb"])
    return 0
