"""
配置加载。

AI 配置（模型 / Key）优先级，从高到低：
  1. 安装目录下的 ai_config.json —— 界面上填的，打包分发时是空的，谁用谁填
  2. 环境变量 / .env           —— 兼容「Key 只维护一份」的老用法
  3. 内置预设默认值

.env 读取顺序（后面的不覆盖前面的）：
  1. 项目自己的 .env    —— 存数据库连接串
  2. 家目录下的 study/.env —— 中央 Key 仓库（DeepSeek 等）

中央仓库的位置可用环境变量 FOCUS_CENTRAL_ENV 覆盖。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# PyInstaller 打包后 OpenSSL legacy provider 找不到 → cryptography 崩溃。
# 放在最顶上，保证任何 import 之前就生效。
os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

# —— 版本号 ——
# 语义化版本 MAJOR.MINOR.PATCH：加功能升 MINOR，修 bug 升 PATCH。
# 打包成 exe 后靠它分辨新旧，也在「设置」窗口里显示。
__version__ = "0.3.0"

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

# —— AI（整理总结用）——
# 注意：产品名「DeepSeek-V4.1-Flash」不是 API 参数，模型名是 deepseek-flash。
# 旧默认值 deepseek-chat 是 V3 系列的模型，已换掉。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

# 界面里填的 AI 配置存这儿 —— 安装目录下的一个 JSON 文件。
# 刻意**不存数据库**：打包分发时这个文件是空的，谁用谁自己填，
# 于是就不会再出现「exe 必须带着 .env 才能跑」的问题。
AI_CONFIG_NAME = "ai_config.json"

# 预设。设置窗口里是个下拉框，用户也可以选「自定义」自己填 URL / 模型。
PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek 官方 · V4.1 Flash",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "note": "Key 在 platform.deepseek.com 申请",
    },
}
DEFAULT_PRESET = "deepseek"

# 设置窗口里「自定义」那一项的值（不是一个真预设，只是个标记）
PRESET_CUSTOM = "__custom__"

# 环境变量里拿到的值（老用法：Key 只维护一份在 study/.env）。
# 真正的取值走 ai_settings()，它会把 ai_config.json 的优先级排在最前。
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)

# —— 资源目录 ——
# 打包后 = exe 所在目录，和 .env 同一套逻辑：
# 音效想换就换个 wav 文件，不用重新打包。
ASSETS_DIR = PROJECT_ROOT / "assets"

# —— 界面 ——
WINDOW_SIZE = 160          # 悬浮窗边长（px）
IDLE_TIMEOUT_HINT = False  # 预留


# ────────────────────────── AI 配置 ──────────────────────────

def ai_config_path() -> Path:
    """ai_config.json 的完整路径（跟着 exe / 项目根目录走）。"""
    return PROJECT_ROOT / AI_CONFIG_NAME


def load_ai_config() -> dict[str, str]:
    """
    读界面里保存的 AI 配置。

    文件不存在、读不动、内容不是 JSON —— 一律返回空 dict（当作「没配过」），
    绝不抛异常：配置文件坏了不该让整个程序起不来。
    """
    path = ai_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[config] {AI_CONFIG_NAME} 读不动（{e}），当作没配置")
        return {}
    if not isinstance(data, dict):
        print(f"[config] {AI_CONFIG_NAME} 内容不是对象，当作没配置")
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def save_ai_config(
    base_url: str, api_key: str, model: str, preset: str = ""
) -> Path:
    """把界面上填的 AI 配置写进 ai_config.json，返回文件路径。"""
    path = ai_config_path()
    data = {
        "preset": (preset or "").strip(),
        "base_url": (base_url or "").strip(),
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def ai_settings() -> tuple[str, str, str]:
    """
    (base_url, api_key, model) —— AI 调用的最终取值。

    优先级：ai_config.json（界面填的） > 环境变量 / 中央 .env > 内置默认。

    保留环境变量这条路有两个用处：兼容以前「Key 只维护一份在 study/.env」
    的用法；以及不开界面、直接用脚本跑批量整理时也能取到配置。
    """
    file_cfg = load_ai_config()
    preset = PRESETS.get(file_cfg.get("preset", ""), {})

    base_url = (
        file_cfg.get("base_url")
        or os.environ.get("DEEPSEEK_BASE_URL")
        or preset.get("base_url")
        or DEFAULT_BASE_URL
    )
    api_key = file_cfg.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
    model = (
        file_cfg.get("model")
        or os.environ.get("DEEPSEEK_MODEL")
        or preset.get("model")
        or DEFAULT_MODEL
    )
    return base_url, api_key, model


def ai_config_source() -> str:
    """当前 AI 配置是从哪儿来的 —— 设置窗口和自检里显示给用户看。"""
    if load_ai_config().get("api_key"):
        return f"{AI_CONFIG_NAME}（界面里填的）"
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "环境变量 / study/.env"
    return "未配置"


def check() -> list[str]:
    """自检，返回问题列表（空列表 = 一切正常）。"""
    problems = []
    if not DATABASE_URL:
        problems.append("FOCUS_DATABASE_URL 未配置（见项目 .env）")
    if not ai_settings()[1]:
        problems.append(
            "AI 的 API Key 未配置 —— 打开「设置 → AI」填一个，"
            f"或设 DEEPSEEK_API_KEY（也可以放在 study/.env）"
        )
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
    base, key, model = ai_settings()
    print(f"  AI    : {model} @ {base}")
    print(f"  Key   : {'已配置' if key else '未配置'}（来源：{ai_config_source()}）")
    print(f"  配置文件:", ai_config_path(), "(存在)" if ai_config_path().exists() else "(不存在)")
