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

import fun
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
MEME_SAVE = os.getenv("MEME_SAVE", "0") == "1"   # 存档会多一次上传，稳字优先默认关
SHARE_DIR = os.getenv("NAPCAT_SHARE_DIR", "/opt/dafeiyu/napcat/cache")
SHARE_PATH = os.getenv("NAPCAT_SHARE_PATH", "/app/napcat/cache")

# 连接参数，工具调 API 时另开一条连接用
WS_KWARGS = {}

USE_TOOLS = os.getenv("USE_TOOLS", "1") == "1"
MAX_TOOL_STEPS = 3

# 像人的行为参数
REPLY_DELAY_MIN = float(os.getenv("REPLY_DELAY_MIN", "1.2"))   # 打字前先愣一会儿
REPLY_DELAY_MAX = float(os.getenv("REPLY_DELAY_MAX", "5.0"))
SPLIT_REPLY = os.getenv("SPLIT_REPLY", "1") == "1"             # 偶尔分两条发
AT_CHANCE = float(os.getenv("AT_CHANCE", "0.3"))               # 回复时顺带 @ 对方的概率
REPLY_CHANCE = float(os.getenv("REPLY_CHANCE", "0.92"))        # 看到了就回的概率（偶尔已读不回）
QUIET_START = int(os.getenv("QUIET_START", "1"))               # 深夜装睡
QUIET_END = int(os.getenv("QUIET_END", "7"))

AUTO_CHANCE = float(os.getenv("AUTO_CHANCE", "0.3"))     # 每条消息被考虑接话的概率，0 = 不接
AUTO_COOLDOWN = float(os.getenv("AUTO_COOLDOWN", "90"))   # 两次接话最短间隔（秒）
AUTO_MAX_PER_HOUR = int(os.getenv("AUTO_MAX_PER_HOUR", "20"))
AUTO_ALIVE_WINDOW = float(os.getenv("AUTO_ALIVE_WINDOW", "600"))
AUTO_ALIVE_MIN = int(os.getenv("AUTO_ALIVE_MIN", "1"))

# 主动 @ 人开话题：频率低，默认 2~4 小时一次，每天最多 3 次，只在白天
TOPIC_ENABLE = os.getenv("TOPIC_ENABLE", "1") == "1"
TOPIC_AT_CHANCE = float(os.getenv("TOPIC_AT_CHANCE", "0.5"))  # 主动开口时 @ 人的概率
TOPIC_MIN_INTERVAL = float(os.getenv("TOPIC_MIN_INTERVAL", "7200"))    # 2 小时
TOPIC_MAX_INTERVAL = float(os.getenv("TOPIC_MAX_INTERVAL", "14400"))   # 4 小时
TOPIC_MAX_PER_DAY = int(os.getenv("TOPIC_MAX_PER_DAY", "3"))
TOPIC_START_HOUR = int(os.getenv("TOPIC_START_HOUR", "9"))
TOPIC_END_HOUR = int(os.getenv("TOPIC_END_HOUR", "23"))
# 只 @ 最近这么多天里发过言的人（死人不 @）
TOPIC_ACTIVE_DAYS = float(os.getenv("TOPIC_ACTIVE_DAYS", "7"))

# 对话延续：刚跟某人聊过，这段时间内他再说就不用重新 @
FOLLOW_UP_SECONDS = float(os.getenv("FOLLOW_UP_SECONDS", "180"))

# 健康检查：定期比对「群里最新消息」和「我们收到的最后一条」，抓半死连接
HEALTH_INTERVAL = float(os.getenv("HEALTH_INTERVAL", "300"))
HEALTH_MISS_SECONDS = float(os.getenv("HEALTH_MISS_SECONDS", "120"))
AUTO_RECOVER = os.getenv("AUTO_RECOVER", "1") == "1"   # 连续漏消息时自动重启 NapCat


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

