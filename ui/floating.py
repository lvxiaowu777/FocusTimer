"""
悬浮窗 —— 常驻桌面的正方形小窗（浅色）。

用 PyQt5 而不是 PySide6：
    Anaconda 自带的 msvcp140.dll（2024-09, 575KB）比系统的新版（2026-05, 643KB）旧，
    它的 Qt5 DLL 在搜索路径里排前面，导致 PySide6 的 Qt6 DLL 加载失败
    （报错「找不到指定的程序」）。PyQt5 已装且可用，能力完全够。

要点：
  · 无边框 + 永远置顶 + 不占任务栏（Qt.Tool）
  · 半透明背景，用 paintEvent 手绘圆角
  · 按住任意位置拖动
  · 单击 = 开始 / 暂停 / 继续；双击 = 开关学习面板；右键 = 菜单
  · 休息时底下多一个「跳过休息」按钮
  · 写总结时（pending_review）大数字换成「已计入的学习时长」，只涨不跌

单独预览：
    python ui/floating.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QPushButton, QWidget

from config import WINDOW_SIZE

# —— 浅色配色 ——
BG          = QColor(255, 255, 255, 236)   # 白底，微透
BORDER      = QColor(0, 0, 0, 26)          # 淡描边
TEXT_MAIN   = QColor(31, 41, 55)           # 深灰，数字
TEXT_SUB    = QColor(107, 114, 128)        # 次级文字
TEXT_DIM    = QColor(156, 163, 175)        # 暂停时的灰
ACCENT      = QColor(59, 130, 246)         # 蓝，专注进度弧
ACCENT_DIM  = QColor(191, 219, 254)        # 淡蓝，弧底
BREAK       = QColor(34, 197, 94)          # 绿，休息进度弧
BREAK_DIM   = QColor(187, 247, 208)        # 淡绿，休息弧底


class FloatingTimer(QWidget):
    """正方形悬浮计时窗。"""

    # 对外事件（主程序接）
    started = pyqtSignal()
    paused = pyqtSignal()
    resumed = pyqtSignal()
    stop_requested = pyqtSignal()
    review_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    dashboard_requested = pyqtSignal()
    skip_break_requested = pyqtSignal()

    # 单击延迟多久才执行（毫秒）。
    # 为什么要有这个延迟：Qt 的双击序列是「按下→抬起→双击→抬起」，
    # 直接抬起就动作的话，双击的第一下会先把「开始/暂停」做掉，
    # 学习面板根本轮不到打开。所以单击等一下，确认没有第二下再执行。
    # 250ms 是人感觉不到、又能稳定区分双击的折中（Windows 默认双击间隔是 500ms）。
    CLICK_DELAY_MS = 250

    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                      # 不显示在任务栏
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WINDOW_SIZE, WINDOW_SIZE)
        self.setWindowTitle("专注计时器")

        # —— 状态 ——
        self.state = "idle"                # idle | running | paused | pending_review
        self.category_name = "RAG"
        self.total_hours = 12.5            # 该类别累计小时
        self.phase = "focus"               # focus | break
        self.phase_remaining = 25 * 60     # 本阶段还剩多少秒
        self.phase_total = 25 * 60         # 本阶段总共多少秒
        self._elapsed = 0                  # 已专注总秒数（给提示用）
        self._study = 0                    # 写总结时显示：已计入的学习时长
        self._drag_offset = None           # QPoint | None
        self._drag_start = None            # 用来区分「点击」和「拖动」
        self._suppress_click = False       # 双击之后那次「抬起」要吞掉

        # 休息时冒出来的「跳过休息」。
        # 用真的 QPushButton 而不是自己在 paintEvent 里画 —— 它自己就带命中测试，
        # 而且按在按钮上的那一下不会冒泡到父窗口，也就不会误触发「暂停」。
        self.btn_skip = QPushButton("跳过休息", self)
        self.btn_skip.setCursor(Qt.PointingHandCursor)
        self.btn_skip.setFixedSize(74, 21)
        self.btn_skip.move((WINDOW_SIZE - 74) // 2, 120)
        self.btn_skip.setStyleSheet(
            "QPushButton {"
            "  background:#dcfce7; color:#15803d; border:none; border-radius:10px;"
            "  font-family:'Microsoft YaHei UI'; font-size:10px;"
            "}"
            "QPushButton:hover { background:#bbf7d0; }"
            "QPushButton:pressed { background:#86efac; }"
        )
        self.btn_skip.clicked.connect(self.skip_break_requested.emit)
        self.btn_skip.hide()

        # 单击延迟执行 —— 见 CLICK_DELAY_MS 的说明
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(self.CLICK_DELAY_MS)
        self._click_timer.timeout.connect(self._on_click)

        # 每秒重绘（只刷新显示，真实时长由时间戳算）
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.update)
        self._tick.start(1000)

    # ────────────────────────── 外部接口 ──────────────────────────

    def set_state(self, state: str) -> None:
        self.state = state
        self._sync_skip_button()
        self.update()

    def set_elapsed(self, seconds: int) -> None:
        """已专注总秒数（用于判断周期位置，不直接显示）。"""
        self._elapsed = max(0, seconds)
        self.update()

    def set_phase(self, phase: str, remaining: int, total: int) -> None:
        """当前阶段：focus（专注）/ break（休息），及本阶段剩余与总时长。"""
        self.phase = phase
        self.phase_remaining = max(0, remaining)
        self.phase_total = max(1, total)
        self._sync_skip_button()
        self.update()

    def set_study_seconds(self, seconds: int) -> None:
        """写总结期间的大数字：已计入的学习时长（只涨不跌）。"""
        self._study = max(0, seconds)
        self.update()

    def _sync_skip_button(self) -> None:
        """只有「正在休息」才让这个按钮露脸；暂停中不给跳（先继续再说）。"""
        self.btn_skip.setVisible(self.state == "running" and self.phase == "break")

    def set_category(self, name: str, total_hours: float) -> None:
        self.category_name = name
        self.total_hours = total_hours
        self.update()

    # ────────────────────────── 绘制 ──────────────────────────

    def _fmt(self) -> str:
        m, s = divmod(int(self.phase_remaining), 60)
        return "%02d:%02d" % (m, s)

    def _fmt_study(self) -> str:
        """学习时长：不到 1 小时 MM:SS，过了 1 小时 H:MM:SS。"""
        t = int(self._study)
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else "%02d:%02d" % (m, s)

    def _accent(self) -> tuple[QColor, QColor]:
        """(进度弧颜色, 弧底颜色) —— 专注蓝，休息绿。"""
        if self.phase == "break":
            return BREAK, BREAK_DIM
        return ACCENT, ACCENT_DIM

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pad = 6
        rect = QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2)

        # —— 底盘：圆角 + 淡边 ——
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        p.fillPath(path, BG)
        p.setPen(BORDER)
        p.drawPath(path)

        # —— 写总结中：计时还在跑，但周期计数已经停了 ——
        # 不画进度弧，大数字换成「已计入的学习时长」往上走，一眼能看出没停。
        if self.state == "pending_review":
            f = QFont("Consolas")
            f.setPixelSize(26 if self._study >= 3600 else 30)
            f.setBold(True)
            p.setFont(f)
            p.setPen(ACCENT)
            p.drawText(rect.adjusted(0, -14, 0, -14), Qt.AlignCenter, self._fmt_study())

            f2 = QFont("Microsoft YaHei UI")
            f2.setPixelSize(10)
            p.setFont(f2)
            p.setPen(TEXT_SUB)
            p.drawText(
                rect.adjusted(0, 34, 0, 12), Qt.AlignCenter,
                "总结中 · %s" % self.category_name,
            )

            p.setPen(ACCENT)
            p.drawText(rect.adjusted(0, 62, 0, 0), Qt.AlignHCenter | Qt.AlignTop, "计时继续")
            p.end()
            return

        # —— 进度弧（计时中/暂停时画）——
        if self.state in ("running", "paused"):
            inset = pad + 10
            arc_rect = QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2)

            arc_color, dim_color = self._accent()

            p.setPen(Qt.NoPen)
            p.setBrush(dim_color)
            p.drawEllipse(arc_rect)

            done = self.phase_total - self.phase_remaining
            ratio = min(1.0, max(0.0, done / self.phase_total))
            pen = p.pen()
            pen.setWidth(4)
            pen.setCapStyle(Qt.RoundCap)
            # 暂停时弧变淡，但颜色仍能看出是专注还是休息
            pen.setColor(dim_color if self.state == "paused" else arc_color)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            # Qt 角度单位是 1/16 度，从 12 点方向顺时针
            p.drawArc(arc_rect, 90 * 16, -int(360 * 16 * ratio))

        # —— 主数字 ——
        f = QFont("Consolas")
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT_DIM if self.state == "paused" else TEXT_MAIN)
        p.drawText(rect.adjusted(0, -14, 0, -14), Qt.AlignCenter, self._fmt())

        # —— 副标题：阶段 · 类别 ——
        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(10)
        p.setFont(f2)
        p.setPen(TEXT_SUB)
        if self.state in ("running", "paused"):
            head = "休息" if self.phase == "break" else "专注"
            sub = "%s · %s" % (head, self.category_name)
        else:
            sub = "%s · %gh" % (self.category_name, self.total_hours)
        p.drawText(rect.adjusted(0, 34, 0, 12), Qt.AlignCenter, sub)

        # —— 状态提示 ——
        f3 = QFont("Microsoft YaHei UI")
        f3.setPixelSize(10)
        p.setFont(f3)
        # pending_review 在上面就 return 了，到不了这儿
        hint = {"idle": ("点击开始", TEXT_SUB),
                "paused": ("已暂停", TEXT_SUB)}.get(self.state)
        if hint:
            p.setPen(hint[1])
            p.drawText(rect.adjusted(0, 62, 0, 0), Qt.AlignHCenter | Qt.AlignTop, hint[0])

        p.end()

    # ────────────────────────── 交互 ──────────────────────────

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self._drag_start = event.globalPos()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        moved = (
            self._drag_start is not None
            and (event.globalPos() - self._drag_start).manhattanLength() > 4
        )
        self._drag_offset = None
        self._drag_start = None
        # 双击之后的收尾「抬起」，什么都不做（双击已经在上面处理过了）
        if self._suppress_click:
            self._suppress_click = False
            return
        # 拖动过就不算点击（避免拖完手一松误触发）
        if not moved:
            self._click_timer.start()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        self._click_timer.stop()      # 撤销挂起的那次单击
        self._drag_offset = None
        self._drag_start = None
        self._suppress_click = True   # 吞掉紧随其后的第二次「抬起」
        self.dashboard_requested.emit()

    def contextMenuEvent(self, event):  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:4px;}"
            "QMenu::item{padding:6px 18px;border-radius:5px;font-size:12px;color:#1f2937;}"
            "QMenu::item:selected{background:#eff6ff;color:#1d4ed8;}"
        )
        if self.state == "idle":
            menu.addAction(QAction("开始专注", self, triggered=self.started.emit))
        elif self.state == "pending_review":
            # 已经点过结束了 —— 这时候再来一次「结束」只会报错，给个回去补写的入口
            menu.addAction(QAction("✍ 继续写总结", self, triggered=self.review_requested.emit))
        else:
            menu.addAction(QAction("结束并写总结", self, triggered=self.stop_requested.emit))
        menu.addSeparator()
        # ★ 学习面板的**正门**。以前只能双击悬浮窗，关掉之后根本找不到 —— 见开发文档踩坑记录。
        menu.addAction(QAction("📋 学习面板", self, triggered=self.dashboard_requested.emit))
        menu.addAction(QAction("⚙ 设置", self, triggered=self.settings_requested.emit))
        menu.addSeparator()
        menu.addAction(QAction("退出", self, triggered=QApplication.quit))
        menu.exec_(event.globalPos())

    def _on_click(self) -> None:
        if self.state == "idle":
            self.started.emit()
        elif self.state == "running":
            self.paused.emit()
        elif self.state == "paused":
            self.resumed.emit()
        elif self.state == "pending_review":
            self.review_requested.emit()


# ────────────────────────── 单独预览 ──────────────────────────

def _preview() -> None:
    app = QApplication(sys.argv)
    w = FloatingTimer()

    def on_start():
        w.set_state("running")
        demo_start = datetime.now()

        from db.database import SessionLocal
        from services import settings as settings_svc

        def tick():
            el = int((datetime.now() - demo_start).total_seconds())
            with SessionLocal() as db:
                cfg = settings_svc.get_all(db)
            phase, remain, total = settings_svc.phase_at(
                el, cfg["focus_minutes"], cfg["break_minutes"]
            )
            w.set_phase(phase, remain, total)

        t = QTimer()
        t.timeout.connect(tick)
        t.start(1000)
        tick()
        w._demo_timer = t                      # 挂住，别被回收

    w.started.connect(on_start)
    w.paused.connect(lambda: w.set_state("paused"))
    w.resumed.connect(lambda: w.set_state("running"))
    w.stop_requested.connect(lambda: w.set_state("pending_review"))

    w.show()
    print("预览已启动 —— 单击开始/暂停，右键出菜单")
    sys.exit(app.exec_())


if __name__ == "__main__":
    _preview()
