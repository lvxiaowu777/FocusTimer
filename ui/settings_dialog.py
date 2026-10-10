"""
设置弹窗 —— 专注时长、休息时长。

透明地说明周期怎么跑：「专注 25 分钟 → 休息 5 分钟 → 再专注 25 分钟…」
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from db.database import SessionLocal
from services import settings as settings_svc

QSS = """
QDialog { background: #ffffff; }
QLabel { color: #374151; font-size: 12px; }
QLabel#title { font-size: 15px; font-weight: bold; color: #1f2937; }
QLabel#hint  { color: #9ca3af; font-size: 11px; }
QCheckBox { color: #374151; font-size: 12px; spacing: 8px; }
QSpinBox {
    border: 1px solid #e5e7eb; border-radius: 6px;
    padding: 4px 6px; font-size: 12px; color: #1f2937;
    background: #fafafa; min-width: 80px;
}
QSpinBox:focus { border-color: #3b82f6; background: #fff; }
QPushButton {
    border: 1px solid #e5e7eb; border-radius: 7px;
    padding: 7px 18px; font-size: 12px; color: #374151; background: #fff;
}
QPushButton:hover { background: #f9fafb; }
QPushButton#primary { background: #3b82f6; color: #fff; border: none; font-weight: bold; }
QPushButton#primary:hover { background: #2563eb; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(340)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        with SessionLocal() as db:
            cfg = settings_svc.get_all(db)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        title = QLabel("专注节奏")
        title.setObjectName("title")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        lo_f, hi_f = settings_svc.LIMITS["focus_minutes"]
        self.sp_focus = QSpinBox()
        self.sp_focus.setRange(lo_f, hi_f)
        self.sp_focus.setSuffix(" 分钟")
        self.sp_focus.setValue(cfg["focus_minutes"])
        self.sp_focus.valueChanged.connect(self._refresh_hint)
        form.addRow("每次专注", self.sp_focus)

        lo_b, hi_b = settings_svc.LIMITS["break_minutes"]
        self.sp_break = QSpinBox()
        self.sp_break.setRange(lo_b, hi_b)
        self.sp_break.setSuffix(" 分钟")
        self.sp_break.setValue(cfg["break_minutes"])
        self.sp_break.valueChanged.connect(self._refresh_hint)
        form.addRow("每次休息", self.sp_break)

        self.cb_sound = QCheckBox("阶段切换时响一声")
        self.cb_sound.setChecked(bool(cfg["sound_enabled"]))
        form.addRow("提示音", self.cb_sound)

        root.addLayout(form)

        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self._refresh_hint()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _refresh_hint(self) -> None:
        f, b = self.sp_focus.value(), self.sp_break.value()
        self.hint.setText(
            f"节奏：专注 {f} 分钟 → 休息 {b} 分钟 → 再专注 {f} 分钟……\n"
            f"一个完整周期 {f + b} 分钟。改完从下次开始生效。"
        )

    def _on_save(self) -> None:
        try:
            with SessionLocal() as db:
                settings_svc.set_many(
                    db,
                    focus_minutes=self.sp_focus.value(),
                    break_minutes=self.sp_break.value(),
                    sound_enabled=1 if self.cb_sound.isChecked() else 0,
                )
        except ValueError as e:
            QMessageBox.warning(self, "没能保存", str(e))
            return
        self.accept()
