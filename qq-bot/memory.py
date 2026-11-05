# -*- coding: utf-8 -*-
"""大肥鱼的记忆：谁惹过它、谁对它好、谁是谁。

存在一个 JSON 文件里，重启不丢。
"""

import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
MEM_PATH = os.path.join(DATA_DIR, "memory.json")

_lock = threading.Lock()

# kind 只能是这三种
KINDS = {"grudge": "惹过它", "praise": "对它好", "fact": "关于此人"}


def _empty():
    return {"users": {}, "updated": 0}


def load():
    try:
        with open(MEM_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _empty()
    data.setdefault("users", {})
    return data


def save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    data["updated"] = int(time.time())
    tmp = MEM_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MEM_PATH)          # 原子写，防止写一半断电


def _key(nick, user_id=None):
    if user_id:
        return str(user_id)
    return (nick or "某人").strip()


def remember(nick, content, kind="fact", user_id=None):
    """记一件事。kind: grudge / praise / fact。"""
    if kind not in KINDS:
        kind = "fact"
    content = (content or "").strip()[:120]
    if not content:
        return "没内容，不记"

    with _lock:
        data = load()
        key = _key(nick, user_id)
        u = data["users"].setdefault(key, {"nick": nick, "items": []})
        u["nick"] = nick or u.get("nick")
        u.setdefault("items", []).append({
            "kind": kind,
            "what": content,
            "when": time.strftime("%Y-%m-%d %H:%M"),
        })
        u["items"] = u["items"][-30:]      # 每人最多留 30 条
        save(data)

    return "记住了：%s %s" % (nick, content)


def recall(nick, user_id=None, limit=5):
    """把某个人的记录翻出来。"""
    with _lock:
        data = load()
        u = data["users"].get(_key(nick, user_id))
    if not u or not u.get("items"):
        return "没有关于 %s 的记录" % nick
    lines = []
    for item in u["items"][-limit:]:
        lines.append("[%s] %s" % (KINDS.get(item.get("kind"), "记录"), item.get("what")))
    return "%s 的记录：\n%s" % (u.get("nick") or nick, "\n".join(lines))


def profile(nick, user_id=None, limit=4):
    """给提示词用的一小段人物画像，没有就返回空串。"""
    with _lock:
        data = load()
        u = data["users"].get(_key(nick, user_id))
    if not u or not u.get("items"):
        return ""
    items = u["items"][-limit:]
    parts = []
    for item in items:
        kind = item.get("kind")
        mark = {"grudge": "得罪过你", "praise": "对你不错", "fact": ""}.get(kind, "")
        parts.append("%s%s" % (mark, item.get("what")))
    return "；".join(p for p in parts if p)


def grudge_count(nick, user_id=None):
    with _lock:
        data = load()
        u = data["users"].get(_key(nick, user_id))
    if not u:
        return 0
    return sum(1 for i in u.get("items", []) if i.get("kind") == "grudge")


def stats():
    with _lock:
        data = load()
    return "%d 个人有记录" % len(data.get("users", {}))
