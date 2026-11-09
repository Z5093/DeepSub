"""离线回归测试：128 维 mel 模型（large-v3）的 preprocessor_config.json。

背景：
  large-v3 / large-v3-turbo 需要 128 维 mel 特征，faster-whisper 按模型目录里的
  `preprocessor_config.json` 的 `feature_size` 决定维度，缺这个文件就按 80 维算，
  进程直接报：

      ValueError: Invalid input features shape: expected (1, 128, 3000),
      but got (1, 80, 3000)

  早期下载清单漏了该文件，装出来的 large-v3 目录只有 config/model/tokenizer/
  vocabulary，一识别就失败，而界面错误提示还错误地归因为「CPU 可执行文件不支持」。

覆盖点：
  1. 模型名 → 是否需要 128 维的判断
  2. 缺失时自动补齐（内容与官方 340 字节文件一致）
  3. 已有正确文件时不改动
  4. 错误维度（80）的文件会被修正为 128
  5. 80 维模型（large-v2/small）不会被写入多余文件
  6. 损坏的 JSON 视为缺失并重建
  7. `_get_model_n_mels` 优先读 preprocessor_config，config.json 的 n_mels 只兜底
  8. 写盘失败时抛 ASRSetupError（配置级错误，不逐块重试）
  9. 下载清单包含该文件，且允许镜像 404 跳过

运行：
    python tests/test_model_features.py
"""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest.mock as mock

BASE = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "__file__" in globals() else os.getcwd()
)
sys.path.insert(0, BASE)

# app.core.__init__ 会连带 import 整条流水线（pysbd 等第三方依赖），
# 缺依赖时塞空壳，避免测试因无关依赖跑不起来。
try:
    import pysbd  # noqa: F401
except ImportError:
    _fake = types.ModuleType("pysbd")
    _fake.Segmenter = lambda *a, **k: types.SimpleNamespace(segment=lambda s: [s])
    sys.modules["pysbd"] = _fake

from app.core.asr.faster_whisper import ASRSetupError, FasterWhisperASR  # noqa: E402
from app.core.asr.whisper_model_files import (  # noqa: E402
    PREPROCESSOR_FILENAME,
    ensure_preprocessor_config,
    needs_128_mels,
    read_feature_size,
)

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def make_asr(exe_path):
    """绕过 __init__，避免触发显卡检测与真实程序探测。"""
    asr = FasterWhisperASR.__new__(FasterWhisperASR)
    asr._gpu_mode = True
    asr._exe_path = exe_path
    asr._program_name = os.path.basename(exe_path)
    asr._last_model_link_error = ""
    asr._model_move_note = ""
    asr._process = None
    return asr


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_needs_128_mels():
    cases = {
        "large-v3": True,
        "large-v3-turbo": True,
        "distil-large-v3": True,
        "large-v3_fp16": True,
        "large-v2": False,
        "large-v1": False,
        "medium": False,
        "small": False,
        "tiny": False,
        "": False,
    }
    for name, expected in cases.items():
        check(f"维度判定: {name or '(空)'} -> 128={expected}",
              needs_128_mels(name) == expected, f"实际={needs_128_mels(name)}")


