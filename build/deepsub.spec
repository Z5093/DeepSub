# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 绿色版）。

用法：python scripts/build_exe.py
产物：dist/DeepSub/DeepSub.exe（资源与内置工具在 exe 同级 resource/ 下，可直接替换）

刻意不打包的东西：
- resource/bin 下的 ffmpeg / ffprobe / yt-dlp / 7z：体积近 500MB，由构建脚本按需拷贝，
  不进 PYZ，方便用户自己换新版本
- models/ 下的大模型（faster-whisper，3.4GB）与 tools/ 下的识别程序：运行时按需下载
- 注意：models/silero-vad 这个 2.3MB 的 VAD 小模型必须随包发（构建脚本会拷到
  exe 同级 models/silero-vad），否则只能联网下载，GitHub 不通时会退化成定长分块
- torch / transformers / faster-whisper 等 Python 包：本程序只调用外部 exe，用不到
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON_PATH = os.path.join(ROOT, "resource", "icons", "app.ico")
VERSION_FILE = os.path.join(SPECPATH, "_version_info.txt")

EXCLUDES = [
    # 翻译 / ASR 全部走外部进程与 HTTP，不需要这些重量级包
    "torch", "torchaudio", "torchvision",
    "faster_whisper", "ctranslate2", "transformers", "tokenizers",
    "pandas", "matplotlib", "scipy", "sklearn", "sympy",
    "IPython", "notebook", "jupyter", "pytest",
    # pytest 时代留下的辅助库，运行时不引用；它的 hook 同样会开隔离子进程
    "py",
    # 纯语法高亮库，运行时用不到；它的 hook 会开隔离子进程全量扫描 lexers，
    # 在受限环境里会被拒绝，直接排除更省事
    "pygments",
    # ModelScope SDK 只是「模型下载」的可选加速路径，
    # model_download_thread 里 ImportError 会自动回退到直连 HTTP 下载。
    # 不排除的话 PyInstaller 会把整个 modelscope 依赖树（pydantic/uvicorn/zmq…）
    # 扫进来，构建慢十几分钟、产物还大几十 MB
    "modelscope",
    "PyQt5.QtWebEngineWidgets", "PyQt5.QtQuick", "PyQt5.QtQml",
    "PyQt5.QtMultimediaWidgets", "PyQt5.Qt3DCore",
    "tkinter", "turtle",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # pysbd 按语言分模块，语言类在运行时按语言码取用，显式声明避免漏收
        "pysbd.lang",
        "onnxruntime.capi._pybind_state",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepSub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON_PATH if os.path.isfile(ICON_PATH) else None,
    version=VERSION_FILE if os.path.isfile(VERSION_FILE) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DeepSub",
)
