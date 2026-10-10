"""
配置加载。

.env 读取顺序（后面的不覆盖前面的）：
  1. 项目自己的 .env    —— 存数据库连接串
  2. 家目录下的 study/.env —— 中央 Key 仓库（DeepSeek 等）

这样 API Key 只维护一份，不用在各项目里复制。
中央仓库的位置可用环境变量 FOCUS_CENTRAL_ENV 覆盖。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller 打包后 OpenSSL legacy provider 找不到 → cryptography 崩溃。
# 放在最顶上，保证任何 import 之前就生效。
os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

# —— 版本号 ——
# 语义化版本 MAJOR.MINOR.PATCH：加功能升 MINOR，修 bug 升 PATCH。
# 打包成 exe 后靠它分辨新旧，也在「设置」窗口里显示。
__version__ = "0.2.0"

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：__file__ 指向临时解压目录，
    # .env 要按 exe 自己所在的位置去找
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent

# 中央 Key 仓库（见 memory：大模型key仓库）。
# 默认取家目录下的 study/.env；想放别处就设 FOCUS_CENTRAL_ENV。
CENTRAL_ENV = Path(os.environ.get("FOCUS_CENTRAL_ENV", Path.home() / "study" / ".env"))


def _load_env(path: Path) -> None:
    """把 .env 里的键值塞进 os.environ。已存在的键不覆盖。"""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env(PROJECT_ROOT / ".env")
_load_env(CENTRAL_ENV)


# —— 数据库 ——
DATABASE_URL: str = os.environ.get("FOCUS_DATABASE_URL", "")

# —— DeepSeek（整理总结用）——
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 注意：产品名「DeepSeek-V4.1-Flash」不是 API 参数，模型名是 deepseek-flash。
# 旧默认值 deepseek-chat 是 V3 系列的模型，已换掉。
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

# —— 资源目录 ——
# 打包后 = exe 所在目录，和 .env 同一套逻辑：
# 音效想换就换个 wav 文件，不用重新打包。
ASSETS_DIR = PROJECT_ROOT / "assets"

# —— 界面 ——
WINDOW_SIZE = 160          # 悬浮窗边长（px）
IDLE_TIMEOUT_HINT = False  # 预留


def check() -> list[str]:
    """自检，返回问题列表（空列表 = 一切正常）。"""
    problems = []
    if not DATABASE_URL:
        problems.append("FOCUS_DATABASE_URL 未配置（见项目 .env）")
    if not DEEPSEEK_API_KEY:
        problems.append("DEEPSEEK_API_KEY 未配置（见 study/.env）")
    return problems


if __name__ == "__main__":
    issues = check()
    if issues:
        print("配置有问题：")
        for i in issues:
            print("  -", i)
    else:
        print("配置正常")
        print("  数据库:", DATABASE_URL.split("@")[-1])
        print("  模型  :", DEEPSEEK_MODEL, "@", DEEPSEEK_BASE_URL)
