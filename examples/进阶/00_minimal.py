# -*- coding: utf-8 -*-
"""最小对照：workflow vs agent。一个文件，没有类，没有第三方库。

任务：把中文译成英文。术语表规定「缓存击穿」必须译成 cache breakdown。

跑法：
    python3 examples/00_minimal.py                  # 正常跑
    python3 examples/00_minimal.py --no-retry       # 拿掉 workflow 里"要不要返工"的判断
    python3 examples/00_minimal.py --dumb-model     # 拿掉模型里"要不要返工"的判断
    python3 examples/00_minimal.py --no-retry --dumb-model   # 两个都拿掉
"""

import sys

NO_RETRY = "--no-retry" in sys.argv        # 让 workflow 不返工
DUMB_MODEL = "--dumb-model" in sys.argv    # 让模型不返工


# ============ 工具（两边共用，一模一样） ============

def translate(fixed=False):
    """翻译。fixed=False 时故意译错，制造"需要返工"的局面。"""
    if fixed:
        return "Cache breakdown happens when a hot key expires."
    return "Cache penetration happens when a hot key expires."   # 故意错


def check(en):
    """检查译文，返回 True 表示术语有问题。"""
    return "cache penetration" in en.lower()


# ============ 做法 A：workflow ============
# 走哪几步，写在代码里。

def run_workflow():
    print("--- A. workflow（流程由代码决定）---")

    en = translate()                       # 第 1 步
    print("1. 翻译 ->", en)

    bad = check(en)                        # 第 2 步
    print("2. 检查 ->", "术语错了" if bad else "通过")

    if bad and not NO_RETRY:               # 第 3 步：这个 if 是"我"写的
        en = translate(fixed=True)
        print("3. 返工 ->", en)
    elif bad:
        print("3. (返工被关掉了，直接交付错的译文)")

    return en


# ============ 做法 B：agent ============
# 每一步问一次模型，由它决定。

def model(state):
    """假模型。真实项目里，这个函数就是一次大模型 API 调用。"""
    if "en" not in state:
        return "translate"                 # 还没有译文
    if "problems" not in state:
        return "check"                     # 有译文，该检查了
    if state["problems"]:
        return "stop" if DUMB_MODEL else "translate"   # 查出问题：返工 or 收工
    return "stop"                          # 没问题，收工


def run_agent(max_steps=8):
    print("--- B. agent（流程由模型决定）---")

    state = {}
    for step in range(1, max_steps + 1):
        action = model(state)
        print("%d. 模型决定 -> %s" % (step, action))

        if action == "stop":
            return state["en"]
        if action == "translate":
            state = {"en": translate(state.get("problems", False))}
        elif action == "check":
            state["problems"] = check(state["en"])

    print("！到 max_steps 还没停，强制结束")
    return None


if __name__ == "__main__":
    print("=" * 52)
    a = run_workflow()
    print()
    b = run_agent()
    print("=" * 52)

    ok = lambda s: "正确 ✅" if s and "breakdown" in s else "错误 ❌"
    print("workflow :", ok(a), a)
    print("agent    :", ok(b), b)
    print()
    print("两个程序的工具完全一样，代码量也差不多。")
    print("唯一的区别：A 的 if 在 run_workflow() 里（我写的），")
    print("            B 的 if 在 model() 里（真实项目里 = 大模型）。")
