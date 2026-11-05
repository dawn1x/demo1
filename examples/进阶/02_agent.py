# -*- coding: utf-8 -*-
"""Agent：路径由模型决定。

循环本身只有四行：
    问模型 -> 执行工具 -> 把结果放回历史 -> 再问
没有任何 if 判断"该不该返工"，那是模型的事。
"""

from common import TOOLS, get_model


def run_agent(model, max_steps=8):
    history = []
    for step in range(1, max_steps + 1):
        decision = model.decide(history)
        action, args = decision["action"], decision.get("args", {})
        reason = decision.get("reason", "")

        print("[%d] 模型决定: %-12s  (%s)" % (step, action, reason))

        if action == "finish":
            print("    最终译文:", args.get("answer"))
            return args.get("answer"), step

        if action not in TOOLS:
            print("    未知工具，忽略:", action)
            continue

        result = TOOLS[action](**args)
        print("    工具返回:", str(result)[:70])
        history.append({"tool": action, "args": args, "result": result})

    print("！达到 max_steps，强制停止 —— 这就是 agent 的典型风险")
    return None, max_steps


def main():
    print("=" * 64)
    print("Agent：循环 + 自主决策")
    print("=" * 64)

    model = get_model()
    print("模型:", type(model).__name__)
    print()

    answer, steps = run_agent(model)

    print()
    print("最终译文：", answer)
    print()
    print("观察点：代码里没有「如果术语错就重译」这种分支。")
    print("       它跑了几步，取决于模型当时看到了什么、决定了什么。")


if __name__ == "__main__":
    main()
