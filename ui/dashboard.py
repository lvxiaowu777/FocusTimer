"""
看板 —— 左边类别（可增删改、置顶），右边该类别下每次专注的**段落卡片**。

右侧从「条目清单」改成了「段落卡片」：
    以前每次专注被 AI 切碎成一条条短词，挂在类别下平铺展示，叙述本身丢了；
    现在每次专注是一段完整的话，关键词作为附属信息挂在这一段下面。

每张卡片显示：开始 → 结束时间、专注时长、其中休息时长、整理后的段落、关键词。
没整理过的（AI 没配 / 调用失败 / 早于 polished_text 字段的历史记录）回退显示原文原文，
并在角上打一个「未整理」标记。
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db.database import SessionLocal
from services import llm as llm_svc
from services import timer as timer_svc
from ui import theme

# 树控件里存自定义数据的角色
ROLE_ID = Qt.UserRole              # 类别 id
ROLE_IS_GROUP = Qt.UserRole + 1    # 是不是「分组」（有子类别，不能计时）

QSS_TEMPLATE = """
QWidget#root { background: #ffffff; }

/* —— 顶部 —— */
QLabel#h1 { color: INK; font-size: 16px; font-weight: 600; }
QLabel#h2 { color: MUTED; font-size: 11.5px; }
QLabel#today {
    color: ACCENT; font-size: 11.5px; font-weight: 600;
    background: ACCENT_SOFT; border-radius: 10px; padding: 5px 12px;
}
QLabel#total { color: INK; font-size: 21px; font-weight: 600; }
QLabel#unit  { color: FAINT; font-size: 12px; }

/* —— 左：类别树 —— */
QTreeWidget {
    border: none; background: #ffffff;
    font-size: 13px; color: INK_SOFT; outline: none; padding: 0;
}
QTreeWidget::item {
    padding: 8px 6px; border-radius: 7px; min-height: 16px;
    border: none;
}
QTreeWidget::item:selected { background: ACCENT_SOFT; color: ACCENT; }
QTreeWidget::item:hover { background: BG_SOFT; }
QTreeWidget::branch { background: transparent; }

/* —— 按钮 —— */
QPushButton {
    border: 1px solid LINE_SOFT; border-radius: 7px;
    padding: 6px 13px; font-size: 12px; color: INK_SOFT; background: #ffffff;
}
QPushButton:hover { background: BG_SOFT; border-color: GHOST; }
QPushButton:disabled { color: GHOST; background: #ffffff; border-color: LINE; }
QPushButton#primary {
    background: ACCENT; color: #ffffff; border: 1px solid ACCENT; font-weight: 600;
}
QPushButton#primary:hover { background: ACCENT_HOVER; border-color: ACCENT_HOVER; }
QPushButton#primary:disabled { background: #ffffff; color: GHOST; border-color: LINE; }
QPushButton#link {
    border: none; background: transparent; color: ACCENT;
    font-size: 11.5px; padding: 0; text-align: left;
}
QPushButton#link:hover { color: ACCENT_HOVER; }

/* —— 右：段落卡片 —— */
QScrollArea#scroll { border: none; background: #ffffff; }
QWidget#cardsHost { background: #ffffff; }
QFrame#card {
    background: #ffffff;
    border: 1px solid LINE;
    border-radius: 10px;
}
QFrame#card:hover { border-color: LINE_SOFT; }
QLabel#cardWhen { color: MUTED; font-size: 11.5px; }
QLabel#cardCat  {
    color: TAG; background: TAG_SOFT;
    border-radius: 9px; padding: 2px 8px; font-size: 10.5px; font-weight: 600;
}
QLabel#cardStat { color: MUTED; font-size: 11.5px; }
QLabel#cardTag  {
    color: WARN; background: WARN_SOFT;
    border-radius: 9px; padding: 2px 8px; font-size: 10.5px;
}
QLabel#cardBody { color: INK_SOFT; font-size: 13px; line-height: 165%; }
QLabel#cardKw   { color: MUTED; font-size: 11.5px; }
QTextBrowser#viewer {
    border: 1px solid LINE; border-radius: 10px;
    background: BG_SOFT; padding: 10px; font-size: 13.5px; color: INK_SOFT;
}
"""


def _palette_css(css: str) -> str:
    """把模板里的颜色名换成真值（QSS 不支持变量，只能自己替换）。"""
    return theme.palette_css(css)


def build_qss() -> str:
    """当前字号档下的完整样式表。字号档变了要重新调一次。"""
    return theme.styled(QSS_TEMPLATE)

MENU_TEMPLATE = (
    "QMenu{background:#fff;border:1px solid LINE_SOFT;border-radius:9px;padding:5px;}"
    "QMenu::item{padding:7px 20px;border-radius:6px;font-size:12.5px;color:INK_SOFT;}"
    "QMenu::item:selected{background:ACCENT_SOFT;color:ACCENT;}"
    "QMenu::item:disabled{color:GHOST;}"
    "QMenu::separator{height:1px;background:LINE;margin:5px 8px;}"
)


def menu_qss() -> str:
    return theme.qss(_palette_css(MENU_TEMPLATE))

# 判断段落是不是 Markdown（行首 1~4 个 # + 空格）—— 决定用哪种方式渲染
_MD_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+\S", re.M)


def fmt_hours(seconds: int) -> str:
    """秒 → 「3.5 h」/「45 min」这样的人话。"""
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds / 3600:.1f} h"


def hour_parts(seconds: int) -> tuple[str, str]:
    """(数值, 单位) —— 大数字和单位分开排版才好看（见左下角那个合计）。"""
    if seconds < 3600:
        return str(seconds // 60), "min"
    return f"{seconds / 3600:.1f}", "h"


def fmt_duration(seconds: int) -> str:
    """秒 → 「2h43m」/「45m」/「30s」。卡片上的时长用这个，比「2.7 h」看得准。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def fmt_clock(dt) -> str:
    """datetime → 「10-10 10:46」。"""
    return dt.strftime("%m-%d %H:%M") if dt else "—"