def test_ensure_creates_missing():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        model = os.path.join(root, "large-v3")
        os.makedirs(model)
        write(os.path.join(model, "config.json"), json.dumps({"n_mels": 80}))

        ok, note = ensure_preprocessor_config(model, "large-v3")
        cfg_path = os.path.join(model, PREPROCESSOR_FILENAME)
        check("补齐: 返回成功", ok is True, note)
        check("补齐: 文件已生成", os.path.isfile(cfg_path), cfg_path)
        check("补齐: feature_size=128", read_feature_size(model) == 128,
              read_feature_size(model))
        check("补齐: 有提示文字", bool(note) and "补齐" in note, note)

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        check("补齐: 字段与官方文件一致",
              data.get("n_fft") == 400 and data.get("hop_length") == 160
              and data.get("sampling_rate") == 16000
              and data.get("nb_max_frames") == 3000,
              data)
        check("补齐: 文件大小接近官方 340 字节",
              200 < os.path.getsize(cfg_path) < 600, os.path.getsize(cfg_path))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ensure_keeps_valid_file():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        model = os.path.join(root, "large-v3")
        os.makedirs(model)
        cfg_path = os.path.join(model, PREPROCESSOR_FILENAME)
        # 用户自己放的文件（带额外字段）不应被覆盖
        original = json.dumps({"feature_size": 128, "custom": "keep-me"})
        write(cfg_path, original)

        ok, note = ensure_preprocessor_config(model, "large-v3")
        check("幂等: 返回成功", ok is True, note)
        check("幂等: 没有提示（无需改动）", note == "", note)
        with open(cfg_path, "r", encoding="utf-8") as f:
            check("幂等: 原文件未被覆盖", f.read() == original)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ensure_fixes_wrong_dimension():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        model = os.path.join(root, "large-v3")
        os.makedirs(model)
        write(os.path.join(model, PREPROCESSOR_FILENAME),
              json.dumps({"feature_size": 80}))

        ok, note = ensure_preprocessor_config(model, "large-v3")
        check("修正: 返回成功", ok is True, note)
        check("修正: 维度已改为 128", read_feature_size(model) == 128,
              read_feature_size(model))
        check("修正: 提示说明原因", "不符" in note, note)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ensure_ignores_80dim_models():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        for name in ("large-v2", "small", "medium"):
            model = os.path.join(root, name)
            os.makedirs(model)
            ok, note = ensure_preprocessor_config(model, name)
            check(f"80 维模型: {name} 不写文件",
                  ok is True and note == ""
                  and not os.path.isfile(os.path.join(model, PREPROCESSOR_FILENAME)),
                  note)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ensure_rebuilds_broken_json():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        model = os.path.join(root, "large-v3")
        os.makedirs(model)
        # 下载源 404 时被写进去的 HTML 错误页
        write(os.path.join(model, PREPROCESSOR_FILENAME), "<html>404 Not Found</html>")
        check("损坏: 读取返回 None", read_feature_size(model) is None)

        ok, _note = ensure_preprocessor_config(model, "large-v3")
        check("损坏: 已重建为 128", ok and read_feature_size(model) == 128)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_get_model_n_mels_prefers_preprocessor():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")
        asr = make_asr(exe_path)

        models = os.path.join(root, "models")
        model = os.path.join(models, "large-v3")
        os.makedirs(model)
        # 镜像里的 config.json 把 large-v3 也写成 80，不能当依据
        write(os.path.join(model, "config.json"), json.dumps({"n_mels": 80}))

        check("优先级: 只有 config.json 时兜底 80",
              asr._get_model_n_mels("large-v3", models) == 80,
              asr._get_model_n_mels("large-v3", models))

        write(os.path.join(model, PREPROCESSOR_FILENAME),
              json.dumps({"feature_size": 128}))
        check("优先级: preprocessor 的 128 胜出",
              asr._get_model_n_mels("large-v3", models) == 128,
              asr._get_model_n_mels("large-v3", models))

        check("优先级: 没有模型目录时返回 None",
              asr._get_model_n_mels("tiny", os.path.join(root, "nope")) is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ensure_model_features_warns_and_repairs():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")
        asr = make_asr(exe_path)

        models = os.path.join(root, "models")
        model = os.path.join(models, "large-v3")
        os.makedirs(model)

        logs = []
        asr._ensure_model_features(
            "large-v3", "large-v3", models,
            log_callback=lambda m, lv="info": logs.append((lv, m)),
        )
        check("预检: 自动补齐文件",
              os.path.isfile(os.path.join(model, PREPROCESSOR_FILENAME)))
        check("预检: 向用户说明原因",
              any(lv == "warning" and "preprocessor_config" in m for lv, m in logs),
              logs)

        # 再跑一次不应重复提示
        logs.clear()
        asr._ensure_model_features("large-v3", "large-v3", models,
                                   log_callback=lambda m, lv="info": logs.append((lv, m)))
        check("预检: 幂等（不重复提示）", logs == [], logs)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_write_failure_raises_setup_error():
    root = tempfile.mkdtemp(prefix="deepsub_mels_")
    try:
        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")
        asr = make_asr(exe_path)

        models = os.path.join(root, "models")
        model = os.path.join(models, "large-v3")
        os.makedirs(model)

        with mock.patch(
            "app.core.asr.whisper_model_files.os.replace",
            side_effect=OSError("拒绝访问"),
        ):
            try:
                asr._ensure_model_features("large-v3", "large-v3", models)
                check("写盘失败: 抛 ASRSetupError", False, "没有抛异常")
            except ASRSetupError as e:
                msg = str(e)
                check("写盘失败: 抛 ASRSetupError", True)
                check("写盘失败: 文案点明缺哪个文件",
                      "preprocessor_config.json" in msg, msg[:200])
                check("写盘失败: 给出可操作修复方式", "large-v2" in msg, msg[:200])
            except Exception as e:  # noqa: BLE001
                check("写盘失败: 抛 ASRSetupError", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_download_list_contains_preprocessor():
    try:
        from app.thread.model_download_thread import ModelDownloadThread
    except Exception as e:  # noqa: BLE001
        check("下载: 导入下载线程", False, str(e))
        return
    check("下载: 文件清单包含 preprocessor_config.json",
          PREPROCESSOR_FILENAME in ModelDownloadThread.MODEL_FILES,
          ModelDownloadThread.MODEL_FILES)
    check("下载: 该文件允许镜像 404 跳过",
          PREPROCESSOR_FILENAME in ModelDownloadThread.OPTIONAL_FILES,
          ModelDownloadThread.OPTIONAL_FILES)
    check("下载: 声明了最小体积",
          ModelDownloadThread.MIN_FILE_SIZES.get(PREPROCESSOR_FILENAME, 0) > 0,
          ModelDownloadThread.MIN_FILE_SIZES)


def main():
    print("128 维 mel 模型配置回归测试")
    for fn in (
        test_needs_128_mels,
        test_ensure_creates_missing,
        test_ensure_keeps_valid_file,
        test_ensure_fixes_wrong_dimension,
        test_ensure_ignores_80dim_models,
        test_ensure_rebuilds_broken_json,
        test_get_model_n_mels_prefers_preprocessor,
        test_ensure_model_features_warns_and_repairs,
        test_write_failure_raises_setup_error,
        test_download_list_contains_preprocessor,
    ):
        print(f"\n- {fn.__name__}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAILED.append(fn.__name__)
            print(f"  [FAIL] 异常: {type(e).__name__}: {e}")

    print(f"\n通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("失败项: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
