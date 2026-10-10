"""
计时服务 —— 所有跟「一次专注」有关的数据库操作。

设计核心（见 规划文档 §4.4）：
  · 时长永远用时间戳现算，不累加 —— 休眠/卡顿/关工具都不漂
  · 暂停只记 paused_seconds（累计）+ paused_at（本次起点），不建额外表
  · status 状态机是强制回顾的执行者，想跳过？数据库层面就没这条路
  · 学习时长**不含休息**，但**含写总结的时间**（见 开发文档 §5.9）
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Category, FocusSession, Item, SessionStatus
from services import settings as settings_svc

ACTIVE_STATES = (SessionStatus.running, SessionStatus.paused)


# ────────────────────────── 学习时长 ──────────────────────────

def study_seconds(
    session: FocusSession,
    focus_minutes: int,
    break_minutes: int,
    now: datetime | None = None,
) -> int:
    """
    真正的「学习时长」—— 休息不算，写总结的时间算。

    分两段量：

      1. 计时段 [start_time, review_started_at 或 now)
           真实跨度（减暂停）里，落在休息段的那部分要扣掉。休息段有多少秒
           由 settings.cycle_break_seconds 现算，再减去「跳过休息」提前走掉的部分
           —— 跳过的休息根本没休，不该扣。

      2. 写总结段 [review_started_at, now)
           整段算学习。所以点「结束」之后弹窗写多久都算，不设上限。

    纯内存计算、不查库：写总结弹窗上每秒往上跳的那个数就是它。
    """
    now = now or datetime.now()
    stopped_at = session.review_started_at
    upto = stopped_at or now

    real = max(0.0, (upto - session.start_time).total_seconds() - session.paused_seconds)

    skipped = max(0, session.skipped_seconds)
    virtual = real + skipped
    rested = settings_svc.cycle_break_seconds(virtual, focus_minutes, break_minutes) - skipped
    study = real - max(0.0, rested)

    if stopped_at is not None:
        study += max(0.0, (now - stopped_at).total_seconds())

    return max(0, int(study))


def is_break_now(
    session: FocusSession, focus_minutes: int, break_minutes: int
) -> tuple[str, int]:
    """(阶段, 本阶段剩余秒数) —— 只关心阶段时用这个。"""
    phase, remain, _ = settings_svc.phase_at(
        session.cycle_elapsed_seconds, focus_minutes, break_minutes
    )
    return phase, remain


def rest_seconds(session: FocusSession) -> int:
    """
    这段专注里**落在休息段的秒数** —— 纯派生，不存库、不查库。

    怎么来的（和 study_seconds 的算法严丝合缝）：

        study_seconds 定死时长那一刻算的是
            study = (when − start_time) − paused_seconds − rested
        而 when 就是 end_time，所以反推：
            rested = (end_time − start_time) − paused_seconds − duration_seconds

    ★ 为什么不在 settle 的时候顺手存一列：
      duration_seconds 是**当时按当时的设置**定死的。如果休息时长现算，
      用户以后把「专注 25 分 / 休息 5 分」改成别的，所有历史记录的休息时间
      会跟着一起变 —— 那是篡改历史。而上面这个式子只用已经定死的数，
      算出来永远一致。顺带好处：17 条早于本次改动的历史记录也能直接算出休息时间。

    没结束（end_time / duration_seconds 为空）就返回 0。
    """
    if session.end_time is None or session.duration_seconds is None:
        return 0

    span = (session.end_time - session.start_time).total_seconds()
    rest = span - max(0, int(session.paused_seconds or 0)) - int(session.duration_seconds)
    return max(0, int(rest))


def today_total(db: Session) -> int:
    """
    今天写完总结的学习秒数 —— 看板头部那个「今日累计」。

    两条口径（跟软件其它地方保持一致）：
      · 按 **start_time** 归日，不是 end_time
      · 只算 completed —— 「没写总结 = 没学到东西」，还在跑 / 欠着总结的那条
        不算进来，所以这个数只增不跳
    """
    day_start = datetime.combine(date.today(), time.min)
    day_end = day_start + timedelta(days=1)

    total = db.scalar(
        select(func.coalesce(func.sum(FocusSession.duration_seconds), 0)).where(
            FocusSession.status == SessionStatus.completed,
            FocusSession.start_time >= day_start,
            FocusSession.start_time < day_end,
        )
    )
    return int(total or 0)


# ────────────────────────── 类别（二级目录）──────────────────────────
#
# 层级规则（刻意只允许两级 —— 够用，而且不容易搞乱）：
#   · 顶层类别可以带子类别。带了子类别就是「分组」，**不能直接拿来计时**
#   · 没有子类别的类别 = 叶子，可以计时
#   · 子类别不能再带子类别；带子类别的类别也不能被移进别人下面（否则就三级了）
#   · 父类别的显示时长 = 自己 + 所有子类别
#
# 树是用 parent_id 显式查出来拼的，不走 ORM 自引用关系 —— 原因见 db/models.py。

def _duration_by_category(db: Session) -> dict[int, int]:
    """每个类别**自己**的（不含子类别）已完成学习秒数。一次查询拿完。"""
    rows = db.execute(
        select(
            FocusSession.category_id,
            func.coalesce(func.sum(FocusSession.duration_seconds), 0),
        )
        .where(FocusSession.status == SessionStatus.completed)
        .group_by(FocusSession.category_id)
    ).all()
    return {int(cid): int(total) for cid, total in rows}


def category_tree(db: Session) -> list[dict]:
    """
    两级类别树 + 汇总时长。

    返回 [{cat, own, total, children: [{cat, own, total}, ...]}, ...]
      own   = 这个类别自己的学习秒数
      total = 自己 + 所有子类别（父类别在界面上显示这个）

    排序：顶层 is_pinned DESC, sort_order, id —— 置顶优先，且顺序稳定不跳。
    两级都现算，不存冗余字段。
    """
    cats = list(db.scalars(select(Category)))
    own = _duration_by_category(db)
    by_id = {c.id: c for c in cats}

    kids: dict[int, list[Category]] = {}
    roots: list[Category] = []
    for c in cats:
        pid = c.parent_id
        # 父不在了（外键的 ON DELETE SET NULL 本该兜住）就当顶层 ——
        # 无论如何不能让一个类别从界面上凭空消失
        if pid and pid in by_id and pid != c.id:
            kids.setdefault(pid, []).append(c)
        else:
            roots.append(c)

    roots.sort(key=lambda c: (not c.is_pinned, c.sort_order, c.id))

    tree = []
    for r in roots:
        children = sorted(kids.get(r.id, []), key=lambda c: (c.sort_order, c.id))
        own_r = own.get(r.id, 0)
        tree.append(
            {
                "cat": r,
                "own": own_r,
                "total": own_r + sum(own.get(k.id, 0) for k in children),
                # 子节点也带上 children: [] —— 形状统一，调用方不用先判断
                # 「这是父还是子」再决定能不能取 node["children"]
                "children": [
                    {
                        "cat": k,
                        "own": own.get(k.id, 0),
                        "total": own.get(k.id, 0),
                        "children": [],
                    }
                    for k in children
                ],
            }
        )
    return tree


def category_totals(db: Session) -> list[tuple[Category, int]]:
    """
    平铺的 [(Category, 总秒数)]，父类别的总秒数是「自己 + 子类别」的合计。

    保留平铺形状是为了不动现有的调用点（悬浮窗、状态恢复、选择器）。
    父总是排在它的子类别前面；要看层级结构用 category_tree()。
    """
    out: list[tuple[Category, int]] = []
    for node in category_tree(db):
        out.append((node["cat"], node["total"]))
        for ch in node["children"]:
            out.append((ch["cat"], ch["total"]))
    return out


def leaf_categories(db: Session) -> list[tuple[Category, int]]:
    """
    能拿来计时的类别 —— 只有叶子。

    父类别只是分组、不参与计时：它的时长本来就是子类别的合计，
    要是它自己也能计时，那段时间该算给谁就说不清了。
    """
    out: list[tuple[Category, int]] = []
    for node in category_tree(db):
        if node["children"]:
            out.extend((ch["cat"], ch["total"]) for ch in node["children"])
        else:
            out.append((node["cat"], node["total"]))
    return out


def category_children(db: Session, category_id: int) -> list[Category]:
    """某个类别的直接子类别，按 sort_order。"""
    return list(
        db.scalars(
            select(Category)
            .where(Category.parent_id == category_id)
            .order_by(Category.sort_order, Category.id)
        )
    )


def toggle_pin(db: Session, category_id: int) -> Category:
    """置顶 / 取消置顶（只对顶层有意义）。"""
    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("类别不存在")
    cat.is_pinned = not cat.is_pinned
    db.commit()
    db.refresh(cat)
    return cat


def add_category(db: Session, name: str, parent_id: int | None = None) -> Category:
    """
    新建类别，排在最后。

    parent_id 给了就建成它的子类别 —— 只允许两级，所以父必须是顶层类别。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("类别名不能为空")
    if db.scalars(select(Category).where(Category.name == name)).first():
        raise ValueError(f"已经有「{name}」这个类别了")

    if parent_id is not None:
        parent = db.get(Category, parent_id)
        if parent is None:
            raise ValueError("父类别不存在")
        if parent.parent_id is not None:
            raise ValueError(f"「{parent.name}」已经是子类别了 —— 只支持两级目录")

    # 排序号在同一层内递增
    max_order = db.scalar(
        select(func.max(Category.sort_order)).where(Category.parent_id == parent_id)
    ) or 0
    cat = Category(name=name, sort_order=max_order + 1, parent_id=parent_id)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def rename_category(db: Session, category_id: int, new_name: str) -> Category:
    """改名。"""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("类别名不能为空")

    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("类别不存在")
    if cat.name == new_name:
        return cat

    dup = db.scalars(select(Category).where(Category.name == new_name)).first()
    if dup:
        raise ValueError(f"已经有「{new_name}」这个类别了")

    cat.name = new_name
    db.commit()
    db.refresh(cat)
    return cat


