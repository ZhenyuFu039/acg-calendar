"""Versioned provider boundary. Plugins supply data, the host owns personal state.

API 1 supports trusted built-in collectors and local declarative JSON packages.
No third-party Python, JavaScript, install scripts or remote code is executed.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .parser import ROOT, iso, load_rules, stamp
from .plugin_media import import_image

API_VERSION = 1
ID = re.compile(r"[a-z][a-z0-9-]{1,63}\Z")
CATEGORIES = {"event", "gacha", "exchange", "update", "maintenance"}


class Provider(Protocol):
    descriptor: dict
    sync_key: str
    collector: object

    def rules(self) -> dict: ...
    def prepare(self, version: dict) -> None: ...


class BuiltinProvider:
    def __init__(self, descriptor, collector, sync_key):
        self.descriptor = copy.deepcopy(descriptor)
        self.collector, self.sync_key = collector, sync_key
        self.probes = {}
        self.descriptor.update(api_version=API_VERSION, plugin_kind="builtin", capabilities=["sync", "rules"])

    def rules(self):
        return load_rules(ROOT / "rules" / (self.descriptor["id"] + ".json"))

    def prepare(self, version):
        pass


class EndfieldProvider(BuiltinProvider):
    def __init__(self, descriptor, collector, data_dir):
        super().__init__(descriptor, collector, "sync")
        self.data_dir = data_dir
        from .skland import SklandProbe
        self.probes = {"skland": ("source:skland", SklandProbe(collector.store))}
        self.descriptor["currency"] = {"id": "oroberyl", "name": "嵌晶玉", "icon": "/assets/oroberyl.png"}
        self.descriptor["capabilities"] += ["maintenance", "rewards", "briefing"]

    def prepare(self, version):
        from .rewards import summarize_rewards
        # Preserve all pre-plugin event IDs and retained text/reward evidence.
        if "reward_mentions" not in version:
            source = self.data_dir / "sources" / (version["id"] + ".txt")
            if not source.is_file() and version["source"].get("cid") == "6094":
                source = ROOT / "samples/endfield-cn-6094.txt"
            if source.is_file():
                try:
                    parsed = self.collector.parser.parse(source.read_text(encoding="utf-8"), cid=version["source"]["cid"], source_url=version["source"]["url"], title=version["source"]["title"], published_at=version["source"].get("published_at"))
                except (OSError, ValueError):
                    parsed = None
                if parsed:
                    by_id = {e["id"]: e for e in parsed["events"]}
                    version["reward_mentions"] = parsed["reward_mentions"]
                    for event in version["events"]:
                        event["source"]["reward_lines"] = by_id.get(event["id"], {}).get("source", {}).get("reward_lines", [])
        summarize_rewards(version, self.rules().get("rewards"))


def bounded_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("插件 JSON 超过 2 MB")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def date_value(value):
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("时间必须为包含时区的 ISO 日期")
    return iso(stamp(value))


def text_value(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(name + "格式或长度无效")
    return value.strip()


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("来源网址无效")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("来源必须为 HTTPS 网址")
    return value


class JsonProvider:
    """A local feed adapter, also usable by an independently run scraper.

    Writes a complete game snapshot atomically. Invalid refreshes retain the
    previous snapshot; IDs are namespaced and hashed from publisher IDs.
    """
    def __init__(self, directory, manifest, store):
        if not isinstance(manifest, dict) or type(manifest.get("api_version")) is not int or manifest["api_version"] != API_VERSION:
            raise ValueError("不兼容的插件 api_version（主程序支持 1）")
        if manifest.get("kind") != "local-json":
            raise ValueError("目前外部插件仅支持 local-json 数据包")
        game = manifest.get("game", {})
        if not isinstance(game, dict) or not isinstance(game.get("id"), str) or not ID.fullmatch(game["id"]):
            raise ValueError("游戏 ID 需为 2–64 位小写字母、数字或连字符")
        for key in ("name", "short_name", "server", "timezone", "source_label"):
            text_value(game.get(key), key)
        try:
            ZoneInfo(game["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("游戏时区无效") from None
        color = game.get("color", "#6D8658")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("游戏颜色需为 #RRGGBB")
        # Only declared metadata and validated static local images cross the boundary.
        self.descriptor = {k: game[k] for k in ("id", "name", "short_name", "server", "timezone", "source_label")}
        self.descriptor.update(color=color, monogram=game.get("short_name", "?")[:2], icon=None, source_kind="local_json", api_version=API_VERSION, plugin_kind="local-json", capabilities=["sync", "rules"])
        self.directory, self.store = directory, store
        self.sync_key = "sync:" + game["id"]
        self.collector = self
        self.probes = {}
        if game.get("icon"):
            self.descriptor["icon"] = import_image(directory, game["icon"], store.path.parent)["path"]

    def rules(self):
        return {"api_version": API_VERSION, "strategy": "local-json", "game_id": self.descriptor["id"], "file": "events.json", "ai_used": False}

    def prepare(self, version):
        pass

    def normalize(self, data):
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("活动数据需要 schema_version: 1")
        periods = data.get("versions")
        if not isinstance(periods, list) or not 1 <= len(periods) <= 100:
            raise ValueError("versions 应为 1–100 个版本")
        game, seen_versions, seen_events, result = self.descriptor["id"], set(), set(), []
        for period in periods:
            if not isinstance(period, dict):
                raise ValueError("版本格式无效")
            publisher_id = text_value(period.get("id"), "版本 ID")
            version_id = game + "-" + hashlib.sha256(publisher_id.encode()).hexdigest()[:20]
            if version_id in seen_versions:
                raise ValueError("版本 ID 重复")
            seen_versions.add(version_id)
            rows = period.get("events")
            if not isinstance(rows, list) or len(rows) > 2000:
                raise ValueError("每版本 events 必须为数组，最多 2000 项")
            events = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("活动格式无效")
                local_id = text_value(row.get("id"), "活动 ID")
                # IDs are game-global: moving an event to another period retains user state.
                event_id = game + "-" + hashlib.sha256(local_id.encode()).hexdigest()[:20]
                if event_id in seen_events:
                    raise ValueError("活动 ID 重复")
                seen_events.add(event_id)
                category = row.get("category", "event")
                if category not in CATEGORIES:
                    raise ValueError("未知活动类型")
                endpoints = {}
                for role in ("start", "end"):
                    value = row.get(role)
                    if not isinstance(value, dict) or value.get("state") not in {"exact", "estimated", "unknown"}:
                        raise ValueError("起止时间必须为 at/state 对象")
                    at = date_value(value["at"]) if value.get("at") is not None else None
                    if (at is None) != (value["state"] == "unknown"):
                        raise ValueError("待定时间必须为 at:null、state:unknown")
                    endpoints[role] = {"at": at, "state": value["state"], "reference": None, "raw": "", "precision": "minute"}
                if endpoints["start"]["at"] and endpoints["end"]["at"] and stamp(endpoints["end"]["at"]) <= stamp(endpoints["start"]["at"]):
                    raise ValueError("活动结束需晚于开始")
                parent = row.get("parent_id")
                parent_id = game + "-" + hashlib.sha256(text_value(parent, "父活动 ID").encode()).hexdigest()[:20] if parent else None
                events.append({"id": event_id, "publisher_id": local_id, "version_id": version_id, "title": text_value(row.get("title"), "活动标题"), "category": category, "subtype": text_value(row.get("subtype", "活动"), "子类型"), "section": "活动", "time_kind": "interval", **endpoints, "parent_id": parent_id, "parent_title": None, "phases": [], "description": str(row.get("description", ""))[:5000], "requirements": [], "warnings": [], "source": {"url": public_url(row.get("source_url")), "kind": "local_json", "title": self.descriptor["source_label"], "time_text": "", "block_text": ""}})
                if row.get("artwork") is not None:
                    picture = row["artwork"]
                    if not isinstance(picture, dict) or not isinstance(picture.get("file"), str):
                        raise ValueError("插件头图需指定本地 file")
                    events[-1]["artwork"] = {**import_image(self.directory, picture["file"], self.store.path.parent), "source_url": public_url(picture.get("source_url", row["source_url"])), "match": "plugin_local_asset"}
            result.append({"schema_version": 1, "id": version_id, "game": game, "name": text_value(period.get("name"), "版本名"), "timezone": self.descriptor["timezone"], "source": {"url": public_url(period.get("source_url")), "title": self.descriptor["source_label"], "origin": "local-json", "kind": "local_json"}, "maintenance": {"start": None, "planned_end": None}, "warnings": [], "analysis": {"strategy": "local-json", "ai_used": False, "event_count": len(events)}, "events": events})
        for version in result:
            parents = {e["id"]: e.get("parent_id") for e in version["events"]}
            for event in version["events"]:
                visited, current = {event["id"]}, event.get("parent_id")
                while current:
                    if current not in parents or current in visited:
                        raise ValueError("父活动必须存在于同一版本且不能循环引用")
                    visited.add(current)
                    current = parents[current]
        return result

    def seed(self):
        self.start()

    def start(self, force=False):
        # Local reads are bounded and synchronous; no untrusted network requests.
        now = iso(dt.datetime.now(dt.timezone.utc))
        previous = self.store.get_meta(self.sync_key, {})
        try:
            path = (self.directory / "events.json").resolve()
            if path.parent != self.directory.resolve():
                raise ValueError("数据文件不得指向插件目录外")
            versions = self.normalize(bounded_json(path))
            self.store.replace_game_versions(self.descriptor["id"], versions)
            self.store.set_meta(self.sync_key, {"running": False, "last_attempt": now, "last_success": now, "error": None, "mode": "local-json", "message": "本地插件数据已加载"})
            return True
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.store.set_meta(self.sync_key, {**previous, "running": False, "last_attempt": now, "error": str(error)[:400], "message": "插件读取失败，保留上次成功数据"})
            return False


class ProviderRegistry:
    def __init__(self):
        self.providers, self.errors = {}, []

    @property
    def ids(self):
        return set(self.providers)

    @property
    def games(self):
        return [copy.deepcopy(p.descriptor) for p in self.providers.values()]

    def add(self, provider):
        key = provider.descriptor["id"]
        if key in self.providers:
            raise ValueError("游戏 ID 已注册：" + key)
        self.providers[key] = provider

    def rules(self, game):
        return {"games": {g: p.rules() for g, p in self.providers.items()}} if game == "all" else self.providers[game].rules()

    def name(self, game):
        return self.providers[game].descriptor["name"] if game in self.providers else game

    def discover(self, root, store):
        if not root.exists():
            return
        for path in sorted(root.glob("*/manifest.json")):
            try:
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("插件文件不得指向插件目录外")
                manifest = bounded_json(path)
                if isinstance(manifest, dict) and manifest.get("enabled") is False:
                    continue
                source = manifest.get("source", {}) if isinstance(manifest, dict) else {}
                if source and (not isinstance(source, dict) or source.get("type") not in {"gamekee", "azurlane-wiki", "zzz-official"}):
                    raise ValueError("不支持的声明式采集来源")
                if source and source["type"] == "zzz-official":
                    from .zzz import ZZZProvider
                    self.add(ZZZProvider(path.parent, manifest, store))
                elif source and source["type"] == "azurlane-wiki":
                    from .azurlane import AzurLaneProvider
                    self.add(AzurLaneProvider(path.parent, manifest, store))
                elif source:
                    from .gamekee import GameKeeProvider
                    self.add(GameKeeProvider(path.parent, manifest, store))
                else:
                    self.add(JsonProvider(path.parent, manifest, store))
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.errors.append({"plugin": path.parent.name, "error": str(error)[:400]})


def create_registry(store, data_dir):
    # Adding a trusted code adapter only touches this composition root.
    from .collector import Collector
    from .arknights import ArknightsCollector
    from .games import GAMES
    registry = ProviderRegistry()
    registry.add(EndfieldProvider(GAMES[0], Collector(store, data_dir), data_dir))
    registry.add(BuiltinProvider(GAMES[1], ArknightsCollector(store, data_dir), "sync:arknights-cn"))
    registry.discover(data_dir / "plugins", store)
    return registry
