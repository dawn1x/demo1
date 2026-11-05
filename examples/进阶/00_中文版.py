# -*- coding: utf-8 -*-
"""给没学过 Python 的人看的版本。

函数名、变量名全用中文，读得懂中文就读得懂这段代码。
跑法：python3 examples/00_中文版.py
"""


def 翻译(要改正=False):
    """把中文翻成英文。要改正=True 时返回正确的术语。"""
    if 要改正:
        return "Cache breakdown happens when a hot key expires."
    return "Cache penetration happens when a hot key expires."


def 检查(译文):
    """译文里有 penetration 就算术语错，返回 True。"""
    return "penetration" in 译文


# ==================== 第一种：workflow ====================
print("=== 第一种：workflow（步骤写死）===")

译文 = 翻译()                        # 第 1 步：翻译
print("第 1 步 翻译：", 译文)

问题 = 检查(译文)                     # 第 2 步：检查
print("第 2 步 检查：", "有错" if 问题 else "通过")

if 问题:                             # 第 3 步：这个 if 是写代码的人决定的
    译文 = 翻译(要改正=True)
    print("第 3 步 返工：", 译文)

print("最终译文：", 译文)


# ==================== 第二种：agent ====================
def 模型(状态):
    """这里是"大模型"站的位置。它看当前状态，决定下一步。"""
    if "译文" not in 状态:
        return "翻译"
    if "问题" not in 状态:
        return "检查"
    if 状态["问题"]:
        return "返工"
    return "结束"


print()
print("=== 第二种：agent（模型决定）===")

状态 = {}
for 次数 in range(1, 9):
    动作 = 模型(状态)
    print("第 %d 步 模型决定：" % 次数, 动作)

    if 动作 == "结束":
        break
    if 动作 == "翻译":
        状态 = {"译文": 翻译()}
    elif 动作 == "检查":
        状态["问题"] = 检查(状态["译文"])
    elif 动作 == "返工":
        状态 = {"译文": 翻译(要改正=True)}

print("最终译文：", 状态["译文"])
