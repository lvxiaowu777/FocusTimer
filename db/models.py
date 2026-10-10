"""
数据模型 —— 3 张表：category / session / item

设计要点（详见 规划文档 §4）：
  1. status 状态机 = 强制回顾的执行者，不写总结就进不了 completed
  2. 计时靠时间戳现算，不靠累加 —— 休眠/卡顿/关工具都不漂
  3. 暂停用 paused_seconds + paused_at 两个字段记录，不建额外表
  4. 类别总时间现算，不存冗余字段
  5. 学习时长**不含休息**；跳过休息 = 给时间线加一个偏移量（见 开发文档 §5.9）
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有模型的基类。"""


class SessionStatus(str, enum.Enum):
    """一次专注的生命周期。"""

    running = "running"                 # 计时中
    paused = "paused"                   # 已暂停
    pending_review = "pending_review"   # 已停止，等着写总结（强制回顾的卡点）
    completed = "completed"             # 已写完总结，正式归档


class Category(Base):
    """类别 —— 「RAG」「Agent」「机器学习」。界面只显示它的总时间。"""

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 置顶：钉住的排在前面（排序时 is_pinned DESC, sort_order ASC）
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
    # 二级目录：NULL = 顶层。
    # 父类别只用来分组、不能直接拿来计时，它的时长是「自己 + 所有子类别」的合计
    # （见 services/timer.category_tree）。
    # 刻意**不建** ORM 的 parent/children 自引用关系：自引用关系在脱管对象上很容易
    # 踩懒加载的坑，而树只有两级，显式查询反而更清楚。
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    sessions: Mapped[list["FocusSession"]] = relationship(back_populates="category")
    items: Mapped[list["Item"]] = relationship(back_populates="category")

    __table_args__ = (
        Index("idx_category_parent", "parent_id"),
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r} parent={self.parent_id}>"


class FocusSession(Base):
    """
    一次专注记录。

    表名用 session（跟规划文档一致），类名加 Focus 前缀避免和 SQLAlchemy 的 Session 混淆。

    时长计算：
        实时显示 = (now - start_time) - paused_seconds - (paused_at ? now - paused_at : 0)
        最终存档 = services.timer.study_seconds() —— 上面那个再扣掉休息段，
                   加上写总结的时间（所以 end_time 到 submit_review 才写）
    """

    __tablename__ = "session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("category.id"), nullable=False
    )

    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # —— 暂停 ——
    paused_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # —— 结果 ——
    # —— 跳过休息 ——
    # 累加「跳掉的休息秒数」。阶段是 elapsed % 周期 现算的，想让时间线往前
    # 跳一段就只能记一个偏移量，不能去改 start_time（那会把真实时长也改掉）。
    skipped_seconds: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )

    # —— 写总结 ——
    # 点「结束」的那一刻。写总结期间计时继续、这段时间也算学习时长；
    # 但换算休息段时要按这个点截断 —— 写总结不算休息（见 services/timer.study_seconds）。
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # —— AI 整理后的段落 ——
    # 归档时由 AI 把 raw_text 整理成通顺的话（见 services/llm.py）。
    # NULL = 还没整理过（AI 没配 / 调用失败 / 早于本字段的历史记录），
    # 界面这时回退显示 raw_text 原文。
    polished_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=True),
        default=SessionStatus.running,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    category: Mapped["Category"] = relationship(back_populates="sessions")
    items: Mapped[list["Item"]] = relationship(back_populates="session")

    __table_args__ = (
        # ★ 强制回顾的最后一道闸：不写总结，数据库层面就是进不了 completed
        CheckConstraint(
            "status <> 'completed' OR (raw_text IS NOT NULL AND raw_text <> '')",
            name="chk_review_required",
        ),
        Index("idx_session_category", "category_id"),
        Index("idx_session_status", "status"),
        Index("idx_session_start", "start_time"),
    )

    # —— 便捷属性 ——

    @property
    def elapsed_seconds(self) -> int:
        """当前已专注秒数（暂停期间冻结）。"""
        now = datetime.now()
        total = (now - self.start_time).total_seconds() - self.paused_seconds
        if self.paused_at is not None:
            total -= (now - self.paused_at).total_seconds()
        return max(0, int(total))

    @property
    def cycle_elapsed_seconds(self) -> int:
        """
        拿来推算阶段的「虚拟时长」= 真实时长 + 跳过的休息。

        跳过休息不改时钟，只让周期计数提前走 —— 所以推算阶段、判断
        「现在是不是休息」都用这个，真实时长还是看 elapsed_seconds。
        """
        return self.elapsed_seconds + self.skipped_seconds

    def __repr__(self) -> str:
        return f"<FocusSession id={self.id} status={self.status.value} elapsed={self.elapsed_seconds}s>"


class Setting(Base):
    """
    偏好设置，键值对。

    只存「跟用户有关、可能改」的东西：专注时长、休息时长。
    不放业务数据。
    """

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)

    def __repr__(self) -> str:
        return f"<Setting {self.key}={self.value!r}>"


class Item(Base):
    """
    关键词 —— AI 从某次专注的总结里提炼出来的名词性短语。

    从「挂在类别下的独立清单」改成了「挂在某次专注下的关键词」：
    看板现在是按段落展示，关键词展示在对应那一段下面（见 ui/dashboard.py）。

    session_id 为 NULL = 早于本字段的历史数据（当时条目只挂类别、没挂某次专注），
    这些历史关键词不再展示，但数据一条都没删。
    """

    __tablename__ = "item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    category: Mapped["Category"] = relationship(back_populates="items")
    session: Mapped["FocusSession | None"] = relationship(back_populates="items")

    __table_args__ = (
        Index("idx_item_category", "category_id"),
        Index("idx_item_session", "session_id"),
    )

    def __repr__(self) -> str:
        return f"<Item id={self.id} name={self.name!r}>"
