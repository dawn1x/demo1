# -*- coding: utf-8 -*-
"""大肥鱼的玩法：好感度、群友档案、黑历史语录、小游戏。

数据都放在 data/fun.json，重启不丢。
"""

import json
import os
import random
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
PATH = os.path.join(DATA_DIR, "fun.json")

_lock = threading.Lock()

# 好感度区间 -> 说法（拼进提示词，影响语气）
LEVELS = [
    (-100, "看你就烦，张嘴就想怼"),
    (-20, "对你没什么好脸"),
    (-1, "对你一般，不冷不热"),
    (10, "还算顺眼"),
    (40, "你说话我愿意接"),
    (1000, "自己人，谁骂你我帮你骂回去"),
]


def _empty():
    return {"affinity": {}, "stats": {}, "quotes": [], "last_report": ""}


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _empty()
    for k, v in _empty().items():
        data.setdefault(k, v)
    return data


def save(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, PATH)
    except OSError:
        pass


def _level_text(score):
    for bound, text in LEVELS:
        if score < bound:
            continue
        if bound == LEVELS[-1][0]:
            return text
    for i in range(len(LEVELS) - 1, -1, -1):
        if score >= LEVELS[i][0]:
            return LEVELS[i][1]
    return LEVELS[0][1]


# ---------------------------------------------------------------- 好感度
def affinity_add(nick, uid, delta, reason=""):
    """加减好感，返回一句给模型看的结果。"""
    with _lock:
        data = load()
        key = str(uid or nick)
        item = data["affinity"].setdefault(key, {"nick": nick, "score": 0})
        item["nick"] = nick
        item["score"] = max(-100, min(100, item["score"] + delta))
        score = item["score"]
        save(data)
    sign = "+" if delta >= 0 else ""
    return "%s 好感 %s%d → %d（%s）%s" % (
        nick, sign, delta, score, _level_text(score),
        ("：" + reason) if reason else "")


def affinity_get(nick, uid=None):
    with _lock:
        data = load()
        item = data["affinity"].get(str(uid or nick))
        if not item:
            for v in data["affinity"].values():
                if v.get("nick") == nick:
                    item = v
                    break
    return item or {"nick": nick, "score": 0}


def affinity_prompt(nick, uid=None):
    item = affinity_get(nick, uid)
    score = item.get("score", 0)
    return "你对「%s」的好感度 %d，态度：%s" % (nick, score, _level_text(score))


# 本地关键词判定，不依赖模型记得调工具
INSULTS = ("傻狗", "傻逼", "sb", "SB", "滚", "闭嘴", "放屁", "锤子", "垃圾", "废物",
           "菜狗", "煞笔", "神经病", "草泥", "闭嘴吧", "真菜", "太菜", "你很菜",
           "调教你", "废物", "不行啊", "弱爆")
PRAISES = ("牛", "厉害", "在理", "说得对", "有道理", "强", "顶", "支持", "服了",
           "爱你", "喜欢你", "靠谱", "谢谢", "感谢", "辛苦")


def auto_affinity(nick, uid, text):
    """按消息内容自动加减好感，返回变化说明或 None。"""
    t = text or ""
    hit_bad = next((w for w in INSULTS if w in t), None)
    if hit_bad:
        return affinity_add(nick, uid, -2, "说了「%s」" % hit_bad)
    hit_good = next((w for w in PRAISES if w in t), None)
    if hit_good:
        return affinity_add(nick, uid, 1, "说了「%s」" % hit_good)
    return None


# ---------------------------------------------------------------- 群聊关系
def record_interaction(prev_uid, uid):
    """记一条「A 说完 B 接着说」的关系。"""
    if not prev_uid or not uid or str(prev_uid) == str(uid):
        return
    with _lock:
        data = load()
        rel = data.setdefault("relations", {})
        key = "%s->%s" % (prev_uid, uid)
        rel[key] = rel.get(key, 0) + 1
        save(data)


def relations_text(top=3):
    """把关系数据翻译成人话。"""
    with _lock:
        data = load()
        rel = dict(data.get("relations") or {})
        stats = dict(data.get("stats") or {})

    if not rel:
        return None

    def nick_of(uid):
        st = stats.get(str(uid)) or {}
        return st.get("nick") or str(uid)

    pairs = sorted(rel.items(), key=lambda x: -x[1])[:top]
    lines = []
    for key, cnt in pairs:
        a, b = key.split("->")
        lines.append("%s → %s（%d 次）" % (nick_of(a), nick_of(b), cnt))
    return lines


