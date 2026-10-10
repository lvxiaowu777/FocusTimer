"""
AI 拆条目 —— 把用户写的总结原文，拆成一条条知识点。

铁律（见 需求文档 §3.4、设计原则 §7.2）：
    **AI 只做格式，不做内容重构。**
    不添加、不改写、不提炼、不总结 —— 只把原文里已经说过的点，拆出来。

失败处理：
    拆不出来不算事故。总结原文已经存进数据库了，AI 只是锦上添花。
    所以这里所有异常都吞掉返回空列表，绝不让 AI 故障影响记录保存。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 让 `python services/llm.py` 也能直接跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

SYSTEM_PROMPT = """你是一个学习记录的格式化助手。

用户会给你一段他刚学完某块内容后写的总结。你的唯一任务是把这段原文里**已经提到过**的知识点，拆成一条条简短的条目。

严格遵守：
1. 只拆解，不创造 —— 原文没提到的，一个字都不许加
2. 不改写、不提炼、不总结、不美化 —— 用原话里的说法
3. 每条不超过 20 个字，是一个名词性短语（如"最左前缀原则"、"Depends 缓存机制"）
4. 最多 8 条，挑最具体的
5. 原文如果只是流水账没有明确知识点，宁可少拆也不要硬凑

只输出 JSON，不要任何解释：
{"items": ["条目1", "条目2"]}"""


def _parse(text: str) -> list[str]:
    """从模型回复里挖出 {"items": [...]}。容错各种包裹格式。"""
    text = text.strip()
    # 去掉 ```json ... ``` 包裹
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 退一步：直接找第一个 {...}
        brace = re.search(r"\{.*\}", text, re.S)
        if not brace:
            return []
        try:
            data = json.loads(brace.group(0))
        except json.JSONDecodeError:
            return []

    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if str(x).strip()]


def extract_items(raw_text: str, category_name: str = "") -> list[str]:
    """
    总结原文 → 条目列表。

    Args:
        raw_text: 用户写的强制回顾原文
        category_name: 类别名（给模型一点上下文，减少跑偏）

    Returns:
        条目列表；任何失败都返回 []，绝不抛异常打断主流程。
    """
    text = (raw_text or "").strip()
    if not text or not DEEPSEEK_API_KEY:
        return []

    user_prompt = f"所属类别：{category_name or '未分类'}\n\n总结原文：\n{text}"

    try:
        resp = httpx.post(
            f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 400,
                "response_format": {"type": "json_object"},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse(content)

    except Exception as e:                       # noqa: BLE001 —— 故意全吞
        print(f"[llm] 拆条目失败（不影响记录保存）: {e}")
        return []


if __name__ == "__main__":
    demo = "今天看了联合索引的最左前缀原则，踩了个坑：范围查询后面的列用不上索引。顺便复习了 B+ 树的叶子节点链表结构。"
    print("原文:", demo)
    print("拆出:", extract_items(demo, "数据库"))