def move_category(db: Session, category_id: int, parent_id: int | None) -> Category:
    """
    把类别移到某个父类别下；parent_id=None 表示移回顶层。

    三条限制，都是为了防止层级被搞乱：
      · 不能移到自己下面
      · 目标必须是**顶层**类别（只允许两级）
      · 自己不能带着子类别 —— 否则移进去就成三级了
    """
    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("类别不存在")

    if parent_id is None:
        cat.parent_id = None
        db.commit()
        db.refresh(cat)
        return cat

    if parent_id == category_id:
        raise ValueError("不能把类别移到自己下面")

    parent = db.get(Category, parent_id)
    if parent is None:
        raise ValueError("目标父类别不存在")
    if parent.parent_id is not None:
        raise ValueError(f"「{parent.name}」已经是子类别了 —— 只支持两级目录")

    if category_children(db, category_id):
        raise ValueError(f"「{cat.name}」下面还有子类别，不能再被移进别的类别")

    cat.parent_id = parent_id
    db.commit()
    db.refresh(cat)
    return cat


def category_stats(db: Session, category_id: int) -> tuple[int, int]:
    """(记录数, 总秒数) —— 删类别前拿这个去警告用户。只算它自己的记录。"""
    row = db.execute(
        select(
            func.count(FocusSession.id),
            func.coalesce(func.sum(FocusSession.duration_seconds), 0),
        ).where(
            FocusSession.category_id == category_id,
            FocusSession.status == SessionStatus.completed,
        )
    ).one()
    return int(row[0]), int(row[1])


