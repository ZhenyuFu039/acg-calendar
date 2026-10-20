"""Bounded, redirect-checked reads from the games' official sources only."""
from __future__ import annotations

import urllib.parse
import urllib.request
import zlib

from .network import read_response
from . import __version__

CDN_PREFIX = "/endfield/webview/_version_briefing/"


def official_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port not in {None, 443}:
            return False
        return p.hostname in {"endfield.hypergryph.com", "ak.hypergryph.com", "web-news.hypergryph.com"} or (p.hostname == "web.hycdn.cn" and p.path.startswith(CDN_PREFIX))
    except (TypeError, ValueError):
        return False


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official_url(newurl):
            raise ValueError("拒绝跳转到非官方来源")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(url):
    if not official_url(url):
        raise ValueError("采集仅支持已配置的游戏官方来源")
    limit = 6_000_000
    request = urllib.request.Request(url, headers={"User-Agent": f"Mozilla/5.0 (compatible; Playtime/{__version__})", "Accept": "application/json,text/html,application/javascript;q=0.9", "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"})
    response = read_response(request, OfficialRedirect, limit=limit, timeout=20)
    raw, charset = response.data, response.charset
    if raw[:2] == b"\x1f\x8b":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(raw, limit + 1)
        if decoder.unconsumed_tail:
            raise ValueError("公告解压后过大")
    if len(raw) > limit:
        raise ValueError("公告响应过大，保留现有缓存")
    return raw.decode(charset)
