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
# 必应中文分词差（"海克斯大乱斗"会被拆成"海"），所以先试 360，再退回必应
_A_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BING_BLOCK = re.compile(r'<li class="b_algo".*?</li>', re.S)
_TITLE_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIP_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)


def _clean(x):
    return html.unescape(_TAG_RE.sub("", x or "")).strip()


def _strip_tags(s):
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _extract_links(page, query, limit=4):
    """通用提取：抓住页面里跟查询词相关的链接标题 + 后文摘要。"""
    clean_q = query.replace('"', "").replace("“", "").replace("”", "")
    tokens = [t for t in re.split(r"[\s,，]+", clean_q) if len(t) >= 2]
    if not tokens:
        return []
    out, seen = [], set()
    for m in _A_RE.finditer(page):
        title = _strip_tags(m.group(2))
        if not (6 <= len(title) <= 90):
            continue
        if not any(t in title for t in tokens):
            continue
        key = title[:18]
        if key in seen:
            continue
        seen.add(key)
        # 取链接后一段文字当摘要，顺手清掉 URL 残渣和多余空白
        tail = _strip_tags(page[m.end():m.end() + 1500])
        tail = re.sub(r"<[^>]*>?", " ", tail)              # 漏网的标签碎片
        tail = re.sub(r"\w+=[\w&%/.:-]+", " ", tail)       # data-ext= / href= 之类
        tail = re.sub(r"[\w%&=/?.#-]{18,}", " ", tail)      # 长 token 基本都是链接
        tail = re.sub(r"\s+", " ", tail).strip(" 　|·-")
        snippet = tail[:100] if len(tail) >= 10 else ""
        out.append(("%s｜%s" % (title, snippet)).strip("｜"))
        if len(out) >= limit:
            break
    return out


def _search_360(query, limit):
    """360 搜索。带引号会搜不到，先剥掉引号再试一次。"""
    for q in (query, query.replace('"', "")):
        try:
            url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": q})
            items = _extract_links(_get(url), q, limit)
        except Exception:
            items = []
        if items:
            return items
    return []


def _search_bing(query, limit):
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "setlang": "zh-CN"})
    page = _get(url)
    items = []
    for block in _BING_BLOCK.findall(page)[: limit * 2]:
        tm = _TITLE_RE.search(block)
        if not tm:
            continue
        title = _clean(tm.group(2))
        sm = _SNIP_RE.search(block)
        snippet = _clean(sm.group(1)) if sm else ""
        if title:
            items.append("%s｜%s" % (title[:80], snippet[:110]))
        if len(items) >= limit:
            break
    return items


_BILI_API = "https://api.bilibili.com/x/web-interface/search/all/v2?"


def _search_bilibili(query, limit=4):
    """B 站搜索：游戏内容这里最全，返回标题+作者+播放量。"""
    import json as _json

    url = _BILI_API + urllib.parse.urlencode({"keyword": query})
    page = _get(url, headers={"Referer": "https://www.bilibili.com"})
    data = _json.loads(page)
    if data.get("code") != 0:
        return []

    items = []
    for block in (data.get("data") or {}).get("result") or []:
        if block.get("result_type") != "video":
            continue
        for it in (block.get("data") or [])[:limit]:
            title = _strip_tags(it.get("title") or "")
            if not title:
                continue
            play = it.get("play") or 0
            author = it.get("author") or ""
            desc = _strip_tags(it.get("description") or "")[:80]
            line = "%s（B站·%s·%s播放）" % (title[:70], author, play)
            if desc:
                line += " 摘要：" + desc
            items.append(line)
        break
    return items[:limit]


def search_web(query, limit=4):
    """搜一下，返回几条标题和摘要。360 优先，必应兜底。"""
    query = (query or "").strip()
    if not query:
        return "没给关键词"

    collected = []
    for fn in (_search_bilibili, _search_360, _search_bing):
        try:
            items = fn(query, limit)
        except Exception:
            items = []
        for it in items:
            if it not in collected:
                collected.append(it)
        if len(collected) >= limit:
            break

    if not collected:
        return "搜「%s」没搜到东西" % query
    return "\n".join(collected[:limit])


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


def _explain_from_360(term):
    """去 360 搜「xxx 是什么意思」，把摘要拼起来当解释。"""
    q = '"%s" 什么意思 什么梗' % term
    for query in (q, "%s 什么意思 什么梗" % term):
        try:
            url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": query})
            items = _extract_links(_get(url), query, 3)
        except Exception:
            items = []
        good = [i for i in items if any(k in i for k in ("梗", "意思", "流行", "指", "源于"))]
        if good:
            return good
    return []


def lookup_slang(term):
    """查某个词/梗是什么意思、哪来的。返回尽量可读的解释。"""
    term = (term or "").strip()
    if not term:
        return "没给词"

    clues = []

    # 1) 360 搜「什么意思」，摘要通常直接就是解释
    clues.extend(_explain_from_360(term))

    # 2) B 站视频标题+简介，补出处
    try:
        for line in _search_bilibili("%s 什么梗" % term, 3):
            if term in line or "梗" in line:
                clues.append(line)
    except Exception:
        pass

    # 3) 最后再用必应兜底
    if not clues:
        try:
            clues.extend(_search_bing("%s 什么梗 什么意思" % term, 3))
        except Exception:
            pass

    if not clues:
        return "「%s」没查到，可能太小众" % term

    seen, out = set(), []
    for c in clues:
        key = c[:24]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= 4:
            break
    return "\n".join(out)


# ---------------------------------------------------------------- 核实
def fact_check(claim):
    """对一个说法做快速核实：搜两次，把证据摆出来。"""
    claim = (claim or "").strip()
    if not claim:
        return "没给要核实的说法"

    lines = []
    for q in (claim, claim + " 真的吗"):
        try:
            res = search_web(q, limit=2)
        except Exception:
            continue
        if res and "没搜到" not in res:
            lines.append(res)
        if len(lines) >= 2:
            break

    if not lines:
        return "查不到「%s」的准信" % claim
    return "\n".join(lines)[:900]


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
            "name": "fact_check",
            "description": "核实一个说法是真的还是假的。被质疑、或者不确定自己说得对不对时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "要核实的说法"},
                },
                "required": ["claim"],
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
    "fact_check": fact_check,
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
