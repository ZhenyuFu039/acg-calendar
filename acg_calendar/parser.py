"""Config-driven, section-aware Endfield announcement parser.

Source text is data. All extracted fields retain their source block and line.
Scheduled maintenance is never silently promoted to an actual opening time.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from .rewards import summarize_rewards

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def iso(value: dt.datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def stamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return result.astimezone(UTC)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag in {"p", "div", "section", "li", "br", "h1", "h2", "h3", "h4", "tr"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "div", "section", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)

    @property
    def text(self):
        return "\n".join(line for line in (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(self.chunks).splitlines()) if line)


def html_to_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.text


def load_rules(path: Path | None = None) -> dict:
    return json.loads((path or ROOT / "rules/endfield-cn.json").read_text(encoding="utf-8"))


class AnnouncementParser:
    def __init__(self, rules: dict | None = None):
        self.rules = rules or load_rules()
        self.zone = dt.timezone(dt.timedelta(minutes=self.rules["utc_offset_minutes"]))
        self.date_re = re.compile(self.rules["time"]["datetime_pattern"])
        self.structure = self.rules["structure"]
        self.version_re = re.compile(self.rules["sources"]["version_title_pattern"])
        self.heading_re = re.compile(self.structure["numbered_heading_pattern"])
        self.section_re = re.compile(self.structure["section_pattern"])
        self.variant_re = re.compile(self.structure["variant_heading_pattern"])
        self.inline_re = re.compile(self.structure["inline_event_pattern"])
        self.shop_re = re.compile(self.structure["shop_time_pattern"])

    def dates(self, text: str, year: int) -> list[dict]:
        result = []
        for match in self.date_re.finditer(text):
            values = match.groupdict()
            date = dt.datetime(int(values.get("year") or year), int(values["month"]), int(values["day"]), int(values["hour"]), int(values["minute"]), int(values.get("second") or 0), tzinfo=self.zone)
            result.append({"at": iso(date), "state": "exact", "reference": None, "raw": match.group(), "precision": "second" if values.get("second") else "minute", "year_inferred": not bool(values.get("year"))})
        return result

    @staticmethod
    def unknown(reference="unpublished", raw=""):
        return {"at": None, "state": "unknown", "reference": reference, "raw": raw, "precision": None, "year_inferred": False}

    def endpoint(self, text: str, year: int, role: str, maintenance: dict) -> dict:
        time_rules = self.rules["time"]
        # Cycle conditions describe an endpoint, never a calculable calendar date.
        if role == "end" and re.search(time_rules["cycle_end_pattern"], text):
            return self.unknown("recruitment_cycles", text)
        dates = self.dates(text, year)
        if dates:
            return dates[0]
        if any(marker in text for marker in time_rules["next_maintenance_markers"]):
            return self.unknown("next_maintenance_start", text)
        if any(marker in text for marker in time_rules["maintenance_end_markers"]):
            return {"at": maintenance.get("planned_end"), "state": "estimated" if maintenance.get("planned_end") else "unknown", "reference": "maintenance_end", "raw": text, "precision": "minute", "year_inferred": False}
        return self.unknown("unpublished", text)

    def window(self, text: str, year: int, maintenance: dict) -> tuple[dict, dict, list[str]]:
        parts = re.split(self.rules["time"]["range_separator"], text, maxsplit=1)
        warnings = []
        try:
            start = self.endpoint(parts[0], year, "start", maintenance)
            if len(parts) == 2:
                end = self.endpoint(parts[1], year, "end", maintenance)
            else:
                end = self.unknown("recruitment_cycles" if re.search(self.rules["time"]["cycle_end_pattern"], text) else "unpublished", text)
            if start["at"] and end["at"] and stamp(end["at"]) <= stamp(start["at"]):
                warnings.append("结束时间早于或等于开始时间，请检查公告")
                end = self.unknown("invalid_interval", text)
        except ValueError:
            start, end = self.unknown("invalid_date", text), self.unknown("invalid_date", text)
            warnings.append("日期格式或数值异常，保留原文待核对")
        return start, end, warnings

    def parse(self, text: str, *, cid: str, source_url: str, title: str = "", published_at: str | None = None, fetched_at: str | None = None, origin: str = "live") -> dict:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        version_match = self.version_re.search(title or "\n".join(lines[:45]))
        if not version_match:
            raise ValueError("未识别到版本更新说明标题，未覆盖已有数据")
        name = version_match.group("name")
        year_match = re.search(r"公告\s*(\d{4})[./年]", text)
        year = int(year_match.group(1)) if year_match else (stamp(published_at).year if published_at else dt.datetime.now(UTC).year)
        maintenance = {"start": None, "planned_end": None, "actual_end": None, "source_text": "", "source_line": None}
        maintenance_heading = self.rules["time"]["maintenance_heading"]
        for index, line in enumerate(lines):
            if maintenance_heading in line:
                for next_index in range(index, min(index + 4, len(lines))):
                    dates = self.dates(lines[next_index], year)
                    if len(dates) == 2:
                        maintenance.update(start=dates[0]["at"], planned_end=dates[1]["at"], source_text=lines[next_index], source_line=next_index + 1)
                        break
                if maintenance["start"]:
                    break
        version_id = "endfield-cn-" + str(cid)
        events = []
        parser_warnings = []
        section = ""
        block = None

        def flush():
            nonlocal block
            if block:
                events.extend(self.parse_block(block, version_id, year, maintenance, source_url))
            block = None

        for index, line in enumerate(lines):
            match = self.section_re.match(line)
            if match:
                flush()
                section = match.group("name").strip()
                continue
            if line.startswith("▼"):
                flush()
                section = ""
                continue
            if section not in self.structure["event_sections"]:
                continue
            heading = self.heading_re.match(line)
            if heading:
                flush()
                block = {"title": heading.group("name"), "section": section, "line": index + 1, "lines": []}
            elif block:
                block["lines"].append((index + 1, line))
        flush()
        if maintenance["start"]:
            events.insert(0, self.make_maintenance(version_id, maintenance, source_url))
        else:
            parser_warnings.append("公告中未识别到维护区间，维护后开放的活动继续保留为待定")
        if len(events) < 2:
            raise ValueError("未识别到足够的活动条目，可能是公告结构变化，未覆盖已有数据")
        version = {"schema_version": 1, "ruleset_version": self.rules["ruleset_version"], "id": version_id, "name": name, "game": self.rules["game_id"], "timezone": self.rules["timezone"], "source": {"cid": str(cid), "url": source_url, "title": title or f"「{name}」版本更新说明", "published_at": published_at, "fetched_at": fetched_at, "origin": origin, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}, "maintenance": maintenance, "events": events, "warnings": parser_warnings, "analysis": {"strategy": "section_rules", "ai_used": False, "event_count": len(events), "exact_windows": sum(e["start"]["state"] == "exact" and e["end"]["state"] == "exact" for e in events), "estimated_starts": sum(e["start"]["state"] == "estimated" for e in events), "unknown_ends": sum(e["end"]["at"] is None for e in events)}}
        return summarize_rewards(version, self.rules.get("rewards"), text)

    def parse_block(self, block, version_id, year, maintenance, source_url):
        segments = [{"title": block["title"], "category": self.structure["event_sections"][block["section"]], "line": block["line"], "lines": [], "suffix": "main"}]
        current = segments[0]
        for line_number, line in block["lines"]:
            variant = self.variant_re.match(line)
            inline = self.inline_re.match(line)
            field = re.match(r"^[·•]?\s*(?P<label>.+?)[:：]\s*(?P<value>.+)$", line)
            shop = self.shop_re.match(field.group("label")) if field else None
            if variant:
                current = {"title": block["title"] + " · " + variant.group("name"), "category": "event", "line": line_number, "lines": [], "suffix": "variant:" + variant.group("name")}
                segments.append(current)
            elif inline:
                current = {"title": inline.group("name"), "category": "event", "line": line_number, "lines": [], "suffix": "inline:" + inline.group("name")}
                segments.append(current)
            elif shop:
                # The reward shop is a separate deadline, not the parent activity end.
                shop_segment = {"title": shop.group("name") + " · 奖励兑换", "category": "exchange", "line": line_number, "lines": [(line_number, "· 开放时间：" + field.group("value"))], "suffix": "shop:" + shop.group("name")}
                segments.append(shop_segment)
            else:
                current["lines"].append((line_number, line))
        result = []
        for segment in segments:
            main_time, phases, description, requirements = None, [], [], []
            for number, line in segment["lines"]:
                match = re.match(r"^[·•]?\s*(?P<label>.+?)[:：]\s*(?P<value>.+)$", line)
                if not match:
                    continue
                label, value = match.group("label").strip(), match.group("value").strip()
                if label in self.rules["time"]["phase_labels"]:
                    for date in self.dates(value, year):
                        phases.append({"title": label, **date, "source_line": number, "raw": value})
                elif label in self.rules["time"]["primary_labels"] and main_time is None:
                    main_time = (number, label, value)
                elif label in self.rules["classification"]["description_labels"]:
                    description.append(value)
                elif label in self.rules["classification"]["requirement_prefixes"]:
                    requirements.append(value)
            if main_time is None:
                continue
            number, label, raw = main_time
            start, end, warnings = self.window(raw, year, maintenance)
            category = segment["category"]
            time_kind = "point" if category == "update" and end["reference"] == "unpublished" else "interval"
            if category == "update" and time_kind == "interval":
                category = "event"
            key = "|".join([version_id, block["section"], block["title"], segment["suffix"]])
            subtype = next((value for keyword, value in self.rules["classification"]["subtypes"].items() if keyword in segment["title"]), "玩法更新" if category == "update" else "活动")
            source_lines = [line for _, line in segment["lines"]]
            result.append({"id": hashlib.sha256(key.encode()).hexdigest()[:20], "version_id": version_id, "title": segment["title"], "parent_title": block["title"] if segment["suffix"] != "main" else None, "section": block["section"], "category": category, "subtype": subtype, "time_kind": time_kind, "start": start, "end": end, "phases": phases, "description": "\n".join(description), "requirements": requirements, "source": {"url": source_url, "line": number, "time_label": label, "time_text": raw, "block_text": "\n".join([segment["title"], *source_lines])}, "warnings": warnings})
            result[-1]["source"]["reward_lines"] = [{"line": n, "text": line} for n, line in segment["lines"] if "嵌晶玉" in line]
        return result

    def make_maintenance(self, version_id, maintenance, url):
        return {"id": version_id + "-maintenance", "version_id": version_id, "title": "版本更新维护", "parent_title": None, "section": "更新维护", "category": "maintenance", "subtype": "维护", "time_kind": "interval", "start": {"at": maintenance["start"], "state": "exact", "reference": None, "raw": maintenance["source_text"], "precision": "minute"}, "end": {"at": maintenance["planned_end"], "state": "estimated", "reference": "maintenance_end", "raw": maintenance["source_text"], "precision": "minute"}, "phases": [], "description": "维护结束时间为公告预计值。确认实际开服后，可在此记录实际时间。", "requirements": [], "source": {"url": url, "line": maintenance["source_line"], "time_label": "更新维护时间", "time_text": maintenance["source_text"], "block_text": maintenance["source_text"]}, "warnings": []}


def resolve_versions(versions: list[dict], local_state: dict) -> list[dict]:
    """Resolve references on copies so refreshes never overwrite user corrections."""
    result = json.loads(json.dumps(versions))
    ordered = sorted(result, key=lambda version: (version.get("game", "endfield-cn"), version["maintenance"].get("start") or ""))
    for index, version in enumerate(ordered):
        maintenance = version["maintenance"]
        confirmed = local_state.get("version:" + version["id"], {}).get("actual_end")
        maintenance["actual_end"] = confirmed
        next_start = ordered[index + 1]["maintenance"].get("start") if index + 1 < len(ordered) and ordered[index + 1].get("game") == version.get("game") else None
        for event in version["events"]:
            overrides = local_state.get(event["id"], {})
            for role in ("start", "end"):
                endpoint = event[role]
                if endpoint.get("reference") == "maintenance_end" and confirmed:
                    endpoint.update(at=confirmed, state="confirmed")
                if endpoint.get("reference") == "next_maintenance_start" and next_start:
                    endpoint.update(at=next_start, state="exact")
                if overrides.get(role + "_override"):
                    endpoint.update(at=overrides[role + "_override"], state="manual", reference="manual")
            event["completed"] = bool(overrides.get("completed"))
            event["ignored"] = bool(overrides.get("ignored"))
            event["reminder_minutes"] = overrides.get("reminder_minutes")
            event["has_overrides"] = bool(overrides.get("start_override") or overrides.get("end_override"))
            event["notes"] = overrides.get("notes", "")
            if event["start"]["at"] and event["end"]["at"] and stamp(event["end"]["at"]) <= stamp(event["start"]["at"]):
                event["warnings"].append("校正或关联后的时间顺序有误，请重新核对")
                event["invalid_interval"] = True
    return result
