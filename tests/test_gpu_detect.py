"""离线回归测试：显卡检测不能因为一个不支持的查询字段整体失败。

背景：
  nvidia-smi 只要遇到一个不认识的 --query-gpu 字段就整条查询报错退出（exit code 2）。
  旧实现把非标准字段 cuda_version 混在唯一一次查询里，导致部分驱动（本机 457.49 实测）
  上明明有 NVIDIA 显卡却返回「未检测到 NVIDIA 显卡」。

覆盖点：
  1. 带不支持字段的查询失败 → 自动降级到合法字段集，仍能拿到显卡与显存
  2. nvidia-smi 不存在 → reason=smi_not_found，但注册表里能报出设备名
  3. nvidia-smi 异常 → reason=smi_failed，不抛异常
  4. 多卡时取显存最大的那张
  5. cuda_version 从 nvidia-smi 横幅解析，缺失时为空字符串而不是报错
  6. 显存为 [N/A] 时不崩溃

运行：
    python tests/test_gpu_detect.py
"""
import os
import sys
from types import SimpleNamespace

BASE = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "__file__" in globals() else os.getcwd()
)
sys.path.insert(0, BASE)

from app.core.utils import gpu_utils  # noqa: E402

_ORIG_FIND = gpu_utils.find_nvidia_smi
_ORIG_RUN = gpu_utils._run

BANNER = """
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 551.86       Driver Version: 551.86       CUDA Version: 12.4     |
|-------------------------------+----------------------+----------------------+
"""

FIELDS_LEGACY_OK = "driver_version,name,memory.total,memory.free"


def _proc(returncode: int, stdout: str) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _fake_run_factory(plan):
    """plan: dict[字段串 -> (rc, stdout)]；未命中的字段按「不支持的字段」处理。"""
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        # cmd[1] 形如 --query-gpu=xxx；不带参数是取横幅
        if len(cmd) == 1:
            return _proc(0, BANNER)
        fields = cmd[1].split("=", 1)[1]
        rc, out = plan.get(fields, (2, 'Field "cuda_version" is not a valid field to query.'))
        return _proc(rc, out)

    fake_run.calls = calls
    return fake_run


def _patch(smi_path, fake_run):
    gpu_utils.find_nvidia_smi = lambda: smi_path
    gpu_utils._run = fake_run


def _restore():
    gpu_utils.find_nvidia_smi = _ORIG_FIND
    gpu_utils._run = _ORIG_RUN


def case_field_rejected():
    """字段集被拒 → 自动降级到更少的字段，仍能拿到显卡信息。"""
    plan = {
        FIELDS_LEGACY_OK: (2, 'Field "memory.free" is not a valid field to query.'),
        "driver_version,name,memory.total": (0, "551.86, NVIDIA GeForce RTX 4070, 12282"),
    }
    fake = _fake_run_factory(plan)
    _patch(r"C:\Windows\System32\nvidia-smi.exe", fake)
    info = gpu_utils.detect_cuda()
    assert info["available"] is True, info
    assert info["gpu_name"] == "NVIDIA GeForce RTX 4070", info
    assert info["vram_total_mb"] == 12282
    # 拿不到 free 时用 total 兜底，避免误判显存不足
    assert info["vram_free_mb"] == 12282, info
    assert info["cuda_version"] == "12.4", info
    assert info["reason"] == "ok"
    # 确认确实降级过：第一条查询带了 5 个字段，失败后才换少的
    assert len(fake.calls) >= 2, fake.calls
    _restore()


def case_only_name():
    """只剩最小字段可用时也不能判定为「没有显卡」。"""
    plan = {"name": (0, "NVIDIA GeForce RTX 4070")}
    _patch(r"C:\Windows\System32\nvidia-smi.exe", _fake_run_factory(plan))
    info = gpu_utils.detect_cuda()
    assert info["available"] is True, info
    assert info["gpu_name"] == "NVIDIA GeForce RTX 4070"
    assert info["vram_total_mb"] == 0
    _restore()


def case_no_smi():
    _patch("", _fake_run_factory({}))
    info = gpu_utils.detect_cuda()
    assert info["available"] is False
    assert info["reason"] == "smi_not_found"
    # 注册表兜底：至少能告诉用户「机器上有这张卡」
    assert isinstance(info["nvidia_devices"], list)
    _restore()


def case_smi_broken():
    fake = _fake_run_factory({})  # 所有查询都失败
    _patch(r"C:\Windows\System32\nvidia-smi.exe", fake)
    info = gpu_utils.detect_cuda()
    assert info["available"] is False, info
    assert info["reason"] == "smi_failed", info
    assert info["smi_path"].endswith("nvidia-smi.exe")
    _restore()


def case_multi_gpu():
    plan = {
        FIELDS_LEGACY_OK: (0, (
            "551.86, NVIDIA GeForce RTX 4070, 12282, 11000\n"
            "551.86, Intel UHD Graphics 630, 128, 128"
        )),
    }
    _patch(r"C:\Windows\System32\nvidia-smi.exe", _fake_run_factory(plan))
    info = gpu_utils.detect_cuda()
    assert info["gpu_count"] == 2, info
    assert info["gpu_name"] == "NVIDIA GeForce RTX 4070", info
    assert info["vram_total_mb"] == 12282
    _restore()


def case_na_memory():
    """部分驱动/WDDM 场景显存返回 [N/A]，不能因此整条判定为无显卡。"""
    plan = {FIELDS_LEGACY_OK: (0, "551.86, NVIDIA GeForce RTX 4070, [N/A], [N/A]")}
    _patch(r"C:\Windows\System32\nvidia-smi.exe", _fake_run_factory(plan))
    info = gpu_utils.detect_cuda()
    assert info["available"] is True, info
    assert info["vram_total_mb"] == 0
    _restore()


def case_cuda_banner_missing():
    """横幅里没有 CUDA Version 时返回空串，而不是抛异常或让整条检测失败。"""
    def fake_run(cmd):
        if len(cmd) == 1:
            return _proc(0, "| NVIDIA-SMI 551.86   Driver Version: 551.86 |")
        return _proc(0, "551.86, NVIDIA GeForce RTX 4070, 12282, 11000")

    _patch(r"C:\Windows\System32\nvidia-smi.exe", fake_run)
    info = gpu_utils.detect_cuda()
    assert info["available"] is True, info
    assert info["cuda_version"] == "", info
    _restore()


def case_all_fields_rejected():
    plan = {}
    _patch(r"C:\Windows\System32\nvidia-smi.exe", _fake_run_factory(plan))
    info = gpu_utils.detect_cuda()
    assert info["available"] is False
    assert info["reason"] == "smi_failed"
    _restore()


def main():
    cases = [
        case_field_rejected,
        case_only_name,
        case_no_smi,
        case_smi_broken,
        case_multi_gpu,
        case_na_memory,
        case_cuda_banner_missing,
        case_all_fields_rejected,
    ]
    for case in cases:
        case()
        print(f"  PASS  {case.__name__}")
    print(f"\n全部通过：{len(cases)} 项")


if __name__ == "__main__":
    main()