# ─────────────────────────────────────────────────────────────
# 关于「排序」：曾经做过拖动排序，翻车三次（详见 开发文档 踩坑记录）：
#   1. 挂 model().rowsMoved —— 那个信号 QListWidget 内部拖动根本不发
#   2. 在 dropEvent 里 refresh() 重建列表 —— 破坏 Qt 拖放内部状态，条目消失
#   3. 自己接管 dropEvent 落位 —— 又引出新的状态问题
# 结论：排序改成**置顶**（右键钉住），比拖动可靠得多，而且真正常用的
#       也就那几个类别。拖动相关代码已全部移除。
# ─────────────────────────────────────────────────────────────


class ParagraphViewer(QDialog):
    """
    长段落的独立查看窗口。

    为什么需要它：库里那份 16767 字的文档，如果直接在卡片里展开，卡片会变成
    **10229 px 高** —— 在看板的滚动区里翻一万像素没法用，而且是个极重的控件。
    所以超过阈值的段落改成开一个可缩放的独立窗口，自带滚动条。
    """

    def __init__(self, rec: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{fmt_clock(rec['start_time'])} 的总结")
        self.resize(780, 640)
        self.setStyleSheet(build_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        head = QLabel(
            f'<span style="color:{theme.MUTED}">'
            f'{fmt_clock(rec["start_time"])} → {fmt_clock(rec["end_time"])}</span>'
            f'<span style="color:{theme.GHOST}">　·　</span>'
            f'<span style="color:{theme.MUTED}">专注</span>'
            f'<span style="color:{theme.INK};font-weight:600">'
            f'  {fmt_duration(rec["duration_seconds"])}</span>'
            f'<span style="color:{theme.GHOST}">　·　</span>'
            f'<span style="color:{theme.MUTED}">休息</span>'
            f'<span style="color:{theme.INK};font-weight:600">'
            f'  {fmt_duration(rec["rest_seconds"])}</span>'
        )
        head.setTextFormat(Qt.RichText)
        head.setObjectName("cardStat")
        lay.addWidget(head)

        view = QTextBrowser()
        view.setObjectName("viewer")
        view.setOpenExternalLinks(False)
        text = rec["polished_text"] or rec["raw_text"]
        if _MD_HEADING.search(text):
            view.setMarkdown(text)
        else:
            view.setPlainText(text)
        lay.addWidget(view)

        if rec["keywords"]:
            sep = f'<span style="color:{theme.GHOST}"> · </span>'
            words = sep.join(
                f'<span style="color:{theme.MUTED}">{k}</span>'
                for k in rec["keywords"]
            )
            kw = QLabel(
                f'<span style="color:{theme.FAINT}">关键词</span>'
                f'<span style="color:{theme.GHOST}">　</span>{words}'
            )
            kw.setTextFormat(Qt.RichText)
            kw.setObjectName("cardKw")
            kw.setWordWrap(True)
            lay.addWidget(kw)

        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(btn)
        lay.addLayout(row)


class RecordCard(QFrame):
    """
    一次专注的段落卡片。

    正文过长默认折叠 —— 库里那份 16767 字、30 章节的学习文档全展开会把看板撑爆，
    所以超过 COLLAPSE_OVER 字先只显示开头的预览，想看全文点「展开全文」。
    """

    COLLAPSE_OVER = 600
    PREVIEW_CHARS = 600
    # 超过这个长度就不在卡片里展开了 —— 展开会得到上万像素高的卡片，没法看
    INLINE_EXPAND_MAX = 2500

    def __init__(self, rec: dict, on_repolish=None, show_category: bool = False) -> None:
        super().__init__()
        self.setObjectName("card")
        self._rec = rec
        self._on_repolish = on_repolish
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 13, 16, 13)
        root.setSpacing(9)

        # —— 时间行 ——
        # 左边时间（次要信息，灰），右边时长（数字要能一眼扫到，所以加重）
        head = QHBoxLayout()
        head.setSpacing(10)

        # 看「分组」时记录来自不同子类别，得标出来是哪一条
        if show_category and rec.get("category_name"):
            cat_tag = QLabel(rec["category_name"])
            cat_tag.setObjectName("cardCat")
            head.addWidget(cat_tag)

        when = QLabel(f"{fmt_clock(rec['start_time'])} → {fmt_clock(rec['end_time'])}")
        when.setObjectName("cardWhen")
        head.addWidget(when)

        if not rec["polished_text"]:
            tag = QLabel("未整理")
            tag.setObjectName("cardTag")
            head.addWidget(tag)

        head.addStretch()

        # 数字用富文本加重、标签保持灰 —— 层次拉开了才好扫
        stat = QLabel(
            f'<span style="color:{theme.MUTED}">专注</span>'
            f'<span style="color:{theme.INK};font-weight:600">'
            f'  {fmt_duration(rec["duration_seconds"])}</span>'
            f'<span style="color:{theme.GHOST}">　·　</span>'
            f'<span style="color:{theme.MUTED}">休息</span>'
            f'<span style="color:{theme.INK};font-weight:600">'
            f'  {fmt_duration(rec["rest_seconds"])}</span>'
        )
        stat.setTextFormat(Qt.RichText)
        stat.setObjectName("cardStat")
        head.addWidget(stat)

        root.addLayout(head)

        # —— 正文 ——
        self._body_text = rec["polished_text"] or rec["raw_text"]
        self.body = QLabel()
        self.body.setObjectName("cardBody")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        root.addWidget(self.body)

        self._toggle = None
        self._inline = len(self._body_text) <= self.INLINE_EXPAND_MAX
        if len(self._body_text) > self.COLLAPSE_OVER:
            self._toggle = QPushButton(
                "展开全文" if self._inline else "在新窗口查看全文"
            )
            self._toggle.setObjectName("link")
            self._toggle.setCursor(Qt.PointingHandCursor)
            self._toggle.clicked.connect(self._toggle_body)
            root.addWidget(self._toggle, alignment=Qt.AlignLeft)

        self._render_body()

        # —— 关键词 ——
        # 「关键词」当标签（很淡），词条用次要灰，中间细点分隔
        if rec["keywords"]:
            sep = f'<span style="color:{theme.GHOST}"> · </span>'
            words = sep.join(
                f'<span style="color:{theme.MUTED}">{k}</span>'
                for k in rec["keywords"]
            )
            kw = QLabel(
                f'<span style="color:{theme.FAINT}">关键词</span>'
                f'<span style="color:{theme.GHOST}">　</span>{words}'
            )
            kw.setTextFormat(Qt.RichText)
            kw.setObjectName("cardKw")
            kw.setWordWrap(True)
            root.addWidget(kw)

    def _render_body(self) -> None:
        text = self._body_text
        if self._toggle is not None and not self._expanded:
            text = text[: self.PREVIEW_CHARS] + "……"
        # 带 Markdown 标题的用 Markdown 渲染（标题、加粗能正常显示），
        # 普通人话用纯文本 —— 免得原文里的 * # 被当成语法吃掉。
        self.body.setTextFormat(
            Qt.MarkdownText if _MD_HEADING.search(text) else Qt.PlainText
        )
        self.body.setText(text)

    def _toggle_body(self) -> None:
        # 长文不在卡片里展开，开独立窗口 —— 见 ParagraphViewer 的说明
        if not self._inline and not self._expanded:
            ParagraphViewer(self._rec, self).exec_()
            return
        self._expanded = not self._expanded
        self._toggle.setText("收起" if self._expanded else "展开全文")
        self._render_body()

    def contextMenuEvent(self, event):  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss())
        menu.addAction("🔄 重新整理这一段", self._repolish)
        menu.exec_(event.globalPos())

    def _repolish(self) -> None:
        if self._on_repolish is not None:
            self._on_repolish(self._rec)


