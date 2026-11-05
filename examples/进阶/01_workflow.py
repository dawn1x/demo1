# -*- coding: utf-8 -*-
"""Workflow：路径写在代码里。

读代码的顺序 = 它执行的顺序。
你不需要运行它，就能说出它一定会走哪几步、最多重试几次。
"""

from common import ZHTEXT, check_terms, translate


def main():
    print("=" * 64)
    print("Workflow：三步固定流程")
    print("=" * 64)

    # 第 1 步：翻译
    en = translate(ZHTEXT)
    print("1) translate    ->", en[:58], "...")

    # 第 2 步：查术语
    problems = check_terms(en)
    print("2) check_terms  ->", problems if problems else "通过")

    # 第 3 步：有问题就返工。分支和次数都是写死的。
    if problems:
        fixes = {p["zh"]: p["should"] for p in problems}
        en = translate(ZHTEXT, fixes=fixes)
        print("3) 返工         ->", en[:58], "...")
    else:
        print("3) 跳过返工")

    print()
    print("最终译文：", en)
    print()
    print("观察点：走哪几步、重试几次，全在 if 里写着。")
    print("       模型只负责产出文字，不参与决定流程。")


if __name__ == "__main__":
    main()
