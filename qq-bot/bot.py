# -*- coding: utf-8 -*-
"""大肥鱼 · QQ 群机器人（OneBot v11）

三个阶段的能力，按开关启用：

    1. 被叫才回（workflow）       —— 一直开着
    2. 会用工具（agent 循环）      —— USE_TOOLS=1
    3. 自己判断要不要插话（自主）  —— AUTO_CHANCE>0

跑法：
    python3 qq-bot/bot.py --fake    # 不连 QQ，本地打字测
    python3 qq-bot/bot.py           # 连 NapCat
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

import memes
import memory
from persona import SYSTEM_PROMPT
from tools import TOOL_SCHEMAS, run_tool


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
MODEL = os.getenv("MODEL", "deepseek-chat")
NAMES = [n.strip() for n in os.getenv("BOT_NAMES", "肥鱼,大肥鱼").split(",") if n.strip()]
BOT_NICK = "大肥鱼"
COOLDOWN = float(os.getenv("COOLDOWN", "3"))
MAX_HISTORY = 20
MAX_REPLY_LEN = 200

USE_TOOLS = os.getenv("USE_TOOLS", "1") == "1"
MAX_TOOL_STEPS = 3

AUTO_CHANCE = float(os.getenv("AUTO_CHANCE", "0"))      # 每条消息插话的概率，0 = 不插话
AUTO_COOLDOWN = float(os.getenv("AUTO_COOLDOWN", "600"))  # 同群两次插话最短间隔（秒）
AUTO_MAX_PER_HOUR = int(os.getenv("AUTO_MAX_PER_HOUR", "3"))


# ---------------------------------------------------------------- 工具
# 记忆类的工具跟 memory.py 绑定，所以写在这里
MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "把某个群友的事记下来。有人骂你、哄你、说自己是谁、说过重要的事，就记一条。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nick": {"type": "string", "description": "群昵称"},
                    "content": {"type": "string", "description": "记什么，一句话"},
                    "kind": {
                        "type": "string",
                        "enum": ["grudge", "praise", "fact"],
                        "description": "grudge=得罪过你，praise=对你好，fact=关于他的事实",
                    },
                },
                "required": ["nick", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "翻某个群友以前的记录。想不起来这人是谁、干过什么时用。",
            "parameters": {
                "type": "object",
                "properties": {"nick": {"type": "string", "description": "群昵称"}},
                "required": ["nick"],
            },
        },
    },
]

MEME_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "send_meme",
            "description": "发一张表情包。别每句都发，气氛到了、要表达情绪时才发。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "想要什么样的表情包，用中文描述。比如「无语」「笑死」"
                                       "「狗头」「嘲讽」「猫猫 震惊」。可以带主体。",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

ALL_TOOLS = TOOL_SCHEMAS + MEMORY_TOOLS + MEME_TOOL


def run_any_tool(name, args, images=None):
    """执行工具。images 用来收集本次要发的表情包图片。"""
    if name == "remember":
        return memory.remember(args.get("nick", ""), args.get("content", ""),
                               args.get("kind", "fact"))
    if name == "recall":
        return memory.recall(args.get("nick", ""))
    if name == "send_meme":
        query = args.get("query") or ""
        data, src = memes.get_meme(query)
        if not data:
            return "没搜到合适的表情包，这次先不发"
        if images is not None:
            images.append(data)
        return "已发一张「%s」表情包" % query
    return run_tool(name, args)


# ---------------------------------------------------------------- 模型
CANNED = ["典", "绷不住了", "确实", "6", "有一说一，这话我接不住"]


def _client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)


def _build_messages(history, text, nickname, user_id=None):
    """拼提示词：人设 + 此人画像 + 最近聊天 + 当前这句。"""
    system = SYSTEM_PROMPT
    prof = memory.profile(nickname, user_id)
    if prof:
        system += "\n\n你对「%s」的印象：%s" % (nickname, prof)

    messages = [{"role": "system", "content": system}]
    for item in history:
        if item["nick"] == BOT_NICK:
            messages.append({"role": "assistant", "content": item["text"]})
        else:
            messages.append({"role": "user", "content": "%s: %s" % (item["nick"], item["text"])})
    messages.append({
        "role": "user",
        "content": "%s: %s\n（以群友身份回一两句，最多 80 字，像真人聊天，别解释。需要事实/天气/梗就先用工具查；想配个表情包就调 send_meme）"
                   % (nickname, text),
    })
    return messages


async def ask_model(history, text, nickname, user_id=None):
    """调模型生成回复。返回 (文字, 要发的表情包图片列表)。"""
    if not os.getenv("OPENAI_API_KEY"):
        return random.choice(CANNED), []

    client = _client()
    messages = _build_messages(history, text, nickname, user_id)
    tools = ALL_TOOLS if USE_TOOLS else None
    steps = MAX_TOOL_STEPS if USE_TOOLS else 1
    images = []

    for _ in range(steps):
        kwargs = {"model": MODEL, "messages": messages, "temperature": 0.9, "max_tokens": 300}
        if tools:
            kwargs["tools"] = tools
        resp = await client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return (msg.content or "").strip()[:MAX_REPLY_LEN], images

        # 想调工具：先把它的话放回历史，再逐个执行
        messages.append(msg.model_dump(exclude_none=True))
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except ValueError:
                args = {}
            result = run_any_tool(call.function.name, args, images)
            print("    [工具] %s(%s) -> %s" % (call.function.name, args, result[:80]))
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })

    # 工具用完还没收敛，再要一次纯回答
    resp = await client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0.9, max_tokens=300)
    return (resp.choices[0].message.content or "").strip()[:MAX_REPLY_LEN], images


# ---------------------------------------------------------------- 自主插话
AUTO_PROMPT = """你在看一个 QQ 群聊天。你是群友「大肥鱼」，贴吧老哥那套嘴脸。