# ---------------------------------------------------------------- 群友档案
def record_message(uid, nick, has_image=False):
    if uid is None:
        return
    with _lock:
        data = load()
        key = str(uid)
        st = data["stats"].setdefault(key, {"nick": nick, "count": 0, "images": 0, "hours": {}})
        st["nick"] = nick
        st["count"] = st.get("count", 0) + 1
        if has_image:
            st["images"] = st.get("images", 0) + 1
        h = str(time.localtime().tm_hour)
        st.setdefault("hours", {})[h] = st["hours"].get(h, 0) + 1
        save(data)


def weekly_report():
    """生成一份群友档案，返回文字；没数据返回 None。"""
    with _lock:
        data = load()
        stats = dict(data["stats"])
        aff = dict(data["affinity"])

    rows = []
    for key, st in stats.items():
        if st.get("count", 0) < 3:
            continue
        hours = st.get("hours") or {}
        late = sum(v for h, v in hours.items() if int(h) in (0, 1, 2, 3, 4))
        top_hour = max(hours.items(), key=lambda x: x[1])[0] if hours else "-"
        rows.append({
            "nick": st.get("nick") or key,
            "count": st.get("count", 0),
            "images": st.get("images", 0),
            "late": late,
            "top_hour": top_hour,
        })

    if not rows:
        return None

    rows.sort(key=lambda r: -r["count"])
    lines = ["【本周群友档案】"]

    top = rows[0]
    lines.append("· 话最多：%s（%d 条）" % (top["nick"], top["count"]))

    imgs = max(rows, key=lambda r: r["images"])
    if imgs["images"] > 0:
        lines.append("· 图最多：%s（%d 张）" % (imgs["nick"], imgs["images"]))

    late = max(rows, key=lambda r: r["late"])
    if late["late"] > 0:
        lines.append("· 夜猫子：%s（凌晨说了 %d 句）" % (late["nick"], late["late"]))

    quiet = [r for r in rows if r["count"] <= 5]
    if quiet:
        lines.append("· 潜水王：%s（%d 条，人还在吗）" % (quiet[0]["nick"], quiet[0]["count"]))

    if aff:
        best = max(aff.values(), key=lambda v: v.get("score", 0))
        worst = min(aff.values(), key=lambda v: v.get("score", 0))
        if best.get("score", 0) > 0:
            lines.append("· 我最待见：%s（好感 %d）" % (best["nick"], best["score"]))
        if worst.get("score", 0) < 0:
            lines.append("· 我最烦：%s（好感 %d）" % (worst["nick"], worst["score"]))

    rel = relations_text(2)
    if rel:
        lines.append("· 最常接话：" + "；".join(rel))

    lines.append("—— 就这些，散了。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 黑历史语录
def add_quote(nick, text):
    text = (text or "").strip()[:80]
    if not text:
        return "空的，不记"
    with _lock:
        data = load()
        data["quotes"].append({
            "nick": nick,
            "text": text,
            "when": time.strftime("%m-%d %H:%M"),
        })
        data["quotes"] = data["quotes"][-200:]
        save(data)
    return "记下了：%s「%s」" % (nick, text)


def recall_quote(nick=None, n=2):
    with _lock:
        data = load()
        quotes = list(data["quotes"])
    if nick:
        hit = [q for q in quotes if q.get("nick") == nick]
        if hit:
            quotes = hit
    if not quotes:
        return "没什么可翻的旧账"
    picked = random.sample(quotes, min(n, len(quotes)))
    return "\n".join("%s（%s）说过：%s" % (q["nick"], q["when"], q["text"]) for q in picked)


# ---------------------------------------------------------------- 小游戏
RPS = {"石头": "剪刀", "剪刀": "布", "布": "石头"}
BEATS = {"剪刀": "石头", "布": "剪刀", "石头": "布"}


def play_rps(choice):
    """只返回事实（谁出什么、谁赢），话让模型自己说。"""
    choice = (choice or "").strip()
    for k in RPS:
        if k in choice:
            choice = k
            break
    if choice not in RPS:
        return "对方没出石头/剪刀/布，让他重出。"

    mine = random.choice(list(RPS.keys()))
    if choice == mine:
        result = "平局"
    elif BEATS[choice] == mine:
        result = "大肥鱼赢"
    else:
        result = "对方赢"

    return "猜拳结果：对方出%s，大肥鱼出%s，%s。（据此回一句，别报菜名）" % (choice, mine, result)


LOTS = [
    ("大吉", "今天你说的都对，我说了算"),
    ("中吉", "差不多顺，别乱花钱"),
    ("小吉", "凑合，能过"),
    ("平", "没什么事，别瞎折腾"),
    ("小凶", "少说话，多说多错"),
    ("凶", "今天别碰游戏，也别碰键盘"),
    ("大凶", "建议闭麦一天，先保命"),
]


def draw_lot():
    """只返回签文，话让模型自己说。"""
    name, tip = random.choice(LOTS)
    return "抽签结果：%s（签文大意：%s）。据此回一句，别照读签文。" % (name, tip)
