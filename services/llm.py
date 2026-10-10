"""
AI 整理 —— 把用户写的总结原文整理成通顺的话，并提炼关键词。

和以前「拆成一条条知识点」的根本区别：
    以前是把原文**切碎**成短条目，叙述本身丢了；
    现在是**保留完整表达**，只做语言层面的整理 —— 去冗余、理顺序、通顺化，
    信息点一个不增、一个不减。

铁律（沿用原设计）：
    **AI 只做格式，不做内容重构。**
    不添加、不发挥、不提炼观点 —— 用户没说过的东西一个字都不许写。

长短分流：
    原文短（≤ SHORT_LIMIT 字）→ 整理成**一整段连续的话**（用户写的就是这种流水账）。
    原文长或本身带 Markdown 标题 → **保留原有层次**只润色行文。
    后者不是理论情况：库里真有一份 16767 字、30 个章节的学习文档，
    硬压成一段会把结构彻底毁掉。

失败处理：
    整理不出来不算事故。总结原文已经存进数据库了，AI 只是锦上添花。
    所以这里所有异常都吞掉返回 ("", [])，绝不让 AI 故障影响记录保存。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 让 `python services/llm.py` 也能直接跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import config

# 超过这个字数就不硬压成一段了，改为保留结构润色。
SHORT_LIMIT = 800

# 判断「原文自带 Markdown 标题结构」：行首 1~4 个 # + 空格
_MD_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+\S", re.M)

_COMMON_RULES = """整理规则（无论长短都必须遵守）：
1. 忠实原意 —— 只整理表达，不改内容。不添加、不删除、不合并任何信息点。
   原文没提过的东西，一个字都不许写。不许自己总结升华、不许补结论。
