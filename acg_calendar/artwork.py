"""Match official announcement covers to named activities and cache locally."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
import urllib.parse
import urllib.request

HOSTS = {"web.hycdn.cn"}
MAX_BYTES = 5_000_000


def official_image(url):
    try:
        parsed = urllib.parse.urlsplit(url or "")
        return parsed.scheme == "https" and parsed.hostname in HOSTS and parsed.port in {None, 443} and not parsed.username and not parsed.password and parsed.path.startswith("/upload/image/")
    except (ValueError, TypeError):
        return False


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official_image(newurl):
            raise ValueError("图片跳转到非官方来源")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_kind(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("不是支持的静态图片")


def download(url):
    if not official_image(url):
        raise ValueError("仅缓存官方图片 CDN")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Playtime/0.0.1)"})
    with urllib.request.build_opener(OfficialRedirect()).open(request, timeout=10) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("图片超过 5MB")
    image_kind(data)
    return data


def names(title):
    return {re.sub(r"\s+", "", name) for name in re.findall(r"「([^」]+)」", title) if len(name) >= 2}


class ArtworkCache:
    def __init__(self, data_dir: Path, downloader=download):
        self.directory = data_dir / "artwork"
        self.downloader = downloader

    def path(self, key):
        if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|webp)", key):
            return None
        path = self.directory / key
        return path if path.is_file() else None

    def cache(self, url):
        if not official_image(url):
            return None
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        for suffix in ("jpg", "png", "webp"):
            if self.path(digest + "." + suffix):
                return digest + "." + suffix
        data = self.downloader(url)
        if len(data) > MAX_BYTES:
            raise ValueError("图片超过 5MB")
        key = digest + "." + image_kind(data)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / (key + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(self.directory / key)
        return key

    def attach(self, version, articles, previous=None):
        old = {e["id"]: e for e in (previous or {}).get("events", [])}
        covers, errors = {}, 0
        for article in articles:
            if official_image(article.get("cover")) and re.fullmatch(r"\d+", str(article.get("cid", ""))):
                for name in names(article.get("title", "")):
                    covers.setdefault(name, article)
        used = set()
        for event in version["events"]:
            # Restore an existing, proven match if it fell outside the latest list.
            saved = old.get(event["id"], {}).get("artwork")
            if saved and self.path(saved.get("key", "")):
                event["artwork"] = saved
            candidates = names(event["title"])
            article = next((covers[name] for name in sorted(candidates) if name in covers), None)
            if not article or (article["cover"] not in used and len(used) >= 12):
                continue
            try:
                key = self.cache(article["cover"])
                used.add(article["cover"])
                event["artwork"] = {"key": key, "path": "/api/artwork/" + key, "url": article["cover"], "source_url": "https://endfield.hypergryph.com/news/" + str(article["cid"]), "source_title": article["title"], "match": "exact_activity_name"}
            except Exception:
                # Artwork must never prevent announcement times from updating.
                errors += 1
        version["artwork_summary"] = {"matched": sum(bool(e.get("artwork")) for e in version["events"]), "total": len(version["events"]), "errors": errors}
