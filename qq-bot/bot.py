# -*- coding: utf-8 -*-
"""大肥鱼 · QQ 群机器人（OneBot v11）

整条管线是写死的 —— 这就是 workflow：

    收到群消息
      → 存进上下文
      → 是不是在叫我？（@ 或名字）
      → 冷却时间到了吗？
      → 拼人设 + 最近 20 条聊天 → 调模型
      → 发回群里

跑法：
    python3 qq-bot/bot.py --fake    # 不连 QQ，本地打字测试人设
    python3 qq-bot/bot.py           # 连 NapCat（默认 ws://127.0.0.1:3001）
"""

import asyncio
import json
import os
import random
import sys
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from persona import SYSTEM_PROMPT


# ---------------------------------------------------------------- 配置
def load_env(path):
    """读 .env（每行 KEY=VALUE），避免额外装依赖。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env(os.path.join(HERE, ".env"))

WS_URL = os.getenv("ONEBOT_WS", "ws://127.0.0.1:3001")
WS_TOKEN = os.getenv("ONEBOT_TOKEN", "").strip()
MODEL = os.getenv("MODEL", "gpt-4o-mini")
NAMES = [n.strip() for n in os.getenv("BOT_NAMES", "肥鱼,大肥鱼").split(",") if n.strip()]
BOT_NICK = "大肥鱼"                                # 它自己的名字，用于区分"谁说的"
COOLDOWN = float(os.getenv("COOLDOWN", "3"))      # 同一群最短回复间隔（秒）
MAX_HISTORY = 20                                   # 带多少条上下文
MAX_REPLY_LEN = 120                                # 回复过长截断


# ---------------------------------------------------------------- 模型
CANNED = [
    "典",
    "绷不住了",
    "你在说什么，我鱼都听懵了",
    "确实",
    "6",
    "有一说一，这话我接不住",
]


async def ask_model(history, text, nickname):
    """调模型生成一句回复。没配 key 时用兜底句子，方便先测通链路。"""
    if not os.getenv("OPENAI_API_KEY"):
        return random.choice(CANNED)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history:
        # 自己说过的话用 assistant 角色，且不带昵称前缀，否则模型会模仿出「大肥鱼：…」
        if item["nick"] == BOT_NICK:
            messages.append({"role": "assistant", "content": item["text"]})
        else:
            messages.append({"role": "user", "content": "%s: %s" % (item["nick"], item["text"])})
    messages.append({
        "role": "user",
        "content": "%s: %s\n（以群友身份回一句，最多 40 字，别解释）" % (nickname, text),
    })

    resp = await client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0.9, max_tokens=120)
    reply = (resp.choices[0].message.content or "").strip()
    return reply[:MAX_REPLY_LEN]


# ---------------------------------------------------------------- 消息处理
def plain_text(message):
    """从 OneBot 消息段里取出纯文字。"""
    parts = []
    for seg in message or []:
        if seg.get("type") == "text":
            parts.append(seg.get("data", {}).get("text", ""))
    return "".join(parts).strip()


def should_reply(event, text, self_id):
    """规则写死：被 @ 或者叫了名字才回。"""
    for seg in event.get("message") or []:
        if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == self_id:
            return True
    return any(name in text for name in NAMES)


async def send_group(ws, group_id, text):
    payload = {
        "action": "send_group_msg",
        "params": {"group_id": group_id, "message": text},
        "echo": "reply",
    }
    await ws.send(json.dumps(payload, ensure_ascii=False))


async def handle_event(ws, event, state):
    if event.get("post_type") != "message":
        return
    if event.get("message_type") != "group":
        return

    group_id = event["group_id"]
    self_id = str(event.get("self_id", ""))
    sender = event.get("sender") or {}
    nickname = sender.get("card") or sender.get("nickname") or "某人"
    text = plain_text(event.get("message"))
    if not text:
        return

    history = state["history"].setdefault(group_id, deque(maxlen=MAX_HISTORY))
    history.append({"nick": nickname, "text": text})

    if not should_reply(event, text, self_id):
        return

    last = state["last_reply"].get(group_id, 0)
    if time.time() - last < COOLDOWN:
        print("[跳过] 冷却中，%.1f 秒后再说话" % (COOLDOWN - (time.time() - last)))
        return
    state["last_reply"][group_id] = time.time()

    print("[收到] 群 %s | %s: %s" % (group_id, nickname, text))
    try:
        reply = await ask_model(list(history)[:-1], text, nickname)
    except Exception as exc:                       # 模型挂了也要活
        print("[模型出错]", exc)
        return
    if not reply:
        return

    print("[回复]", reply)
    await send_group(ws, group_id, reply)
    # 把自己说的话也记进上下文，否则它会忘了刚说过什么、反复重复
    history.append({"nick": "大肥鱼", "text": reply})


# ---------------------------------------------------------------- 两种模式
async def run_fake():
    """本地模拟：不连 QQ，直接打字看它怎么回。"""
    print("模拟模式（不连 QQ）。直接打字回车，Ctrl-D 退出。")
    if not os.getenv("OPENAI_API_KEY"):
        print("提示：没配 OPENAI_API_KEY，只能用兜底句子，回复会很傻。\n")

    history = deque(maxlen=MAX_HISTORY)
    while True:
        try:
            text = input("你 > ").strip()
        except EOFError:
            print()
            break
        if not text:
            continue
        try:
            reply = await ask_model(list(history), text, "你")
        except Exception as exc:
            print("模型出错:", exc)
            continue
        print("大肥鱼 >", reply, "\n")
        history.append({"nick": "你", "text": text})
        history.append({"nick": "大肥鱼", "text": reply})


async def run_bot():
    """连 OneBot 正向 WebSocket。"""
    import websockets

    # 不同版本参数名不一样：新版本叫 additional_headers，老版本叫 extra_headers
    headers = {"Authorization": "Bearer " + WS_TOKEN} if WS_TOKEN else None
    kwargs = {"max_size": 2 ** 24}
    if headers:
        try:
            websockets.connect(WS_URL, additional_headers=headers)
            kwargs["additional_headers"] = headers
        except TypeError:
            kwargs["extra_headers"] = headers

    state = {"history": {}, "last_reply": {}}
    while True:
        try:
            print("连接", WS_URL, "...")
            async with websockets.connect(WS_URL, **kwargs) as ws:
                print("已连接。在群里 @ 它 或叫它名字就会说话。")
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    asyncio.create_task(handle_event(ws, event, state))
        except Exception as exc:
            print("连接断开：", exc, "，5 秒后重连")
            await asyncio.sleep(5)


if __name__ == "__main__":
    if "--fake" in sys.argv:
        asyncio.run(run_fake())
    else:
        asyncio.run(run_bot())
