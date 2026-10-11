"""
数据库连接与建表。

支持两种后端，由 config.DATABASE_URL 决定：
  · **SQLite（默认）** —— Python 自带，零安装，就是安装目录下的 focus_timer.db。
    打包发给别人时对方不用装任何数据库，解压双击就能用。
  · **MySQL** —— 在 .env 里设 FOCUS_DATABASE_URL 就切过去（见 .env.example）。

用法：
    from db.database import engine, SessionLocal, init_db
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 `python db/database.py` 也能直接跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL, IS_SQLITE
from db.models import Base, Category

# —— 引擎参数按后端区分 ——
_engine_kwargs: dict = {
    "pool_pre_ping": True,      # 连接失效自动重连
    "echo": False,
}
if IS_SQLITE:
    # ★ check_same_thread=False 是必须的：AI 整理跑在后台线程里，而 SQLite
    #   默认禁止一个连接被别的线程用。关掉这个检查后由 SQLAlchemy 的连接池
    #   保证同一时刻只有一个线程在用某个连接，是安全的。
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
else:
    _engine_kwargs["pool_recycle"] = 3600

engine = create_engine(DATABASE_URL, **_engine_kwargs)

if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 驱动回调
        """
        每次建立 SQLite 连接时设几个 pragma。

        · foreign_keys=ON —— **SQLite 默认根本不强制外键**。我们的
          ON DELETE CASCADE / SET NULL 全靠它，不打开等于白写。
        · journal_mode=WAL —— 读写能并发，后台整理写库时主线程查询不会被卡住。
        · busy_timeout —— 撞上写锁时等一会儿再报错，而不是立刻 "database is locked"。
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# 首次使用预置的类别，避免开箱空白
DEFAULT_CATEGORIES = ["机器学习", "深度学习", "RAG", "Agent", "Python 工程"]


# 表名 → [(列名, 建列 SQL), ...]
# 以后加字段往这儿追加一行就行，不用再写一遍循环。
# 这些 DDL 在 SQLite 上也能跑：SQLite 的类型名是自由文本，ADD COLUMN 也支持
# （NOT NULL 必须带 DEFAULT，下面都带着）。
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "category": [
        ("is_pinned",
         "ALTER TABLE category ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0"),
        ("parent_id",
         "ALTER TABLE category ADD COLUMN parent_id INT NULL"),
    ],
    "session": [
        ("skipped_seconds",
         "ALTER TABLE session ADD COLUMN skipped_seconds INT NOT NULL DEFAULT 0"),
        ("review_started_at",
         "ALTER TABLE session ADD COLUMN review_started_at DATETIME NULL"),
        ("polished_text",
         "ALTER TABLE session ADD COLUMN polished_text TEXT NULL"),
    ],
    "item": [
        ("session_id",
         "ALTER TABLE item ADD COLUMN session_id INT NULL"),
    ],
}

# 表名 → [(索引名, 索引列, 建索引 SQL), ...]
_INDEXES: dict[str, list[tuple[str, list[str], str]]] = {
    "category": [
        ("idx_category_parent", ["parent_id"],
         "CREATE INDEX idx_category_parent ON category (parent_id)"),
    ],
    "item": [
        ("idx_item_session", ["session_id"],
         "CREATE INDEX idx_item_session ON item (session_id)"),
    ],
}

# 表名 → [(外键名, 本表列, 被引用表, 建外键 SQL), ...]
#
# ★ 判断「已存在」用的是**列 + 被引用表**，不是约束名。
#   因为 create_all() 按模型建出来的外键是数据库自动命名的（MySQL 叫
#   category_ibfk_1），按名字比对会以为不存在 → 又加一个重复的。
#   实测全新库上因此多出 3 个外键（6 个而不是 3 个）。
_FKS: dict[str, list[tuple[str, list[str], str, str]]] = {
    "category": [
        # ON DELETE SET NULL：万一父类别被直接删掉（绕过应用层），
        # 子类别会变成顶层，而不是被连带删除 —— 保住学习记录。
        ("fk_category_parent", ["parent_id"], "category",
         "ALTER TABLE category ADD CONSTRAINT fk_category_parent "
         "FOREIGN KEY (parent_id) REFERENCES category(id) ON DELETE SET NULL"),
    ],
    "item": [
        ("fk_item_session", ["session_id"], "session",
         "ALTER TABLE item ADD CONSTRAINT fk_item_session "
         "FOREIGN KEY (session_id) REFERENCES `session`(id) ON DELETE CASCADE"),
    ],
}


def _migrate() -> None:
    """
    给**已存在**的表补新列 / 新索引 / 新外键。

    create_all() 只建缺失的表，不会改已有表的结构 —— 所以加字段必须手写迁移。
    这里的写法是幂等的：列（索引、外键）已存在就跳过。

    全新安装时这些其实全是空转：create_all() 按当前模型建出来的表本来就齐全。
    迁移真正起作用的对象是**老版本的库**。
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

    for table, indexes in _INDEXES.items():
        if table not in existing_tables:
            continue
        existing = inspect(engine).get_indexes(table)
        have_names = {i["name"] for i in existing}
        have_cols = {tuple(i.get("column_names") or ()) for i in existing}
        for name, cols, ddl in indexes:
            # 名字对得上，或者已经有个同样列的索引 —— 都算已存在
            if name in have_names or tuple(cols) in have_cols:
                continue
            with engine.begin() as conn:
                conn.execute(text(ddl))
            print(f"[db] 迁移：{table} 表已补上 {name} 索引")

    for table, fks in _FKS.items():
        if table not in existing_tables:
            continue
        existing = inspect(engine).get_foreign_keys(table)
        have = {
            (tuple(fk.get("constrained_columns") or ()), fk.get("referred_table"))
            for fk in existing
        }
        for name, cols, referred, ddl in fks:
            if (tuple(cols), referred) in have:
                continue
            if IS_SQLITE:
                # SQLite 不支持 ALTER TABLE ADD CONSTRAINT。
                # 新建的 SQLite 库在 create_all() 阶段就带上了外键，
                # 压根走不到这儿；真走到了也没法补，跳过而不是崩掉。
                print(f"[db] 跳过：SQLite 不支持给已有表补外键（{name}）")
                continue
            with engine.begin() as conn:
                conn.execute(text(ddl))
            print(f"[db] 迁移：{table} 表已补上 {name} 外键")


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


def describe() -> str:
    """一句话说明当前连的是哪个库 —— 自检和启动日志用。"""
    if IS_SQLITE:
        return f"SQLite（{DATABASE_URL.split('///')[-1]}）"
    # MySQL 连接串里有密码，只留 host/db 部分
    return f"MySQL（{DATABASE_URL.split('@')[-1]}）"


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
    print("目标:", describe())
    if health():
        print("[db] 连接正常")
        init_db()