class Dashboard(QWidget):
    """类别总时间 + 每次专注的段落。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("学习看板")
        self.setStyleSheet(build_qss())
        self.resize(880, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        head = QHBoxLayout()
        h1 = QLabel("学习看板")
        h1.setObjectName("h1")
        head.addWidget(h1)
        head.addStretch()

        # 今日累计 —— 只算今天「写完总结」的那些记录（见 timer.today_total）
        self.today_label = QLabel("今日累计 —")
        self.today_label.setObjectName("today")
        head.addWidget(self.today_label)
        root.addLayout(head)

        cols = QHBoxLayout()
        cols.setSpacing(22)

        # ───────────── 左：类别 ─────────────
        left = QVBoxLayout()
        left.setSpacing(8)

        lab1 = QLabel("类别")
        lab1.setObjectName("h2")
        left.addWidget(lab1)

        # 二级目录 → 用树。父类别是「分组」，时长是自己 + 子类别的合计。
        # 两列：左边名字，右边时长右对齐 —— 比在名字后面拼 "    ·    3.1 h" 干净得多。
        self.cat_list = QTreeWidget()
        self.cat_list.setHeaderHidden(True)
        self.cat_list.setColumnCount(2)
        self.cat_list.setIndentation(theme.px(14))
        self.cat_list.setExpandsOnDoubleClick(False)
        self.cat_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._on_context_menu)
        self.cat_list.currentItemChanged.connect(self._on_selection_changed)
        self.cat_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.cat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 字号档调大时左栏得跟着变宽。210 是量出来的：字号档 130% 时
        # 「Agent项目-mewagent」实际要 ~186px + 缩进 18 + 时长列 86，约 300px。
        self.cat_list.setMinimumWidth(theme.px(232))
        # 第 0 列（名字）吃掉剩余宽度，第 1 列（时长）固定宽并右对齐
        hdr = self.cat_list.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        self.cat_list.setColumnWidth(1, theme.px(66))
        left.addWidget(self.cat_list)

        self.total_label = QLabel("—")
        self.total_label.setObjectName("total")
        left.addWidget(self.total_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        btn_add = QPushButton("+ 新建类别")
        btn_add.setObjectName("primary")
        btn_add.setToolTip("新建一个顶层类别")
        btn_add.clicked.connect(self._on_add)
        row.addWidget(btn_add)

        self.btn_add_child = QPushButton("+ 子类别")
        self.btn_add_child.setToolTip("在当前选中的类别下面建一个子类别")
        self.btn_add_child.clicked.connect(self._on_add_child)
        row.addWidget(self.btn_add_child)

        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._on_delete)
        row.addWidget(btn_del)
        row.addStretch()
        left.addLayout(row)

        cols.addLayout(left, 3)

        # ───────────── 右：段落卡片 ─────────────
        right = QVBoxLayout()
        right.setSpacing(10)

        head2 = QHBoxLayout()
        head2.setSpacing(8)
        self.items_title = QLabel("")
        self.items_title.setObjectName("h2")
        head2.addWidget(self.items_title)
        head2.addStretch()

        self.btn_polish_all = QPushButton("批量整理")
        self.btn_polish_all.setToolTip(
            "把还没整理过的记录一次性交给 AI 整理。\n"
            "历史记录和之前调用失败的都算在内。"
        )
        self.btn_polish_all.clicked.connect(self._on_polish_all)
        head2.addWidget(self.btn_polish_all)
        right.addLayout(head2)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.cards_host = QWidget()
        self.cards_host.setObjectName("cardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 8, 0)
        self.cards_layout.setSpacing(theme.px(10))
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.cards_host)

        right.addWidget(self.scroll)
        cols.addLayout(right, 7)

        root.addLayout(cols)

        # —— 后台整理任务的进度 ——
        # 用「工作线程写状态 + 主线程定时轮询」而不是 QTimer.singleShot 回推：
        # 普通 threading.Thread 没有 Qt 事件循环，singleShot 从那边调根本不会触发。
        self._job = {"running": False, "done": 0, "total": 0, "ok": 0}
        self._job_timer = QTimer(self)
        self._job_timer.setInterval(400)
        self._job_timer.timeout.connect(self._poll_job)

        # 缓存的类别树（category_tree 的结果），避免每次点击都重建
        self._tree: list = []
        self.refresh()

    # ────────────────────────── 关窗 = 收起，不是销毁 ──────────────────────────

    def closeEvent(self, event):  # noqa: N802
        """
        点 X 只是**收起来**，不是关掉。

        看板是常驻对象，关了就活不过来（悬浮窗右键的「📋 学习面板」会打不开）。
        所以这里拦下关闭事件，改成 hide —— 下次要开直接 show 回来。
        """
        event.ignore()
        self.hide()

    def reapply_theme(self) -> None:
        """
        字号档变了之后重新套一遍样式。

        看板是常驻对象，不像对话框那样每次新建 —— 所以得主动通知它。
        重新 setStyleSheet 会让 Qt 把新样式刷到这个控件**及其所有子控件**上；
        卡片间距、树列宽这些是代码里算的，也得跟着重设。
        """
        self.setStyleSheet(build_qss())
        self.cat_list.setIndentation(theme.px(14))
        self.cat_list.setMinimumWidth(theme.px(232))
        self.cat_list.setColumnWidth(1, theme.px(66))
        self.cards_layout.setSpacing(theme.px(10))
        # 卡片是按字号算过的尺寸画的，重建一遍最省心
        self.refresh(select_id=self._current_id())

    # ────────────────────────── 数据 ──────────────────────────

    def _current_id(self) -> int | None:
        item = self.cat_list.currentItem()
        return item.data(0, ROLE_ID) if item else None

    def _current_is_group(self) -> bool:
        """当前选中的是不是「分组」（有子类别）。"""
        item = self.cat_list.currentItem()
        return bool(item.data(0, ROLE_IS_GROUP)) if item else False

    def _find_node(self, cid: int) -> dict | None:
        """在缓存的树里按 id 找节点（顶层或子层都能找到）。"""
        for node in self._tree:
            if node["cat"].id == cid:
                return node
            for ch in node["children"]:
                if ch["cat"].id == cid:
                    return ch
        return None

    def refresh(self, select_id: int | None = None) -> None:
        """重新拉数据并重建左边的树。select_id 指定要选中的类别。"""
        if select_id is None:
            select_id = self._current_id()

        # 记住哪些分组是展开的，重建之后恢复 —— 否则每次刷新都折叠回去
        expanded = set()
        it = QTreeWidgetItem()
        root = self.cat_list.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            if child.isExpanded():
                expanded.add(child.data(0, ROLE_ID))

        with SessionLocal() as db:
            self._tree = timer_svc.category_tree(db)
            today = timer_svc.today_total(db)
            pending = len(timer_svc.unpolished_records(db))
        self.today_label.setText(f"今日累计 {fmt_hours(today)}")

        self.btn_polish_all.setEnabled(pending > 0 and not self._job["running"])
        self.btn_polish_all.setText(
            f"批量整理 {pending}" if pending else "批量整理"
        )

        if select_id is None and self._tree:
            select_id = self._tree[0]["cat"].id

        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        target: QTreeWidgetItem | None = None
        first: QTreeWidgetItem | None = None

        for node in self._tree:
            cat, total = node["cat"], node["total"]
            is_group = bool(node["children"])
            name = f"📌 {cat.name}" if cat.is_pinned else cat.name
            if is_group:
                # 折起来的时候也得知道下面有几个，所以挂在名字后面（用浅色括号）
                name += f"  ({len(node['children'])})"

            item = QTreeWidgetItem([name, fmt_hours(total)])
            item.setData(0, ROLE_ID, cat.id)
            item.setData(0, ROLE_IS_GROUP, is_group)
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setForeground(1, QBrush(QColor(theme.FAINT)))
            # 左栏窄的时候名字会被省略号截掉，tooltip 兜底显示全名
            tip = f"{cat.name}　·　{fmt_hours(total)}"
            if is_group:
                tip += "\n这是分组，不能直接计时 —— 请选它下面的子类别"
            item.setToolTip(0, tip)
            self.cat_list.addTopLevelItem(item)

            for ch in node["children"]:
                sub = QTreeWidgetItem([ch["cat"].name, fmt_hours(ch["total"])])
                sub.setData(0, ROLE_ID, ch["cat"].id)
                sub.setData(0, ROLE_IS_GROUP, False)
                sub.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                sub.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                sub.setForeground(1, QBrush(QColor(theme.FAINT)))
                sub.setToolTip(0, f"{ch['cat'].name}　·　{fmt_hours(ch['total'])}")
                item.addChild(sub)

            # 有子类别的默认展开；用户手动折起来的保持折起
            item.setExpanded(True if not expanded and is_group else cat.id in expanded)

            if first is None:
                first = item
            if cat.id == select_id:
                target = item
            elif not target:
                for j in range(item.childCount()):
                    if item.child(j).data(0, ROLE_ID) == select_id:
                        target = item.child(j)

        self.cat_list.blockSignals(False)

        # 选中目标；目标在折起来的父节点里就把它展开
        pick = target or first
        if pick is not None:
            parent = pick.parent()
            if parent is not None:
                parent.setExpanded(True)
            self.cat_list.setCurrentItem(pick)
            self._on_selection_changed()

    def _set_total(self, seconds: int, prefix: str = "") -> None:
        """左下角那个大合计 —— 数字大、单位小，层次分明。"""
        num, unit = hour_parts(seconds)
        self.total_label.setTextFormat(Qt.RichText)
        self.total_label.setText(
            f'<span style="color:{theme.FAINT};font-size:{theme.px(12)}px;'
            f'font-weight:400">{prefix}</span>'
            f'{num}'
            f'<span style="color:{theme.FAINT};font-size:{theme.px(12)}px;'
            f'font-weight:400"> {unit}</span>'
        )

    def _on_selection_changed(self, *_) -> None:
        cid = self._current_id()
        if cid is None:
            self.total_label.setText("—")
            self.items_title.setText("学过什么")
            self._clear_cards()
            return

        node = self._find_node(cid)
        if node is None:
            return
        cat = node["cat"]
        is_group = bool(node["children"])

        # 分组显示合计；子类别/叶子显示自己的
        self._set_total(node["total"], prefix="共 ")

        with SessionLocal() as db:
            records = timer_svc.category_records(
                db, cat.id, include_children=is_group
            )

        if is_group:
            self.items_title.setText(
                f"{cat.name}  ·  分组  ·  {len(node['children'])} 个子类别  ·  "
                f"{len(records)} 次专注"
            )
        else:
            self.items_title.setText(f"{cat.name}  ·  {len(records)} 次专注")
        self._rebuild_cards(records, show_category=is_group)

    def _on_item_double_clicked(self, item, _column) -> None:
        """双击分组 = 展开/折叠（改了 setExpandsOnDoubleClick，得自己来）。"""
        if item.childCount():
            item.setExpanded(not item.isExpanded())

    # ────────────────────────── 段落卡片 ──────────────────────────

    def _clear_cards(self) -> None:
        """
        清空卡片区。

        ★ 必须先 setParent(None) 再 deleteLater()：
          takeAt() 只是把控件从**布局**里摘掉，它仍然是 cards_host 的**子控件**、
            仍然是可见的 —— 只有等 Qt 处理 DeferredDelete 事件才真正消失。
            中间这段时间就会看到「上一个类别的卡片残影」叠在新卡片下面。
            setParent(None) 会立刻断开父子关系（连带隐藏），deleteLater() 再回收内存。
        """
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _rebuild_cards(self, records: list[dict], show_category: bool = False) -> None:
        self._clear_cards()

        if not records:
            ph = QLabel("（还没有记录 —— 完成一次专注并写完总结就会出现在这里）")
            ph.setObjectName("h2")
            ph.setWordWrap(True)
            self.cards_layout.addWidget(ph)
            self.cards_layout.addStretch()
            return

        for rec in records:
            self.cards_layout.addWidget(
                RecordCard(rec, on_repolish=self._repolish_one, show_category=show_category)
            )
        self.cards_layout.addStretch()
        self.scroll.verticalScrollBar().setValue(0)

    # ────────────────────────── 后台整理 ──────────────────────────

    def _start_job(self, records: list[dict], verb: str) -> None:
        """把一批记录丢给后台线程整理。同一时刻只允许一个任务。"""
        if self._job["running"] or not records:
            return

        total = len(records)
        self._job = {"running": True, "done": 0, "total": total, "ok": 0}
        self.btn_polish_all.setEnabled(False)
        self.btn_polish_all.setText(f"整理中 0/{total}")
        self._job_timer.start()

        job = self._job

        def work():
            for rec in records:
                paragraph, keywords = llm_svc.polish(rec["raw_text"], rec["category_name"])
                if paragraph:
                    try:
                        with SessionLocal() as db:
                            timer_svc.set_polish(db, rec["id"], paragraph, keywords)
                        job["ok"] += 1
                    except Exception as e:              # noqa: BLE001
                        print(f"[看板] 写入整理结果失败 id={rec['id']}: {e}")
                job["done"] += 1
            job["running"] = False

        threading.Thread(target=work, daemon=True).start()
        print(f"[看板] {verb}：共 {total} 条，后台开始")

    def _poll_job(self) -> None:
        job = self._job
        if job["running"]:
            self.btn_polish_all.setText(f"整理中 {job['done']}/{job['total']}")
            return

        self._job_timer.stop()
        ok, total = job["ok"], job["total"]
        self.btn_polish_all.setText("批量整理")

        if ok == 0:
            QMessageBox.warning(
                self, "整理没成功",
                f"{total} 条都没整理成功。\n\n"
                "常见原因：后台没有配 AI（DEEPSEEK_API_KEY），或者网络/接口报错。\n"
                "详细原因看控制台输出。",
            )
        elif ok < total:
            QMessageBox.information(
                self, "部分完成",
                f"成功 {ok} 条，失败 {total - ok} 条。失败的多半是接口超时或输出被截断。",
            )
        self.refresh(select_id=self._current_id())

    def _on_polish_all(self) -> None:
        with SessionLocal() as db:
            pending = timer_svc.unpolished_records(db)
        if not pending:
            QMessageBox.information(self, "都整理过了", "没有需要整理的记录。")
            return

        chars = sum(len(p["raw_text"]) for p in pending)
        box = QMessageBox(self)
        box.setWindowTitle("批量整理")
        box.setText(
            f"有 {len(pending)} 条记录还没整理，合计 {chars} 字。\n\n"
            f"会逐条调用 AI 整理，时间取决于原文长度（最长的一条可能要几分钟）。\n"
            f"期间看板可以正常用，进度显示在右上角按钮上。\n\n要开始吗？"
        )
        box.setIcon(QMessageBox.Question)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec_() != QMessageBox.Yes:
            return

        self._start_job(pending, "批量整理")

    def _repolish_one(self, rec: dict) -> None:
        """重新整理某一段（右键卡片）。"""
        if self._job["running"]:
            QMessageBox.information(self, "正在忙", "上一个整理任务还没跑完，稍等一下。")
            return
        if not rec["raw_text"].strip():
            QMessageBox.warning(self, "没有原文", "这条记录没有原文，整理不了。")
            return
        self._start_job([rec], f"重新整理 id={rec['id']}")

    # ────────────────────────── 置顶 ──────────────────────────

    def _on_toggle_pin(self) -> None:
        """置顶 / 取消置顶当前选中的类别（只对顶层有意义）。"""
        cid = self._current_id()
        if cid is None:
            return
        if self.cat_list.currentItem() is not None and self.cat_list.currentItem().parent():
            QMessageBox.information(
                self, "子类别不能置顶", "置顶只作用于顶层类别 —— 子类别跟着父类别走。"
            )
            return
        with SessionLocal() as db:
            timer_svc.toggle_pin(db, cid)
        self.refresh(select_id=cid)

    # ────────────────────────── 增 / 删 / 改 / 移动 ──────────────────────────

    def _on_add(self) -> None:
        """新建顶层类别。"""
        name, ok = QInputDialog.getText(self, "新建类别", "类别名（如：RAG / Agent）")
        if not ok or not name.strip():
            return
        try:
            with SessionLocal() as db:
                cat = timer_svc.add_category(db, name)
                new_id = cat.id
        except ValueError as e:
            QMessageBox.warning(self, "没能新建", str(e))
            return
        self.refresh(select_id=new_id)

    def _on_add_child(self) -> None:
        """在当前选中的类别下面建子类别。"""
        cid = self._current_id()
        if cid is None:
            QMessageBox.information(self, "先选一个类别", "先在上面选中要挂到哪个类别下面。")
            return
        node = self._find_node(cid)
        if node is None:
            return
        parent = node["cat"]

        name, ok = QInputDialog.getText(
            self, "新建子类别", f"挂在「{parent.name}」下面，子类别名："
        )
        if not ok or not name.strip():
            return
        try:
            with SessionLocal() as db:
                cat = timer_svc.add_category(db, name, parent.id)
                new_id = cat.id
        except ValueError as e:
            QMessageBox.warning(self, "没能新建", str(e))
            return
        self.refresh(select_id=new_id)

    def _on_move(self) -> None:
        """
        把当前类别移到别的顶层类别下面，或移回顶层。

        只允许两级，所以候选父只能是顶层类别；自己带着子类别的也不能移。
        """
        cid = self._current_id()
        if cid is None:
            return
        node = self._find_node(cid)
        if node is None:
            return
        cat = node["cat"]

        if node["children"]:
            QMessageBox.information(
                self, "移不了",
                f"「{cat.name}」下面还有 {len(node['children'])} 个子类别，"
                f"不能再被移进别的类别（否则就成三级了）。\n\n"
                f"想移的话，先把它的子类别移走或删掉。",
            )
            return

        # 候选：所有顶层类别（排除自己）+ 顶层
        options = ["（顶层）"]
        ids: list[int | None] = [None]
        for n in self._tree:
            if n["cat"].id == cid:
                continue
            options.append(n["cat"].name)
            ids.append(n["cat"].id)

        if len(options) == 1:
            QMessageBox.information(self, "没地方可移", "现在还没有别的顶层类别可以当父。")
            return

        cur_idx = 0
        if cat.parent_id is not None:
            for i, pid in enumerate(ids):
                if pid == cat.parent_id:
                    cur_idx = i
                    break

        choice, ok = QInputDialog.getItem(
            self, "移动到…", f"把「{cat.name}」移到：", options, cur_idx, False
        )
        if not ok:
            return
        target = ids[options.index(choice)]

        if target == cat.parent_id:
            return          # 没变，什么都不用做
        try:
            with SessionLocal() as db:
                timer_svc.move_category(db, cid, target)
        except ValueError as e:
            QMessageBox.warning(self, "没能移动", str(e))
            return
        self.refresh(select_id=cid)

    def _on_delete(self) -> None:
        cid = self._current_id()
        if cid is None:
            return
        node = self._find_node(cid)
        if node is None:
            return
        cat = node["cat"]
        kids = node["children"]

        with SessionLocal() as db:
            n_sessions, seconds = timer_svc.category_stats(db, cid)

        msg = f"「{cat.name}」"
        if n_sessions:
            msg += (
                f"有 {n_sessions} 条学习记录，共 {fmt_hours(seconds)}。\n\n"
                f"删除会把这些记录和 AI 整理出来的段落、关键词**一并清掉**，且无法恢复。"
            )
        if kids:
            names = "、".join(k["cat"].name for k in kids)
            msg += (
                f"\n\n它下面还有 {len(kids)} 个子类别（{names}）。\n"
                f"这些子类别会被**移到顶层**，它们自己的学习记录一条都不会丢。"
            )
        if not n_sessions and not kids:
            msg += "确定删除吗？"
        else:
            msg += "\n\n确定要删吗？"

        box = QMessageBox(self)
        box.setWindowTitle("删除类别")
        box.setText(msg)
        box.setIcon(QMessageBox.Warning if n_sessions or kids else QMessageBox.Question)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec_() != QMessageBox.Yes:
            return

        try:
            with SessionLocal() as db:
                promoted = timer_svc.delete_category(db, cid, force=True)
        except ValueError as e:
            QMessageBox.warning(self, "没能删除", str(e))
            return

        if promoted:
            QMessageBox.information(
                self, "子类别保住了",
                "这些子类别已经移到顶层，学习记录都在：\n  " + "\n  ".join(promoted),
            )
        self.refresh()

    def _on_rename(self) -> None:
        cid = self._current_id()
        if cid is None:
            return
        node = self._find_node(cid)
        if node is None:
            return
        old_name = node["cat"].name

        name, ok = QInputDialog.getText(self, "重命名类别", "新的类别名", text=old_name)
        if not ok or not name.strip():
            return
        try:
            with SessionLocal() as db:
                timer_svc.rename_category(db, cid, name)
        except ValueError as e:
            QMessageBox.warning(self, "没能改名", str(e))
            return
        self.refresh(select_id=cid)

    def _on_context_menu(self, pos) -> None:
        hit = self.cat_list.itemAt(pos)
        if not hit:
            return
        self.cat_list.setCurrentItem(hit)

        cid = self._current_id()
        node = self._find_node(cid) if cid is not None else None
        if node is None:
            return
        is_group = bool(node["children"])
        is_child = hit.parent() is not None
        pinned = node["cat"].is_pinned

        menu = QMenu(self)
        menu.setStyleSheet(menu_qss())

        # 子类别不能置顶（跟着父走）
        act_pin = menu.addAction("取消置顶" if pinned else "📌 置顶", self._on_toggle_pin)
        act_pin.setEnabled(not is_child)

        menu.addAction("➕ 新建子类别", self._on_add_child).setEnabled(not is_group)
        act_move = menu.addAction("📁 移动到…", self._on_move)
        act_move.setEnabled(not is_group)

        menu.addSeparator()
        menu.addAction("重命名", self._on_rename)
        menu.addAction("删除", self._on_delete)
        menu.exec_(self.cat_list.viewport().mapToGlobal(pos))
