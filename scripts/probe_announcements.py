"""One-shot public announcement probe; standard library only, no AI calls.

Usage: python scripts/probe_announcements.py [--output logs/scrape-probe]
       python scripts/probe_announcements.py --page name=https://official/url
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_PAGES = {
    "arknights": "https://ak.hypergryph.com/",
    "endfield": "https://endfield.hypergryph.com/news",
    "azurlane": "https://game.bilibili.com/blhx/",
    "nikke": "https://nikke-en.com/news.html",
}


class PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.text: list[str] = []
        self.links: list[dict] = []
        self.scripts: list[str] = []
        self.inline_scripts: list[str] = []
        self._skip = 0
        self._script = False
        self._anchor: dict | None = None
        self._title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "script":
            self._script = True
            if attrs.get("src"):
                self.scripts.append(urllib.parse.urljoin(self.base_url, attrs["src"]))
        if tag == "title":
            self._title = True
        if tag == "a" and attrs.get("href"):
            self._anchor = {"url": urllib.parse.urljoin(self.base_url, attrs["href"]), "text": ""}
        if tag in {"p", "div", "section", "li", "br", "h1", "h2", "h3", "tr"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"}:
            self._skip = max(0, self._skip - 1)
        if tag == "script":
            self._script = False
        if tag == "title":
            self._title = False
        if tag == "a" and self._anchor is not None:
            self._anchor["text"] = re.sub(r"\s+", " ", self._anchor["text"]).strip()
            self.links.append(self._anchor)
            self._anchor = None
        if tag in {"p", "div", "section", "li", "h1", "h2", "h3", "tr"}:
            self.text.append("\n")

    def handle_data(self, data: str):
        if self._script:
            self.inline_scripts.append(data)
        if not self._skip:
            self.text.append(data)
            if self._anchor is not None:
                self._anchor["text"] += data
        if self._title:
            self.title += data

    @property
    def plain_text(self):
        return "\n".join(line for line in (re.sub(r"[\t \r\f\v]+", " ", x).strip() for x in "".join(self.text).splitlines()) if line)


def probe(name: str, url: str, output: Path) -> dict:
    started = time.monotonic()
    result = {"name": name, "requested_url": url, "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "method": "python-urllib", "ai_used": False}
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ACGCalendarFeasibility/0.1)",
            "Accept": "text/html,application/json,text/javascript;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Response exceeded 8 MB cap")
            result.update(status=response.status, final_url=response.url, content_type=response.headers.get("Content-Type"), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            encoding = response.headers.get_content_charset()
        if not encoding:
            meta = re.search(rb'charset=["\s\']*([a-zA-Z0-9_-]+)', raw[:8192])
            encoding = meta.group(1).decode("ascii") if meta else "utf-8"
        body = raw.decode(encoding, errors="replace")
        (output / (name + ".html")).write_text(body, encoding="utf-8")
        parser = PageParser(result["final_url"])
        parser.feed(body)
        plain = parser.plain_text
        (output / (name + ".txt")).write_text(plain, encoding="utf-8")
        result.update(title=parser.title.strip(), text_characters=len(plain), links=parser.links, scripts=parser.scripts)
        result["time_related_lines"] = [line for line in plain.splitlines() if re.search(r"活动时间|活动期间|维护时间|服务器时间|UTC|Duration|duration|Maintenance|maintenance|\d{1,2}:\d{2}", line)][:45]
        (output / (name + ".inline-js.txt")).write_text("\n".join(parser.inline_scripts), encoding="utf-8")
        result["ok"] = True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result.update(ok=False, error=str(exc))
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=Path("logs/scrape-probe"))
    ap.add_argument("--page", action="append", help="name=https://official/url (repeatable)")
    args = ap.parse_args()
    pages = dict(item.split("=", 1) for item in args.page) if args.page else DEFAULT_PAGES
    for name, url in pages.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or urllib.parse.urlsplit(url).scheme != "https":
            ap.error("Use a simple filename and an HTTPS URL")
    args.output.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: probe(*item, args.output), pages.items()))
    (args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        summary = {k: result[k] for k in ("name", "ok", "status", "title", "bytes", "text_characters", "elapsed_seconds", "error") if k in result}
        summary["time_related_line_count"] = len(result.get("time_related_lines", []))
        print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
