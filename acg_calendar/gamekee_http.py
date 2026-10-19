"""Anonymous, bounded reads from a configured GameKee Wiki and its media CDN."""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request

from . import __version__


def secure_url(url):
    if not isinstance(url, str):
        return None
    url = "https:" + url if url.startswith("//") else url
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port not in {None, 443}:
            return None
        if ".." in urllib.parse.unquote(p.path).split("/"):
            return None
        return p
    except ValueError:
        return None


class GameKeeHTTP:
    def __init__(self, alias, wiki_id, interval=.5):
        self.alias, self.wiki_id, self.interval = alias, wiki_id, interval
        self.last = 0

    def allowed(self, url, *, image=False):
        p = secure_url(url)
        if not p:
            return False
        if image:
            return p.hostname == "cdnimg-v2.gamekee.com" and (p.path.startswith(f"/wiki2.0/pro/{self.wiki_id}/images/") or p.path.startswith("/wiki2.0/pro/215/video/")) and p.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        if p.hostname == "www.gamekee.com":
            return p.path in {"/v1/gameServer/list", "/v1/activity/page-list"} or bool(re.fullmatch(r"/v1/content/detail/\d+", p.path))
        return p.hostname == "api-cdn.gamekee.com" and bool(re.fullmatch(rf"/wiki2\.0/pro/{self.wiki_id}/content/\d+\.json", p.path))

    def read(self, url, *, image=False):
        url = "https:" + url if url.startswith("//") else url
        if not self.allowed(url, image=image):
            raise ValueError("GameKee 地址超出已配置的公开来源")
        parent = self
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not parent.allowed(newurl, image=image) or urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
                    raise ValueError("GameKee 跳转超出配置来源")
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        delay = self.interval - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()
        headers = {"User-Agent": f"Mozilla/5.0 (compatible; PlaytimeCalendar/{__version__})", "game-alias": self.alias, "Lang": "zh-cn", "X-Requested-With": "XMLHttpRequest", "Referer": f"https://www.gamekee.com/{self.alias}/", "Accept-Encoding": "identity"}
        limit = 5_000_000 if image else 6_000_000
        with urllib.request.build_opener(Redirect()).open(urllib.request.Request(url, headers=headers), timeout=20) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("GameKee 响应超过大小限制")
        return data
