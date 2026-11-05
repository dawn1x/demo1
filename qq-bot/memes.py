# -*- coding: utf-8 -*-
"""表情包库：按情绪分类，本地存图，随机取一张发出去。

图来源：必应图片搜索（一次性抓取，存到本地，之后不再联网）。
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

# 分类名 -> 搜索词
CATEGORIES = {
    "笑": "表情包 笑 搞笑",
    "无语": "表情包 无语 流汗",
    "生气": "表情包 生气 怒",
    "赞": "表情包 竖起大拇指 点赞",
    "疑惑": "表情包 问号 疑惑",
    "怕": "表情包 害怕 瑟瑟发抖",
    "摸鱼": "表情包 摸鱼 摆烂",
    "嘲讽": "表情包 嘲讽 阴阳怪气",
}

_MURL_RE = re.compile(r'murl&quot;:&quot;(.*?)&quot;', re.S)
_MURL_RE2 = re.compile(r'"murl":"(.*?)"', re.S)
_OK_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


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


def fetch_category(cat, keyword, want=8):
    """抓某个分类的图存到本地，返回成功数。"""
    folder = os.path.join(MEME_DIR, cat)
    os.makedirs(folder, exist_ok=True)

    have = len([f for f in os.listdir(folder) if f.lower().endswith(_OK_EXT)])
    if have >= want:
        return 0

    saved = 0
    for u in search_image_urls(keyword, limit=want * 3):
        if saved >= want - have:
            break
        try:
            data = _get(u, referer="https://cn.bing.com/")
        except Exception:
            continue
        if not (8 * 1024 <= len(data) <= 3 * 1024 * 1024):
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
    """把所有分类补齐到 per_cat 张。首次部署跑一次即可。"""
    report = {}
    for cat, kw in CATEGORIES.items():
        report[cat] = fetch_category(cat, kw, per_cat)
    return report


def list_categories():
    """只返回本地确实有图的分类。"""
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


def pick(tag=None):
    """取一张表情包，返回 (bytes, 分类)。没有图就返回 (None, None)。"""
    cats = list_categories()
    if not cats:
        return None, None

    tag = (tag or "").strip()
    if tag not in cats:
        # 模型给的名字没对上，猜一个最接近的
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
    path = os.path.join(folder, random.choice(files))
    try:
        with open(path, "rb") as f:
            return f.read(), tag
    except OSError:
        return None, None


def to_segment(data):
    """把图片字节转成 OneBot 的 image 消息段。"""
    return {
        "type": "image",
        "data": {"file": "base64://" + base64.b64encode(data).decode()},
    }
