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
import base64
import json
import os
import random
import sys
import time
import urllib.request
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

# 表情包存档：写到 NapCat 容器也能看到的共享目录，再调 add_custom_face 存进 QQ 收藏表情
MEME_SAVE = os.getenv("MEME_SAVE", "1") == "1"
SHARE_DIR = os.getenv("NAPCAT_SHARE_DIR", "/opt/dafeiyu/napcat/cache")
SHARE_PATH = os.getenv("NAPCAT_SHARE_PATH", "/app/napcat/cache")

# 连接参数，工具调 API 时另开一条连接用
WS_KWARGS = {}

USE_TOOLS = os.getenv("USE_TOOLS", "1") == "1"
MAX_TOOL_STEPS = 3

AUTO_CHANCE = float(os.getenv("AUTO_CHANCE", "0.3"))     # 每条消息被考虑插话的概率，0 = 不插话
AUTO_COOLDOWN = float(os.getenv("AUTO_COOLDOWN", "120"))  # 同群两次插话最短间隔（秒）
AUTO_MAX_PER_HOUR = int(os.getenv("AUTO_MAX_PER_HOUR", "10"))
AUTO_ALIVE_WINDOW = float(os.getenv("AUTO_ALIVE_WINDOW", "180"))  # 多久内有 2 条消息才算"在聊"
AUTO_ALIVE_MIN = int(os.getenv("AUTO_ALIVE_MIN", "2"))


def log(*parts):
    """带时间戳打日志，方便回头看什么时候发生了什么。"""
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


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
            "description": "发一张表情包（同时会存进 QQ 收藏表情）。别每句都发，气氛到了才发。",
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
    {
        "type": "function",
        "function": {
            "name": "save_meme",
            "description": "只存进 QQ 收藏表情，不发出去。群友说「这张收了」「存一下」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要存的表情包描述"},
                },
                "required": ["query"],
            },
        },
    },
]

ALL_TOOLS = TOOL_SCHEMAS + MEMORY_TOOLS + MEME_TOOL


async def call_api(action, params, timeout=20):
    """调一个 OneBot 接口。

    另开一条连接，不复用收事件的那条——否则会跟事件循环抢 ws.recv()。
    """
    import websockets

    echo = "tool-" + str(random.randint(1, 10 ** 9))
    try:
        async with websockets.connect(WS_URL, **WS_KWARGS) as conn:
            await conn.send(json.dumps({"action": action, "params": params, "echo": echo}))
            while True:
                raw = await asyncio.wait_for(conn.recv(), timeout=timeout)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("echo") == echo:
                    return msg
    except Exception as exc:
        print("[API出错] %s: %s" % (action, exc))
        return None


async def save_to_favorites(data):
    """把图片存进 QQ 的收藏表情（add_custom_face）。"""
    if not MEME_SAVE:
        return "存档已关闭"
    try:
        os.makedirs(SHARE_DIR, exist_ok=True)
    except OSError as exc:
        return "共享目录不可写：%s" % exc

    name = "meme_%d_%d.png" % (int(time.time()), random.randint(1000, 9999))
    local = os.path.join(SHARE_DIR, name)
    remote = SHARE_PATH.rstrip("/") + "/" + name
    try:
        with open(local, "wb") as f:
            f.write(data)
        resp = await call_api("add_custom_face", {"file": remote, "is_origin": True})
    except Exception as exc:
        return "存档失败：%s" % exc
    finally:
        try:
            os.remove(local)          # 存完就删，不在磁盘上留图
        except OSError:
            pass

    if not resp:
        return "存档没响应"
    if resp.get("status") == "ok":
        return "已存进 QQ 收藏表情"
    return "存档失败：%s" % (resp.get("message") or resp.get("wording") or "")


async def run_any_tool(name, args, images=None):
    """执行工具。images 用来收集本次要发的表情包图片。"""
    if name == "remember":
        return memory.remember(args.get("nick", ""), args.get("content", ""),
                               args.get("kind", "fact"))
    if name == "recall":
        return memory.recall(args.get("nick", ""))

    if name in ("send_meme", "save_meme"):
        query = args.get("query") or ""
        data, src = memes.get_meme(query)
        if not data:
            return "没搜到合适的表情包"
        saved = await save_to_favorites(data)
        if name == "save_meme":
            return "存了一张「%s」表情包（%s）" % (query, saved)
        if images is not None:
            images.append(data)
        return "已发一张「%s」表情包（%s）" % (query, saved)

    return run_tool(name, args)


# ---------------------------------------------------------------- 模型
CANNED = ["典", "绷不住了", "确实", "6", "有一说一，这话我接不住"]


def _client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)


def _build_messages(history, text, nickname, user_id=None, image_urls=None):
    """拼提示词：人设 + 此人画像 + 最近聊天 + 当前这句（可带图）。"""
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

    if image_urls:
        # 对方发了图：把图喂给模型，让它真看一眼再回
        hint = "（对方发了图，先看图再说。以群友身份回一两句，最多 80 字，别解释）"
        content = [{"type": "text", "text": "%s: %s\n%s" % (nickname, text, hint)}]
        for u in image_urls[:2]:              # 最多看两张，控制成本
            content.append({"type": "image_url", "image_url": {"url": u}})
        messages.append({"role": "user", "content": content})
    else:
        hint = ("（以群友身份回一两句，最多 80 字，像真人聊天，别解释。"
                "需要事实/天气/梗就先用工具查；想配个表情包就调 send_meme）")
        messages.append({"role": "user",
                         "content": "%s: %s\n%s" % (nickname, text, hint)})
    return messages


