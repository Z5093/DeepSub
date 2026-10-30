"""生成打包发布所需的图标资源。

产物（默认 resource/icons/）：
    app.ico                 多尺寸应用图标（16/24/32/48/64/128/256），给 PyInstaller --icon
    app.png / app_<n>.png   各尺寸 PNG，给安装器、商店页、文档
    app_mark.svg            矢量标识（品牌位、官网页）
    line/<name>.svg         界面线性图标（矢量，可换色）
    line/png/<name>_24.png  线性图标位图（24 / 48 两档，透明底）

用法：
    python scripts/make_icons.py                # 生成全部资源
    python scripts/make_icons.py --preview DIR  # 只出图标方案对比图
"""

import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import (  # noqa: E402
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.common.theme import UITheme as T  # noqa: E402

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]

GRAD_TOP = QColor("#2E8FF5")
GRAD_BOTTOM = QColor("#0053B8")


# ---------------------------------------------------------------- 形状
def squircle(rect: QRectF, n: float = 4.6, steps: int = 180) -> QPainterPath:
    """超椭圆（squircle）。比普通圆角矩形更接近 macOS / Windows 11 的图标轮廓。"""
    import math

    cx, cy = rect.center().x(), rect.center().y()
    a, b = rect.width() / 2.0, rect.height() / 2.0
    path = QPainterPath()
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = cx + a * (1 if ct >= 0 else -1) * abs(ct) ** (2.0 / n)
        y = cy + b * (1 if st >= 0 else -1) * abs(st) ** (2.0 / n)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _bar(p: QPainter, x: float, cy: float, w: float, h: float):
    p.drawRoundedRect(QRectF(x, cy - h / 2.0, w, h), w / 2.0, w / 2.0)


# ---------------------------------------------------------------- 主图标
def draw_app_icon(size: int, design: str = "caption") -> QImage:
    """在 size×size 上绘制应用图标。

    小尺寸（<48）自动简化：波形从 5 根降到 3 根，去掉字幕线，
    保证 16/24 px 下依然能看出「音频」这个语义。
    """
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)

    inset = size * 0.03
    body = QRectF(inset, inset, size - inset * 2, size - inset * 2)

    grad = QLinearGradient(body.topLeft(), body.bottomRight())
    grad.setColorAt(0.0, GRAD_TOP)
    grad.setColorAt(1.0, GRAD_BOTTOM)
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawPath(squircle(body))

    if size >= 64:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 46), max(size * 0.004, 1.0)))
        p.drawPath(squircle(body.adjusted(size * 0.012, size * 0.012, -size * 0.012, -size * 0.012)))

    u = size / 1024.0
    small = size < 48
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FFFFFF"))

    if small:
        # 小尺寸：3 根粗波形居中，语义仍然清楚
        bar_w, gap, max_h = 118 * u, 90 * u, 560 * u
        heights = [0.44, 0.74, 0.50]
        total_w = bar_w * len(heights) + gap * (len(heights) - 1)
        x0 = (size - total_w) / 2.0
        for i, ratio in enumerate(heights):
            h = max_h * ratio
            y = size * 0.5 + max_h / 2.0 - h
            p.drawRoundedRect(QRectF(x0 + i * (bar_w + gap), y, bar_w, h), bar_w / 2.0, bar_w / 2.0)
    elif design == "baseline":
        # 波形底边对齐，下方一条字幕基线：像时间轴上的音轨 + 字幕轨
        bar_w, gap, max_h = 78 * u, 58 * u, 560 * u
        heights = [0.44, 0.72, 1.0, 0.74, 0.46]
        total_w = bar_w * 5 + gap * 4
        gap_line, line_h = 64 * u, 70 * u
        group_h = max_h + gap_line + line_h
        top = (size - group_h) / 2.0
        baseline = top + max_h
        x0 = (size - total_w) / 2.0
        for i, ratio in enumerate(heights):
            h = max_h * ratio
            p.drawRoundedRect(
                QRectF(x0 + i * (bar_w + gap), baseline - h, bar_w, h),
                bar_w / 2.0, bar_w / 2.0,
            )
        p.drawRoundedRect(QRectF(x0, baseline + gap_line, total_w, line_h), line_h / 2, line_h / 2)
    else:
        bar_w, gap = 78 * u, 58 * u
        heights = [280, 480, 640, 480, 280]
        total_w = bar_w * 5 + gap * 4
        x0 = (size - total_w) / 2.0
        for i, h in enumerate(heights):
            _bar(p, x0 + i * (bar_w + gap), size * 0.5, bar_w, h * u)

    p.end()
    return img


# ---------------------------------------------------------------- 导出
def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return data


def write_ico(path: str, images: dict):
    """手写 ICO：每个尺寸塞一张 PNG（Vista+ 支持），保证每档都是原生渲染。"""
    blobs = [(s, png_bytes(img)) for s, img in sorted(images.items())]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for size, blob in blobs:
        w = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(b for _, b in blobs))