FUN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "affinity",
            "description": "调整某人对你的好感度。有人骂你/损你就减，夸你/帮你说话就加。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nick": {"type": "string", "description": "群昵称"},
                    "delta": {"type": "integer", "description": "正整数加分，负整数减分，一般 ±1~3"},
                    "reason": {"type": "string", "description": "为什么加减"},
                },
                "required": ["nick", "delta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_quote",
            "description": "把某人的一句名场面存进黑历史，以后可以翻出来。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nick": {"type": "string", "description": "谁说的"},
                    "text": {"type": "string", "description": "原话"},
                },
                "required": ["nick", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_quote",
            "description": "翻某人的黑历史语录。有人嘴硬、装没说过、或者求锤时用。",
            "parameters": {
                "type": "object",
                "properties": {"nick": {"type": "string", "description": "谁的，可不填"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_rps",
            "description": "跟群友猜拳。对方说猜拳/石头剪刀布时用。你自己出什么不用传，交给工具随机。",
            "parameters": {
                "type": "object",
                "properties": {
                    "choice": {
                        "type": "string",
                        "description": "【群友出的】是什么，只能是 石头/剪刀/布。"
                                       "注意：这是对方的出法，不是你自己的。",
                    },
                },
                "required": ["choice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draw_lot",
            "description": "抽个签。有人说抽签、求个吉凶、问今天运气时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

ALL_TOOLS = TOOL_SCHEMAS + MEMORY_TOOLS + MEME_TOOL + FUN_TOOLS


async def call_api(action, params, timeout=20):
    """调一个 OneBot 接口。

    另开一条连接，不复用收事件的那条——否则会跟事件循环抢 ws.recv()。
    """
    import websockets

    kwargs = WS_KWARGS
    if not kwargs and WS_TOKEN:           # 兜底：没继承连接参数时自己带 token
        kwargs = {"additional_headers": {"Authorization": "Bearer " + WS_TOKEN},
                  "max_size": 2 ** 24}

    echo = "tool-" + str(random.randint(1, 10 ** 9))
    try:
        async with websockets.connect(WS_URL, **kwargs) as conn:
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

    if name == "affinity":
        try:
            delta = int(args.get("delta", 0))
        except (TypeError, ValueError):
            delta = 0
        return fun.affinity_add(args.get("nick", ""), args.get("uid"), delta,
                                args.get("reason", ""))
    if name == "save_quote":
        return fun.add_quote(args.get("nick", ""), args.get("text", ""))
    if name == "recall_quote":
        return fun.recall_quote(args.get("nick") or None)
    if name == "play_rps":
        return fun.play_rps(args.get("choice", ""))
    if name == "draw_lot":
        return fun.draw_lot()

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
    system += "\n" + fun.affinity_prompt(nickname, user_id)

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
AUTO_PROMPT = """你在水一个 QQ 群。你是群友「大肥鱼」，贴吧暴躁老哥，嘴快、爱接话、爱损人。

现在没人 @ 你。你要判断：这句话你接不接？

默认接。群里水群就是这样，别人说一句，你搭一句。
只有这几种情况不接：
1. 别人在认真排查技术故障、处理正事（那就让他们弄，别打扰）
2. 你刚说过话，还在同一个话题上重复
3. 对方是纯操作指令或跟旁人私聊（"你把文件发我""我看看"）

其余都接：吐槽、装逼、破防、开玩笑、问问题、发癫、玩梗、感慨、发图、纯表情。
接的时候：
- 一到两句，最多 60 字
- 贴着他说的话回，别自说自话
- 可以反问、可以损、可以阴阳
- 别每句都用同一个口头禅

只输出 JSON：
{"speak": true 或 false, "reply": "要接的话写这里"}"""


def group_is_alive(state, group_id):
    """最近一段时间里群里有几个人在说话？太冷清就不插。"""
    now = time.time()
    times = [t for t in state["msg_times"].get(group_id, []) if now - t < AUTO_ALIVE_WINDOW]
    state["msg_times"][group_id] = times          # 顺手清掉旧的
    return len(times) >= AUTO_ALIVE_MIN


def is_quiet_hour():
    h = time.localtime().tm_hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    return h >= QUIET_START or h < QUIET_END


def auto_allowed(state, group_id):
    """粗筛：冷清判断 + 冷却 + 每小时上限 + 概率。先过这层再问模型，省钱。"""
    if AUTO_CHANCE <= 0:
        return False
    if is_quiet_hour():
        return False          # 深夜装睡，只回被 @ 的
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


SPEAKER_PATH = os.path.join(HERE, "data", "speakers.json")


def load_speakers():
    try:
        with open(SPEAKER_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_speakers(data):
    try:
        os.makedirs(os.path.dirname(SPEAKER_PATH), exist_ok=True)
        tmp = SPEAKER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, SPEAKER_PATH)
    except OSError as exc:
        log("[发言人] 保存失败:", str(exc)[:60])


def record_speaker(group_id, uid, nick):
    """记下谁刚说过话，@ 人时只从这些人里挑。"""
    if uid is None:
        return
    data = load_speakers()
    g = data.setdefault(str(group_id), {})
    key = str(uid)
    item = g.setdefault(key, {"nick": nick, "count": 0})
    item["nick"] = nick
    item["last"] = time.time()
    item["count"] = item.get("count", 0) + 1
    save_speakers(data)


def active_speakers(group_id, limit=20):
    """最近说过话的人，按时间倒序。"""
    data = load_speakers()
    g = data.get(str(group_id), {})
    cutoff = time.time() - TOPIC_ACTIVE_DAYS * 86400
    rows = [(int(uid), v) for uid, v in g.items()
            if v.get("last", 0) >= cutoff and v.get("nick")]
    rows.sort(key=lambda x: -x[1].get("last", 0))
    return rows[:limit]


TOPIC_PROMPT = """你是 QQ 群群友「大肥鱼」，贴吧暴躁老哥。

现在群里没人说话，你要主动 @ 一个人，抛个话题把场子热起来。

要求：
- 就一句，最多 30 字
- 像老哥随口找话茬，别像客服，别问"最近怎么样"这种空话
- 可以拿他之前的事开涮、催他、阴阳他一句，或者抛个能接的话头
- 不骂父母，不带脏字，不涉黄
- 不要写 @ 和名字，直接写话

只输出这句话，不要引号不要解释。"""


def topic_allowed(state):
    """今天还能不能开话题？现在是不是合适的时间？"""
    if not TOPIC_ENABLE:
        return False
    hour = time.localtime().tm_hour
    if TOPIC_START_HOUR <= TOPIC_END_HOUR:
        if not (TOPIC_START_HOUR <= hour < TOPIC_END_HOUR):
            return False
    else:                                  # 跨零点的时间窗
        if not (hour >= TOPIC_START_HOUR or hour < TOPIC_END_HOUR):
            return False

    today = time.strftime("%Y-%m-%d")
    if state["topic_day"] != today:
        state["topic_day"] = today
        state["topic_today"] = 0
    return state["topic_today"] < TOPIC_MAX_PER_DAY


async def seed_speakers(group_id):
    """启动时从群历史里补一批"谁最近说过话"，免得刚跑起来只能退回成员列表。"""
    resp = await call_api("get_group_msg_history", {"group_id": group_id, "count": 100})
    msgs = (resp or {}).get("data", {}).get("messages") or []
    if not msgs:
        return 0

    data = load_speakers()
    g = data.setdefault(str(group_id), {})
    n = 0
    for m in msgs:
        sender = m.get("sender") or {}
        uid = sender.get("user_id")
        nick = sender.get("card") or sender.get("nickname")
        if not uid or not nick:
            continue
        key = str(uid)
        if nick == BOT_NICK or "大肥鱼" in nick:
            continue
        item = g.setdefault(key, {"nick": nick, "count": 0})
        item["nick"] = nick
        item["last"] = max(item.get("last", 0), float(m.get("time") or 0))
        item["count"] = item.get("count", 0) + 1
        n += 1
    save_speakers(data)
    return n


async def pick_member(group_id, self_id, state):
    """从最近说过话的人里随机挑一个，死人不 @。"""
    rows = active_speakers(group_id, limit=20)
    candidates = []
    for uid, info in rows:
        if str(uid) == str(self_id):
            continue
        if BOT_NICK in (info.get("nick") or ""):
            continue
        candidates.append({"user_id": uid, "nickname": info.get("nick"), "card": ""})

    if not candidates:
        # 还没记录到谁说过话（刚重启/新群），退回群成员列表
        log("[开话题] 还没有活跃发言人记录，退回群成员列表")
        resp = await call_api("get_group_member_list", {"group_id": group_id})
        members = (resp or {}).get("data") or []
        candidates = [
            {"user_id": m.get("user_id"), "nickname": m.get("nickname"),
             "card": m.get("card")}
            for m in members
            if str(m.get("user_id")) != str(self_id)
            and BOT_NICK not in ((m.get("card") or "") + (m.get("nickname") or ""))
        ]

    if not candidates:
        return None

    last = state["topic_last_user"].get(group_id)
    pool = [c for c in candidates if c.get("user_id") != last] or candidates
    # 越近说过话的人权重越高：取前 20 个，随机挑
    m = random.choice(pool[:20])
    state["topic_last_user"][group_id] = m.get("user_id")
    return m


async def make_topic(state, group_id):
    """生成一句主动开口的话。返回 (昵称, 对方QQ或None, 文字)。"""
    if not topic_allowed(state):
        return None, None, None

    history = list(state["history"].get(group_id) or [])

    # 一半概率 @ 个人，一半概率自己冒一句（自言自语更像真人，也更安全）
    member = None
    if random.random() < TOPIC_AT_CHANCE:
        member = await pick_member(group_id, state.get("self_id"), state)

    if member:
        nick = member.get("card") or member.get("nickname") or "群友"
        target = member.get("user_id")
        ask = "你要 @ 的人是：%s" % nick
        prof = memory.profile(nick, target)
        if prof:
            ask += "\n你对他的印象：%s" % prof
    else:
        nick = "（谁也不 @，自己随口冒一句）"
        target = None
        ask = "没人叫你，就是你自己想说话了。随便说一句，别写名字，别 @ 人。"

    recent = "\n".join("%s: %s" % (h["nick"], h["text"]) for h in history[-6:]) or "（群里一直没人说话）"

    try:
        resp = await _client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + TOPIC_PROMPT},
                      {"role": "user", "content": "最近群里的聊天：\n%s\n\n%s" % (recent, ask)}],
            temperature=1.0, max_tokens=100)
        text = (resp.choices[0].message.content or "").strip().strip('"“”')
    except Exception as exc:
        log("[主动开口] 生成失败:", str(exc)[:60])
        return None, None, None

    if member:
        for prefix in ("@" + nick, nick + "，", nick + ",", nick + " "):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip("，, ")
                break

    if not text or len(text) > 60:
        return None, None, None
    return nick, target, text[:60]


async def report_loop(state):
    """每周一上午发一次群友档案。"""
    while True:
        await asyncio.sleep(1800)
        try:
            now = time.localtime()
            week = time.strftime("%Y-%W", now)
            if now.tm_wday != 0 or now.tm_hour < 10:
                continue
            data = fun.load()
            if data.get("last_report") == week:
                continue
            text = fun.weekly_report()
            if not text:
                continue
            gid = state.get("topic_group")
            if not gid or not state.get("ws"):
                continue
            await send_group(state["ws"], gid, text)
            data["last_report"] = week
            fun.save(data)
            log("[周报] 已发")
        except Exception as exc:
            log("[周报] 出错:", str(exc)[:60])


async def health_loop(state):
    """定期检查「是不是漏消息了」。

    假死故障：WS 连着、心跳也在，但 QQ 那边的事件不再推过来。
    靠比对群历史里最新消息的时间，就能发现我们错过了。
    """
    misses = 0
    while True:
        await asyncio.sleep(HEALTH_INTERVAL)
        gid = state.get("topic_group")
        if not gid:
            continue

        resp = await call_api("get_group_msg_history", {"group_id": gid, "count": 3})
        if not resp or resp.get("status") != "ok":
            log("[健康检查] 调不通 OneBot，强制重连")
            ws = state.get("ws")
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
            continue

        msgs = (resp.get("data") or {}).get("messages") or []
        if not msgs:
            continue

        newest = msgs[-1]
        newest_time = float(newest.get("time") or 0)
        sender = newest.get("sender") or {}
        is_self = str(sender.get("user_id")) == str(state.get("self_id"))
        last = state.get("last_msg_time", 0)

        if is_self or newest_time <= last + HEALTH_MISS_SECONDS:
            misses = 0
            continue

        misses += 1
        log("[健康检查] 漏消息了：群里最新消息比我们处理的晚 %.0f 秒（连续 %d 次）"
            % (newest_time - last, misses))

        ws = state.get("ws")
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

        if misses >= 2 and AUTO_RECOVER:
            log("[健康检查] 连续漏消息，重启 NapCat 恢复会话")
            try:
                import subprocess
                subprocess.Popen(["docker", "restart", "napcat"])
                state["last_msg_time"] = time.time()
                misses = 0
            except Exception as exc:
                log("[健康检查] 重启失败:", str(exc)[:60])


async def topic_loop(state):
    """后台循环：隔一阵子主动 @ 个人开话题。"""
    while True:
        wait = random.uniform(TOPIC_MIN_INTERVAL, TOPIC_MAX_INTERVAL)
        await asyncio.sleep(wait)
        try:
            group_id = state.get("topic_group")
            if not group_id:
                resp = await call_api("get_group_list", {})
                groups = (resp or {}).get("data") or []
                if not groups:
                    continue
                group_id = groups[0].get("group_id")
                state["topic_group"] = group_id

            nick, member_id, text = await make_topic(state, group_id)
            if not text:
                continue
            state["topic_today"] += 1
            log("[主动开口] %s -> %s" % ("@" + nick if member_id else "自说自话", text))
            await send_like_human(state["ws"], group_id, text, at_user=member_id)
        except Exception as exc:
            log("[开话题] 出错:", str(exc)[:80])


async def maybe_chime_in(history, state, group_id):
    """让模型看一眼最近的聊天，决定要不要自己插话。"""
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if not auto_allowed(state, group_id):
        log("  [自主] 被粗筛挡掉（冷清/冷却/配额/概率）")
        return None

    recent = list(history)[-6:]
    if not recent:
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


def split_message(text, limit=2):
    """把一句话拆成最多两段，像人打字一段段发。"""
    text = (text or "").strip()
    if len(text) < 18:
        return [text]
    for sep in ("。", "！", "？", "\n"):
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if len(parts) >= 2:
                head = parts[0] + ("。" if sep == "。" else sep if sep != "\n" else "")
                tail = (sep if sep != "\n" else " ").join(parts[1:])
                return [head, tail][:limit]
    return [text]


async def send_like_human(ws, group_id, text, images=None, at_user=None, reply_to=None):
    """先愣一会儿再发；长句拆成两段；偶尔 @ 一下。"""
    await asyncio.sleep(random.uniform(REPLY_DELAY_MIN, REPLY_DELAY_MAX))

    use_at = bool(at_user) and random.random() < AT_CHANCE
    parts = split_message(text) if (SPLIT_REPLY and not images) else [text]

    for i, part in enumerate(parts):
        if not part:
            continue
        await send_group(ws, group_id, part,
                         images=images if i == len(parts) - 1 else None,
                         at_user=at_user if (i == 0 and use_at) else None,
                         reply_to=reply_to if i == 0 else None)
        if i < len(parts) - 1:
            await asyncio.sleep(random.uniform(0.6, 1.8))


async def send_group(ws, group_id, text, images=None, at_user=None, reply_to=None):
    """发一句文字，可以顺带 @ 人、带一张表情包。"""
    if images or at_user or reply_to:
        message = []
        if reply_to:
            message.append({"type": "reply", "data": {"id": str(reply_to)}})
        if at_user:
            message.append({"type": "at", "data": {"qq": str(at_user)}})
            message.append({"type": "text", "data": {"text": " "}})
        message.append({"type": "text", "data": {"text": text}})
        if images:
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
    state["self_id"] = state.get("self_id") or event.get("self_id")
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
    state["last_msg_time"] = time.time()
    record_speaker(group_id, user_id, nickname)
    fun.record_message(user_id, nickname, has_image=bool(images))

    # ---- 路径 A0：刚跟这人聊过，他接着说就继续接 ----
    last_user = state["last_reply_user"].get(group_id)
    following = (
        last_user
        and user_id is not None
        and last_user[0] == user_id
        and time.time() - last_user[1] < FOLLOW_UP_SECONDS
    )

    log("[消息] %s: %s" % (nickname, display[:40]))

    # ---- 路径 A：被叫了，或者刚聊过在延续，正经回 ----
    if should_reply(event, text, self_id) or following:
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
        # 偶尔"看到了但不想理"
        if not following and random.random() > REPLY_CHANCE:
            log("[已读不回] 这条懒得回")
            return

        log("[回复]", reply, ("+ 表情包 x%d" % len(memes_out)) if memes_out else "")
        await send_like_human(ws, group_id, reply or "", memes_out,
                              at_user=user_id if random.random() < 0.5 else None,
                              reply_to=event.get("message_id"))
        history.append({"nick": BOT_NICK, "text": reply})
        state["last_reply_user"][group_id] = (user_id, time.time())
        return

    # ---- 路径 B：没叫它，看心情插不插话 ----
    try:
        reply = await maybe_chime_in(history, state, group_id)
    except Exception as exc:
        print("[自主判断出错]", exc)
        return
    if reply:
        log("[自主插话] -> %s" % reply)
        await send_like_human(ws, group_id, reply, at_user=user_id)
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
             "auto_times": {}, "msg_times": {},
             "topic_today": 0, "topic_day": "", "topic_last_user": {},
             "topic_group": None, "self_id": None,
             "last_reply_user": {}}
    while True:
        try:
            print("连接", WS_URL, "...")
            global WS_KWARGS
            WS_KWARGS = kwargs
            async with websockets.connect(WS_URL, **kwargs) as ws:
                print("已连接。工具 %s，自主插话概率 %.0f%%，记忆 %s，表情存档 %s，开话题 %s"
                      % ("开" if USE_TOOLS else "关", AUTO_CHANCE * 100, memory.stats(),
                         "开" if MEME_SAVE else "关", "开" if TOPIC_ENABLE else "关"))
                state["ws"] = ws
                if not state.get("seeded"):
                    state["seeded"] = True
                    try:
                        info = ((await call_api("get_login_info", {})) or {}).get("data") or {}
                        if info.get("user_id"):
                            state["self_id"] = info["user_id"]
                            log("[登录] 自己是 %s（%s）" % (info["user_id"], info.get("nickname")))
                    except Exception as exc:
                        log("[登录] 取账号信息失败:", str(exc)[:60])
                    try:
                        groups = ((await call_api("get_group_list", {})) or {}).get("data") or []
                        for grp in groups:
                            gid = grp.get("group_id")
                            n = await seed_speakers(gid)
                            state["topic_group"] = state.get("topic_group") or gid
                            log("[发言人] 从历史补了 %d 条记录（群 %s）" % (n, gid))
                    except Exception as exc:
                        log("[发言人] 补历史失败:", str(exc)[:60])
                if not state.get("topic_task"):
                    state["topic_task"] = asyncio.create_task(topic_loop(state))
                if not state.get("health_task"):
                    state["health_task"] = asyncio.create_task(health_loop(state))
                if not state.get("report_task"):
                    state["report_task"] = asyncio.create_task(report_loop(state))
                # 不用 async for：加看门狗，90 秒收不到任何东西就认为连接假死
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=90)
                    except asyncio.TimeoutError:
                        log("[看门狗] 90 秒没收到任何事件（心跳也没有），重连")
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if event.get("post_type") == "meta_event":
                        continue          # 心跳不用处理，收到就说明连接活着
                    asyncio.create_task(handle_event(ws, event, state))
        except Exception as exc:
            print("连接断开：", exc, "，5 秒后重连")
            await asyncio.sleep(5)


if __name__ == "__main__":
    if "--fake" in sys.argv:
        asyncio.run(run_fake())
    else:
        asyncio.run(run_bot())
