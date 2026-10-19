"""Extract auditable time-window candidates from the saved live scrape.

No model calls. Deliberately leaves maintenance-relative endpoints unresolved.
This is a feasibility extractor, not a complete production announcement parser.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from probe_announcements import PageParser

CN = re.compile(r"(?:(?P<year>\d{4})[年/\-])?(?P<month>\d{1,2})[月/\-](?P<day>\d{1,2})日?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?")
MONTHS = {name: i for i, name in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
MONTH_PATTERN = "(?:" + "|".join(MONTHS) + ")"
EN = re.compile(r"(?P<month>" + MONTH_PATTERN + r")\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4}),?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})", re.I)
EN_REVERSE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2}),\s*(?P<month>" + MONTH_PATTERN + r")\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})", re.I)
DATE_HINT = re.compile(r"\d{1,2}月\d{1,2}日|\d{4}/\d{1,2}/\d{1,2}|" + MONTH_PATTERN, re.I)
RANGE_HINT = re.compile(r"[~～—]|\s-\s|\bto\b|\bend on\b", re.I)


def text_from_html(html: str, url: str) -> str:
    parser = PageParser(url)
    parser.feed(html)
    return parser.plain_text


def normalize_window(line: str, year: int, offset: int) -> dict:
    """Return only timestamps explicitly present; do not invent maintenance times."""
    timezone = dt.timezone(dt.timedelta(hours=offset))
    english = list(EN.finditer(line)) + list(EN_REVERSE.finditer(line))
    matches = sorted(english, key=lambda m: m.start()) if english else list(CN.finditer(line))
    dates, inferred = [], False
    for match in matches:
        values = match.groupdict()
        value_year = int(values["year"]) if values.get("year") else year
        inferred |= not bool(values.get("year"))
        month = MONTHS[values["month"].capitalize()] if english else int(values["month"])
        try:
            value = dt.datetime(value_year, month, int(values["day"]), int(values["hour"]), int(values["minute"]), int(values.get("second") or 0), tzinfo=timezone)
            dates.append(value)
        except ValueError:
            return {"start": None, "end": None, "review_reasons": ["invalid date"]}
    start = end = None
    reasons = []
    if len(dates) == 2:
        start, end = dates
        # Cross-year inference is permitted only when the end year is omitted.
        if end < start and not matches[1].groupdict().get("year"):
            end = end.replace(year=start.year + 1)
        if end <= start:
            reasons.append("non-positive interval")
            start = end = None
    elif len(dates) == 1:
        pos = matches[0].start()
        before, after = line[:pos], line[matches[0].end():]
        if re.search(r"维护后|maintenance\s+to|\bend on\b", before, re.I) or re.search(r"(?:^|\s)-\s*$", before):
            end = dates[0]
        elif re.search(r"维护前|维护后结束|后结束|maintenance", after, re.I):
            start = dates[0]
        else:
            reasons.append("single timestamp has no unambiguous endpoint role")
    elif len(dates) > 2:
        reasons.append("more than two explicit timestamps")
    if start is None or end is None:
        reasons.append("one or both endpoints unspecified; preserve source text")
    if inferred:
        reasons.append("year inferred from announcement publication year")
    if not re.search(r"UTC\s*[+]\s*" + str(offset), line, re.I):
        reasons.append("timezone from game/server configuration")
    def encode(value):
        if value is None:
            return None
        return {"source": value.isoformat(), "utc": value.astimezone(dt.timezone.utc).isoformat(), "new_york": value.astimezone(ZoneInfo("America/New_York")).isoformat()}
    return {"start": encode(start), "end": encode(end), "review_reasons": reasons}


def extract(text: str, source: dict) -> list[dict]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    output = []
    heading = source["title"]
    for index, line in enumerate(lines):
        if re.match(r"^(?:[一二三四五六七八九十]+、|\d+(?:\.\d+)*[.、\s])", line) and len(line) < 180:
            heading = line.split("活动时间", 1)[0].strip("，, ")
        if len(line) > 900 or not DATE_HINT.search(line) or not RANGE_HINT.search(line):
            continue
        # Avoid treating descriptive prose as a schedule merely because it has dates.
        if source["game"] != "nikke" and not (CN.search(line) or "活动时间" in line or "维护后" in line):
            continue
        normalized = normalize_window(line, source["publication_year"], source["offset_hours"])
        output.append({"id": source["game"] + "-" + str(index + 1), "game": source["game"], "title_candidate": heading, "source_url": source["url"], "source_file": source["file"], "source_line": index + 1, "source_quote": line, "source_offset_hours": source["offset_hours"], "precision_note": "Preserved as written; inclusive/exclusive end semantics not inferred", **normalized, "requires_review": bool(normalized["review_reasons"])})
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("logs/scrape-probe"))
    args = ap.parse_args()
    root = args.input
    nikke = json.loads((root / "browser-details/nikke-data-1.txt").read_text(encoding="utf-8"))["data"]
    azur = json.loads((root / "azurlane-detail/azurlane-18457.html").read_text(encoding="utf-8"))["data"]
    nikke_text = text_from_html(nikke["content"], "https://nikke-en.com/")
    azur_text = text_from_html(azur["content"], "https://game.bilibili.com/blhx/")
    (root / "browser-details/nikke-article.txt").write_text(nikke_text, encoding="utf-8")
    (root / "azurlane-detail/azurlane-article.txt").write_text(azur_text, encoding="utf-8")
    sources = [
        {"game": "arknights", "title": "恒远津梁活动公告", "url": "https://ak.hypergryph.com/news/1454", "file": "details/arknights-1454.txt", "offset_hours": 8, "publication_year": 2026},
        {"game": "endfield", "title": "丹青渡版本更新说明", "url": "https://endfield.hypergryph.com/news/6094", "file": "public-data/endfield-6094.txt", "offset_hours": 8, "publication_year": 2026},
        {"game": "azurlane", "title": azur["title"], "url": "https://game.bilibili.com/blhx/news/?news_detail_id=18457", "file": "azurlane-detail/azurlane-article.txt", "offset_hours": 8, "publication_year": 2026},
        {"game": "nikke", "title": nikke["title"], "url": "https://nikke-en.com/newsdetail.html?content_id=" + nikke["content_id"], "file": "browser-details/nikke-article.txt", "offset_hours": 9, "publication_year": 2026},
    ]
    candidates = []
    for source in sources:
        text = (root / source["file"]).read_text(encoding="utf-8")
        source["text_characters"] = len(text)
        rows = extract(text, source)
        source["candidate_count"] = len(rows)
        source["complete_window_count"] = sum(bool(row["start"] and row["end"]) for row in rows)
        candidates.extend(rows)
    result = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "ai_used": False, "scope": "One sampled article per game; this does not measure exhaustive coverage or long-term reliability", "sources": sources, "candidates": candidates}
    (root / "events.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sources": sources, "candidate_total": len(candidates), "complete_windows": sum(bool(row["start"] and row["end"]) for row in candidates)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
