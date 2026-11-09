"""离线回归测试：XXL 版识别程序必须收到「模型名」而不是「绝对路径」。

背景：
  faster-whisper-xxl 收到 `-m` 后，如果该值不是已知模型名，会把它当目录名拼成
  `<exe目录>/_models/faster-whisper-<值>`。旧实现直接把模型的绝对路径传给 `-m`，
  于是程序去找 `_models/faster-whisper-E:\\...\\models\\faster-whisper\\large-v3`，
  报 `Unknown model not found at: ...`，退出码 0、无字幕产出（用户只看到
  「未产出结果（退出码 0）」）。

覆盖点：
  1. XXL 模式下 `-m` 是模型名，命令里不出现模型绝对路径
  2. 会把 `<models>/<模型名>` 目录联接（junction）到 `<exe目录>/_models/faster-whisper-<模型名>`
  3. junction 已存在时不重复创建
  4. 模型目录不存在时不抛异常，回退为传模型名
  5. 诊断表能把 `Unknown model not found` 归因为模型路径问题

运行：
    python tests/test_xxl_model_path.py
"""
import os
import shutil
import sys
import tempfile
import types

BASE = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "__file__" in globals() else os.getcwd()
)
sys.path.insert(0, BASE)

# 本项目测试只关心 faster_whisper 的命令拼装，但 app.core.__init__ 会连带
# import 整条流水线（pysbd 等第三方依赖）。缺依赖时塞一个空壳，避免测试
# 因为无关依赖缺失而跑不起来。
try:
    import pysbd  # noqa: F401
except ImportError:
    _fake = types.ModuleType("pysbd")
    _fake.Segmenter = lambda *a, **k: types.SimpleNamespace(segment=lambda s: [s])
    sys.modules["pysbd"] = _fake

from app.core.asr.faster_whisper import FasterWhisperASR, diagnose_asr_failure  # noqa: E402

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
    asr._process = None
    return asr


def build(exe_dir, models_dir, model="small", audio="audio.wav"):
    exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
    with open(exe_path, "wb") as f:
        f.write(b"MZ")
    asr = make_asr(exe_path)
    cmd = asr._build_command(
        audio, model=model, model_dir=models_dir, output_dir=exe_dir, language="en"
    )
    return asr, cmd


def model_arg(cmd):
    return cmd[cmd.index("-m") + 1]


def test_xxl_uses_model_name():
    root = tempfile.mkdtemp(prefix="deepsub_xxl_")
    try:
        models_dir = os.path.join(root, "models", "faster-whisper")
        os.makedirs(os.path.join(models_dir, "small"))
        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)

        asr, cmd = build(exe_dir, models_dir)

        arg = model_arg(cmd)
        check("XXL: -m 传模型名", arg == "small", f"实际={arg!r}")
        check("XXL: 命令不含模型绝对路径", models_dir not in " ".join(cmd),
              " ".join(cmd))
        check("XXL: 不再追加 --model_dir", "--model_dir" not in cmd,
              " ".join(cmd))

        linked = os.path.join(exe_dir, "_models", "faster-whisper-small")
        check("XXL: 已建立 _models/faster-whisper-small", os.path.isdir(linked), linked)

        # 第二次调用不应重建（旧目录仍在，且不会抛文件已存在）
        asr2, cmd2 = build(exe_dir, models_dir)
        check("XXL: 重复调用幂等", model_arg(cmd2) == "small" and os.path.isdir(linked))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_xxl_missing_model_dir():
    root = tempfile.mkdtemp(prefix="deepsub_xxl_")
    try:
        models_dir = os.path.join(root, "models", "faster-whisper")
        os.makedirs(models_dir)  # 目录存在但里面没有 small
        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)

        asr, cmd = build(exe_dir, models_dir)
        check("XXL: 模型缺失时不崩、仍传模型名", model_arg(cmd) == "small",
              " ".join(cmd))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_non_xxl_keeps_name():
    root = tempfile.mkdtemp(prefix="deepsub_xxl_")
    try:
        models_dir = os.path.join(root, "models", "faster-whisper")
        os.makedirs(os.path.join(models_dir, "small"))
        exe_dir = os.path.join(root, "faster-whisper")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")

        asr = make_asr(exe_path)
        cmd = asr._build_command(
            "audio.wav", model="small", model_dir=models_dir, output_dir=exe_dir
        )
        check("CPU 版: -m 仍是模型名", model_arg(cmd) == "small", " ".join(cmd))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_diagnosis():
    out = (
        "Unknown model not found at: "
        "C:\\App\\_models\\faster-whisper-E:\\DeepSub\\models\\faster-whisper\\large-v3\n"
        "Try these known models:"
    )
    hint = diagnose_asr_failure(out)
    check("诊断: 能识别 Unknown model not found", hint is not None)
    if hint:
        check("诊断: 归因为模型路径问题", "模型" in hint[0], hint[0])


def main():
    print("XXL 模型参数回归测试")
    for fn in (
        test_xxl_uses_model_name,
        test_xxl_missing_model_dir,
        test_non_xxl_keeps_name,
        test_diagnosis,
    ):
        print(f"\n- {fn.__name__}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAILED.append(fn.__name__)
            print(f"  [FAIL] 异常: {e}")

    print(f"\n通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("失败项: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
