# -*- coding: utf-8 -*-
"""表情包：按需实时搜索并发送，不依赖预建图库。

两种模式：
  MEME_LIVE=1（默认）  每次现搜现发，用完即弃，不占磁盘
  MEME_LIVE=0          退回本地图库（data/memes/），适合网络不稳的环境

本地图库仍然保留，只作为实时搜索失败时的兜底。
"""

import base64
import hashlib
import html
import os
import random
import re
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MEME_DIR = os.path.join(HERE, "data", "memes")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TIMEOUT = 12

# 兜底分类（MEME_LIVE=0 时按这个目录找图）
CATEGORIES = {
    "笑": "表情包 笑 搞笑",
    "无语": "表情包 无语 流汗",
    "生气": "表情包 生气 怒",
    "赞": "表情包 点赞 大拇指",
    "疑惑": "表情包 问号 疑惑",
    "怕": "表情包 害怕 瑟瑟发抖",
    "摸鱼": "表情包 摸鱼 摆烂",
    "嘲讽": "表情包 嘲讽 阴阳怪气",
}

_MURL_RE = re.compile(r'murl&quot;:&quot;(.*?)&quot;', re.S)
_MURL_RE2 = re.compile(r'"murl":"(.*?)"', re.S)
_OK_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
MAX_BYTES = 3 * 1024 * 1024
MIN_BYTES = 6 * 1024


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


# 这些域名出的是书封/文档封面，不是表情包
BLOCK_DOMAINS = ("udocz.com", "docin.com", "book118.com", "doc88.com")
# 这些来源更可能是表情包站
GOOD_HINTS = ("biaoqing", "dout", "meme", "dtstatic", "qiubiaoqing",
              "nximg", "sinaimg", "gif", "tieba", "hdslb")


def _rank(url):
    """给图片 URL 打个分：越像表情包分越高，垃圾源直接 -1。"""
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


def fetch_remote(query, tries=5):
    """现搜一张表情包，返回 (bytes, 来源URL)。搜不到返回 (None, None)。"""
    query = (query or "").strip()
    if not query:
        return None, None
    # 补上"表情包"这个词，命中率更高
    if "表情包" not in query and "表情" not in query:
        query = query + " 表情包"

    urls = [u for u in search_image_urls(query, limit=tries * 4) if _rank(u) >= 0]
    # 按可信度排序，同分内随机，避免每次都是同一张
    random.shuffle(urls)
    urls.sort(key=_rank, reverse=True)
    for u in urls[:tries]:
        try:
            data = _get(u, referer="https://cn.bing.com/")
        except Exception:
            continue
        if not (MIN_BYTES <= len(data) <= MAX_BYTES):
            continue
        return data, u
    return None, None


# ---------------------------------------------------------------- 本地兜底
def list_categories():
    if not os.path.isdir(MEME_DIR):
        return []
    out = []
    for name in os.listdir(MEME_DIR):
        folder = os.path.join(MEME_DIR, name)
        if os.path.isdir(folder):
            files = [f for f in os.listdir(folder) if f.lower().endswith(_OK_EXT)]
            if files:
                out.append(name)
    return sorted(out)


def count():
    total = 0
    for cat in list_categories():
        total += len(os.listdir(os.path.join(MEME_DIR, cat)))
    return total


def fetch_category(cat, keyword, want=8):
    """把某个分类补齐到 want 张（只给 MEME_LIVE=0 用）。"""
    folder = os.path.join(MEME_DIR, cat)
    os.makedirs(folder, exist_ok=True)
    have = len([f for f in os.listdir(folder) if f.lower().endswith(_OK_EXT)])
    saved = 0
    for u in search_image_urls(keyword, limit=want * 3):
        if saved >= want - have:
            break
        try:
            data = _get(u, referer="https://cn.bing.com/")
        except Exception:
            continue
        if not (MIN_BYTES <= len(data) <= MAX_BYTES):
            continue
        name = hashlib.md5(u.encode()).hexdigest()[:12] + os.path.splitext(u.split("?")[0])[1].lower()
        try:
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
            saved += 1
        except OSError:
            continue
    return saved


def build(per_cat=8):
    """预抓本地图库（只有 MEME_LIVE=0 才需要）。"""
    return {cat: fetch_category(cat, kw, per_cat) for cat, kw in CATEGORIES.items()}


def pick_local(tag=None):
    """从本地图库取一张。"""
    cats = list_categories()
    if not cats:
        return None, None
    tag = (tag or "").strip()
    if tag not in cats:
        for c in cats:
            if tag and (c in tag or tag in c):
                tag = c
                break
        else:
            tag = random.choice(cats)
    folder = os.path.join(MEME_DIR, tag)
    files = [f for f in os.listdir(folder) if f.lower().endswith(_OK_EXT)]
    if not files:
        return None, None
    try:
        with open(os.path.join(folder, random.choice(files)), "rb") as f:
            return f.read(), tag
    except OSError:
        return None, None


# ---------------------------------------------------------------- 对外
def get_meme(query, live=True):
    """要一张表情包。返回 (bytes, 描述)。

    live=True  → 现搜；失败了才退回本地图库
    live=False → 只用本地图库
    """
    if live:
        data, src = fetch_remote(query)
        if data:
            return data, query
    return pick_local(query)


def to_segment(data):
    """把图片字节转成 OneBot 的 image 消息段。"""
    return {
        "type": "image",
        "data": {"file": "base64://" + base64.b64encode(data).decode()},
    }
