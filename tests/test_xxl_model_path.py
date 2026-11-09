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
  4. 模型缺失时抛 ASRSetupError（不再回退成必然失败的绝对路径）
  5. 配置级错误不逐块重试：run_chunks 立即中止
  6. 诊断表能把 `Unknown model not found` 归因为模型路径问题

运行：
    python tests/test_xxl_model_path.py
"""
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

# 本项目测试只关心 faster_whisper 的命令拼装，但 app.core.__init__ 会连带
# import 整条流水线（pysbd 等第三方依赖）。缺依赖时塞一个空壳，避免测试
# 因为无关依赖缺失而跑不起来。
try:
    import pysbd  # noqa: F401
except ImportError:
    _fake = types.ModuleType("pysbd")
    _fake.Segmenter = lambda *a, **k: types.SimpleNamespace(segment=lambda s: [s])
    sys.modules["pysbd"] = _fake

from app.core.asr.chunker import AudioChunk  # noqa: E402
from app.core.asr.faster_whisper import (  # noqa: E402
    ASRSetupError,
    FasterWhisperASR,
    diagnose_asr_failure,
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


def build(exe_dir, models_dir, model="small", audio="audio.wav"):
    exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
    with open(exe_path, "wb") as f:
        f.write(b"MZ")
    asr = make_asr(exe_path)
    # 重要：隔离真实项目目录。否则「默认目录找不到模型」时会去
    # E:\DeepSub\models 或 tools 下找到真实模型并搬走，测试变成破坏性操作。
    with mock.patch(
        "app.core.asr.faster_whisper.resolve_whisper_model_dir",
        return_value=os.path.join(models_dir, model),
    ):
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

        try:
            build(exe_dir, models_dir)
            check("XXL: 模型缺失时抛 ASRSetupError", False, "没有抛异常")
        except ASRSetupError as e:
            msg = str(e)
            check("XXL: 模型缺失时抛 ASRSetupError", True)
            check("XXL: 报错含可复制的 mklink 命令", "mklink /J" in msg, msg[:200])
            check("XXL: 报错指出 XXL 只认模型名", "只接受模型名" in msg, msg[:200])
            check("XXL: 报错给出 CPU 兜底", "CPU" in msg, msg[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_setup_error_aborts_without_retry():
    """配置级错误必须在第 1 个语块就中止，而不是 44 块 × 10 次。"""
    root = tempfile.mkdtemp(prefix="deepsub_xxl_")
    try:
        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")

        asr = make_asr(exe_path)
        calls = []

        def fake_extract(audio, chunk, idx):
            return os.path.join(root, "chunk.wav")

        def fake_run(*args, **kwargs):
            calls.append(1)
            raise ASRSetupError("模型目录联接未就绪")

        asr._extract_chunk_audio = fake_extract
        asr.run = fake_run

        chunks = [AudioChunk(offset=float(i) * 30.0, duration=30.0) for i in range(5)]
        try:
            asr.run_chunks(chunks, os.path.join(root, "a.wav"), model="small")
            check("配置错误: run_chunks 抛出", False, "没有抛异常")
        except ASRSetupError:
            check("配置错误: run_chunks 抛出", True)
        check("配置错误: 只尝试 1 次（不重试）", len(calls) == 1, f"实际调用={len(calls)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_xxl_moves_model_when_junction_fails():
    """exFAT/U 盘/受控目录上 mklink 会失败，此时应移动模型而不是复制。"""
    import unittest.mock as mock

    root = tempfile.mkdtemp(prefix="deepsub_xxl_")
    try:
        models_dir = os.path.join(root, "models", "faster-whisper")
        src = os.path.join(models_dir, "small")
        os.makedirs(src)
        with open(os.path.join(src, "model.bin"), "wb") as f:
            f.write(b"\0" * 4096)
        with open(os.path.join(src, "config.json"), "w", encoding="utf-8") as f:
            f.write("{}")

        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")

        asr = make_asr(exe_path)
        logs = []
        # 模拟 mklink 不可用（exFAT / 无权限 / 杀软拦截）
        with mock.patch(
            "app.core.asr.faster_whisper.subprocess.run",
            side_effect=OSError("mklink 不可用"),
        ):
            cmd = asr._build_command(
                "audio.wav", model="small", model_dir=models_dir, output_dir=exe_dir,
                _log_callback=lambda m, lv="info": logs.append((lv, m)),
            )

        check("移动: 联接失败后仍传模型名", model_arg(cmd) == "small", " ".join(cmd))

        linked = os.path.join(exe_dir, "_models", "faster-whisper-small")
        check("移动: 目标目录已生成", os.path.isdir(linked), linked)
        check(
            "移动: 模型文件已就位",
            os.path.isfile(os.path.join(linked, "model.bin")),
            linked,
        )
        check("移动: 原目录已删除（不占双份空间）", not os.path.isdir(src), src)
        check("移动: 不留下 .copying 残留",
              not os.path.exists(linked + ".copying"), "存在残留目录")
        check(
            "移动: 已向用户提示新位置",
            any("移动" in m for _lv, m in logs),
            logs,
        )
        check(
            "移动: 提示只发一次",
            sum(1 for _lv, m in logs if "移动" in m) == 1,
            logs,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_xxl_move_across_volumes_deletes_source():
    """跨卷时 os.replace 会失败，必须退化为「复制 → 校验 → 删源」，仍然不占双份。"""
    import unittest.mock as mock

    root = tempfile.mkdtemp(prefix="deepsub_xxl_")
    try:
        models_dir = os.path.join(root, "models", "faster-whisper")
        src = os.path.join(models_dir, "small")
        os.makedirs(src)
        with open(os.path.join(src, "model.bin"), "wb") as f:
            f.write(b"\0" * 4096)

        exe_dir = os.path.join(root, "Faster-Whisper-XXL")
        os.makedirs(exe_dir)
        exe_path = os.path.join(exe_dir, "faster-whisper-xxl.exe")
        with open(exe_path, "wb") as f:
            f.write(b"MZ")

        asr = make_asr(exe_path)
        real_replace = os.replace
        state = {"n": 0}

        def fake_replace(a, b):
            state["n"] += 1
            if state["n"] == 1:
                # 首次是 源目录 -> 目标目录，模拟跨卷
                raise OSError(18, "Invalid cross-device link")
            return real_replace(a, b)

        with mock.patch(
            "app.core.asr.faster_whisper.subprocess.run",
            side_effect=OSError("mklink 不可用"),
        ), mock.patch("app.core.asr.faster_whisper.os.replace", side_effect=fake_replace):
            cmd = asr._build_command(
                "audio.wav", model="small", model_dir=models_dir, output_dir=exe_dir
            )

        linked = os.path.join(exe_dir, "_models", "faster-whisper-small")
        check("跨卷: 仍传模型名", model_arg(cmd) == "small", " ".join(cmd))
        check("跨卷: 目标目录已生成", os.path.isdir(linked), linked)
        check("跨卷: 文件已搬运",
              os.path.isfile(os.path.join(linked, "model.bin")), linked)
        check("跨卷: 校验通过后删除源目录", not os.path.isdir(src), src)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resolve_finds_model_under_exe_models():
    """模型被移动到 exe 的 _models 后，UI 与识别都应认为它「已安装」。"""
    import unittest.mock as mock

    import app.core.utils.file_utils as fu

    root = tempfile.mkdtemp(prefix="deepsub_resolve_")
    try:
        gpu_root = os.path.join(root, "tools", "faster-whisper-xxl")
        exe_sub = os.path.join(gpu_root, "Faster-Whisper-XXL")
        model_at_exe = os.path.join(exe_sub, "_models", "faster-whisper-small")
        os.makedirs(model_at_exe)
        with open(os.path.join(model_at_exe, "model.bin"), "wb") as f:
            f.write(b"\0" * 16)

        models_root = os.path.join(root, "models", "faster-whisper")
        os.makedirs(models_root)  # 默认目录为空，模拟「已被移走」

        with mock.patch.object(fu, "get_fw_gpu_dir", return_value=gpu_root), \
                mock.patch.object(
                    fu, "get_whisper_models_dir", return_value=models_root
                ):
            resolved = fu.resolve_whisper_model_dir("small")
            check(
                "解析: 默认目录为空时找到 exe 下的模型",
                os.path.normcase(resolved) == os.path.normcase(model_at_exe),
                resolved,
            )
            check("解析: 判定为已安装", fu.is_whisper_model_installed("small"))

        # 都没装时应回落到默认目录，供下载流程使用
        with mock.patch.object(fu, "get_fw_gpu_dir", return_value=gpu_root), \
                mock.patch.object(
                    fu, "get_whisper_models_dir", return_value=models_root
                ):
            check(
                "解析: 未安装时回落到默认目录",
                os.path.normcase(fu.resolve_whisper_model_dir("large-v3"))
                == os.path.normcase(os.path.join(models_root, "large-v3")),
                fu.resolve_whisper_model_dir("large-v3"),
            )
            check(
                "解析: 未安装时判定为未安装",
                not fu.is_whisper_model_installed("large-v3"),
            )
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
        test_xxl_moves_model_when_junction_fails,
        test_xxl_move_across_volumes_deletes_source,
        test_resolve_finds_model_under_exe_models,
        test_setup_error_aborts_without_retry,
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