现在没人叫你。你判断一下：这时候插一句合适吗？

规则：
- 别人在认真讨论正事、指名道姓聊天、或者聊天已经结束，就别插。
- 有人抛了个能被吐槽的点、有人在感慨、有人问了个明显没人接的问题，可以插。
- 插也只插一句，最多 25 个字，别解释，别说"我觉得"。
- 拿不准就别插。

只输出一个 JSON，不要别的：
{"speak": true 或 false, "reply": "要插的话写这里"}"""


def auto_allowed(state, group_id):
    """粗筛：概率 + 冷却 + 每小时上限。先过这层再问模型，省钱。"""
    if AUTO_CHANCE <= 0:
        return False
    now = time.time()
    if now - state["last_auto"].get(group_id, 0) < AUTO_COOLDOWN:
        return False
    hour_ago = now - 3600
    recent = [t for t in state["auto_times"].get(group_id, []) if t > hour_ago]
    if len(recent) >= AUTO_MAX_PER_HOUR:
        return False
    return random.random() < AUTO_CHANCE


async def maybe_chime_in(history, state, group_id):
    """让模型看一眼最近的聊天，决定要不要自己插话。"""
    if not os.getenv("OPENAI_API_KEY") or not auto_allowed(state, group_id):
        return None

    recent = list(history)[-6:]
    if len(recent) < 3:
        return None

    lines = "\n".join("%s: %s" % (h["nick"], h["text"]) for h in recent)
    try:
        resp = await _client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": AUTO_PROMPT},
                      {"role": "user", "content": lines}],
            temperature=0.8, max_tokens=120)
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if "\n" in text else text
        data = json.loads(text)
    except Exception as exc:
        print("[自主判断] 跳过：", exc)
        return None

    if not data.get("speak"):
        return None
    reply = (data.get("reply") or "").strip()[:MAX_REPLY_LEN]
    if not reply:
        return None

    now = time.time()
    state["last_auto"][group_id] = now
    state["auto_times"].setdefault(group_id, []).append(now)
    return reply


# ---------------------------------------------------------------- 消息处理
def plain_text(message):
    parts = []
    for seg in message or []:
        if seg.get("type") == "text":
            parts.append(seg.get("data", {}).get("text", ""))
    return "".join(parts).strip()


def should_reply(event, text, self_id):
    """被 @ 或者叫了名字才回。"""
    for seg in event.get("message") or []:
        if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == self_id:
            return True
    return any(name in text for name in NAMES)


async def send_group(ws, group_id, text, images=None):
    """发一句文字，可以顺带一张表情包（合成一条消息发出去）。"""
    if images:
        message = [{"type": "text", "data": {"text": text}}]
        message.extend(memes.to_segment(img) for img in images[:1])
    else:
        message = text
    await ws.send(json.dumps({
        "action": "send_group_msg",
        "params": {"group_id": group_id, "message": message},
        "echo": "reply",
    }, ensure_ascii=False))


async def handle_event(ws, event, state):
    if event.get("post_type") != "message" or event.get("message_type") != "group":
        return

    group_id = event["group_id"]
    self_id = str(event.get("self_id", ""))
    sender = event.get("sender") or {}
    nickname = sender.get("card") or sender.get("nickname") or "某人"
    user_id = sender.get("user_id")
    text = plain_text(event.get("message"))
    if not text:
        return

    history = state["history"].setdefault(group_id, deque(maxlen=MAX_HISTORY))
    history.append({"nick": nickname, "text": text, "uid": user_id})

    # ---- 路径 A：被叫了，正经回 ----
    if should_reply(event, text, self_id):
        last = state["last_reply"].get(group_id, 0)
        if time.time() - last < COOLDOWN:
            print("[跳过] 冷却中，%.1f 秒后再说话" % (COOLDOWN - (time.time() - last)))
            return
        state["last_reply"][group_id] = time.time()

        print("[收到] 群 %s | %s: %s" % (group_id, nickname, text))
        try:
            reply, images = await ask_model(list(history)[:-1], text, nickname, user_id)
        except Exception as exc:
            print("[模型出错]", exc)
            return
        if not reply and not images:
            return
        print("[回复]", reply, ("+ 表情包 x%d" % len(images)) if images else "")
        await send_group(ws, group_id, reply or "", images)
        history.append({"nick": BOT_NICK, "text": reply})
        return

    # ---- 路径 B：没叫它，看心情插不插话 ----
    try:
        reply = await maybe_chime_in(history, state, group_id)
    except Exception as exc:
        print("[自主判断出错]", exc)
        return
    if reply:
        print("[自主插话] 回应 %s: %s -> %s" % (nickname, text, reply))
        await send_group(ws, group_id, reply)
        history.append({"nick": BOT_NICK, "text": reply})


# ---------------------------------------------------------------- 两种运行模式
async def run_fake():
    """本地模拟：不连 QQ，直接打字看它怎么回。"""
    print("模拟模式（不连 QQ）。直接打字回车，Ctrl-D 退出。")
    print("工具：%s | 记忆：%s" % ("开" if USE_TOOLS else "关", memory.stats()))
    if not os.getenv("OPENAI_API_KEY"):
        print("提示：没配 OPENAI_API_KEY，只能用兜底句子。\n")

    history = deque(maxlen=MAX_HISTORY)
    while True:
        try:
            text = input("你 > ").strip()
        except EOFError:
            print()
            break
        if not text:
            continue
        history.append({"nick": "你", "text": text, "uid": "self"})
        try:
            reply, images = await ask_model(list(history)[:-1], text, "你", "self")
        except Exception as exc:
            print("模型出错:", exc)
            continue
        print("大肥鱼 >", reply)
        if images:
            print("        [表情包] 发了 %d 张" % len(images))
        print()
        history.append({"nick": BOT_NICK, "text": reply})


async def run_bot():
    """连 OneBot 正向 WebSocket。"""
    import websockets

    headers = {"Authorization": "Bearer " + WS_TOKEN} if WS_TOKEN else None
    kwargs = {"max_size": 2 ** 24}
    if headers:
        try:
            websockets.connect(WS_URL, additional_headers=headers)
            kwargs["additional_headers"] = headers
        except TypeError:
            kwargs["extra_headers"] = headers

    state = {"history": {}, "last_reply": {}, "last_auto": {}, "auto_times": {}}
    while True:
        try:
            print("连接", WS_URL, "...")
            async with websockets.connect(WS_URL, **kwargs) as ws:
                print("已连接。工具 %s，自主插话概率 %.0f%%，记忆 %s"
                      % ("开" if USE_TOOLS else "关", AUTO_CHANCE * 100, memory.stats()))
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
