"""
统一的字号与配色。

为什么单独一个模块：
  · 字号要能整体调（设置 → 显示 → 字号），散在各文件里的 QSS 就没法统一缩放
  · 配色和间距也得一致，否则每个窗口各写一套，很快就花了

用法：
    from ui import theme

    QSS_TEMPLATE = ''' QLabel { font-size: 13px; color: ... } '''

    class Foo(QWidget):
        def __init__(self):
            self.setStyleSheet(theme.qss(QSS_TEMPLATE))   # ← 在 __init__ 里算，
                                                          #   不能在模块级算：
                                                          #   那时 set_scale 还没跑

字号按**比例**缩放：模板里写的 px 是「标准档」下的值，实际渲染时整体乘系数。
"""
from __future__ import annotations

import re

from PyQt5.QtGui import QFont

# ────────────────────────── 字号 ──────────────────────────

# 设置里能选的档位：数据库里存的是百分比整数（setting 表只存 int）
FONT_PRESETS: list[tuple[int, str]] = [
    (88, "小"),
    (100, "标准"),
    (115, "大"),
    (130, "特大"),
]
FONT_DEFAULT = 100

_scale_percent = FONT_DEFAULT

_PX_RE = re.compile(r"(\d+(?:\.\d+)?)px")


def set_scale(percent: int) -> None:
    """设置全局字号档（百分比）。"""
    global _scale_percent
    try:
        p = int(percent)
    except (TypeError, ValueError):
        p = FONT_DEFAULT
    # 夹一下，防止数据库里存了奇怪的值把界面撑爆
    _scale_percent = max(70, min(160, p))


def scale_percent() -> int:
    return _scale_percent


def factor() -> float:
    return _scale_percent / 100.0


def px(value: float) -> int:
    """把「标准档」下的 px 值换算成当前字号下的 px。下限 8，免得缩没了。"""
    return max(8, round(value * factor()))


def qfont(family: str, size: float, bold: bool = False,
          max_px: float | None = None) -> QFont:
    """
    悬浮窗是手绘的，字体得自己建 —— 走这里才能跟着字号档一起缩放。

    max_px: 上限。悬浮窗固定 160px 宽，小字放到「特大」档会宽到顶出窗口，
        这种地方得压住（配合 elidedText 截断）。
    """
    px_size = size * factor()
    if max_px is not None:
        px_size = min(px_size, max_px)
    f = QFont(family)
    f.setPixelSize(max(8, round(px_size)))
    f.setBold(bold)
    return f


def qss(text: str) -> str:
    """
    把 QSS 里所有 `Npx` 按当前档位缩放。

    ★ 不只是 font-size —— padding / border-radius / min-height 也一起缩。
      只缩字号的话，字变大了而内边距没变，界面会显得挤；一起缩才协调。
      1px 的边框缩放后 round 回来还是 1px，不受影响。
    """
    f = factor()
    if f == 1.0:
        return text
    return _PX_RE.sub(lambda m: f"{max(1, round(float(m.group(1)) * f))}px", text)


def palette_css(template: str) -> str:
    """
    把模板里的颜色名（INK / MUTED / ACCENT…）换成真实色值。

    QSS 不支持变量，而字符串替换有两个坑：
      · 本模块里不止有颜色，还有 FONT_DEFAULT = 100 这种 int —— 只替换 str
      · 必须用**词边界**匹配并长名优先：否则 ACCENT 会先命中 ACCENT_SOFT
        的前半截，把它改成 "#2563eb_SOFT"。
        （`_` 算单词字符，所以 \\bACCENT\\b 不会匹配 ACCENT_SOFT，正合需要。）
    """
    names = sorted(
        (n for n, v in globals().items() if n.isupper() and isinstance(v, str)),
        key=len,
        reverse=True,
    )
    for name in names:
        template = re.sub(rf"\b{name}\b", globals()[name], template)
    return template


def styled(template: str) -> str:
    """调色板 + 字号，一步到位。各界面统一用它。"""
    return qss(palette_css(template))


# ────────────────────────── 配色 ──────────────────────────
# 收敛成一套灰阶 + 一个主色。少即是多：颜色多了就显廉价。

# 文字层级
INK = "#111827"          # 主文字（标题、数字）
INK_SOFT = "#374151"     # 正文
MUTED = "#6b7280"        # 次要信息（时间、说明）
FAINT = "#9ca3af"        # 辅助（占位、提示）
GHOST = "#c7cbd1"        # 更淡（版本号）

# 背景与线
BG = "#ffffff"
BG_SOFT = "#f9fafb"      # 悬停 / 浅底
BG_INSET = "#f3f4f6"     # 内嵌块（正文底）
LINE = "#eef0f3"         # 极淡分隔线
LINE_SOFT = "#e5e7eb"    # 常规边框

# 主色（只有一个）
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#eff6ff"

# 语义色（克制使用）
WARN = "#b45309"
WARN_SOFT = "#fef3c7"
OK = "#15803d"
BAD = "#b91c1c"
TAG = "#6d28d9"          # 子类别标记
TAG_SOFT = "#f5f3ff"
