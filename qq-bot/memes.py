# -*- coding: utf-8 -*-
"""表情包：现搜现发，用完即弃，不落盘、不建库。

流程：模型给一个中文描述 -> 必应图片搜索 -> 来源打分过滤 -> 下载一张 -> base64 发出去。
"""

import base64
import html
import random
import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TIMEOUT = 12

_MURL_RE = re.compile(r'murl&quot;:&quot;(.*?)&quot;', re.S)
_MURL_RE2 = re.compile(r'"murl":"(.*?)"', re.S)
_OK_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
MAX_BYTES = 3 * 1024 * 1024
MIN_BYTES = 6 * 1024

# 这些域名出的是书封/文档封面，不是表情包
BLOCK_DOMAINS = ("udocz.com", "docin.com", "book118.com", "doc88.com")
# 这些来源更可能是表情包站
GOOD_HINTS = ("biaoqing", "dout", "meme", "dtstatic", "qiubiaoqing",
              "nximg", "sinaimg", "gif", "tieba", "hdslb")


def _get(url, referer=None):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def search_image_urls(keyword, limit=12):
    """从必应图片搜索里取出若干图片直链。"""
    url = "https://cn.bing.com/images/search?" + urllib.parse.urlencode(
        {"q": keyword, "form": "HDRSC2", "first": "1"})
    try:
        page = _get(url).decode("utf-8", "ignore")
    except Exception:
        return []

    urls = _MURL_RE.findall(page) or _MURL_RE2.findall(page)
    out = []
    for raw in urls:
        u = html.unescape(raw).replace("\\/", "/")
        if not u.startswith("http"):
            continue
        if not u.lower().split("?")[0].endswith(_OK_EXT):
            continue
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def _rank(url):
    """给图片 URL 打分：越像表情包分越高，垃圾源直接 -1。"""
    low = url.lower()
    if any(d in low for d in BLOCK_DOMAINS):
        return -1
    score = 0
    for hint in GOOD_HINTS:
        if hint in low:
            score += 2
    if low.endswith(".gif"):
        score += 1
    return score


def _try(query, tries):
    """按一个查询词试一轮，拿到就返回。"""
    if "表情包" not in query and "表情" not in query:
        query = query + " 表情包"

    urls = [u for u in search_image_urls(query, limit=tries * 4) if _rank(u) >= 0]
    random.shuffle(urls)                       # 同分内随机，避免每次同一张
    urls.sort(key=_rank, reverse=True)         # 可信来源优先

    for u in urls[:tries]:
        try:
            data = _get(u, referer="https://cn.bing.com/")
        except Exception:
            continue
        if not (MIN_BYTES <= len(data) <= MAX_BYTES):
            continue
        return data, u
    return None, None


def get_meme(query, tries=4):
    """现搜一张表情包。返回 (图片字节, 来源URL)；搜不到返回 (None, None)。

    查询词太长往往搜不到（"无语 翻白眼 嘲讽"），所以逐级退化成更短的词再试。
    """
    query = (query or "").strip()
    if not query:
        return None, None

    # 候选：完整词 -> 前两个词 -> 第一个词 -> 兜底
    words = query.replace(",", " ").replace("、", " ").split()
    cands = [query]
    if len(words) > 2:
        cands.append(" ".join(words[:2]))
    if len(words) > 1:
        cands.append(words[0])
    cands.append("无语")

    seen = set()
    for cand in cands:
        if cand in seen:
            continue
        seen.add(cand)
        data, src = _try(cand, tries)
        if data:
            return data, src
    return None, None


def to_segment(data):
    """把图片字节转成 OneBot 的 image 消息段。"""
    return {
        "type": "image",
        "data": {"file": "base64://" + base64.b64encode(data).decode()},
    }
