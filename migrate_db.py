"""
数据库迁移 —— 把学习记录从一个后端搬到另一个。

为什么要这个工具：
    程序默认用 SQLite（零安装，方便分发），但很多人的老数据在 MySQL 里。
    换后端、换电脑、或者把自己的记录打包给另一台机器，都用它搬。

用法：
    python migrate_db.py --to sqlite
        把当前 .env 指向的库，搬到程序目录下的 focus_timer.db

    python migrate_db.py --to sqlite --path "D:/备份/focus_timer.db"
        搬到指定文件

    python migrate_db.py --to "mysql+pymysql://root:密码@localhost:3306/focus_timer"
        反向：搬到 MySQL（目标库要先自己 CREATE DATABASE）

    python migrate_db.py --to sqlite --dry-run
        只统计两边的数量，一个字都不写

    python migrate_db.py --to sqlite --force
        目标里已经有数据时，允许清空重来

    python migrate_db.py --verify-only --to sqlite
        只比对数量，不搬

设计要点：
  · **保持 id 不变** —— session.category_id / item.session_id / category.parent_id
    就都不用重映射，省掉一大类难查的 bug
  · category.parent_id 是**自引用外键**：先全部以 NULL 插入，第二遍再填父子关系。
    否则子类别恰好排在父类别前面时会违反外键约束
  · 插入顺序 category → session → item → setting（照外键依赖）
  · 搬完**对比两边数量**，对不上就报错退出，绝不静默「成功」
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import delete, func, insert, select, update

import config
from db.database import ensure_schema, make_engine
from db.models import Category, FocusSession, Item, Setting

# 按外键依赖排好序。第三项是排序键（Setting 没有 id）
TABLES = [
    ("category", Category, "id"),
    ("session", FocusSession, "id"),
    ("item", Item, "id"),
    ("setting", Setting, "key"),
]
# 清空时的顺序：先删孩子
DELETE_ORDER = ["item", "session", "category", "setting"]


def resolve_url(text: str) -> str:
    """
    把命令行里写的目标转成真正的连接串。

    'sqlite'        → 程序目录下的 focus_timer.db（默认位置）
    'sqlite:路径'    → 指定的文件
    其它             → 当成完整连接串（mysql+pymysql://... 之类）
    """
    if text == "sqlite":
        return f"sqlite:///{config.SQLITE_FILE.as_posix()}"
    if text.startswith("sqlite:"):
        p = Path(text.split(":", 1)[1]).expanduser().resolve()
        return f"sqlite:///{p.as_posix()}"
    return text


def show_url(url: str) -> str:
    """给用户看的库里位置 —— MySQL 连接串里有密码，不能整条打出来。"""
    if url.startswith("sqlite"):
        return f"SQLite → {url.split('///')[-1]}"
    if "@" in url:
        head, tail = url.split("://", 1)
        return f"{head} → {tail.split('@')[-1]}"
    return url


def read_all(eng) -> dict[str, list[dict]]:
    """从源库读全量数据。"""
    data: dict[str, list[dict]] = {}
    with eng.connect() as conn:
        for name, model, order in TABLES:
            rows = conn.execute(
                select(model.__table__).order_by(model.__table__.c[order])
            ).mappings()
            data[name] = [dict(r) for r in rows]
    return data


def counts(eng) -> dict[str, int]:
    """各表现有行数。"""
    from db.models import Base

    with eng.connect() as conn:
        return {
            name: conn.execute(
                select(func.count()).select_from(Base.metadata.tables[name])
            ).scalar()
            or 0
            for name, _, _ in TABLES
        }


def write_all(eng, data: dict[str, list[dict]], force: bool) -> None:
    """把数据写进目标库。"""
    from db.models import Base

    # 自引用外键：先把 parent_id 抠出来，插完全部再补
    parents = {r["id"]: r["parent_id"] for r in data["category"]}
    for r in data["category"]:
        r["parent_id"] = None

    with eng.begin() as conn:
        if force:
            for name in DELETE_ORDER:
                conn.execute(delete(Base.metadata.tables[name]))

        for name, model, _ in TABLES:
            rows = data[name]
            if rows:
                conn.execute(insert(model.__table__), rows)

        # 第二遍：补父子关系
        for cid, pid in parents.items():
            if pid is None:
                continue
            conn.execute(
                update(Category.__table__)
                .where(Category.__table__.c.id == cid)
                .values(parent_id=pid)
            )


def report(src_counts: dict[str, int], dst_counts: dict[str, int]) -> bool:
    """打印两边对比，返回是否完全一致。"""
    print(f"\n  {'表':<10} {'源':>6} {'目标':>6}   结果")
    ok = True
    for name, _, _ in TABLES:
        a, b = src_counts[name], dst_counts[name]
        same = a == b
        ok = ok and same
        print(f"  {name:<10} {a:>6} {b:>6}   {'✅ 一致' if same else '✗ 不一致'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把学习记录从一个数据库搬到另一个",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--to", required=True,
                    help="目标：sqlite / sqlite:路径 / 完整连接串")
    ap.add_argument("--from", dest="src", default=None,
                    help="源（默认用当前配置的库）")
    ap.add_argument("--force", action="store_true",
                    help="目标里已有数据时，清空后重来")
    ap.add_argument("--dry-run", action="store_true",
                    help="只统计，不写任何东西")
    ap.add_argument("--verify-only", action="store_true",
                    help="只比对数量，不搬")
    args = ap.parse_args()

    src_url = args.src or config.DATABASE_URL
    dst_url = resolve_url(args.to)

    if src_url == dst_url:
        print("源和目标是同一个库，没东西可搬。")
        return 1

    print("=" * 60)
    print("  源  :", show_url(src_url))
    print("  目标:", show_url(dst_url))
    print("=" * 60)

    src_eng = make_engine(src_url)
    dst_eng = make_engine(dst_url)

    # —— 源库读一遍（顺便确认连得上）——
    try:
        src_counts = counts(src_eng)
    except Exception as e:                       # noqa: BLE001
        print(f"\n✗ 连不上源库：{e}")
        return 1
    print("\n源库数据：", ", ".join(f"{k} {v} 行" for k, v in src_counts.items()))
    if sum(src_counts.values()) == 0:
        print("\n源库是空的，没什么可搬。")
        return 1

    data = read_all(src_eng)

    # —— 目标库的情况 ——
    # dry-run 时**不建任何东西**（免得「只想看看」却留下一个空库文件）。
    # ★ SQLite 只要一连上就会把文件创建出来 —— 所以这里必须先判断文件在不在，
    #   不能靠 try 去连。
    if args.dry_run:
        dst_counts = None
        if dst_url.startswith("sqlite"):
            if Path(dst_url.split("///")[-1]).exists():
                dst_counts = counts(dst_eng)
        else:
            try:
                dst_counts = counts(dst_eng)
            except Exception:                    # noqa: BLE001
                dst_counts = None

        if dst_counts is None:
            print("\n目标库还不存在 —— 真跑的时候会新建。")
        else:
            print("\n目标库已有：", ", ".join(f"{k} {v} 行" for k, v in dst_counts.items()))

        print("\n[dry-run] 会搬这些：")
        for name, _, _ in TABLES:
            print(f"  {name:<10} {len(data[name]):>6} 行")
        print("\n没有任何东西被写入。")
        return 0

    # 用 ensure_schema 而**不是** init_db：init_db 会预置 5 个默认类别，
    # 那样搬过去就变成 7 + 5 = 12 个类别了。
    ensure_schema(dst_eng)

    dst_counts = counts(dst_eng)
    if args.verify_only:
        print("\n[只比对，不搬]")
        return 0 if report(src_counts, dst_counts) else 1

    if sum(dst_counts.values()) > 0 and not args.force:
        print(
            f"\n✗ 目标库里已经有数据了："
            f"{', '.join(f'{k} {v} 行' for k, v in dst_counts.items() if v)}"
            f"\n\n  迁移是覆盖性的。确认要清空重来的话，加 --force。"
            f"\n  （先备份：SQLite 直接拷走那个文件；MySQL 用 mysqldump）"
        )
        return 1

    total = sum(len(v) for v in data.values())
    print(f"\n开始搬 {total} 行 ...")
    write_all(dst_eng, data, force=args.force)

    print("\n搬完了，核对数量：")
    if report(src_counts, counts(dst_eng)):
        print("\n✅ 迁移成功，两边数量完全一致")
        print("\n下一步：把 .env 里的 FOCUS_DATABASE_URL 改成指向新库")
        print("（或者删掉那一行，让程序用默认的 SQLite）。")
        return 0

    print("\n✗ 数量对不上！源库没有被改动，请把上面的输出发出来排查。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
