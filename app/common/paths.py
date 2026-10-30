"""统一的路径解析。

源码运行时所有路径相对项目根；用 PyInstaller 打成 exe 后源码目录不复存在，
需要区分三种根：

- ``get_app_root()``    可写数据根（config / downloads / models / tools / cache）。
  冻结后是 exe 所在目录，保证绿色版把数据放在自己旁边，不会写进临时解包目录。
- ``get_resource_root()`` 只读资源根（icons / fonts / styles）。
  冻结后优先找 exe 同级的 ``resource/``，找不到再回退 PyInstaller 的解包目录。
- ``get_bin_dir()``     内置命令行工具目录（ffmpeg / ffprobe / yt-dlp / 7z）。
  与 resource 同源，独立成函数是为了让用户可以直接把新版本 exe 丢进去替换。
"""

from __future__ import annotations

import os
import sys

#: 源码根目录（app 包的上一级），打包后不代表真实磁盘位置
_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_app_root() -> str:
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _SOURCE_ROOT


def get_bundle_root() -> str:
    """PyInstaller 一次性解包目录；源码运行时等同项目根。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", get_app_root())
    return _SOURCE_ROOT


def get_resource_root() -> str:
    if is_frozen():
        external = os.path.join(get_app_root(), "resource")
        if os.path.isdir(external):
            return external
        return os.path.join(get_bundle_root(), "resource")
    return os.path.join(_SOURCE_ROOT, "resource")


def get_resource_path(*parts: str) -> str:
    return os.path.join(get_resource_root(), *parts)


def get_bin_dir() -> str:
    return os.path.join(get_resource_root(), "bin")


def get_bin_path(name: str) -> str:
    return os.path.join(get_bin_dir(), name)


def get_config_dir() -> str:
    return os.path.join(get_app_root(), "config")


def get_data_path(*parts: str) -> str:
    return os.path.join(get_app_root(), *parts)