2. 去冗余 —— 删掉重复的表述、口头禅、语气词、无意义的连接词。
3. 理顺序 —— 把来回跳、想到哪写到哪的叙述理成有逻辑的顺序。
4. 改口语为书面 —— 把零碎的短句连成完整通顺的句子，但保留作者原本的说法和用词习惯。
5. 长度控制在原文的 50%~100%。宁可长一点，也不要为了精简丢掉信息。"""

_SHORT_SHAPE = """输出形状：
原文是随手写的短总结，整理成**一整段连续的话**。
不要分点、不要列表、不要小标题、不要换行。"""

_LONG_SHAPE = """输出形状：
原文较长或本身带有 Markdown 标题结构，说明它已经是组织过的内容。
保留它原有的标题层次：# / ## / ### 原样保留，标题文字不要改写、不要合并、不要删减。
只整理每个标题下面的正文；没有标题的长文按内容自然分成若干段，段落之间用空行隔开。
段落之间可以适当换行，但不要新增原文没有的标题。"""

_KEYWORDS = """关键词：
- 3~8 个，每个不超过 20 个字，是名词性短语（如「最左前缀原则」「Depends 缓存机制」）
- 必须是原文里确实出现过的概念，不要自己发挥
- 挑最具体、最有检索价值的，不要「学习方法」这种空泛的词"""

_JSON_SHAPE = """只输出 JSON，不要任何解释，不要 markdown 代码块：
{"paragraph": "整理后的话", "keywords": ["关键词1", "关键词2"]}"""


def _system_prompt(long_mode: bool) -> str:
    shape = _LONG_SHAPE if long_mode else _SHORT_SHAPE
    return (
        "你是一个学习记录的整理助手。\n\n"
        "用户会给你一段他刚学完某块内容后写的总结。他可能语无伦次、重复、啰嗦、"
        "想到哪写到哪。你的任务是在**完全不改变内容**的前提下，把这段原文整理成通顺的话，"
        "并提炼出这次学到的关键词。\n\n"
        f"{_COMMON_RULES}\n\n{shape}\n\n{_KEYWORDS}\n\n{_JSON_SHAPE}"
    )


def _ai_settings() -> tuple[str, str, str]:
    """
    (base_url, api_key, model)。

    现在从环境变量 / 中央 .env 读；v0.3.0 会改成读安装目录下的 ai_config.json。
    单独抽成函数是为了到时候只改这一处。
    """
    return config.DEEPSEEK_BASE_URL, config.DEEPSEEK_API_KEY, config.DEEPSEEK_MODEL


def _max_tokens(n_chars: int) -> int:
    """
    按原文长度给输出留够空间。

    ★ 两个坑都在这一行里：
      1. 以前固定 400 —— 对 16767 字的原文会直接腰斩。
      2. deepseek-flash **默认开思考模式**，思考的 token 也从这个额度里扣。
         所以哪怕 188 字的输入，给 1200 也可能被思考吃光导致正式输出被截断。
         现在思考已关（见 polish() 请求体里的 thinking 参数），额度纯给正文。
    中文大约 1 字 ≈ 1 token，整理后长度接近原文，留 40% 余量。
    上限 32000（模型 MAX OUTPUT 是 384K，离得很远）。
    """
    return min(32000, max(2000, int(n_chars * 1.4) + 500))


def _timeout(n_chars: int) -> float:
    """长文本生成慢，超时也要跟着放宽 —— 固定 30 秒对长文必扑。"""
    return 60.0 if n_chars <= 2000 else 240.0


def _loads(s: str):
    """尽力把一段文本解析成 JSON；失败返回 None。"""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 退一步：扫出**最外层成对**的 {...}。
    # ★ 不能用 re.search(r"\{.*\}", ...) —— 那是贪婪匹配，会一路吃到最后一个 }；
    #   也不能不管字符串状态，因为正文里的 { } " 都会干扰配对。
    start = s.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _parse(text: str) -> tuple[str, list[str]]:
    """从模型回复里挖出 {"paragraph": ..., "keywords": [...]}。容错各种包裹格式。"""
    text = text.strip()

    data = _loads(text)

    # 有些模型仍会用 ```json ... ``` 把**整个回复**包起来。
    # ★ 只在整个回复以 ``` 开头时才剥。
    #   以前是 re.search(r"```(?:json)?\s*(.+?)\s*```") 全局找第一对围栏 —— 结果会把
    #   正文里自带的代码块当成整个输出：id=30 那份文档正文含 ``` 示意图，
    #   17881 字的正确结果被截成 98 字代码片段，正文全丢、关键词为空。
    if data is None and text.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        body = re.sub(r"\s*```$", "", body)
        data = _loads(body)

    if data is None:
        # 最后退一步：模型可能只吐了纯文本，那就当正文用，关键词留空
        return (text, [])

    if not isinstance(data, dict):
        return ("", [])

    paragraph = str(data.get("paragraph") or "").strip()

    raw_kw = data.get("keywords")
    if not isinstance(raw_kw, list):
        raw_kw = []
    keywords = [str(x).strip() for x in raw_kw if str(x).strip()]

    return (paragraph, keywords)


def polish(raw_text: str, category_name: str = "") -> tuple[str, list[str]]:
    """
    总结原文 → (整理后的段落, 关键词列表)。

    Args:
        raw_text: 用户写的强制回顾原文
        category_name: 类别名（给模型一点上下文，减少跑偏）

    Returns:
        (paragraph, keywords)；任何失败都返回 ("", [])，绝不抛异常打断主流程。
    """
    text = (raw_text or "").strip()
    if not text:
        return ("", [])

    base_url, api_key, model = _ai_settings()
    if not api_key:
        return ("", [])

    long_mode = len(text) > SHORT_LIMIT or bool(_MD_HEADING.search(text))
    user_prompt = f"所属类别：{category_name or '未分类'}\n\n总结原文：\n{text}"

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _system_prompt(long_mode)},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": _max_tokens(len(text)),
                "response_format": {"type": "json_object"},
                # ★ 必须显式关掉思考模式。
                #   deepseek-flash 默认 thinking=enabled/high，而这是个「忠实重写」任务，
                #   不需要推理链，开着有三个坏处：
                #     · 思考 token 吃掉 max_tokens，正式输出被截断（实测 188 字都能截断）
                #     · 思考模式下 temperature 被忽略，0.3 白设
                #     · 长文复现不稳定（16767 字的文档曾被压成 98 字残片）
                "thinking": {"type": "disabled"},
            },
            timeout=_timeout(len(text)),
        )
        resp.raise_for_status()
        payload = resp.json()
        choice = payload["choices"][0]

        # 输出被截断的话，段落会缺尾巴 —— 与其存半截，不如整条不存
        if choice.get("finish_reason") == "length":
            print(f"[llm] 输出被 max_tokens 截断（原文 {len(text)} 字），本次不保存")
            return ("", [])

        content = choice["message"]["content"]
        paragraph, keywords = _parse(content)

        # —— 长度护栏 ——
        # 忠实整理的结果不该比原文短太多（提示词里要求 50%~100%）。
        # 长文被压成一小截 = 模型没照要求做 —— 宁可这条留空等用户重试，
        # 也不能把残缺内容写进库当成果。
        # （16767 字那份文档曾被「成功」存成 98 字残片，就是少了这道闸。）
        if long_mode and paragraph and len(paragraph) < len(text) * 0.5:
            print(
                f"[llm] 整理结果过短（原文 {len(text)} 字 → 段落 {len(paragraph)} 字，"
                f"不足 50%），判定不合格，本次不保存"
            )
            return ("", [])

        return (paragraph, keywords)

    except Exception as e:                       # noqa: BLE001 —— 故意全吞
        print(f"[llm] 整理失败（不影响记录保存）: {e}")
        return ("", [])


if __name__ == "__main__":
    demos = [
        ("短 · 流水账", "今天看了联合索引的最左前缀原则，踩了个坑：范围查询后面的列用不上索引。"
                        "顺便复习了 B+ 树的叶子节点链表结构。然后还看了下 explain 的 type 字段。"),
        ("长 · 带结构", "# 第二章\n\n## 一、两层消息模型\n\n"
                        "这块讲了两层，内层是 ConversationManager，外层是啥我忘了。\n\n"
                        "## 二、状态机\n\n工具调用是个状态机，client 攒 thinking 但不攒 text。"),
    ]
    for title, demo in demos:
        print("=" * 60)
        print(f"[{title}] 原文 {len(demo)} 字")
        para, kws = polish(demo, "数据库")
        print(f"\n整理后 ({len(para)} 字):\n{para}")
        print(f"\n关键词: {kws}")