def export_app_icon(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    images = {s: draw_app_icon(s) for s in sorted(set(ICO_SIZES + PNG_SIZES))}
    for s, img in images.items():
        img.save(os.path.join(out_dir, f"app_{s}.png"), "PNG")
    images[1024].save(os.path.join(out_dir, "app.png"), "PNG")
    write_ico(os.path.join(out_dir, "app.ico"), {s: images[s] for s in ICO_SIZES})
    return len(images)


def tidy_svg(path: str, size: int):
    """QSvgGenerator 会写出 mm 尺寸、空 <defs> 与空 <g>；这里统一成 24×24 的干净文件。"""
    import xml.etree.ElementTree as ET

    ET.register_namespace("", "http://www.w3.org/2000/svg")
    tree = ET.parse(path)
    root = tree.getroot()
    root.set("width", str(size))
    root.set("height", str(size))

    def strip(el):
        for child in list(el):
            strip(child)
            tag = child.tag.split("}")[-1]
            if tag in ("g", "defs") and len(child) == 0:
                el.remove(child)

    strip(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def export_line_icons(out_dir: str):
    from PyQt5.QtSvg import QSvgGenerator

    from app.components.icons import _ICONS, paint_icon

    svg_dir = os.path.join(out_dir, "line")
    png_dir = os.path.join(svg_dir, "png")
    os.makedirs(png_dir, exist_ok=True)

    for name in sorted(_ICONS):
        # 矢量：24×24 viewBox，描边随 currentColor 不可用，故导出深色版
        path = os.path.join(svg_dir, f"{name}.svg")
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(QRectF(0, 0, 24, 24).size().toSize())
        gen.setViewBox(QRectF(0, 0, 24, 24))
        gen.setTitle(f"DeepSub icon · {name}")
        gen.setDescription("DeepSub UI line icon")
        painter = QPainter(gen)
        paint_icon(painter, name, QRectF(0, 0, 24, 24), T.TEXT_PRIMARY, 1.6)
        painter.end()
        tidy_svg(path, 24)

        # 位图：24 / 48 两档
        for px in (24, 48):
            img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing, True)
            paint_icon(painter, name, QRectF(0, 0, px, px), T.TEXT_PRIMARY, 1.6)
            painter.end()
            img.save(os.path.join(png_dir, f"{name}_{px}.png"), "PNG")

    return len(_ICONS)


def export_mark(out_dir: str):
    """矢量标识：只有波形 + 字幕线，透明底，适合放网页或用作品牌位。"""
    from PyQt5.QtSvg import QSvgGenerator

    path = os.path.join(out_dir, "app_mark.svg")
    gen = QSvgGenerator()
    gen.setFileName(path)
    gen.setSize(QRectF(0, 0, 512, 512).size().toSize())
    gen.setViewBox(QRectF(0, 0, 512, 512))
    gen.setTitle("DeepSub mark")
    painter = QPainter(gen)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(T.TINT))
    u = 512 / 1024.0
    bar_w, gap = 58 * u * 1.6, 46 * u * 1.6
    heights = [146, 251, 348, 267, 170]
    total_w = bar_w * 5 + gap * 4
    x0 = (512 - total_w) / 2.0
    for i, h in enumerate(heights):
        _bar(painter, x0 + i * (bar_w + gap), 512 * 0.40, bar_w, h * u * 1.6)
    line_h = 60 * u * 1.6
    painter.drawRoundedRect(QRectF(x0, 512 * 0.62, total_w, line_h), line_h / 2, line_h / 2)
    painter.drawRoundedRect(
        QRectF(x0, 512 * 0.62 + line_h + 30 * u * 1.6, total_w * 0.66, line_h),
        line_h / 2, line_h / 2,
    )
    painter.end()
    tidy_svg(path, 512)


def build_preview(out_dir: str):
    """三种方案在 256 / 64 / 32 / 16 下的对比图。"""
    os.makedirs(out_dir, exist_ok=True)
    variants = ["baseline", "pure"]
    sizes = [256, 128, 64, 48, 32, 16]
    cell = 236
    img = QImage(cell * len(sizes), cell * len(variants), QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#F5F5F7"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    for row, variant in enumerate(variants):
        for col, size in enumerate(sizes):
            icon = draw_app_icon(size, variant)
            cx = col * cell + (cell - size) / 2.0
            cy = row * cell + (cell - size) / 2.0 - 10
            p.drawImage(QPointF(cx, cy), icon)
        p.setPen(QColor("#6E6E73"))
        p.drawText(QRectF(8, row * cell + cell - 26, cell - 16, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, variant)
    p.end()
    path = os.path.join(out_dir, "icon_variants.png")
    img.save(path, "PNG")
    return path


def main():
    app = QApplication(sys.argv)  # noqa: F841  QPainter 需要
    if "--preview" in sys.argv:
        target = sys.argv[sys.argv.index("--preview") + 1] if len(sys.argv) > sys.argv.index("--preview") + 1 \
            else os.path.join(ROOT, "downloads", "ui_preview")
        print(build_preview(target))
        return

    out_dir = os.path.join(ROOT, "resource", "icons")
    n_png = export_app_icon(out_dir)
    export_mark(out_dir)
    n_line = export_line_icons(out_dir)

    print(f"app icon: {n_png} 张 PNG + app.ico ({'/'.join(map(str, ICO_SIZES))})")
    print(f"line icons: {n_line} 个 SVG + 2 档 PNG")
    print(f"输出目录: {out_dir}")


if __name__ == "__main__":
    main()
