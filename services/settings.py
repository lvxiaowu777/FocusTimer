"""
偏好设置 —— 专注时长、休息时长、提示音、界面字号。

存 `setting` 表（键值对）。读的时候带上默认值，所以第一次跑不用手动初始化。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Setting

# 键 → 默认值
DEFAULTS: dict[str, str] = {
    "focus_minutes": "25",   # 一次专注多少分钟
    "break_minutes": "5",    # 一次休息多少分钟
    "sound_enabled": "1",    # 阶段切换时要不要响（1 开 / 0 关）
    "font_scale": "100",     # 界面字号档，百分比（见 ui/theme.FONT_PRESETS）
}

# 取值范围（防手滑填 0 或 999）
LIMITS: dict[str, tuple[int, int]] = {
    "focus_minutes": (1, 180),
    "break_minutes": (1, 60),
    "sound_enabled": (0, 1),
    "font_scale": (70, 160),
}

# 越界报错时跟在数字后面的单位，让提示是人话
LIMIT_UNITS: dict[str, str] = {
    "focus_minutes": " 分钟",
    "break_minutes": " 分钟",
    "sound_enabled": "（0 = 关，1 = 开）",
    "font_scale": "%（70 ~ 160）",
}


def get_all(db: Session) -> dict[str, int]:
    """读全部设置（带默认值），值统一转成 int。"""
    result = {k: int(v) for k, v in DEFAULTS.items()}
    for row in db.scalars(select(Setting)).all():
        if row.key in DEFAULTS:
            try:
                result[row.key] = int(row.value)
            except ValueError:
                pass  # 存坏了就用默认值
    return result


def get(db: Session, key: str) -> int:
    return get_all(db)[key]


def set_value(db: Session, key: str, value: int) -> None:
    """写一个设置。越界会抛 ValueError。"""
    if key not in DEFAULTS:
        raise ValueError(f"未知的设置项：{key}")

    lo, hi = LIMITS[key]
    if not (lo <= value <= hi):
        raise ValueError(f"取值范围是 {lo} ~ {hi}{LIMIT_UNITS.get(key, '')}")

    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.commit()


def set_many(db: Session, **kwargs: int) -> None:
    """一次写多个，比如 set_many(db, focus_minutes=45, break_minutes=10)。"""
    for k, v in kwargs.items():
        set_value(db, k, v)


def phase_at(elapsed_seconds: int, focus_minutes: int, break_minutes: int) -> tuple[str, int, int]:
    """
    根据「已专注总秒数」推算当前处在哪个阶段。

    这是整个周期休息机制的**核心**：阶段不需要单独存数据库，
    完全由「总时长 + 设置」算出来 —— 所以关掉工具再打开，阶段也是对的。

    ⚠️ 传进来的应该是 FocusSession.cycle_elapsed_seconds（真实时长 + 跳过的休息），
    不是裸的 elapsed_seconds —— 否则跳过休息就不生效了。

    Returns:
        (阶段, 本阶段剩余秒数, 本阶段总秒数)
        阶段 = "focus" | "break"
    """
    focus_s = max(1, focus_minutes) * 60
    break_s = max(1, break_minutes) * 60
    cycle = focus_s + break_s

    pos = max(0, elapsed_seconds) % cycle
    if pos < focus_s:
        return "focus", focus_s - pos, focus_s
    return "break", cycle - pos, break_s


def cycle_break_seconds(
    virtual_seconds: int, focus_minutes: int, break_minutes: int
) -> int:
    """
    一段虚拟时长里，**落在休息段的秒数**。

    休息不算学习时长，所以算学习时长时得知道「这段路里有多少秒是在休息」。
    和 phase_at 一样是纯粹由「时长 + 设置」推出来的，不落库、不累加 ——
    关掉工具再打开，算出来的数还是同一个。

    为什么传的是虚拟时长：跳过休息会把周期计数提前，休息段的位置跟着往前挪，
    所以用 cycle_elapsed_seconds（真实 + 跳过的）来量才准。
    """
    focus_s = max(1, focus_minutes) * 60
    break_s = max(1, break_minutes) * 60
    cycle = focus_s + break_s

    full, pos = divmod(max(0, virtual_seconds), cycle)
    # 每个走完的整周期各含一整段休息，再加当前这个周期里已经走到的休息部分
    return full * break_s + max(0, pos - focus_s)
