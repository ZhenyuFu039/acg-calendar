"""Read only the official public Endfield list and selected version articles."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re
import threading
import urllib.parse
import urllib.request

from .parser import AnnouncementParser, ROOT, html_to_text, iso


def fetch(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"web-news.hypergryph.com", "endfield.hypergryph.com"}:
        raise ValueError("公告采集仅支持终末地官方来源")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ACGCalendar/0.1)", "Accept": "application/json,text/html;q=0.9", "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("公告响应过大，保留现有缓存")
        return raw.decode(response.headers.get_content_charset() or "utf-8")


class Collector:
    def __init__(self, store, data_dir: Path, parser=None, fetcher=fetch):
        self.store, self.data_dir = store, data_dir
        self.parser = parser or AnnouncementParser()
        self.fetcher = fetcher
        self.lock = threading.Lock()
        self.last_attempt = None

    def seed(self):
        if self.store.versions():
            return
        metadata = json.loads((ROOT / "samples/source.json").read_text(encoding="utf-8"))
        text = (ROOT / "samples/endfield-cn-6094.txt").read_text(encoding="utf-8")
        version = self.parser.parse(text, **metadata, origin="snapshot")
        self.store.save_version(version)
        self.store.set_meta("sync", {"running": False, "last_success": None, "last_attempt": None, "error": None, "mode": "snapshot", "message": "内置真实官网快照；联网同步后更新"})

    def start(self, force=False):
        if not self.lock.acquire(blocking=False):
            return False
        now = dt.datetime.now(dt.timezone.utc)
        if not force and self.last_attempt and (now - self.last_attempt).total_seconds() < 30:
            self.lock.release()
            return False
        self.last_attempt = now
        previous = self.store.get_meta("sync", {})
        self.store.set_meta("sync", {**previous, "running": True, "last_attempt": iso(now), "error": None})
        threading.Thread(target=self._run, daemon=True, name="announcement-sync").start()
        return True

    def _run(self):
        try:
            self.collect()
        except Exception as error:
            previous = self.store.get_meta("sync", {})
            self.store.set_meta("sync", {**previous, "running": False, "error": str(error)[:400], "message": "同步失败，保留本地数据"})
        finally:
            self.lock.release()

    def collect(self):
        rules = self.parser.rules
        listing = json.loads(self.fetcher(rules["sources"]["list_url"]))
        if listing.get("code") != 0 or not isinstance(listing.get("data", {}).get("list"), list):
            raise ValueError("公告列表结构变化，保留已有数据")
        articles = [item for item in listing["data"]["list"] if self.parser.version_re.search(item.get("title", ""))]
        articles.sort(key=lambda item: item.get("displayTime", 0), reverse=True)
        if not articles:
            raise ValueError("列表中未找到版本更新说明，保留已有数据")
        collected = []
        fetched_at = iso(dt.datetime.now(dt.timezone.utc))
        for article in articles[:rules["sources"]["max_version_articles"]]:
            cid = str(article["cid"])
            if not re.fullmatch(r"\d+", cid):
                raise ValueError("公告 ID 格式异常")
            url = rules["sources"]["detail_url_template"].format(cid=cid)
            text = html_to_text(self.fetcher(url))
            published = iso(dt.datetime.fromtimestamp(article["displayTime"], dt.timezone.utc)) if article.get("displayTime") else None
            version = self.parser.parse(text, cid=cid, source_url=url, title=article["title"], published_at=published, fetched_at=fetched_at)
            collected.append((version, text))
        # Parse every selected source before replacing anything.
        source_dir = self.data_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        for version, text in collected:
            (source_dir / (version["id"] + ".txt")).write_text(text, encoding="utf-8")
            self.store.save_version(version)
        self.store.set_meta("sync", {"running": False, "last_attempt": fetched_at, "last_success": fetched_at, "error": None, "mode": "live", "message": "已同步官方公告", "article_count": len(collected)})
        return [version for version, _ in collected]