async def ask_model(history, text, nickname, user_id=None, image_urls=None):
    """调模型生成回复。返回 (文字, 要发的表情包图片列表)。"""
    if not os.getenv("OPENAI_API_KEY"):
        return random.choice(CANNED), []

    client = _client()
    messages = _build_messages(history, text, nickname, user_id, image_urls)
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
            result = await run_any_tool(call.function.name, args, images)
            log("  [工具] %s(%s) -> %s" % (call.function.name, args, result[:80]))
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
- 只有一种情况必须闭嘴：别人在认真排查技术问题、处理正事。
- 其余情况你都可以插：有人吐槽、有人发癫、有人装逼、有人自问自答、
  群里在抖机灵、话题正热。这是水群，不是开会。
- 插就插一句，最多 25 个字，别解释，别说"我觉得"。
- 没人说话（上面只有一两条）就别硬插。

只输出一个 JSON，不要别的：
{"speak": true 或 false, "reply": "要插的话写这里"}"""


def group_is_alive(state, group_id):
    """最近一段时间里群里有几个人在说话？太冷清就不插。"""
    now = time.time()
    times = [t for t in state["msg_times"].get(group_id, []) if now - t < AUTO_ALIVE_WINDOW]
    state["msg_times"][group_id] = times          # 顺手清掉旧的
    return len(times) >= AUTO_ALIVE_MIN


def auto_allowed(state, group_id):
    """粗筛：冷清判断 + 冷却 + 每小时上限 + 概率。先过这层再问模型，省钱。"""
    if AUTO_CHANCE <= 0:
        return False
    if not group_is_alive(state, group_id):
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
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if not auto_allowed(state, group_id):
        return None

    recent = list(history)[-6:]
    if len(recent) < 2:
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
        log("  [自主] 模型决定不插")
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


def parse_message(message):
    """拆消息段：返回 (文字, 图片段列表, 表情个数)。"""
    texts, images, faces = [], [], 0
    for seg in message or []:
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            texts.append(data.get("text", ""))
        elif kind == "image":
            images.append(data)
        elif kind in ("face", "mface"):
            faces += 1
    return "".join(texts).strip(), images, faces


def fetch_image(url, max_bytes=3 * 1024 * 1024, timeout=10):
    """把图片下载成 bytes。失败返回 None。"""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://q.qq.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
    except Exception as exc:
        log("  [图片] 下载失败:", str(exc)[:60])
        return None
    if len(data) > max_bytes:
        return None
    return data


def to_data_url(data):
    """图片字节转 data URL，喂给支持看图的模型。"""
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] == b"GIF8":
        mime = "image/gif"
    elif data[:4] == b"RIFF":
        mime = "image/webp"
    else:
        mime = "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())


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
    text, images, faces = parse_message(event.get("message"))

    # 图片/表情消息也要能进上下文，不然它根本不知道有人发了图
    placeholder = ""
    if images:
        placeholder += "[图片]"
    if faces and not text:
        placeholder += "[表情]"
    display = (text + (" " + placeholder if placeholder else "")).strip() or "[图片]"

    if not text and not images and not faces:
        return

    history = state["history"].setdefault(group_id, deque(maxlen=MAX_HISTORY))
    history.append({"nick": nickname, "text": display, "uid": user_id})
    state["msg_times"].setdefault(group_id, []).append(time.time())

    # ---- 路径 A：被叫了，正经回 ----
    if should_reply(event, text, self_id):
        last = state["last_reply"].get(group_id, 0)
        if time.time() - last < COOLDOWN:
            log("[跳过] 冷却中，%.1f 秒后再说话" % (COOLDOWN - (time.time() - last)))
            return
        state["last_reply"][group_id] = time.time()

        log("[收到] %s: %s" % (nickname, text))
        # 有图就下载下来喂给模型（最多两张）
        pics = []
        for seg in images[:2]:
            data = fetch_image(seg.get("url"))
            url = to_data_url(data)
            if url:
                pics.append(url)

        try:
            reply, memes_out = await ask_model(list(history)[:-1], text or "[图片]",
                                               nickname, user_id, pics)
        except Exception as exc:
            print("[模型出错]", exc)
            return
        if not reply and not memes_out:
            return
        log("[回复]", reply, ("+ 表情包 x%d" % len(memes_out)) if memes_out else "")
        await send_group(ws, group_id, reply or "", memes_out)
        history.append({"nick": BOT_NICK, "text": reply})
        return

    # ---- 路径 B：没叫它，看心情插不插话 ----
    try:
        reply = await maybe_chime_in(history, state, group_id)
    except Exception as exc:
        print("[自主判断出错]", exc)
        return
    if reply:
        log("[自主插话] -> %s" % reply)
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

    state = {"history": {}, "last_reply": {}, "last_auto": {},
             "auto_times": {}, "msg_times": {}}
    while True:
        try:
            print("连接", WS_URL, "...")
            global WS_KWARGS
            WS_KWARGS = kwargs
            async with websockets.connect(WS_URL, **kwargs) as ws:
                print("已连接。工具 %s，自主插话概率 %.0f%%，记忆 %s，表情存档 %s"
                      % ("开" if USE_TOOLS else "关", AUTO_CHANCE * 100, memory.stats(),
                         "开" if MEME_SAVE else "关"))
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
