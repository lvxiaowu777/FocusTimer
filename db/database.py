"""
数据库连接与建表。

用法：
    from db.database import engine, SessionLocal, init_db
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 `python db/database.py` 也能直接跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from db.models import Base, Category

if not DATABASE_URL:
    raise RuntimeError(
        "FOCUS_DATABASE_URL 未配置。请在项目根目录的 .env 里填写，例如：\n"
        'FOCUS_DATABASE_URL=mysql+pymysql://用户:密码@localhost:3306/focus_timer?charset=utf8mb4'
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,     # 连接失效自动重连
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# 首次使用预置的类别，避免开箱空白
DEFAULT_CATEGORIES = ["机器学习", "深度学习", "RAG", "Agent", "Python 工程"]


# 表名 → [(列名, 建列 SQL), ...]
# 以后加字段往这儿追加一行就行，不用再写一遍循环。
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "category": [
        ("is_pinned",
         "ALTER TABLE category ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0"),
    ],
    "session": [
        ("skipped_seconds",
         "ALTER TABLE session ADD COLUMN skipped_seconds INT NOT NULL DEFAULT 0"),
        ("review_started_at",
         "ALTER TABLE session ADD COLUMN review_started_at DATETIME NULL"),
    ],
}


def _migrate() -> None:
    """
    给**已存在**的表补新列。

    create_all() 只建缺失的表，不会改已有表的结构 —— 所以加字段必须手写迁移。
    这里的写法是幂等的：列已存在就跳过。
    """
    existing_tables = set(inspect(engine).get_table_names())

    for table, columns in _MIGRATIONS.items():
        if table not in existing_tables:
            continue                    # 表都还没有，create_all() 会按新结构建出来
        have = {c["name"] for c in inspect(engine).get_columns(table)}
        for name, ddl in columns:
            if name in have:
                continue
            with engine.begin() as conn:
                conn.execute(text(ddl))
            print(f"[db] 迁移：{table} 表已补上 {name} 列")


def init_db() -> None:
    """建表（幂等）+ 迁移旧表 + 预置默认类别。"""
    Base.metadata.create_all(engine)
    _migrate()
    print("[db] 建表完成:", ", ".join(inspect(engine).get_table_names()))

    with SessionLocal() as db:
        if db.query(Category).count() == 0:
            for i, name in enumerate(DEFAULT_CATEGORIES):
                db.add(Category(name=name, sort_order=i))
            db.commit()
            print(f"[db] 预置类别: {' / '.join(DEFAULT_CATEGORIES)}")


def health() -> bool:
    """连通性自检。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print("[db] 连接失败:", e)
        return False


if __name__ == "__main__":
    print("目标:", DATABASE_URL.split("@")[-1])
    if health():
        print("[db] 连接正常")
        init_db()