def delete_category(db: Session, category_id: int, force: bool = False) -> list[str]:
    """
    删除类别。**连同它自己的学习记录和关键词一起删。**

    ★ 如果它下面还有子类别：**先把子类别提到顶层**（它们的记录一条都不动），
      再删它自己。绝不连带删子类别 —— 那会一下子抹掉几十个小时的学习记录。

    有记录时默认拒绝（force=False），逼调用方先去问用户。

    Returns: 被提到顶层的子类别名（界面拿去提示用户）
    """
    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("类别不存在")

    n_sessions, _ = category_stats(db, category_id)
    if n_sessions and not force:
        raise ValueError(f"该类有 {n_sessions} 条学习记录，删除会一并清掉")

    children = category_children(db, category_id)
    for k in children:
        k.parent_id = None
    if children:
        db.flush()

    # 手动级联（模型里没配 ON DELETE CASCADE）
    db.query(Item).filter(Item.category_id == category_id).delete()
    db.query(FocusSession).filter(FocusSession.category_id == category_id).delete()
    db.delete(cat)
    db.commit()
    return [k.name for k in children]


# ────────────────────────── 专注记录 ──────────────────────────

def get_active(db: Session) -> FocusSession | None:
    """取出「还在跑」的那条 —— 关掉工具再打开就靠它恢复。"""
    return db.scalars(
        select(FocusSession)
        .where(FocusSession.status.in_(ACTIVE_STATES))
        .order_by(FocusSession.start_time.desc())
        .limit(1)
    ).first()


