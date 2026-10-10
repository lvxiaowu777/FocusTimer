"""
专注计时器 —— 主程序。

启动：
    python main.py

数据流：
    点开始 → 选类别 → 计时（时间戳）→ 暂停/继续 → 结束
      → 强制写总结 → 存库 → 后台 DeepSeek 拆条目 → 看板更新
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# ★ 必须在任何会 import cryptography 的模块之前设置。
#   PyInstaller 打包后 OpenSSL 的 legacy provider 找不到，cryptography 会直接崩。
#   MySQL 8 的 caching_sha2_password 认证不需要 legacy 算法，关掉即可。
os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt5.QtCore import Qt, QThread, QTimer
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from config import __version__
from db.database import SessionLocal, init_db
from db.models import SessionStatus
from services import llm as llm_svc
from services import settings as settings_svc
from services import sound as sound_svc
from services import timer as timer_svc
from ui.dashboard import Dashboard, fmt_hours
from ui.floating import FloatingTimer
from ui.review_dialog import ReviewDialog
from ui.settings_dialog import SettingsDialog

# 单实例用的名字（Windows 下是一个命名管道名，必须是 ASCII）
INSTANCE_KEY = "FocusTimer.SingleInstance.v1"


def detach(db, session):
    """
    把 session 从 db 里摘出来，同时**预先读好 category 名字**。

    ⚠️ 为什么必须这么做：
    db.expunge() 之后对象就「脱管」了，再访问 session.category 会触发懒加载，
    而脱管对象没有 session 可查 → DetachedInstanceError 直接崩。
    所以必须在还连着数据库的时候把要用的字段读成普通属性。
    """
    if session is None:
        return None
    session._category_name = session.category.name if session.category else "未分类"
    db.expunge(session)
    return session


# ────────────────────────── 选类别 ──────────────────────────

class CategoryPicker(QDialog):
    """开始前选一个类别。1 秒的事。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("学哪个方向？")
        self.setMinimumWidth(260)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setStyleSheet("""
            QDialog { background:#fff; }
            QLabel { color:#6b7280; font-size:12px; }
            QListWidget { border:1px solid #e5e7eb; border-radius:8px; font-size:13px;
                          color:#1f2937; outline:none; padding:4px; }
            QListWidget::item { padding:9px 10px; border-radius:6px; }
            QListWidget::item:selected { background:#eff6ff; color:#1d4ed8; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(QLabel("选一个类别，开始计时"))

        self.list = QListWidget()
        with SessionLocal() as db:
            self._cats = timer_svc.category_totals(db)
        for cat, total in self._cats:
            item = QListWidgetItem(f"{cat.name}    ·    {fmt_hours(total)}")
            item.setData(Qt.UserRole, cat.id)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None


# ────────────────────────── 主应用 ──────────────────────────

class FocusApp:
    def __init__(self) -> None:
        self.win = FloatingTimer()
        self.dashboard = Dashboard()
        self.active = None                  # 当前在跑的 FocusSession（内存副本）

        # 悬浮窗信号
        self.win.started.connect(self.on_start)
        self.win.paused.connect(self.on_pause)
        self.win.resumed.connect(self.on_resume)
        self.win.stop_requested.connect(self.on_stop)
        self.win.review_requested.connect(self.on_review_pending)
        self.win.settings_requested.connect(self.on_settings)
        self.win.dashboard_requested.connect(self.toggle_dashboard)
        self.win.skip_break_requested.connect(self.on_skip_break)

        # 偏好设置（缓存，避免每秒查库）
        with SessionLocal() as db:
            self.cfg = settings_svc.get_all(db)

        # 上一次看到的阶段。None = 还没接上，第一次只记录不响铃。
        self._last_phase: str | None = None

        # 后台整理完成 → 置这个标记，主线程每秒的 tick 里看到就刷新看板。
        # 不在工作线程里直接调 dashboard.refresh()：Qt 的界面只能在主线程动。
        self._dashboard_dirty = False

        # 每秒刷新倒计时（真实时长由时间戳算，这里只是重绘）
        self.tick = QTimer()
        self.tick.timeout.connect(self.refresh_display)
        self.tick.start(1000)

        self.restore_state()

    # ────────────────────────── 状态恢复 ──────────────────────────

    def restore_state(self) -> None:
        """关掉工具再打开 —— 把「还在跑」的接回来。"""
        with SessionLocal() as db:
            # 上次是在写总结时被强杀的话，先把那条的时长定死再往下走。
            # 否则停机那段时间会被算进学习时长 —— 隔夜没关就凭空多十几小时。
            timer_svc.settle_pending(db)

            active = timer_svc.get_active(db)
            pending = timer_svc.get_pending_review(db)
            if active:
                self.active = detach(db, active)
            if pending:
                # 只是判断有没有，不进 UI 就不用摘
                pending = pending.id

        self._last_phase = None      # 刚接上，别把当前阶段当成「刚切换」而误响

        if self.active:
            self._sync_window_from_active()
        elif pending:
            # 有笔欠着的总结
            self.win.set_state("pending_review")
            self.win.set_category("待回顾", 0)
        else:
            self._set_idle()

        self.refresh_display()

    def _sync_window_from_active(self) -> None:
        with SessionLocal() as db:
            totals = dict((c.id, t) for c, t in timer_svc.category_totals(db))
        self.win.set_state(self.active.status.value)
        self.win.set_category(
            self.active._category_name, totals.get(self.active.category_id, 0) / 3600
        )

    def _set_idle(self) -> None:
        with SessionLocal() as db:
            totals = timer_svc.category_totals(db)
        if totals:
            cat, total = totals[0]
            self.win.set_category(cat.name, total / 3600)
        else:
            self.win.set_category("未分类", 0)
        self.win.set_state("idle")
        self.win.set_elapsed(0)
        self.active = None
        self._last_phase = None

    # ────────────────────────── 显示刷新 ──────────────────────────

    def _study_seconds(self, session) -> int:
        """
        这条记录现在攒了多少学习时长。

        已经定死过的（duration_seconds 有值）就直接用那个数 —— 跳也跳不动了；
        否则现算。纯内存，不查库，写总结弹窗上每秒跳的就是它。
        """
        if session.duration_seconds is not None:
            return int(session.duration_seconds)
        return timer_svc.study_seconds(
            session, self.cfg["focus_minutes"], self.cfg["break_minutes"]
        )

    def refresh_display(self) -> None:
        """每秒刷新 —— 阶段和剩余时间都由「总时长 + 设置」算出来，不用存。"""
        # 后台整理完成 → 在这一帧刷新看板（界面只能在主线程动，所以走这个标记）
        if self._dashboard_dirty:
            self._dashboard_dirty = False
            if self.dashboard.isVisible():
                self.dashboard.refresh()

        if self.active is None:
            return

        # 写总结中：计时照跑，但周期计数已经停了，大数字换成累计学习时长
        if self.active.status == SessionStatus.pending_review:
            self.win.set_study_seconds(self._study_seconds(self.active))
            return

        if self.active.status not in (SessionStatus.running, SessionStatus.paused):
            return

        elapsed = self.active.cycle_elapsed_seconds
        self.win.set_elapsed(elapsed)
        phase, remain, total = settings_svc.phase_at(
            elapsed, self.cfg["focus_minutes"], self.cfg["break_minutes"]
        )

        # ── 阶段切换 → 响一声 ──
        # _last_phase is None 表示「刚接上」（刚启动 / 刚开始一轮），只记录不响，
        # 否则每次开工具、点开始都会白响一声。
        if self._last_phase is not None and phase != self._last_phase:
            self._announce_phase(phase)
        self._last_phase = phase

        self.win.set_phase(phase, remain, total)

        # —— 暂停中：把两个数推给悬浮窗 ——
        #   本次 = now − paused_at（这一秒还在涨）
        #   累计 = 之前几次已经结算掉的 paused_seconds + 本次
        if self.active.status == SessionStatus.paused and self.active.paused_at is not None:
            current = max(0, int((datetime.now() - self.active.paused_at).total_seconds()))
            self.win.set_pause_seconds(
                current, max(0, int(self.active.paused_seconds or 0)) + current
            )

    def _announce_phase(self, phase: str) -> None:
        """切到新阶段了 —— 响一声。"""
        if not self.cfg.get("sound_enabled", 1):
            return
        # phase 是「刚进入」的阶段：进了 break 说明专注结束，反之亦然
        sound_svc.play("focus_end" if phase == "break" else "break_end")

    def on_settings(self) -> None:
        """右键 → 设置。改完立刻生效。"""
        dlg = SettingsDialog(self.win)
        if dlg.exec_() == QDialog.Accepted:
            with SessionLocal() as db:
                self.cfg = settings_svc.get_all(db)
            # 改设置 = 重新划分整条时间线，阶段可能**原地跳变**。
            # 这不算真的切换，静默重算（_last_phase=None 让下一帧只记录不响铃）。
            self._last_phase = None
            self.refresh_display()      # 马上按新设置重画

    # ────────────────────────── 动作 ──────────────────────────

    def on_start(self) -> None:
        picker = CategoryPicker()
        if picker.exec_() != QDialog.Accepted:
            return
        cid = picker.selected_id()
        if cid is None:
            return

        with SessionLocal() as db:
            self.active = detach(db, timer_svc.start(db, cid))

        self._last_phase = None      # 新一轮：从 0 秒开始，第一帧只记录不响铃
        self._sync_window_from_active()

    def on_pause(self) -> None:
        if not self.active:
            return
        with SessionLocal() as db:
            self.active = detach(db, timer_svc.pause(db, self.active.id))
        self.win.set_state("paused")
        self.refresh_display()       # 立刻显示 00:00，别等下一秒的 tick

    def on_resume(self) -> None:
        if not self.active:
            return
        with SessionLocal() as db:
            self.active = detach(db, timer_svc.resume(db, self.active.id))
        self.win.set_state("running")
        self.refresh_display()

    def on_skip_break(self) -> None:
        """休息中点「跳过休息」—— 时间线直接跳到下一个专注段开头。"""
        if not self.active:
            return
        try:
            with SessionLocal() as db:
                self.active = detach(db, timer_svc.skip_break(
                    db, self.active.id,
                    self.cfg["focus_minutes"], self.cfg["break_minutes"],
                ))
        except ValueError as e:
            print("[timer] 跳过休息失败:", e)
            return
        # 这是用户主动跳的，不是「阶段自己走到头了」，别把提示音响一下
        self._last_phase = None
        self.refresh_display()

    def on_stop(self) -> None:
        if not self.active:
            return
        try:
            with SessionLocal() as db:
                s = detach(db, timer_svc.stop(
                    db, self.active.id,
                    self.cfg["focus_minutes"], self.cfg["break_minutes"],
                ))
        except ValueError as e:
            QMessageBox.warning(self.win, "没能结束", str(e))
            return
        self.active = s
        self._ask_review(s)

    def on_review_pending(self) -> None:
        """点「写总结」—— 把欠着的那条捞出来补写。"""
        with SessionLocal() as db:
            s = timer_svc.get_pending_review(db)
            if not s:
                self._set_idle()
                return
            s = detach(db, s)
        if s.review_started_at is not None:
            self.active = s      # 还在计时的接回来，悬浮窗接着往上跳
        self._ask_review(s)

    # ────────────────────────── 强制回顾 ──────────────────────────

    def _ask_review(self, session) -> None:
        dlg = ReviewDialog(
            category_name=session._category_name,
            seconds_provider=lambda: self._study_seconds(session),
        )
        if dlg.exec_() != QDialog.Accepted:
            self._confirm_abandon(session)
            return

        text = dlg.get_text()
        try:
            with SessionLocal() as db:
                timer_svc.submit_review(
                    db, session.id, text,
                    self.cfg["focus_minutes"], self.cfg["break_minutes"],
                )
        except Exception as e:
            QMessageBox.critical(None, "保存失败", str(e))
            return

        # ★ 总结已经安全落库 —— 下面 AI 整理失败也不影响记录
        self.active = None
        self._polish_in_background(session.id, session._category_name, text)

        # 顺手重算窗口显示
        with SessionLocal() as db:
            totals = dict((c.id, t) for c, t in timer_svc.category_totals(db))
        self.win.set_category(session._category_name, totals.get(session.category_id, 0) / 3600)
        self.win.set_state("idle")
        self.win.set_elapsed(0)

    def _confirm_abandon(self, session) -> None:
        """
        用户点了「放弃本次记录」。

        「放弃」= **真删掉**，时长也不计入 —— 没写总结就是没学到东西，
        不该算学习时长。所以这里必须问清楚，不能默默挂着一个永不完成的记录。
        """
        box = QMessageBox(self.win)
        box.setWindowTitle("放弃本次记录")
        box.setText(
            f"这次 {fmt_hours(session.duration_seconds or 0)} 不会计入学习时长，"
            f"也不会留下任何记录。\n\n确定放弃吗？"
        )
        box.setIcon(QMessageBox.Warning)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)     # 默认「否」，防手滑
        box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)

        if box.exec_() == QMessageBox.Yes:
            with SessionLocal() as db:
                timer_svc.abandon(db, session.id)
            self.active = None
            self._set_idle()
        else:
            # 反悔了 —— 回到「待写总结」，但不自动重开弹窗（免得成死循环）
            self.win.set_state("pending_review")

    def _polish_in_background(self, session_id: int, category_name: str, text: str) -> None:
        """
        AI 把总结整理成段落 —— 放后台线程，不卡界面。

        ⚠️ text 必须由调用方传进来，不能从 session 对象上读 `session.raw_text`：
           传进来的 session 是**脱管副本**，它的 raw_text 是在 `stop()` 那一刻加载的，
           那时总结还没提交、值是 None。原实现把 text 当参数传正是这个原因。

        失败不影响记录：原文早就落库了，polished_text 留空，
        看板会显示原文并打上「未整理」标记，用户可以右键单条重新整理，
        或用看板右上角的「批量整理」一次性补。
        """
        import threading

        raw = (text or "").strip()
        if not raw:
            return

        def work():
            paragraph, keywords = llm_svc.polish(raw, category_name)   # 失败返回 ("", [])
            if not paragraph:
                print(f"[ai] id={session_id} 整理失败，保持「未整理」（记录本身已安全保存）")
                return
            with SessionLocal() as db:
                created = timer_svc.set_polish(db, session_id, paragraph, keywords)
            print(
                f"[ai] id={session_id} 整理完成："
                f"段落 {len(paragraph)} 字，关键词 {len(created)} 个"
            )
            self._dashboard_dirty = True

        threading.Thread(target=work, daemon=True).start()

    # ────────────────────────── 学习面板 ──────────────────────────

    def show_dashboard(self) -> None:
        """把学习面板叫出来（已经开着就只是提到最前）。"""
        self.dashboard.refresh()
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def toggle_dashboard(self) -> None:
        """右键「📋 学习面板」/ 双击悬浮窗 —— 开着就收，收着就开。"""
        if self.dashboard.isVisible():
            self.dashboard.hide()
        else:
            self.show_dashboard()

    def on_quit(self) -> None:
        """
        退出前把「还在写总结」的那条时长定死。

        定死的时机不一样：正常退出算到**现在**（写总结的时间照算），
        被强杀的话下次启动只能退回到「点结束那一刻」（见 timer.settle_pending）。
        """
        with SessionLocal() as db:
            timer_svc.settle_pending(db, when=datetime.now())

    def wake_up(self) -> None:
        """
        第二个 exe 被启动了 —— 把窗口都叫回前台。

        为什么要这样：软件没有任务栏图标（悬浮窗是 Qt.Tool），
        看板一关就彻底找不到了，只能靠「再开一次 exe」把界面叫回来。
        """
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()
        self.show_dashboard()

    # ────────────────────────── 单实例 ──────────────────────────

    def listen_for_second_launch(self) -> None:
        """占住 INSTANCE_KEY，等第二个实例来敲门。"""
        # FocusApp 是普通 Python 对象，不是 QObject —— 不能当 parent 传进去，
        # 只能自己拿引用拴住，别让 QLocalServer 被 GC 掉。
        self._server = QLocalServer()
        self._server.newConnection.connect(self._on_second_launch)
        self._server.listen(INSTANCE_KEY)

    def _on_second_launch(self) -> None:
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(conn.deleteLater)
            conn.disconnected.connect(conn.deleteLater)
            conn.readAll()               # 内容不重要，连上就代表「有人又开了 exe」
            conn.disconnectFromServer()
        self.wake_up()

    # ────────────────────────── 运行 ──────────────────────────

    def run(self) -> int:
        self.win.show()
        self.win.move(80, 80)
        self.listen_for_second_launch()
        self.dashboard.show()

        app = QApplication.instance()
        app.aboutToQuit.connect(self.on_quit)
        return app.exec_()


def signal_existing_instance(timeout_ms: int = 400, attempts: int = 6) -> bool:
    """
    已经有实例在跑吗？有就把它叫醒，返回 True。

    不这么做的话，第二次双击 exe 会起一个**新进程** —— 屏幕上就多出一个悬浮窗，
    而旧进程还在后台跑着、还连着同一个数据库，两边各算各的。

    为什么要试 `attempts` 次：老实例要等 `QApplication` 建好、MySQL 连上之后才开始监听，
    这中间有一两秒空窗。用户手快连点两下 exe，第二次探测就会扑空 → 又变成两个实例。
    所以探测失败先别下结论，隔一会儿再问一次。

    没有实例在跑时，第一次探测是**立刻**失败的（Windows 直接报"管道不存在"），
    所以正常启动不会白白等这几秒。
    """
    for i in range(attempts):
        sock = QLocalSocket()
        sock.connectToServer(INSTANCE_KEY)
        if sock.waitForConnected(timeout_ms):
            sock.write(b"wake")
            sock.flush()
            sock.waitForBytesWritten(timeout_ms)
            sock.disconnectFromServer()
            return True
        if i < attempts - 1:
            QThread.msleep(250)
    return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FocusTimer")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)      # 关看板不等于退出

    # ★ 单实例必须在 init_db() 之前 —— 第二个实例不该白连一次数据库
    if signal_existing_instance():
        print("已有实例在跑，已把它叫到前台。")
        return 0

    init_db()

    # 上个实例如果是被强杀的，管道名可能还挂着 → 先清掉再占位
    QLocalServer.removeServer(INSTANCE_KEY)

    focus = FocusApp()
    return focus.run()


if __name__ == "__main__":
    sys.exit(main())
