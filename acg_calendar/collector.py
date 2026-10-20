"""Read only the official public Endfield list and selected version articles."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re
import threading
import urllib.parse

from .network import network_job
from .parser import AnnouncementParser, ROOT, html_to_text, iso
from .artwork import ArtworkCache
from .briefing import BriefingReader, merge_briefing, normalize
from .official import fetch_text


def fetch(url: str) -> str:
    return fetch_text(url)


class Collector:
    def __init__(self, store, data_dir: Path, parser=None, fetcher=fetch):
        self.store, self.data_dir = store, data_dir
        self.parser = parser or AnnouncementParser()
        self.fetcher = fetcher
        self.lock = threading.Lock()
        self.last_attempt = None
        self.artwork = ArtworkCache(data_dir)

    def seed(self):
        if any(v.get("game") == "endfield-cn" for v in self.store.versions()):
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

    @network_job
    def collect(self):
        rules = self.parser.rules
        sources = rules["sources"]
        listing_items, seen, pages = [], set(), 0
        for page in range(1, min(5, max(1, sources.get("max_list_pages", 1))) + 1):
            base = urllib.parse.urlsplit(sources["list_url"])
            query = dict(urllib.parse.parse_qsl(base.query))
            query["page"] = str(page)
            url = urllib.parse.urlunsplit(base._replace(query=urllib.parse.urlencode(query)))
            listing = json.loads(self.fetcher(url))
            if listing.get("code") != 0 or not isinstance(listing.get("data", {}).get("list"), list):
                raise ValueError("公告列表结构变化，保留已有数据")
            rows = listing["data"]["list"]
            pages += 1
            added = 0
            for row in rows:
                cid = str(row.get("cid", ""))
                if cid not in seen:
                    seen.add(cid)
                    listing_items.append(row)
                    added += 1
            if len(rows) < int(query.get("pageSize", 20)) or not added:
                break
        articles = [item for item in listing_items if self.parser.version_re.search(item.get("title") or "")]
        articles.sort(key=lambda item: item.get("displayTime", 0), reverse=True)
        briefing_pattern = re.compile(sources.get("briefing_news_pattern", "新版本导览|版本导览专题"))
        news = [item for item in listing_items if briefing_pattern.search(item.get("title") or "")]
        news.sort(key=lambda item: item.get("displayTime", 0), reverse=True)
        if not articles and not news:
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
        reader = BriefingReader(self.data_dir, self.fetcher)
        guides = {normalize(guide["name"]): guide for guide in reader.cached()}
        errors = []
        for article in news[:sources.get("max_briefing_articles", 3)]:
            try:
                guide = reader.read(article, sources["detail_url_template"], fetched_at)
                guides[normalize(guide["name"])] = guide
            except Exception as error:
                errors.append({"news_cid": str(article.get("cid")), "title": article.get("title"), "message": str(error)[:300]})
        previous_versions = self.store.versions()
        for version, _ in collected:
            previous = next((v for v in previous_versions if v["id"] == version["id"]), None)
            self.artwork.attach(version, listing_items, previous)
            guide = guides.get(normalize(version["name"]))
            if guide:
                merge_briefing(version, guide, self.artwork)
                version["briefing"]["cache_reused"] = bool(guide.get("cache_reused", True))
            else:
                version["briefing_status"] = "not_found"
        # Parse every selected source before replacing anything.
        source_dir = self.data_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        for version, text in collected:
            (source_dir / (version["id"] + ".txt")).write_text(text, encoding="utf-8")
            self.store.save_version(version)
        known = {normalize(v["name"]) for v in previous_versions} | {normalize(v["name"]) for v, _ in collected}
        previews = [{key: guide[key] for key in ("name", "version_tag", "source_url", "news_url", "fetched_at")} | {"activity_count": len(guide["entries"])} for name, guide in guides.items() if name not in known]
        self.store.set_meta("briefing_previews", previews)
        self.store.set_meta("sync", {"running": False, "last_attempt": fetched_at, "last_success": fetched_at, "error": None, "mode": "live", "message": "已同步官方更新公告与版本导览" if any(v.get("briefing") for v, _ in collected) else "已同步官方公告", "article_count": len(collected), "list_pages": pages, "briefing_count": sum(bool(v.get("briefing")) for v, _ in collected), "briefing_errors": errors, "pending_briefings": len(previews)})
        return [version for version, _ in collected]