def start(db: Session, category_id: int) -> FocusSession:
    """开始一次专注。若已有在跑的，先返回它（幂等）。"""
    existing = get_active(db)
    if existing:
        return existing

    s = FocusSession(
        category_id=category_id,
        start_time=datetime.now(),
        status=SessionStatus.running,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def pause(db: Session, session_id: int) -> FocusSession:
    """暂停：冻结计时。"""
    s = db.get(FocusSession, session_id)
    if s is None or s.status != SessionStatus.running:
        raise ValueError("这条记录不在计时中，无法暂停")
    s.status = SessionStatus.paused
    s.paused_at = datetime.now()
    db.commit()
    db.refresh(s)
    return s


def resume(db: Session, session_id: int) -> FocusSession:
    """继续：把本次暂停时长结算进 paused_seconds。"""
    s = db.get(FocusSession, session_id)
    if s is None or s.status != SessionStatus.paused:
        raise ValueError("这条记录不在暂停中，无法继续")

    if s.paused_at is not None:
        s.paused_seconds += int((datetime.now() - s.paused_at).total_seconds())
    s.paused_at = None
    s.status = SessionStatus.running
    db.commit()
    db.refresh(s)
    return s


def skip_break(
    db: Session, session_id: int, focus_minutes: int, break_minutes: int
) -> FocusSession:
    """
    跳过剩下的休息 —— 时间线直接跳到下一个专注段的开头。

    做法是把剩余休息秒数记进 skipped_seconds（相当于让周期计数提前走完），
    **不去改时钟** —— 真实时长、开始时间一个字都没动。

    不在休息里就当作没点，不改任何东西（不报错，用户手快连点两下不该弹错）。
    """
    s = db.get(FocusSession, session_id)
    if s is None or s.status not in ACTIVE_STATES:
        raise ValueError("这条记录不在计时中")

    phase, remain = is_break_now(s, focus_minutes, break_minutes)
    if phase != "break":
        return s

    s.skipped_seconds += max(0, remain)
    db.commit()
    db.refresh(s)
    return s


def stop(
    db: Session, session_id: int, focus_minutes: int, break_minutes: int
) -> FocusSession:
    """
    停止计时 → 进入 pending_review，等着写总结。

    这里**不**写 raw_text，也不进 completed —— 强制回顾的卡点就在这。

    和以前不一样的是：**计时不停**。end_time / duration_seconds 留到
    submit_review 才定，这样写总结的这段时间也算学习时长。
    如果是在休息中点的结束，先把剩下的休息跳掉，让写总结落在专注段里
    （不然那几十分钟会被当成休息扣掉）。
    """
    s = db.get(FocusSession, session_id)
    if s is None or s.status not in ACTIVE_STATES:
        raise ValueError("这条记录不在计时中，无法结束")

    now = datetime.now()
    # 暂停中结束：先把这段暂停结算掉，之后计时接着走
    if s.status == SessionStatus.paused and s.paused_at is not None:
        s.paused_seconds += int((now - s.paused_at).total_seconds())
        s.paused_at = None

    phase, remain = is_break_now(s, focus_minutes, break_minutes)
    if phase == "break":
        s.skipped_seconds += max(0, remain)

    s.review_started_at = now
    s.status = SessionStatus.pending_review
    db.commit()
    db.refresh(s)
    return s


def settle_pending(db: Session, when: datetime | None = None) -> FocusSession | None:
    """
    把「欠着总结」的那条记录的时长**定死**。

    写总结期间计时是继续的，所以退出那一刻时长还没定。两种情况：

      · 正常退出（when=now）—— 写总结的时间照算进去
      · 上次是被强杀/崩溃的（when=None）—— 退回 review_started_at，
        停机那几个小时不算。宁可少算，也不能让隔夜没关的程序
        凭空多出十几个小时的学习时长。

    定死之后 review_started_at 清空，再写总结时长也不会变了。
    """
    s = get_pending_review(db)
    if s is None or s.duration_seconds is not None:
        return s

    if when is None:
        when = s.review_started_at or s.start_time

    cfg = settings_svc.get_all(db)
    s.end_time = when
    s.duration_seconds = study_seconds(
        s, cfg["focus_minutes"], cfg["break_minutes"], now=when
    )
    s.review_started_at = None
    db.commit()
    db.refresh(s)
    return s


def submit_review(
    db: Session,
    session_id: int,
    raw_text: str,
    focus_minutes: int,
    break_minutes: int,
) -> FocusSession:
    """
    提交总结 → completed。

    没有这一步，记录永远卡在 pending_review；写了总结才放行。
    （数据库层面还有 chk_review_required 兜底）

    **时长在这一刻才定** —— 从点「结束」到点「保存」之间的时间也算学习时长。
    如果已经被 settle_pending 定死过（上次被强杀留下的），就不重算，尊重那个值。
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("总结不能为空 —— 这是强制回顾的核心")

    s = db.get(FocusSession, session_id)
    if s is None:
        raise ValueError("记录不存在")
    if s.status != SessionStatus.pending_review:
        raise ValueError(f"当前状态是 {s.status.value}，不能提交总结")

    if s.duration_seconds is None:
        now = datetime.now()
        s.end_time = now
        s.duration_seconds = study_seconds(s, focus_minutes, break_minutes, now=now)
        s.review_started_at = None

    s.raw_text = text
    s.status = SessionStatus.completed
    db.commit()
    db.refresh(s)
    return s


def abandon(db: Session, session_id: int) -> None:
    """
    放弃本次记录 —— **彻底删除，时长不计入**。

    设计依据（见 规划文档 §五.1）：没写总结 = 没学到东西 = 不该算学习时长。
    所以「放弃」必须是真删除，而不是挂着一个永远不会完成的占位记录。
    """
    s = db.get(FocusSession, session_id)
    if s is None:
        return
    if s.status == SessionStatus.completed:
        raise ValueError("已写完总结的记录不能放弃")
    db.delete(s)
    db.commit()


def get_pending_review(db: Session) -> FocusSession | None:
    """
    有没有欠着的总结（下次打开时提醒用）。

    按 start_time 排 —— 不能再按 end_time 了：刚停下的那条 end_time 还是 NULL
    （要等提交总结才写），MySQL 里 NULL 在 DESC 里排最后，会捞错人。
    """
    return db.scalars(
        select(FocusSession)
        .where(FocusSession.status == SessionStatus.pending_review)
        .order_by(FocusSession.start_time.desc())
        .limit(1)
    ).first()


# ────────────────────────── AI 整理结果 ──────────────────────────

def set_polish(
    db: Session, session_id: int, paragraph: str, keywords: list[str]
) -> list[Item]:
    """
    把 AI 的整理结果写回库：段落存 session，关键词存 item（挂到这次专注上）。

    重复整理（看板上的「重新整理」）会**先清掉上次的关键词**再写新的 ——
    否则点两次就变成两套关键词叠在一起。
    """
    s = db.get(FocusSession, session_id)
    if s is None:
        raise ValueError("记录不存在")

    text = (paragraph or "").strip()
    if not text:
        return []

    s.polished_text = text

    db.query(Item).filter(Item.session_id == s.id).delete()

    created = []
    for raw in keywords:
        name = (raw or "").strip()
        if not name:
            continue
        item = Item(category_id=s.category_id, session_id=s.id, name=name[:200])
        db.add(item)
        created.append(item)

    db.commit()
    return created


def category_records(
    db: Session, category_id: int, limit: int = 200, include_children: bool = False
) -> list[dict]:
    """
    某类别下写完总结的记录 —— 看板右侧渲染段落卡片用。

    返回**纯数据**而不是 ORM 对象：卡片要在 Session 关闭之后继续用，
    而脱管对象一碰关联属性就炸（DetachedInstanceError，见 main.detach 的说明）。
    两次查询拿完（记录 + 关键词），不做 N+1。

    没整理过的记录 polished_text 是 None，界面上回退显示 raw_text 原文。

    include_children: 选中的是**父类别**（分组）时传 True ——
        这时把子类别的记录也一起捞出来，卡片上会标出各自属于哪个子类别。
        父类别本来就是「自己 + 子类别」的合计，点进去却什么都看不到会很怪。

    limit: 只取最近这么多条 —— 卡片是真实 widget，几千条会把界面拖死。
    """
    cat_ids = [category_id]
    if include_children:
        cat_ids += [c.id for c in category_children(db, category_id)]

    sessions = list(
        db.scalars(
            select(FocusSession)
            .where(
                FocusSession.category_id.in_(cat_ids),
                FocusSession.status == SessionStatus.completed,
            )
            .order_by(FocusSession.start_time.desc(), FocusSession.id.desc())
            .limit(limit)
        )
    )
    if not sessions:
        return []

    by_session: dict[int, list[str]] = {s.id: [] for s in sessions}
    for item in db.scalars(
        select(Item).where(Item.session_id.in_(list(by_session))).order_by(Item.id)
    ):
        by_session[item.session_id].append(item.name)

    # 父类别视角下要标出每条记录属于哪个子类别，先把名字查好（一次查询）
    names = {
        c.id: c.name
        for c in db.scalars(select(Category).where(Category.id.in_(cat_ids)))
    }

    return [
        {
            "id": s.id,
            "category_id": s.category_id,
            "category_name": names.get(s.category_id, ""),
            "start_time": s.start_time,
            "end_time": s.end_time,
            "duration_seconds": s.duration_seconds or 0,
            "rest_seconds": rest_seconds(s),
            "polished_text": s.polished_text,
            "raw_text": s.raw_text or "",
            "keywords": by_session.get(s.id, []),
        }
        for s in sessions
    ]


def unpolished_records(db: Session) -> list[dict]:
    """
    还没整理过的记录 —— 看板「批量整理」用。

    两类都算：早于 polished_text 字段的历史记录，以及当时 AI 调用失败的。
    """
    rows = db.execute(
        select(
            FocusSession.id,
            FocusSession.category_id,
            FocusSession.raw_text,
            Category.name,
        )
        .join(Category, Category.id == FocusSession.category_id)
        .where(
            FocusSession.status == SessionStatus.completed,
            FocusSession.polished_text.is_(None),
        )
        .order_by(FocusSession.start_time)
    ).all()

    return [
        {
            "id": r[0],
            "category_id": r[1],
            "raw_text": r[2] or "",
            "category_name": r[3],
        }
        for r in rows
        if (r[2] or "").strip()
    ]
