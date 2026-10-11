"""
强制回顾弹窗 —— 计时结束后必须写总结才能保存。

这是整个软件的核心机制，不是可跳过的提示框：
    · 「保存」按钮在输入为空时是灰的
    · 即使绕过界面，数据库层面还有 chk_review_required 兜底
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ui import theme

DIALOG_QSS_TEMPLATE = """
QDialog { background: #ffffff; }
QLabel#title { color: INK; font-size: 15px; font-weight: 600; }
QLabel#live  {
    color: ACCENT; font-size: 12px; font-weight: 600;
    background: ACCENT_SOFT; border-radius: 11px; padding: 5px 12px;
}
QLabel#meta  { color: MUTED; font-size: 12.5px; }
QLabel#note  { color: FAINT; font-size: 11.5px; }
QTextEdit {
    border: 1px solid LINE_SOFT; border-radius: 10px;
    padding: 10px; font-size: 13.5px; color: INK;
    background: #ffffff;
}
QTextEdit:focus { border-color: ACCENT; }
QPushButton {
    border: 1px solid LINE_SOFT; border-radius: 8px;
    padding: 8px 18px; font-size: 12.5px; color: INK_SOFT; background: #ffffff;
}
QPushButton:hover { background: BG_SOFT; border-color: GHOST; }
QPushButton#primary {
    background: ACCENT; color: #ffffff; border: 1px solid ACCENT; font-weight: 600;
}
QPushButton#primary:hover { background: ACCENT_HOVER; border-color: ACCENT_HOVER; }
QPushButton#primary:disabled {
    background: #ffffff; color: GHOST; border-color: LINE;
}
"""


class ReviewDialog(QDialog):
    """写总结。关闭窗口 = 放弃本次记录（会提示）。"""

    def __init__(self, category_name: str, seconds_provider=None, parent=None) -> None:
        """
        seconds_provider: 无参可调用对象，返回「当前已计入的学习秒数」。
            有这个就会在标题右边挂一个每秒跳一下的计数 —— 写总结的时间
            也算学习时长（见 开发文档 §5.9），得让它看得见，不然心里没数。
            传 None 就整块不显示（时长已经定死的情况，跳也没意义）。
        """
        super().__init__(parent)
        self.setWindowTitle("写总结")
        self.setStyleSheet(theme.styled(DIALOG_QSS_TEMPLATE))
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self._submitted = False
        self._seconds_provider = seconds_provider

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("这次学了什么？")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch()

        self.live = QLabel("")
        self.live.setObjectName("live")
        head.addWidget(self.live)
        root.addLayout(head)

        meta = QLabel(category_name)
        meta.setObjectName("meta")
        root.addWidget(meta)

        self._live_timer = None
        if seconds_provider is not None:
            self._live_timer = QTimer(self)
            self._live_timer.timeout.connect(self._refresh_live)
            self._live_timer.start(1000)
            self._refresh_live()
        else:
            self.live.hide()

        self.edit = QTextEdit()
        self.edit.setPlaceholderText(
            "随手写就行，不用组织语言。\n"
            "例：看了联合索引的最左前缀原则，踩了个坑——范围查询后面的列用不上索引。"
        )
        self.edit.setMinimumHeight(130)
        root.addWidget(self.edit)

        note = QLabel("必须写才能保存 —— AI 会把它整理成一段通顺的话，并提炼关键词")
        note.setObjectName("note")
        root.addWidget(note)

        # —— 按钮行 ——
        row = QHBoxLayout()
        row.addStretch()

        self.btn_cancel = QPushButton("放弃本次记录")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.setDefault(True)
        self.btn_save.setEnabled(False)          # 空文本时不可点
        self.btn_save.clicked.connect(self._on_save)
        row.addWidget(self.btn_save)

        root.addLayout(row)

        self.edit.textChanged.connect(self._refresh)
        self.edit.setFocus()

    def _refresh_live(self) -> None:
        """每秒把「已计入」往上推一格。算不出来就保持原样，不弹错。"""
        try:
            seconds = int(self._seconds_provider())
        except Exception:
            return
        h, rem = divmod(max(0, seconds), 3600)
        m, s = divmod(rem, 60)
        text = f"{h}:{m:02d}:{s:02d}" if h else "%02d:%02d" % (m, s)
        self.live.setText("已计入 " + text)

    def _refresh(self) -> None:
        self.btn_save.setEnabled(bool(self.edit.toPlainText().strip()))

    def _on_save(self) -> None:
        if not self.edit.toPlainText().strip():
            return
        self._submitted = True
        self.accept()

    # —— 关闭窗口 ——
    # 这里不做二次确认：「放弃」的后果（时长不计入）由主程序统一解释，
    # 免得连着弹两个确认框。
    def reject(self) -> None:  # noqa: D102
        super().reject()

    def get_text(self) -> str:
        return self.edit.toPlainText().strip()
