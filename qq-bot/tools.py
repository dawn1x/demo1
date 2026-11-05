# -*- coding: utf-8 -*-
"""大肥鱼能用的工具：查天气、搜东西、查梗。

全部用标准库 urllib，不额外装依赖。
每个工具函数返回一段简短的文字结果，直接喂给模型。
"""

import html
import json
import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TIMEOUT = 10


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


# ---------------------------------------------------------------- 天气
def get_weather(city):
    """查某个城市现在和今天的情况。"""
    city = (city or "").strip() or "北京"
    try:
        data = json.loads(_get("https://wttr.in/%s?format=j1" % urllib.parse.quote(city)))
    except Exception as exc:
        return "天气查不到（%s）" % exc

    cur = data.get("current_condition", [{}])[0]
    today = (data.get("weather") or [{}])[0]
    desc = (cur.get("lang_zh") or cur.get("weatherDesc") or [{}])[0].get("value", "")
    temp = cur.get("temp_C", "?")
    feel = cur.get("FeelsLikeC", "?")
    humid = cur.get("humidity", "?")
    hi = today.get("maxtempC", "?")
    lo = today.get("mintempC", "?")
    area = (data.get("nearest_area") or [{}])[0]
    name = (area.get("areaName") or [{}])[0].get("value", city)

    return "%s：%s，现在 %s℃，体感 %s℃，湿度 %s%%，今天 %s~%s℃" % (
        name, desc, temp, feel, humid, lo, hi)


# ---------------------------------------------------------------- 搜索
_RESULT_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
_TITLE_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIP_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()


def search_web(query, limit=4):
    """必应搜一下，返回几条标题和摘要。"""
    query = (query or "").strip()
    if not query:
        return "没给关键词"
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode({"q": query, "setlang": "zh-CN", "ensearch": "0"})
    try:
        page = _get(url)
    except Exception as exc:
        return "搜不了（%s）" % exc

    items = []
    for block in _RESULT_RE.findall(page)[:limit]:
        tm = _TITLE_RE.search(block)
        if not tm:
            continue
        title = _clean(tm.group(2))
        link = html.unescape(tm.group(1))
        sm = _SNIP_RE.search(block)
        snippet = _clean(sm.group(1)) if sm else ""
        if title:
            items.append("%s｜%s｜%s" % (title[:80], snippet[:120], link[:120]))

    if not items:
        return "搜「%s」没搜到东西" % query
    return "\n".join(items)


# ---------------------------------------------------------------- 查梗
# 强特征：出现这些基本就是讲梗的；弱特征：只是可能相关
_STRONG = ("梗", "网络流行", "网络用语", "流行语")
_WEAK = ("出处", "来源", "什么意思", "引申")
_QUERIES = ('"%s" 什么梗', '"%s" 网络流行语 什么意思', 'site:zh.moegirl.org.cn %s')
_MIN_SCORE = 4


def _score_line(term, line):
    score = 0
    if term in line:
        score += 1
    for hint in _STRONG:
        if hint in line:
            score += 3
    for hint in _WEAK:
        if hint in line:
            score += 1
    return score


def lookup_slang(term):
    """查某个词/梗是什么意思、哪来的。多个查询式都试一遍，挑最像梗的那条。"""
    term = (term or "").strip()
    if not term:
        return "没给词"

    best = []          # (score, line)
    for tmpl in _QUERIES:
        res = search_web(tmpl % term, limit=4)
        if "没搜到" in res or "搜不了" in res:
            continue
        for line in res.split("\n"):
            if not line.strip():
                continue
            score = _score_line(term, line)
            if score >= _MIN_SCORE:
                best.append((score, line))
        if best and max(s for s, _ in best) >= 6:
            break      # 已经找到很像梗的结果，不用再试了

    if not best:
        return "「%s」没查到靠谱的解释，可能太小众" % term

    best.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _, line in best:
        head = line[:30]
        if head in seen:
            continue
        seen.add(head)
        out.append(line)
        if len(out) >= 3:
            break
    return "\n".join(out)


# ---------------------------------------------------------------- 对外声明
# OpenAI / DeepSeek 的 function calling 格式
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查某个城市当前的天气。群友问天气、下雨、冷不冷、穿什么时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 北京、上海、杭州"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "上网搜一下。需要最新消息、新闻、比分、事实核查时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_slang",
            "description": "查网络梗、黑话、缩写是什么意思、出自哪里。群友用了个你不懂的词时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "要查的词"}
                },
                "required": ["term"],
            },
        },
    },
]

TOOL_FUNCS = {
    "get_weather": get_weather,
    "search_web": search_web,
    "lookup_slang": lookup_slang,
}


def run_tool(name, args):
    """按名字执行工具，永远返回字符串，出错也不抛。"""
    func = TOOL_FUNCS.get(name)
    if not func:
        return "没有这个工具：" + str(name)
    try:
        result = func(**args)
    except TypeError:
        try:
            result = func()
        except Exception as exc:
            return "工具调用失败：%s" % exc
    except Exception as exc:
        return "工具调用失败：%s" % exc
    return str(result)[:1500]
