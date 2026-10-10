"""
看板 —— 左边类别（可增删改、拖动排序），右边该类别下 AI 拆出来的条目。

按需求只显示两样：**类别总时间** 和 **条目清单**。
不显示次数、不显示每次时长。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from db.database import SessionLocal
from services import timer as timer_svc

QSS = """
QWidget#root { background: #ffffff; }
QLabel#h1 { color: #1f2937; font-size: 15px; font-weight: bold; }
QLabel#h2 { color: #6b7280; font-size: 11px; }
QLabel#total { color: #3b82f6; font-size: 22px; font-weight: bold; }
QLabel#today {
    color: #1d4ed8; font-size: 12px; font-weight: bold;
    background: #eff6ff; border-radius: 11px; padding: 5px 13px;
}
QListWidget {
    border: 1px solid #e5e7eb; border-radius: 8px;
    font-size: 12px; color: #374151; outline: none; padding: 4px;
}
QListWidget::item { padding: 7px 8px; border-radius: 6px; }
QListWidget::item:selected { background: #eff6ff; color: #1d4ed8; }
QListWidget::item:hover { background: #f9fafb; }
QPushButton {
    border: 1px solid #e5e7eb; border-radius: 6px;
    padding: 5px 12px; font-size: 11px; color: #374151; background: #ffffff;
}
QPushButton:hover { background: #f9fafb; border-color: #d1d5db; }
QPushButton#primary { background: #3b82f6; color: #fff; border: none; }
QPushButton#primary:hover { background: #2563eb; }
"""


def fmt_hours(seconds: int) -> str:
    """秒 → 「3.5 h」/「45 min」这样的人话。"""
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds / 3600:.1f} h"


# ─────────────────────────────────────────────────────────────
# 关于「排序」：曾经做过拖动排序，翻车三次（详见 开发文档 踩坑记录）：
#   1. 挂 model().rowsMoved —— 那个信号 QListWidget 内部拖动根本不发
#   2. 在 dropEvent 里 refresh() 重建列表 —— 破坏 Qt 拖放内部状态，条目消失
#   3. 自己接管 dropEvent 落位 —— 又引出新的状态问题
# 结论：排序改成**置顶**（右键钉住），比拖动可靠得多，而且真正常用的
#       也就那几个类别。拖动相关代码已全部移除。
# ─────────────────────────────────────────────────────────────


class Dashboard(QWidget):
    """类别总时间 + 条目清单。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("学习看板")
        self.setStyleSheet(QSS)
        self.resize(700, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

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
        cols.setSpacing(16)

        # ───────────── 左：类别 ─────────────
        left = QVBoxLayout()
        left.setSpacing(6)

        lab1 = QLabel("类别 · 累计专注（右键可置顶）")
        lab1.setObjectName("h2")
        left.addWidget(lab1)

        self.cat_list = QListWidget()
        self.cat_list.currentItemChanged.connect(self._on_selection_changed)
        self.cat_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._on_context_menu)
        left.addWidget(self.cat_list)

        self.total_label = QLabel("—")
        self.total_label.setObjectName("total")
        left.addWidget(self.total_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        btn_add = QPushButton("+ 新建类别")
        btn_add.setObjectName("primary")
        btn_add.clicked.connect(self._on_add)
        row.addWidget(btn_add)

        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._on_delete)
        row.addWidget(btn_del)
        row.addStretch()
        left.addLayout(row)

        cols.addLayout(left, 4)

        # ───────────── 右：条目 ─────────────
        right = QVBoxLayout()
        right.setSpacing(6)
        self.items_title = QLabel("学过什么")
        self.items_title.setObjectName("h2")
        right.addWidget(self.items_title)

        self.item_list = QListWidget()
        right.addWidget(self.item_list)
        cols.addLayout(right, 6)

        root.addLayout(cols)

        self._categories: list = []
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

    # ────────────────────────── 数据 ──────────────────────────

    def _current_id(self) -> int | None:
        item = self.cat_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh(self, select_id: int | None = None) -> None:
        """重新拉数据。select_id 指定要选中的类别。"""
        if select_id is None:
            select_id = self._current_id()

        with SessionLocal() as db:
            self._categories = timer_svc.category_totals(db)
            today = timer_svc.today_total(db)
        self.today_label.setText(f"今日累计 {fmt_hours(today)}")

        if select_id is None and self._categories:
            select_id = self._categories[0][0].id

        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        target_row = None
        for i, (cat, total) in enumerate(self._categories):
            mark = "📌 " if cat.is_pinned else "     "
            item = QListWidgetItem(f"{mark}{cat.name}    ·    {fmt_hours(total)}")
            item.setData(Qt.UserRole, cat.id)
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.cat_list.addItem(item)
            if cat.id == select_id:
                target_row = i
        self.cat_list.blockSignals(False)

        if self.cat_list.count():
            self.cat_list.setCurrentRow(target_row if target_row is not None else 0)
            self._on_selection_changed()

    def _on_selection_changed(self, *_) -> None:
        cid = self._current_id()
        if cid is None:
            self.total_label.setText("—")
            self.item_list.clear()
            return

        entry = next((c for c in self._categories if c[0].id == cid), None)
        if entry is None:
            return
        cat, total = entry
        self.total_label.setText(f"共 {fmt_hours(total)}")

        with SessionLocal() as db:
            items = timer_svc.category_items(db, cat.id)

        self.items_title.setText(f"「{cat.name}」学过什么 · {len(items)} 条")
        self.item_list.clear()
        if not items:
            ph = QListWidgetItem("（还没有 —— 写总结后 AI 会自动拆出来）")
            ph.setFlags(Qt.NoItemFlags)
            self.item_list.addItem(ph)
            return
        for it in items:
            self.item_list.addItem(QListWidgetItem("· " + it.name))

    # ────────────────────────── 拖动排序 ──────────────────────────

    def _on_toggle_pin(self) -> None:
        """置顶 / 取消置顶当前选中的类别。"""
        cid = self._current_id()
        if cid is None:
            return
        with SessionLocal() as db:
            timer_svc.toggle_pin(db, cid)
        self.refresh(select_id=cid)

    # ────────────────────────── 增 / 删 / 改 ──────────────────────────

    def _on_add(self) -> None:
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

    def _on_delete(self) -> None:
        cid = self._current_id()
        if cid is None:
            return
        entry = next((c for c in self._categories if c[0].id == cid), None)
        if entry is None:
            return
        cat, total = entry

        with SessionLocal() as db:
            n_sessions, seconds = timer_svc.category_stats(db, cid)

        if n_sessions:
            msg = (
                f"「{cat.name}」有 {n_sessions} 条学习记录，共 {fmt_hours(seconds)}。\n\n"
                f"删除会把这些记录和 AI 拆出来的条目**一并清掉**，且无法恢复。\n\n"
                f"确定要删吗？"
            )
        else:
            msg = f"确定删除「{cat.name}」吗？"

        box = QMessageBox(self)
        box.setWindowTitle("删除类别")
        box.setText(msg)
        box.setIcon(QMessageBox.Warning if n_sessions else QMessageBox.Question)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec_() != QMessageBox.Yes:
            return

        try:
            with SessionLocal() as db:
                timer_svc.delete_category(db, cid, force=True)
        except ValueError as e:
            QMessageBox.warning(self, "没能删除", str(e))
            return
        self.refresh()

    def _on_rename(self) -> None:
        cid = self._current_id()
        if cid is None:
            return
        entry = next((c for c in self._categories if c[0].id == cid), None)
        if entry is None:
            return
        old_name = entry[0].name

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
        entry = next((c for c in self._categories if c[0].id == cid), None)
        pinned = entry[0].is_pinned if entry else False

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:4px;}"
            "QMenu::item{padding:6px 18px;border-radius:5px;font-size:12px;color:#1f2937;}"
            "QMenu::item:selected{background:#eff6ff;color:#1d4ed8;}"
        )
        menu.addAction("取消置顶" if pinned else "📌 置顶", self._on_toggle_pin)
        menu.addSeparator()
        menu.addAction("重命名", self._on_rename)
        menu.addAction("删除", self._on_delete)
        menu.exec_(self.cat_list.viewport().mapToGlobal(pos))
