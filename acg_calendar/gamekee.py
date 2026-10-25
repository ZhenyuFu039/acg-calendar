"""Declarative GameKee source for local JSON plugins; no plugin code execution."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import threading
import urllib.error
import urllib.parse
import unicodedata

from .artwork import image_kind
from .gamekee_http import GameKeeHTTP, secure_url
from .network import deferring_images, network_job
from .parser import iso, stamp
from .plugin_media import local_file
from .providers import JsonProvider, bounded_json, date_value, public_url
from .sync_progress import SyncProgress


def clean(text):
    return str(text or "").replace("\ufeff", "").strip()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def unix(value):
    if type(value) is not int or not 1_262_304_000 <= value <= 4_102_444_800:
        raise ValueError("GameKee 时间戳无效")
    return iso(dt.datetime.fromtimestamp(value, dt.timezone.utc))


def unknown():
    return {"at": None, "state": "unknown", "raw": "", "precision": "minute", "reference": None}


def content_blocks(content):
    """Read Slate-like editor content as text/images, never HTML or JavaScript."""
    if isinstance(content, str):
        content = json.loads(content)
    if isinstance(content, dict):
        content = content.get("children", content.get("root", {}).get("children", []))
    if not isinstance(content, list) or len(content) > 2000:
        raise ValueError("GameKee 公告正文结构变化")
    def walk(node, depth=0):
        if depth > 30:
            raise ValueError("公告嵌套过深")
        if not isinstance(node, dict):
            return "", []
        text, pictures = clean(node.get("text", "")), []
        for key in ("url", "src"):
            value = node.get(key)
            if isinstance(value, str) and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", value, re.I):
                pictures.append(value.split("?")[0])
        for child in node.get("children", []):
            child_text, child_pictures = walk(child, depth + 1)
            text += child_text
            pictures.extend(child_pictures)
        return text, pictures
    return [{"index": i, "text": text, "images": pictures} for i, node in enumerate(content) for text, pictures in [walk(node)]]


class NoticeParser:
    def __init__(self, rules):
        self.rules = rules
        self.date = re.compile(rules["time"]["date_pattern"])
        self.clock = re.compile(rules["time"]["clock_pattern"])
        self.heading = re.compile(rules["sections"]["heading_pattern"])
        self.name = re.compile(rules["matching"]["name_pattern"])

    def offset(self, text, default=None):
        match = re.search(r"UTC\s*([+-])\s*(\d{1,2})(?::(\d{2}))?", text, re.I)
        if match:
            hours, minutes = int(match[2]), int(match[3] or 0)
            if hours > 14 or minutes > 59:
                raise ValueError("公告 UTC 偏移无效")
            return dt.timezone((1 if match[1] == "+" else -1) * dt.timedelta(hours=hours, minutes=minutes))
        hours = self.rules["time"]["maintenance_offset_hours"] if "北京时间" in text else self.rules["time"]["default_notice_offset_hours"] if default is None else default
        return dt.timezone(dt.timedelta(hours=hours))

    def endpoint(self, match, text, *, role, maintenance):
        fields = match.groupdict()
        date = dt.datetime(int(fields["year"]), int(fields["month"]), int(fields["day"]), int(fields.get("hour") or 0), int(fields.get("minute") or 0), int(fields.get("second") or 0), tzinfo=self.offset(text))
        relative = role == "start" and "维护结束后" in text[:text.find("～") if "～" in text else len(text)]
        if relative:
            return {"at": maintenance.get("planned_end"), "state": "estimated" if maintenance.get("planned_end") else "unknown", "reference": "maintenance_end" if maintenance.get("planned_end") else None, "raw": text, "precision": "minute"}
        return {"at": iso(date), "state": "exact" if fields.get("hour") is not None else "estimated", "reference": None, "raw": text, "precision": "second" if fields.get("second") else "minute" if fields.get("hour") else "day"}

    def window(self, text, maintenance):
        dates = list(self.date.finditer(text))
        if not dates:
            return None
        start = self.endpoint(dates[0], text, role="start", maintenance=maintenance)
        end = self.endpoint(dates[1], text, role="end", maintenance=maintenance) if len(dates) > 1 else unknown()
        # Maintenance notices often omit the date before their second clock.
        if len(dates) == 1 and "维护时间" in text:
            clocks = list(self.clock.finditer(text))
            if len(clocks) > 1:
                base = stamp(start["at"]).astimezone(self.offset(text))
                fields = clocks[-1].groupdict()
                value = base.replace(hour=int(fields["hour"]), minute=int(fields["minute"]), second=int(fields.get("second") or 0))
                end = {"at": iso(value), "state": "estimated", "reference": "maintenance_end", "raw": text, "precision": "second"}
        return start, end

    def parse(self, notice):
        cid = str(notice["id"])
        maintenance = {"start": None, "planned_end": None}
        sections, current = [], None
        for block in notice["blocks"]:
            text = clean(block.get("text"))
            if len(text) > 10000:
                raise ValueError("公告段落过长")
            if "维护时间" in text:
                window = self.window(text, maintenance)
                if window:
                    maintenance = {"start": window[0]["at"], "planned_end": window[1]["at"], "source_text": text, "source_line": block["index"] + 1}
            heading = self.heading.match(text)
            if heading:
                current = {"number": heading["number"], "title": heading["title"], "blocks": [], "parent": None}
                sections.append(current)
            elif current and text.startswith("①") and "挑战关卡" in text and self.rules["sections"]["split_challenge"]:
                current = {"number": current["number"] + "-challenge", "title": current["title"] + " · 挑战关卡", "blocks": [], "parent": current["number"]}
                sections.append(current)
            elif current:
                current["blocks"].append(block)
        events = []
        for section in sections:
            number, title = section["number"], section["title"]
            if not any(number == prefix or number.startswith(prefix + ".") or number.startswith(prefix + "-") for prefix in self.rules["sections"]["include"]):
                continue
            if any(number == prefix or number.startswith(prefix + ".") for prefix in self.rules["sections"].get("exclude_paid_shop_sections", [])):
                continue
            category, subtype = "event", "活动"
            for rule in self.rules["sections"]["category_rules"]:
                if any(keyword in title for keyword in rule["contains"]):
                    category, subtype = rule["category"], rule["subtype"]
                    break
            windows, picture = [], None
            for block in section["blocks"]:
                text = clean(block.get("text"))
                picture = picture or next(iter(block.get("images", [])), None)
                # Source time lines and dated boss rows; ignore descriptions.
                if self.date.search(text) and (text.startswith(("*", "①", "【")) or self.date.match(text) or any(label in text for label in self.rules["time"].get("labels", []))):
                    if "兑换将于" not in text:
                        parsed = self.window(text, maintenance)
                        if parsed:
                            windows.append((block, parsed))
            if not windows and category == "update" and self.rules["sections"]["keep_unscheduled_updates"]:
                windows = [({"index": 0, "text": "公告未给出开放时刻"}, (unknown(), unknown()))]
            if not self.rules["sections"].get("split_repeated_windows", True):
                windows = windows[:1]
            for ordinal, (block, (start, end)) in enumerate(windows, 1):
                name = title
                boss = re.match(r"【([^】]+)】", clean(block.get("text")))
                if boss:
                    name += " · " + boss[1]
                if len(windows) > 1 and not boss:
                    name += " · 第 " + str(ordinal) + " 期"
                event = {"id": f"notice-{cid}-{number}-{ordinal}", "title": name, "category": category, "subtype": subtype, "start": start, "end": end, "source_url": notice["source_url"], "time_kind": "point" if category == "update" and end["at"] is None else "interval", "description": "", "warnings": [], "evidence": {"notice_id": cid, "notice_title": notice["title"], "notice_updated_at": notice.get("updated_at"), "line": block["index"] + 1, "time_text": clean(block.get("text")), "section": number, "basis": "notice", "source_offset": str(self.offset(clean(block.get("text"))))}}
                if start["state"] == "estimated":
                    event["warnings"].append("开始为维护预计结束或仅日期值；不代表已确认开服")
                if section["parent"]:
                    event["parent_id"] = f"notice-{cid}-{section['parent']}-1"
                if picture:
                    event["image_url"], event["image_match"] = picture, "same_notice_section"
                events.append(event)
            if windows and self.rules["sections"]["split_shop_deadline"]:
                for block in section["blocks"]:
                    text = clean(block.get("text"))
                    dates = list(self.date.finditer(text))
                    if "兑换将于" not in text or not dates:
                        continue
                    start = copy.deepcopy(windows[0][1][0])
                    start.update(state="estimated" if start["at"] else "unknown", reference=None, raw="公告未单列兑换开始时刻，暂按主活动开始展示")
                    events.append({"id": f"notice-{cid}-{number}-shop", "parent_id": f"notice-{cid}-{number}-1", "title": title + " · 奖励兑换", "category": "exchange", "subtype": "活动商店", "start": start, "end": self.endpoint(dates[0], text, role="end", maintenance=maintenance), "source_url": notice["source_url"], "warnings": [start["raw"]], "evidence": {"basis": "notice", "notice_id": cid, "notice_title": notice["title"], "line": block["index"] + 1, "time_text": text}})
        if maintenance["start"] and maintenance["planned_end"]:
            events.insert(0, {"id": "notice-" + cid + "-maintenance", "title": "版本更新维护", "category": "maintenance", "subtype": "预计维护", "start": {"at": maintenance["start"], "state": "exact", "raw": maintenance["source_text"]}, "end": {"at": maintenance["planned_end"], "state": "estimated", "reference": "maintenance_end", "raw": maintenance["source_text"]}, "source_url": notice["source_url"], "evidence": {"basis": "notice", "notice_id": cid, "notice_title": notice["title"], "line": maintenance["source_line"], "time_text": maintenance["source_text"]}, "warnings": ["维护结束为预计时间，可在详情中确认实际开服"]})
        return events, maintenance

    def names(self, title):
        title = re.sub(self.rules["matching"].get("card_prefix_pattern", r"(?!)"), "", title)
        return {re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", m[0]).casefold()) for m in self.name.finditer(title) if len(m[0].strip()) >= 4}


def curate_events(events, rules):
    """Apply plugin editorial choices to both collected and offline snapshots."""
    policy = rules.get("curation", {})
    if not isinstance(policy, dict) or any(not isinstance(e.get("evidence", {}), dict) for e in events):
        raise ValueError("活动合并规则或公告依据格式无效")
    excluded = {e["id"] for e in events if any(name in e["title"] for name in policy.get("exclude_title_contains", []))}
    by_id = {e["id"]: e for e in events if e["id"] not in excluded}
    for event in events:
        if event.get("parent_id") in excluded:
            excluded.add(event["id"])
        parent = by_id.get(event.get("parent_id"))
        section = str(event.get("evidence", {}).get("section", ""))
        if event["id"] not in excluded and parent and section.endswith("-challenge") and policy.get("merge_challenge_into_parent"):
            # Keep the parent's stable ID, headline, artwork and deadline.
            parent.setdefault("evidence", {}).setdefault("merged_events", []).append({
                "id": event["id"], "title": event["title"], "start": event["start"], "end": event["end"], "evidence": event.get("evidence", {})})
            detail = "挑战关卡时间：" + str(event.get("evidence", {}).get("time_text", "公告未单列"))
            parent["description"] = (parent.get("description", "") + "\n\n" + detail).strip()
            excluded.add(event["id"])
    return [e for e in events if e["id"] not in excluded]


def compile_feed(cards, notices, rules, source, fetched_at, now=None, errors=()):
    now = now or dt.datetime.now(dt.timezone.utc)
    parser = NoticeParser(rules)
    cutoff = now - dt.timedelta(days=rules["collection"]["history_days"])
    active = [row for row in cards if row.get("pub_area") == source["server_name"] and type(row.get("end_at")) is int and stamp(unix(row["end_at"])) >= cutoff]
    if len(active) > rules["collection"]["max_rows"]:
        raise ValueError("活动数量超出插件配置上限")
    parsed, maintenances = [], {}
    for notice in notices:
        rows, maintenance = parser.parse(notice)
        parsed.extend(rows)
        if maintenance["start"]:
            maintenances[str(notice["id"])] = maintenance
    primary = max(maintenances, key=lambda cid: maintenances[cid]["start"]) if maintenances else None
    maintenance = maintenances.get(primary, {"start": None, "planned_end": None})
    remap, conflicts, matched = {}, [], set()
    for card in active:
        names = parser.names(card["title"])
        candidates = [e for e in parsed if e.get("category") != "update" and not e.get("parent_id") and str(e.get("evidence", {}).get("notice_id")) == str(article_id(card.get("link_url", ""), source["game_alias"])) and names & parser.names(e["title"])]
        if len(candidates) == 1:
            event = candidates[0]
            remap[event["id"]] = "calendar-" + str(card["id"])
            event["title"], event["description"] = clean(card["title"]), clean(card.get("description"))
            event["evidence"]["calendar_id"] = card["id"]
            for role, field in (("start", "begin_at"), ("end", "end_at")):
                at = unix(card[field])
                if event[role]["at"] and abs((stamp(at) - stamp(event[role]["at"])).total_seconds()) >= 60:
                    conflict = {"calendar_id": card["id"], "role": role, "calendar_at": at, "notice_at": event[role]["at"]}
                    conflicts.append(conflict)
                    event["warnings"].append("GameKee 活动卡片与公告" + ("开始" if role == "start" else "结束") + "时间不同，采用公告；原卡片时间 " + at)
            if card.get("picture"):
                event["image_url"], event["image_match"] = card["picture"], "exact_calendar_activity"
            matched.add(card["id"])
        else:
            parsed.append({"id": "calendar-" + str(card["id"]), "title": clean(card["title"]), "category": "event", "subtype": "社区日历记录", "start": {"at": unix(card["begin_at"]), "state": "estimated", "raw": "GameKee begin_at；公告尚未核对"}, "end": {"at": unix(card["end_at"]), "state": "estimated", "raw": "GameKee end_at；公告尚未核对"}, "source_url": card.get("link_url") if article_id(card.get("link_url", ""), source["game_alias"]) else source["entry_url"], "description": clean(card.get("description")), "image_url": card.get("picture"), "image_match": "exact_calendar_activity", "warnings": ["仅社区活动卡片时间，公告未核对；不触发精确截止提醒"], "evidence": {"basis": "calendar_only", "calendar_id": card["id"], "time_text": f"begin_at={card['begin_at']}; end_at={card['end_at']}"}})
    for event in parsed:
        event["id"] = remap.get(event["id"], event["id"])
        if event.get("parent_id"):
            event["parent_id"] = remap.get(event["parent_id"], event["parent_id"])
        if str(event.get("evidence", {}).get("notice_id")) != primary:
            for role in ("start", "end"):
                event[role]["reference"] = None
    # Child artwork is only shared with a proven parent, never the version cover.
    by_id = {e["id"]: e for e in parsed}
    for event in parsed:
        parent = by_id.get(event.get("parent_id"))
        if not event.get("image_url") and parent and parent.get("image_url"):
            event["image_url"], event["image_match"] = parent["image_url"], "same_activity_parent"
    parsed = curate_events(parsed, rules)
    if not parsed:
        raise ValueError("未读取到可用的国际服活动，保留上次数据")
    primary_notice = next((n for n in notices if str(n["id"]) == primary), None)
    period = {"id": "notice-" + primary if primary else "gamekee-calendar", "name": (primary_notice["title"].split("版本更新公告")[0] + "更新及近期活动") if primary_notice else "国际服近期活动（公告待核对）", "source_url": primary_notice["source_url"] if primary_notice else source["entry_url"], "maintenance": maintenance, "events": parsed, "source": {"calendar_url": source["entry_url"], "fetched_at": fetched_at}, "analysis": {"strategy": rules["strategy"], "ai_used": False, "calendar_rows": len(active), "matched_cards": len(matched), "notice_events": sum(e.get("evidence", {}).get("basis") == "notice" for e in parsed), "conflicts": conflicts}, "warnings": list(errors)}
    return {"schema_version": 1, "versions": [period]}


def article_id(url, alias):
    p = secure_url(url)
    match = re.fullmatch(r"/" + re.escape(alias) + r"/(\d+)\.html", p.path) if p and p.hostname == "www.gamekee.com" else None
    return int(match[1]) if match else None


class GameKeeProvider(JsonProvider):
    def __init__(self, directory, manifest, store, http=None):
        super().__init__(directory, manifest, store)
        self.source = copy.deepcopy(manifest["source"])
        source = self.source
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", str(source.get("game_alias", ""))) or type(source.get("wiki_id")) is not int or type(source.get("server_id")) is not int or not isinstance(source.get("server_name"), str):
            raise ValueError("GameKee Wiki/区服配置无效")
        if source.get("entry_url") != f"https://www.gamekee.com/{source['game_alias']}/huodong/{source['server_id']}":
            raise ValueError("GameKee 页面与区服 ID 不一致")
        self.config = bounded_json(local_file(directory, source.get("rules", "rules.json")))
        if not isinstance(self.config, dict) or self.config.get("schema_version") != 1 or any(not isinstance(self.config.get(k), dict) for k in ("time", "sections", "matching", "collection")):
            raise ValueError("不支持的 GameKee 规则格式")
        try:
            parser = NoticeParser(self.config)
        except (re.error, KeyError, TypeError):
            raise ValueError("GameKee 日期与章节规则无效") from None
        for pattern in (parser.date, parser.clock, parser.heading, parser.name):
            if len(pattern.pattern) > 500:
                raise ValueError("GameKee 规则表达式过长")
        for key, ceiling in (("history_days", 180), ("max_rows", 500), ("max_announcements", 5), ("max_images", 40)):
            value = self.config["collection"][key]
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("GameKee 采集范围超过上限")
        for key in ("default_notice_offset_hours", "maintenance_offset_hours"):
            value = self.config["time"].get(key)
            if type(value) is not int or not -12 <= value <= 14:
                raise ValueError("公告默认时区偏移无效")
        if self.config["time"].get("conflict_policy") != "prefer_notice" or self.config["time"].get("card_only_time_state") != "estimated":
            raise ValueError("当前引擎要求公告优先且卡片时间为预计")
        if not isinstance(self.config["sections"].get("include"), list) or not all(isinstance(v, str) for v in self.config["sections"]["include"]):
            raise ValueError("公告章节白名单无效")
        policy = self.config.get("curation", {})
        if not isinstance(policy, dict) or not isinstance(policy.get("exclude_title_contains", []), list) or not all(isinstance(v, str) and v for v in policy.get("exclude_title_contains", [])):
            raise ValueError("活动排除规则无效")
        self.http = http or GameKeeHTTP(source["game_alias"], source["wiki_id"], max(.5, float(self.config["collection"]["request_interval_seconds"])))
        self.descriptor.update(source_kind="community_calendar", plugin_kind="gamekee-json", capabilities=["sync", "rules", "maintenance", "artwork"])
        self.lock = threading.Lock()
        self.last_attempt = None

    def rules(self):
        return {"api_version": 1, "game_id": self.descriptor["id"], "source": self.source, **copy.deepcopy(self.config)}

    def normalize(self, feed):
        feed = copy.deepcopy(feed)
        for period in feed.get("versions", []) if isinstance(feed, dict) else []:
            if isinstance(period, dict) and isinstance(period.get("events"), list) and all(isinstance(e, dict) and isinstance(e.get("title"), str) and "id" in e for e in period["events"]):
                period["events"] = curate_events(period["events"], self.config)
        versions = super().normalize(feed)
        for version, raw in zip(versions, feed["versions"]):
            maintenance = raw.get("maintenance", {})
            if not isinstance(maintenance, dict) or not isinstance(raw.get("source", {}), dict) or not isinstance(raw.get("analysis", {}), dict):
                raise ValueError("公告版本依据格式无效")
            version["maintenance"] = {k: date_value(maintenance[k]) if maintenance.get(k) else None for k in ("start", "planned_end")}
            version["maintenance"].update(source_text=str(maintenance.get("source_text", ""))[:2000], source_line=maintenance.get("source_line"))
            version["analysis"] = raw.get("analysis", version["analysis"])
            version["source"].update(kind="gamekee", fetched_at=raw.get("source", {}).get("fetched_at"), calendar_url=self.source["entry_url"])
            version["warnings"] = self.warning_list(raw.get("warnings", []))
            for event, row in zip(version["events"], raw["events"]):
                evidence = row.get("evidence", {})
                if not isinstance(evidence, dict):
                    raise ValueError("公告活动依据格式无效")
                event["source"].update(kind="gamekee", title=str(evidence.get("notice_title", self.descriptor["source_label"])), time_label="公告时间" if evidence.get("basis") == "notice" else "GameKee 时间戳", time_text=str(evidence.get("time_text", ""))[:4000], block_text=str(evidence.get("time_text", ""))[:4000], line=evidence.get("line"), calendar_url=self.source["entry_url"], evidence=evidence)
                event["warnings"] = self.warning_list(row.get("warnings", []))
                event["section"] = str(evidence.get("section", "活动"))
                if row.get("time_kind", "interval") not in {"interval", "point"}:
                    raise ValueError("活动时间类型无效")
                event["time_kind"] = row.get("time_kind", "interval")
                for role in ("start", "end"):
                    endpoint = row[role]
                    reference = endpoint.get("reference")
                    if reference not in {None, "maintenance_end"}:
                        raise ValueError("不支持的公告时间关联")
                    event[role].update(reference=reference, raw=str(endpoint.get("raw", ""))[:2000], precision=endpoint.get("precision", "minute"))
                if event.get("artwork"):
                    event["artwork"].update(match=row.get("image_match", "plugin_local_asset"), source_title=event["title"])
        return versions

    @staticmethod
    def warning_list(value):
        if not isinstance(value, list) or len(value) > 100 or not all(isinstance(v, str) for v in value):
            raise ValueError("公告警告应为文本数组")
        return [v[:2000] for v in value]

    def seed(self):
        if any(v.get("game") == self.descriptor["id"] for v in self.store.versions()):
            return
        path = self.directory / "events.json"
        if path.is_file():
            try:
                versions = self.normalize(bounded_json(path))
                self.store.replace_game_versions(self.descriptor["id"], versions)
                self.store.set_meta(self.sync_key, {"running": False, "mode": "snapshot", "message": "插件真实来源快照；联网后检查更新", "last_success": None})
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.store.set_meta(self.sync_key, {"running": False, "mode": "error", "error": str(error)[:400], "message": "插件快照损坏，可尝试同步资料"})

    def start(self, force=False):
        if not self.lock.acquire(blocking=False):
            return False
        now = dt.datetime.now(dt.timezone.utc)
        if not force and self.last_attempt and (now - self.last_attempt).total_seconds() < 30:
            self.lock.release()
            return False
        self.last_attempt = now
        previous = self.store.get_meta(self.sync_key, {})
        self.store.set_meta(self.sync_key, {**previous, "running": True, "last_attempt": iso(now), "error": None})
        threading.Thread(target=self._run, daemon=True, name="gamekee-" + self.descriptor["id"]).start()
        return True

    def _run(self):
        try:
            self.collect()
        except Exception as error:
            previous = self.store.get_meta(self.sync_key, {})
            self.store.set_meta(self.sync_key, {**previous, "running": False, "stage": None, "waiting": None, "error": str(error)[:400], "message": "同步失败，保留上次成功日历"})
        finally:
            self.lock.release()

    def read_json(self, path):
        result = json.loads(self.http.read("https://www.gamekee.com" + path))
        if result.get("code") != 0 or not isinstance(result.get("data"), (list, dict)):
            raise ValueError("GameKee 公开接口结构变化")
        return result

    def notice(self, cid):
        result = self.read_json("/v1/content/detail/" + str(cid))["data"]
        if result.get("game_id") != self.source["wiki_id"] or result.get("is_del"):
            raise ValueError("公告不属于配置的 Wiki")
        path = local_file(self.directory, "sources/" + str(cid) + ".json")
        cached = bounded_json(path) if path.is_file() else None
        if cached and cached.get("id") == cid and cached.get("updated_at") == result.get("updated_at"):
            return cached
        body = result.get("content_json") or result.get("content")
        if not body:
            body = json.loads(self.http.read(result["content_cdn"])).get("content")
        notice = {"schema_version": 1, "id": cid, "title": clean(result.get("title")), "updated_at": result.get("updated_at"), "source_url": f"https://www.gamekee.com/{self.source['game_alias']}/{cid}.html", "blocks": content_blocks(body)}
        atomic_json(path, notice)
        return notice

    @network_job
    def collect(self, now=None):
        progress = SyncProgress(self.store, self.sync_key)
        with progress.active():
            return self._collect(progress, now)

    def _collect(self, progress, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        progress.stage("fetch")
        servers = self.read_json("/v1/gameServer/list")["data"]
        if not any(s.get("id") == self.source["server_id"] and s.get("game_id") == self.source["wiki_id"] and s.get("name") == self.source["server_name"] for s in servers):
            raise ValueError("GameKee 区服信息变化，未混入其他区服")
        query = {"importance": 0, "sort": -1, "keyword": "", "limit": self.config["collection"]["max_rows"], "page_no": 1, "serverId": self.source["server_id"], "status": 0}
        payload = self.read_json("/v1/activity/page-list?" + urllib.parse.urlencode(query))
        cards = payload["data"]
        if not isinstance(cards, list) or not cards or len(cards) > query["limit"]:
            raise ValueError("活动列表为空或格式变化")
        if payload.get("meta", {}).get("pagination", {}).get("page_total", 1) > 1:
            raise ValueError("活动列表超出单次采集范围，未用不完整快照覆盖")
        cutoff = now - dt.timedelta(days=self.config["collection"]["history_days"])
        eligible = [r for r in cards if r.get("pub_area") == self.source["server_name"] and type(r.get("end_at")) is int and stamp(unix(r["end_at"])) >= cutoff]
        ids = list(dict.fromkeys(cid for r in eligible if (cid := article_id(r.get("link_url", ""), self.source["game_alias"]))))[:self.config["collection"]["max_announcements"]]
        notices, errors = [], []
        for cid in ids:
            try:
                notices.append(self.notice(cid))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"公告 {cid} 读取失败（{type(error).__name__}）；仅卡片条目保留为预计")
                cached_path = local_file(self.directory, "sources/" + str(cid) + ".json")
                if cached_path.is_file():
                    cached = bounded_json(cached_path)
                    if cached.get("id") == cid:
                        notices.append(cached)
                        errors[-1] = f"公告 {cid} 正文暂不可用，继续使用上次核对的正文快照"
        progress.stage("parse")
        feed = compile_feed(cards, notices, self.config, self.source, iso(now), now, errors)
        # Validate the whole snapshot before replacing any successful records. Dates are
        # published with cached images first; missing images are downloaded afterwards.
        old = [v for v in self.store.versions() if v.get("game") == self.descriptor["id"]]
        draft = copy.deepcopy(feed)
        with deferring_images():
            self.attach_images(draft)
        versions = self.normalize(draft)
        atomic_json(self.directory / "events.json", draft)
        self.store.replace_game_versions(self.descriptor["id"], versions)
        progress.calendar_saved(old, versions)
        progress.stage("images")
        self.attach_images(feed)
        versions = self.normalize(feed)
        atomic_json(self.directory / "events.json", feed)
        self.store.replace_game_versions(self.descriptor["id"], versions)
        progress.images_done(versions, sum(v["analysis"]["artwork"]["download_errors"] for v in feed["versions"]))
        self.store.set_meta(self.sync_key, progress.finish({"running": False, "last_attempt": iso(now), "last_success": iso(now), "error": None, "mode": "gamekee", "message": f"GameKee 外服资料已同步；{len(errors)} 篇公告待核对", "warnings": errors}))
        return feed

    def attach_images(self, feed):
        loaded, errors = {}, []
        for version in feed["versions"]:
            for event in version["events"]:
                url = event.get("image_url")
                if not url or not self.http.allowed(url, image=True):
                    continue
                url = "https:" + url if url.startswith("//") else url
                key = hashlib.sha256(url.encode()).hexdigest()[:32]
                existing = next(iter(sorted((self.directory / "assets").glob(key + ".*"))), None)
                if existing:
                    loaded[url] = "assets/" + existing.name
                if url not in loaded and len(loaded) < self.config["collection"]["max_images"]:
                    try:
                        data = self.http.read(url, image=True)
                        if len(data) > min(5_000_000, self.config["collection"].get("max_image_bytes", 5_000_000)):
                            raise ValueError("活动图片超过配置大小限制")
                        suffix = image_kind(data)
                        path = local_file(self.directory, "assets/" + key + "." + suffix)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_suffix(path.suffix + ".tmp")
                        temporary.write_bytes(data)
                        temporary.replace(path)
                        loaded[url] = "assets/" + path.name
                    except (OSError, ValueError) as error:
                        errors.append(str(error)[:100])
                if url in loaded:
                    event["artwork"] = {"file": loaded[url], "source_url": event["source_url"]}
            version["analysis"]["artwork"] = {"matched": sum(bool(e.get("artwork")) for e in version["events"]), "download_errors": len(errors)}
