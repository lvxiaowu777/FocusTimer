"""
设置弹窗 —— 专注节奏 + AI 整理配置。

两块内容：
  · 专注节奏：专注时长、休息时长、提示音。存数据库（setting 表）。
  · AI 整理：接口地址 / API Key / 模型名。存**安装目录下的 ai_config.json**，
    不进数据库 —— 这样打包分发时那个文件是空的，谁用谁填，
    也就不会再出现「exe 必须带着 .env 才能跑」的问题。

「测试连接」会拿当前填的参数真实打一次接口，当场告诉你 key 和模型能不能用，
而不是等你整理失败之后再来排查。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

import config
from config import __version__
from db.database import SessionLocal
from services import llm as llm_svc
from services import settings as settings_svc

QSS = """
QDialog { background: #ffffff; }
QLabel { color: #374151; font-size: 12px; }
QLabel#title { font-size: 15px; font-weight: bold; color: #1f2937; }
QLabel#hint  { color: #9ca3af; font-size: 11px; }
QLabel#version { color: #c0c4cc; font-size: 10px; }
QLabel#sect { font-size: 12px; font-weight: bold; color: #1f2937; }
QLabel#note { color: #9ca3af; font-size: 11px; }
QLabel#ok   { color: #15803d; font-size: 11px; font-weight: bold; }
QLabel#bad  { color: #b91c1c; font-size: 11px; font-weight: bold; }
QCheckBox { color: #374151; font-size: 12px; spacing: 8px; }
QSpinBox, QLineEdit, QComboBox {
    border: 1px solid #e5e7eb; border-radius: 6px;
    padding: 4px 6px; font-size: 12px; color: #1f2937;
    background: #fafafa; min-height: 18px;
}
QSpinBox { min-width: 80px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #3b82f6; background: #fff; }
QLineEdit:disabled, QComboBox:disabled { color: #9ca3af; background: #f3f4f6; }
QPushButton {
    border: 1px solid #e5e7eb; border-radius: 7px;
    padding: 7px 18px; font-size: 12px; color: #374151; background: #fff;
}
QPushButton:hover { background: #f9fafb; }
QPushButton:disabled { color: #c0c4cc; background: #fafafa; }
QPushButton#primary { background: #3b82f6; color: #fff; border: none; font-weight: bold; }
QPushButton#primary:hover { background: #2563eb; }
QPushButton#small { padding: 4px 12px; font-size: 11px; }
QFrame#sep { background: #e5e7eb; max-height: 1px; border: none; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(460)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        with SessionLocal() as db:
            cfg = settings_svc.get_all(db)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        # ───────────────── 专注节奏 ─────────────────
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

        sep = QFrame()
        sep.setObjectName("sep")
        root.addWidget(sep)

        # ───────────────── AI 整理 ─────────────────
        ai_title = QLabel("AI 整理")
        ai_title.setObjectName("title")
        root.addWidget(ai_title)

        ai_form = QFormLayout()
        ai_form.setSpacing(10)

        # —— AI 服务（预设 / 自定义）——
        self.cb_preset = QComboBox()
        for key, p in config.PRESETS.items():
            self.cb_preset.addItem(p["label"], key)
        self.cb_preset.addItem("自定义（自己填接口地址和模型）", config.PRESET_CUSTOM)
        self.cb_preset.currentIndexChanged.connect(self._on_preset_changed)
        ai_form.addRow("AI 服务", self.cb_preset)

        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("https://api.deepseek.com")
        ai_form.addRow("接口地址", self.ed_url)

        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("deepseek-flash")
        ai_form.addRow("模型名", self.ed_model)

        # —— API Key + 显示切换 ——
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("sk-...")
        key_row.addWidget(self.ed_key, 1)

        self.btn_show = QPushButton("显示")
        self.btn_show.setObjectName("small")
        self.btn_show.setCheckable(True)
        self.btn_show.setFixedWidth(56)
        self.btn_show.toggled.connect(self._on_toggle_key)
        key_row.addWidget(self.btn_show)
        ai_form.addRow("API Key", key_row)

        root.addLayout(ai_form)

        self.lb_note = QLabel()
        self.lb_note.setObjectName("note")
        self.lb_note.setWordWrap(True)
        root.addWidget(self.lb_note)

        # —— 测试连接 ——
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setObjectName("small")
        self.btn_test.clicked.connect(self._on_test)
        test_row.addWidget(self.btn_test)

        self.lb_test = QLabel("")
        self.lb_test.setObjectName("note")
        self.lb_test.setWordWrap(True)
        test_row.addWidget(self.lb_test, 1)
        root.addLayout(test_row)

        self.lb_where = QLabel("")
        self.lb_where.setObjectName("note")
        self.lb_where.setWordWrap(True)
        root.addWidget(self.lb_where)

        # —— 后台测试任务的进度 ——
        # 和工作线程 + 主线程轮询（普通 threading.Thread 没有 Qt 事件循环，
        # QTimer.singleShot 从那边回调根本不会触发）
        self._test = {"running": False, "ok": None, "msg": ""}
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(200)
        self._test_timer.timeout.connect(self._poll_test)

        self._load_ai_config()

        # ───────────────── 底部 ─────────────────
        self.version = QLabel(f"FocusTimer v{__version__}")
        self.version.setObjectName("version")
        root.addWidget(self.version)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ────────────────────────── 专注节奏 ──────────────────────────

    def _refresh_hint(self) -> None:
        f, b = self.sp_focus.value(), self.sp_break.value()
        self.hint.setText(
            f"节奏：专注 {f} 分钟 → 休息 {b} 分钟 → 再专注 {f} 分钟……\n"
            f"一个完整周期 {f + b} 分钟。改完从下次开始生效。"
        )

    # ────────────────────────── AI 配置 ──────────────────────────

    def _load_ai_config(self) -> None:
        """把现有的 AI 配置填进界面。没配过就用环境变量里的值兜底。"""
        file_cfg = config.load_ai_config()
        base, key, model = config.ai_settings()

        self.ed_url.setText(base)
        self.ed_model.setText(model)
        self.ed_key.setText(key)

        preset = file_cfg.get("preset", "")
        if preset not in config.PRESETS:
            # 没有预设记录 —— 看当前的值是不是正好等于某个预设
            preset = config.PRESET_CUSTOM
            for k, p in config.PRESETS.items():
                if base == p["base_url"] and model == p["model"]:
                    preset = k
                    break

        idx = self.cb_preset.findData(preset)
        self.cb_preset.setCurrentIndex(idx if idx >= 0 else self.cb_preset.count() - 1)
        self._sync_preset_fields()
        self._refresh_where()

    def _sync_preset_fields(self) -> None:
        """选预设就把地址和模型锁成预设值；选自定义才让填。"""
        key = self.cb_preset.currentData()
        is_custom = key == config.PRESET_CUSTOM

        self.ed_url.setEnabled(is_custom)
        self.ed_model.setEnabled(is_custom)

        if is_custom:
            self.lb_note.setText("自己填接口地址和模型名（任何 OpenAI 兼容接口都可以）。")
            return

        p = config.PRESETS[key]
        self.ed_url.setText(p["base_url"])
        self.ed_model.setText(p["model"])
        self.lb_note.setText(p.get("note", ""))

    def _on_preset_changed(self) -> None:
        self._sync_preset_fields()
        self.lb_test.setText("")

    def _on_toggle_key(self, shown: bool) -> None:
        self.ed_key.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.btn_show.setText("隐藏" if shown else "显示")

    def _refresh_where(self) -> None:
        self.lb_where.setText(
            f"配置保存在：{config.ai_config_path()}\n"
            f"Key 以明文存在这个文件里，别把这个文件发给别人，也不要提交到 Git。"
        )

    # ────────────────────────── 测试连接 ──────────────────────────

    def _on_test(self) -> None:
        if self._test["running"]:
            return
        if not self.ed_key.text().strip():
            self._set_test("还没填 API Key", ok=False)
            return

        self._test = {"running": True, "ok": None, "msg": ""}
        state = self._test
        self.btn_test.setEnabled(False)
        self._set_test("正在测试……", ok=None)
        self._test_timer.start()

        base_url = self.ed_url.text().strip()
        api_key = self.ed_key.text().strip()
        model = self.ed_model.text().strip()

        def work():
            ok, msg = llm_svc.test_connection(base_url, api_key, model)
            state["ok"] = ok
            state["msg"] = msg
            state["running"] = False

        threading.Thread(target=work, daemon=True).start()

    def _poll_test(self) -> None:
        if self._test["running"]:
            return
        self._test_timer.stop()
        self.btn_test.setEnabled(True)
        self._set_test(self._test["msg"], ok=self._test["ok"])

    def _set_test(self, msg: str, ok: bool | None) -> None:
        self.lb_test.setText(msg)
        self.lb_test.setObjectName(
            "ok" if ok is True else "bad" if ok is False else "note"
        )
        # 改了 objectName 必须重新应用样式表，否则颜色不刷新
        self.lb_test.style().unpolish(self.lb_test)
        self.lb_test.style().polish(self.lb_test)

    # ────────────────────────── 保存 ──────────────────────────

    def _on_save(self) -> None:
        # 1) AI 配置 → ai_config.json
        preset = self.cb_preset.currentData()
        try:
            config.save_ai_config(
                self.ed_url.text(),
                self.ed_key.text(),
                self.ed_model.text(),
                "" if preset == config.PRESET_CUSTOM else preset,
            )
        except OSError as e:
            QMessageBox.warning(
                self, "AI 配置没能保存",
                f"写不了 {config.ai_config_path()}：\n{e}\n\n"
                f"（如果程序装在 Program Files 这类需要管理员权限的目录，"
                f"把程序换个位置就行了）",
            )
            return

        # 2) 专注节奏 → 数据库
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
