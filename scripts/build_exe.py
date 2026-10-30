"""一键构建 Windows 绿色版 exe。

    python scripts/build_exe.py              # 构建 dist/DeepSub/DeepSub.exe（含内置工具）
    python scripts/build_exe.py --no-bin     # 不拷贝 ffmpeg/yt-dlp 等大文件，产物只有程序本体
    python scripts/build_exe.py --zip        # 构建完再打一个 zip 便于发布

依赖：pip install pyinstaller
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "build", "deepsub.spec")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build", "work")
APP_DIR = os.path.join(DIST, "DeepSub")

#: 打进 exe 同级 resource/ 的资源子目录（体积小、需要用户能看到/可替换）
RESOURCE_PARTS = ("icons", "fonts", "styles")
#: 体积大、可选拷贝的内置命令行工具
BIN_DIR = os.path.join(ROOT, "resource", "bin")


def read_version() -> str:
    path = os.path.join(ROOT, "app", "common", "constants.py")
    with open(path, "r", encoding="utf-8") as f:
        match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read())
    return match.group(1) if match else "0.0.0"


def write_version_info(version: str) -> str:
    """生成 exe 的 Windows 版本信息（右键属性里能看到）。"""
    nums = [int(x) if x.isdigit() else 0 for x in re.split(r"[.+-]", version)[:4]]
    nums += [0] * (4 - len(nums))
    v_tuple = tuple(nums)
    v_str = ".".join(str(n) for n in v_tuple)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v_tuple},
    prodvers={v_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [StringStruct('CompanyName', 'DeepSub'),
         StringStruct('FileDescription', 'DeepSub 视频字幕处理工具'),
         StringStruct('FileVersion', '{v_str}'),
         StringStruct('InternalName', 'DeepSub'),
         StringStruct('OriginalFilename', 'DeepSub.exe'),
         StringStruct('ProductName', 'DeepSub'),
         StringStruct('ProductVersion', '{v_str}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    path = os.path.join(ROOT, "build", "_version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


SKIP_SUFFIXES = (".bak", ".old", ".tmp", ".log")


def copy_tree(src: str, dst: str) -> None:
    """拷贝目录，跳过备份/临时文件（比如 yt-dlp.exe.2025.01.26.bak）。"""
    if not os.path.isdir(src):
        return
    for base, _dirs, files in os.walk(src):
        rel = os.path.relpath(base, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for name in files:
            if name.lower().endswith(SKIP_SUFFIXES):
                continue
            shutil.copy2(os.path.join(base, name), os.path.join(target, name))


def assemble(with_bin: bool) -> None:
    """把运行时需要、但不该塞进 exe 的东西放到 exe 同级目录。"""
    resource_dst = os.path.join(APP_DIR, "resource")
    for part in RESOURCE_PARTS:
        copy_tree(os.path.join(ROOT, "resource", part), os.path.join(resource_dst, part))

    if with_bin:
        copy_tree(BIN_DIR, os.path.join(resource_dst, "bin"))

    os.makedirs(os.path.join(APP_DIR, "config"), exist_ok=True)
    os.makedirs(os.path.join(APP_DIR, "downloads"), exist_ok=True)

    readme = """DeepSub 绿色版使用说明
========================

1. 双击 DeepSub.exe 启动；首次启动会打开新手引导。
2. resource\\bin 下需要这些工具（发布包里通常已包含）：
     ffmpeg.exe / ffprobe.exe / yt-dlp.exe / 7z.exe
   缺失时「视频下载」和「音频提取」不可用，可直接把新版 exe 覆盖进去。
3. 「语音识别」页首次使用需要下载 faster-whisper 程序和 Whisper 模型，
   默认放在 tools\\ 与 models\\ 下，均在本机运行，不上传任何文件。
4. 翻译同样在本机运行（llama.cpp + Index-Translate），也可改成任意 OpenAI 兼容接口。
5. 配置文件在 config\\settings.json，删掉它即可重新进入新手引导。
6. 卸载：直接删除整个文件夹。
"""
    with open(os.path.join(APP_DIR, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(readme)


def make_zip(version: str) -> str:
    zip_path = os.path.join(DIST, f"DeepSub-v{version}-win64.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for base, _dirs, files in os.walk(APP_DIR):
            for name in files:
                full = os.path.join(base, name)
                zf.write(full, os.path.relpath(full, DIST))
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 DeepSub exe")
    parser.add_argument("--no-bin", action="store_true", help="不拷贝 resource/bin 下的大体积工具")
    parser.add_argument("--zip", action="store_true", help="构建完成后打包 zip")
    parser.add_argument("--clean", action="store_true", help="先清空 build/work 缓存，强制全量重新分析")
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，请先执行：pip install pyinstaller", file=sys.stderr)
        return 1

    version = read_version()
    print(f"[1/4] 生成版本信息 v{version}")
    write_version_info(version)

    print("[2/4] 清理旧产物")
    shutil.rmtree(APP_DIR, ignore_errors=True)
    if args.clean:
        shutil.rmtree(WORK, ignore_errors=True)

    print("[3/4] PyInstaller 构建中（首次约 1-3 分钟）")
    cmd = [
        sys.executable, "-m", "PyInstaller", SPEC,
        "--noconfirm", "--distpath", DIST, "--workpath", WORK,
    ]
    code = subprocess.call(cmd, cwd=ROOT)
    if code != 0:
        print("构建失败，请检查上面的日志", file=sys.stderr)
        return code

    print("[4/4] 组装运行时文件")
    assemble(with_bin=not args.no_bin)

    exe_path = os.path.join(APP_DIR, "DeepSub.exe")
    size_mb = os.path.getsize(exe_path) / 1024 / 1024
    print(f"\n完成：{exe_path}  ({size_mb:.1f} MB)")
    if args.no_bin:
        print("提示：本次未拷贝 resource/bin，运行前请自行放入 ffmpeg / yt-dlp 等工具")

    if args.zip:
        zip_path = make_zip(version)
        print(f"发布包：{zip_path}  ({os.path.getsize(zip_path) / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
