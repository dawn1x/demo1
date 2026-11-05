# -*- coding: utf-8 -*-
"""公共部分：任务、工具、"模型"。

整个仓库只需要记住一句话：

    工具（tools）是 workflow 和 agent 共用的；
    唯一差别是 —— 谁决定下一步调哪个工具。

  workflow：由下面 01_workflow.py 里的 if/for 决定（你写的）
  agent   ：由模型决定（运行时才有答案）
"""

import json
import os

# ---------------------------------------------------------------- 任务定义
# 同一个任务、同一套工具，两个文件共用，方便对比。
ZHTEXT = "缓存击穿是指某个热点 key 过期时，大量并发请求同时打到数据库。"

# 术语表：(中文, 正确译法, 常见错误译法)
GLOSSARY = [
    ("缓存击穿", "cache breakdown", ["cache penetration"]),
    ("热点 key", "hot key", []),
]

GOOD_EN = (
    "Cache breakdown happens when a hot key expires and a large number of "
    "concurrent requests hit the database at once."
)
BAD_EN = (
    "Cache penetration happens when a hot key expires and a large number of "
    "concurrent requests hit the database at once."
)


# ---------------------------------------------------------------- 工具
# 真实场景里这些会是：调翻译 API、查数据库、读文件、发请求……
# 这里用固定返回值模拟，好让流程可复现。

def translate(text, fixes=None):
    """把中文译成英文。

    fixes: {"缓存击穿": "cache breakdown"} 形式，非空表示"按纠正重译"。
    """
    fixes = fixes or {}
    for zh, good, _ in GLOSSARY:
        if fixes.get(zh) == good:
            return GOOD_EN
    return BAD_EN


def check_terms(english):
    """按术语表检查译文，返回所有不合规的条目（空列表 = 通过）。"""
    problems = []
    for zh, good, bads in GLOSSARY:
        for bad in bads:
            if bad.lower() in english.lower():
                problems.append({"zh": zh, "found": bad, "should": good})
    return problems


TOOLS = {
    "translate": translate,
    "check_terms": check_terms,
}


# ---------------------------------------------------------------- 假模型
class FakeModel:
    """不需要 API key 的"假模型"。

    它的 decide() 就是 workflow 里那段 if/else，只不过被搬进了"模型"这一侧。
    这正是关键：逻辑没变，变的是它在谁手里。

    换成真模型时，01/02 两个脚本一行都不用改。
    """

    def decide(self, history):
        """看一眼历史，决定下一步做什么。返回 {"action", "args", "reason"}。"""
        translates = [h for h in history if h["tool"] == "translate"]
        checks = [h for h in history if h["tool"] == "check_terms"]

        if not translates:
            return {
                "action": "translate",
                "args": {"text": ZHTEXT},
                "reason": "还没有译文，先翻译",
            }

        if len(checks) < len(translates):
            return {
                "action": "check_terms",
                "args": {"english": translates[-1]["result"]},
                "reason": "有新译文，按术语表检查",
            }

        if checks[-1]["result"]:
            fixes = {p["zh"]: p["should"] for p in checks[-1]["result"]}
            return {
                "action": "translate",
                "args": {"text": ZHTEXT, "fixes": fixes},
                "reason": "检查发现术语不合规，返工重译",
            }

        return {
            "action": "finish",
            "args": {"answer": translates[-1]["result"]},
            "reason": "检查通过，收敛",
        }


# ---------------------------------------------------------------- 真模型
class OpenAIModel:
    """真模型：把 FakeModel 换掉即可，循环逻辑不变。

    需要 OPENAI_API_KEY；换 DeepSeek / 其他兼容接口时再设 OPENAI_BASE_URL。
    用法：USE_REAL=1 python3 examples/02_agent.py
    """

    SYSTEM = (
        "你是一个执行翻译任务的 agent。每一步只能输出一个 JSON 对象，不要输出别的。\n"
        "可选动作：\n"
        '  {"action": "translate", "args": {"text": "...", "fixes": {"中文":"译法"}}, "reason": "..."}\n'
        '  {"action": "check_terms", "args": {"english": "..."}, "reason": "..."}\n'
        '  {"action": "finish", "args": {"answer": "..."}, "reason": "..."}\n'
        "规则：只有 check_terms 通过后才能 finish；有问题就带着 fixes 重新 translate。"
    )

    def __init__(self):
        from openai import OpenAI

        self.client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
        self.model = os.getenv("MODEL", "gpt-4o-mini")

    def decide(self, history):
        msgs = [{"role": "system", "content": self.SYSTEM}]
        msgs.append({
            "role": "user",
            "content": "原文：%s\n术语表：%s" % (ZHTEXT, json.dumps(GLOSSARY, ensure_ascii=False)),
        })
        for h in history:
            msgs.append({"role": "assistant", "content": json.dumps(
                {"action": h["tool"], "args": h["args"]}, ensure_ascii=False)})
            msgs.append({"role": "user", "content": json.dumps(
                {"result": h["result"]}, ensure_ascii=False)})
        msgs.append({"role": "user", "content": "下一步？只输出 JSON。"})

        resp = self.client.chat.completions.create(
            model=self.model, messages=msgs, temperature=0)
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        return json.loads(text)


def get_model():
    """USE_REAL=1 则用真模型，否则用假模型。"""
    if os.getenv("USE_REAL") == "1":
        return OpenAIModel()
    return FakeModel()
