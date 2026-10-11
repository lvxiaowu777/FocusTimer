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
from ui import theme

QSS_TEMPLATE = """
QDialog { background: #ffffff; }
QLabel { color: INK_SOFT; font-size: 12.5px; }
QLabel#title { font-size: 14px; font-weight: 600; color: INK; }
QLabel#hint  { color: FAINT; font-size: 11.5px; }
QLabel#version { color: GHOST; font-size: 10.5px; }
QLabel#sect { font-size: 12.5px; font-weight: 600; color: INK; }
QLabel#note { color: FAINT; font-size: 11.5px; }
QLabel#ok   { color: OK; font-size: 11.5px; font-weight: 600; }
QLabel#bad  { color: BAD; font-size: 11.5px; font-weight: 600; }
QCheckBox { color: INK_SOFT; font-size: 12.5px; spacing: 8px; }
QSpinBox, QLineEdit, QComboBox {
    border: 1px solid LINE_SOFT; border-radius: 7px;
    padding: 5px 8px; font-size: 12.5px; color: INK;
    background: #ffffff; min-height: 18px;
}
QSpinBox { min-width: 84px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: ACCENT; background: #ffffff;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: GHOST; background: BG_SOFT;
}
QComboBox::drop-down { border: none; width: 18px; }
QPushButton {
    border: 1px solid LINE_SOFT; border-radius: 8px;
    padding: 8px 18px; font-size: 12.5px; color: INK_SOFT; background: #fff;
}
QPushButton:hover { background: BG_SOFT; border-color: GHOST; }
QPushButton:disabled { color: GHOST; background: #ffffff; border-color: LINE; }
QPushButton#primary {
    background: ACCENT; color: #fff; border: 1px solid ACCENT; font-weight: 600;
}
QPushButton#primary:hover { background: ACCENT_HOVER; border-color: ACCENT_HOVER; }
QPushButton#small { padding: 5px 13px; font-size: 11.5px; }
QFrame#sep { background: LINE; max-height: 1px; border: none; }
"""


def build_qss() -> str:
    return theme.styled(QSS_TEMPLATE)


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setStyleSheet(build_qss())
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

        # ───────────────── 显示 ─────────────────
        disp_title = QLabel("显示")
        disp_title.setObjectName("title")
        root.addWidget(disp_title)

        disp_form = QFormLayout()
        disp_form.setSpacing(10)

        # 打开时的档位 —— 点「取消」要能还原回去
        self._font_saved = int(cfg.get("font_scale", theme.FONT_DEFAULT))

        self.cb_font = QComboBox()
        for pct, label in theme.FONT_PRESETS:
            self.cb_font.addItem(f"{label}　（{pct}%）", pct)
        idx = self.cb_font.findData(self._font_saved)
        # 存的值不在预设里（比如手改过数据库）就选最接近的一档
        if idx < 0:
            idx = min(
                range(self.cb_font.count()),
                key=lambda i: abs(self.cb_font.itemData(i) - self._font_saved),
            )
        self.cb_font.setCurrentIndex(idx)
        # ★ 信号必须**在 setCurrentIndex 之后**接。
        #   反过来的话，初始化这一下就会触发 _on_font_preview，
        #   把全局字号改回「数据库里存的值」—— 于是别处刚设的档位被悄悄重置。
        #   （截图验证时踩到的：想渲染 130% 档，一开设置窗口就被打回 100%。）
        self.cb_font.currentIndexChanged.connect(self._on_font_preview)
        disp_form.addRow("界面字号", self.cb_font)

        root.addLayout(disp_form)

        self.lb_font = QLabel("换一档就能看到这个窗口立刻变化；点「保存」后整个界面都跟着变。")
        self.lb_font.setObjectName("hint")
        self.lb_font.setWordWrap(True)
        root.addWidget(self.lb_font)

        sep2 = QFrame()
        sep2.setObjectName("sep")
        root.addWidget(sep2)

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

    # ────────────────────────── 字号 ──────────────────────────

    def _on_font_preview(self) -> None:
        """
        换档时**立刻**在这里预览。

        顺带会让悬浮窗也跟着变 —— 它每秒重绘一次，每次都从 theme 取字号，
        所以不用专门通知它。点「取消」会在 reject() 里改回来，最多晚一秒。
        """
        theme.set_scale(self.cb_font.currentData())
        self.setStyleSheet(build_qss())
        self.adjustSize()

    def reject(self) -> None:  # noqa: D102
        # 取消 = 把预览的字号撤回去，别让界面停在没保存的档位上
        theme.set_scale(self._font_saved)
        super().reject()

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

        # 2) 专注节奏 + 字号 → 数据库
        try:
            with SessionLocal() as db:
                settings_svc.set_many(
                    db,
                    focus_minutes=self.sp_focus.value(),
                    break_minutes=self.sp_break.value(),
                    sound_enabled=1 if self.cb_sound.isChecked() else 0,
                    font_scale=self.cb_font.currentData(),
                )
        except ValueError as e:
            QMessageBox.warning(self, "没能保存", str(e))
            return

        self.accept()
