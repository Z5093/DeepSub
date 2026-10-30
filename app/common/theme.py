"""DeepSub 设计令牌（Design Tokens）。

设计语言：Studio Light —— 以 Apple HIG 的层次、留白与克制为骨架，
用「时间码 / 播出控制台」的行业语汇做唯一一个记忆点（详见 pipeline.py 的轨道流程）。

硬性约束（来自人机界面规范与高星 UI skill 的规则集）：
1. 8pt 网格：所有间距/内边距取自 4/8/12/16/20/24/32。
2. 正文对比度 >= 4.5:1（TEXT_PRIMARY 16.1:1，TEXT_SECONDARY 4.9:1）。
3. 单一强调色（TINT），红色仅用于「进行中 / 错误」这类语义状态。
4. 层次靠留白、发丝线与字重，而不是阴影和渐变。
5. 等宽字体只用于时间码与数据，不作为正文。
"""


class UITheme:
    # ---- 表面 -------------------------------------------------------------
    CANVAS = "#F5F5F7"          # 窗口底色
    SURFACE = "#FFFFFF"         # 卡片
    SURFACE_MUTED = "#FAFAFC"   # 卡片内的次级区块
    SURFACE_SUNKEN = "#F2F2F5"  # 凹陷区（进度槽、表头）
    SIDEBAR = "#ECECF1"         # 侧边栏（毛玻璃质感靠色阶近似）

    HAIRLINE = "#E3E3E8"        # 1px 分隔线
    HAIRLINE_SOFT = "#EDEDF1"
    HAIRLINE_STRONG = "#D2D2D9"

    # ---- 文字 -------------------------------------------------------------
    TEXT_PRIMARY = "#1D1D1F"
    TEXT_SECONDARY = "#6E6E73"
    TEXT_TERTIARY = "#86868B"
    TEXT_ON_TINT = "#FFFFFF"

    # ---- 主色（唯一强调色）------------------------------------------------
    TINT = "#0066CC"
    TINT_HOVER = "#0057B8"
    TINT_PRESSED = "#004C9E"
    TINT_SOFT = "#EAF2FC"
    TINT_BORDER = "#B9D4F2"

    # ---- 语义状态 ---------------------------------------------------------
    LIVE = "#D70015"            # 进行中（播出红灯）
    LIVE_SOFT = "#FDECEE"
    SUCCESS = "#1B7F3B"
    SUCCESS_SOFT = "#E9F5EC"
    WARNING = "#A85B00"
    WARNING_SOFT = "#FCF1E4"

    # ---- 控件 -------------------------------------------------------------
    FIELD_BG = "#FFFFFF"
    FIELD_BG_DISABLED = "#F4F4F6"
    FIELD_BORDER = "#C9C9D0"
    FIELD_BORDER_HOVER = "#A9A9B2"
    BTN_BG = "#FFFFFF"
    BTN_BORDER = "#CFCFD6"
    BTN_BG_HOVER = "#F7F7F9"
    BTN_BG_PRESSED = "#EDEDF1"
    TRACK = "#E6E6EB"           # 滑轨/进度槽
    KNOB = "#FFFFFF"

    SELECTION = "#DCE9F9"

    # ---- 字体 -------------------------------------------------------------
    FONT_UI = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
    FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Microsoft YaHei UI", sans-serif'
    FONT_MONO = '"Cascadia Mono", "Consolas", "SF Mono", monospace'

    SIZE_DISPLAY = 26
    SIZE_TITLE = 20
    SIZE_HEADLINE = 15
    SIZE_BODY = 13
    SIZE_SUB = 12
    SIZE_CAPTION = 11

    # ---- 圆角 / 间距 ------------------------------------------------------
    R_XS = 4
    R_SM = 6
    R_MD = 8
    R_LG = 10
    R_XL = 14

    S1 = 4
    S2 = 8
    S3 = 12
    S4 = 16
    S5 = 20
    S6 = 24
    S7 = 32
    S8 = 40

    @staticmethod
    def ui(size: int = SIZE_BODY, weight: int = 400, color: str = None) -> str:
        """生成一段可直接嵌入 QSS 的字体声明。"""
        color = color or UITheme.TEXT_PRIMARY
        return (
            f'font-family: {UITheme.FONT_UI};'
            f" font-size: {size}px;"
            f" font-weight: {weight};"
            f" color: {color};"
        )


T = UITheme
