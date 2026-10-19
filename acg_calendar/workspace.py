"""Host-owned view models, independent of announcement collection and reminders."""
from __future__ import annotations

import copy
import datetime as dt
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .parser import iso, stamp
from .providers import date_value, text_value

MODULES = ("calendar", "sources", "rewards", "deadlines", "personal-plans")
DEFAULT_LAYOUT = {"schema_version": 1, "modules": [{"id": m, "visible": True, "collapsed": False} for m in MODULES]}
DEFAULT_THEME = {"schema_version": 1, "preset": "light", "accent": "#73894D", "card_opacity": 1.0}
DEFAULT_VIEW = {"selected_games": [], "scope": "single", "sort": "start", "starred_first": True, "hide_future": False, "group_by": "category", "collapsed_groups": [], "focus": {"date": None, "event_id": None}}


class WorkspaceService:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def get(self):
        saved = self.store.get_meta("workspace", {})
        if saved.get("schema_version", 1) != 1:
            raise ValueError("工作区配置版本过新，无法读取")
        view = {**copy.deepcopy(DEFAULT_VIEW), **saved.get("view", {})}
        if "selected_games" not in saved.get("view", {}):
            legacy = self.store.settings()["active_game"]
            view["selected_games"] = list(self.registry.providers) if legacy == "all" else [legacy]
            view["scope"] = "all" if legacy == "all" else "single"
        if view["scope"] == "all":
            view["selected_games"] = list(self.registry.providers)
        # Missing plugins never erase saved selection; it can recover on reinstall.
        return {"schema_version": 1, "view": view, "layout": copy.deepcopy(saved.get("layout", DEFAULT_LAYOUT)), "theme": copy.deepcopy(saved.get("theme", DEFAULT_THEME))}

    def update(self, patch):
        with self.store.lock:
            return self._update(patch)

    def _update(self, patch):
        if set(patch) - {"view", "layout", "theme", "schema_version"} or ("schema_version" in patch and (type(patch["schema_version"]) is not int or patch["schema_version"] != 1)):
            raise ValueError("不支持的工作区配置版本或字段")
        value = self.get()
        if "view" in patch:
            p = patch["view"]
            if not isinstance(p, dict) or set(p) - set(DEFAULT_VIEW):
                raise ValueError("不支持的视图字段")
            v = {**value["view"], **p}
            if v["scope"] not in {"single", "all", "selected"}:
                raise ValueError("选择范围无效")
            games = v["selected_games"]
            if not isinstance(games, list) or len(games) > 100 or any(not isinstance(g, str) or ("selected_games" in p and g not in self.registry.ids) for g in games) or len(set(games)) != len(games):
                raise ValueError("请选择已安装的游戏，且不得重复")
            if v["sort"] not in {"start", "remaining", "title"} or v["group_by"] not in {"none", "category", "game"}:
                raise ValueError("排序或分组规则无效")
            if any(type(v[k]) is not bool for k in ("starred_first", "hide_future")):
                raise ValueError("视图开关必须为布尔值")
            if not isinstance(v["collapsed_groups"], list) or len(v["collapsed_groups"]) > 100 or any(not isinstance(g, str) or len(g) > 100 for g in v["collapsed_groups"]):
                raise ValueError("折叠组格式无效")
            focus = v["focus"]
            if not isinstance(focus, dict) or set(focus) != {"date", "event_id"}:
                raise ValueError("焦点格式无效")
            if focus["date"] is not None:
                try:
                    dt.date.fromisoformat(focus["date"])
                except (TypeError, ValueError):
                    raise ValueError("焦点日期无效") from None
            if focus["event_id"] is not None and (not isinstance(focus["event_id"], str) or len(focus["event_id"]) > 200):
                raise ValueError("焦点活动 ID 无效")
            value["view"] = v
        if "layout" in patch:
            layout = patch["layout"]
            if not isinstance(layout, dict) or set(layout) != {"schema_version", "modules"} or type(layout["schema_version"]) is not int or layout["schema_version"] != 1 or not isinstance(layout["modules"], list):
                raise ValueError("布局格式无效")
            rows = layout["modules"]
            if len(rows) != len(MODULES) or any(not isinstance(r, dict) or set(r) != {"id", "visible", "collapsed"} or r["id"] not in MODULES or type(r["visible"]) is not bool or type(r["collapsed"]) is not bool for r in rows) or len({r["id"] for r in rows}) != len(MODULES):
                raise ValueError("布局需包含每个已知模块且只能出现一次")
            if not next(r["visible"] for r in rows if r["id"] == "calendar"):
                raise ValueError("日历主模块必须保留")
            value["layout"] = layout
        if "theme" in patch:
            p = patch["theme"]
            if not isinstance(p, dict) or set(p) - set(DEFAULT_THEME):
                raise ValueError("主题字段无效")
            theme = {**value["theme"], **p}
            if type(theme["schema_version"]) is not int or theme["schema_version"] != 1 or theme["preset"] not in {"light", "dark"} or not isinstance(theme["accent"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", theme["accent"]) or type(theme["card_opacity"]) not in {int, float} or not .35 <= theme["card_opacity"] <= 1:
                raise ValueError("主题值无效")
            value["theme"] = theme
        self.store.set_meta("workspace", value)
        return value

    def plan(self, payload, key=None):
        allowed = {"title", "start", "end", "timezone", "game_id", "event_id", "notes"}
        if set(payload) - allowed:
            raise ValueError("不支持的个人计划字段")
        existing = next((p for p in self.store.plans() if p["id"] == key), None)
        if key and not existing:
            raise LookupError("个人计划不存在")
        plan = {**(existing or {"schema_version": 1, "id": "plan-" + uuid.uuid4().hex, "game_id": None, "event_id": None, "notes": "", "timezone": "UTC"}), **payload}
        plan["title"] = text_value(plan.get("title"), "计划标题")
        plan["start"], plan["end"] = date_value(plan.get("start")), date_value(plan.get("end"))
        if stamp(plan["end"]) <= stamp(plan["start"]):
            raise ValueError("计划结束时间需晚于开始")
        try:
            if not isinstance(plan["timezone"], str):
                raise ValueError()
            ZoneInfo(plan["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("计划时区无效") from None
        if plan["game_id"] is not None and (not isinstance(plan["game_id"], str) or plan["game_id"] not in self.registry.ids):
            raise ValueError("计划关联游戏不存在")
        if not isinstance(plan["notes"], str) or len(plan["notes"]) > 2000:
            raise ValueError("计划备注最多 2000 字")
        if plan["event_id"] is not None:
            event = next((e for v in self.store.versions() for e in v["events"] if e["id"] == plan["event_id"]), None)
            if not event:
                raise ValueError("关联活动不存在")
            game = next(v["game"] for v in self.store.versions() if any(e["id"] == plan["event_id"] for e in v["events"]))
            if plan["game_id"] and plan["game_id"] != game:
                raise ValueError("计划的游戏和活动不匹配")
            plan["game_id"] = game
        plan["updated_at"] = iso(dt.datetime.now(dt.timezone.utc))
        self.store.save_plan(plan)
        return plan


def sort_events(events, view, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    def key(event):
        start, end = stamp(event["start"].get("at")), stamp(event["end"].get("at"))
        if view["sort"] == "remaining":
            # Running -> future -> unknown deadline -> ended; uncertain ends do not
            # masquerade as confirmed deadlines. Stars affect row order only.
            trusted = event["end"]["state"] in {"exact", "confirmed", "manual"}
            part = (1, start) if start and start > now else (3, end) if end and end <= now else (0, end) if end and trusted else (2, far)
        elif view["sort"] == "title":
            part = (0, event["title"].casefold())
        else:
            part = (0, start or far)
        return (0 if view["starred_first"] and event.get("starred") else 1, *part, event["id"])
    return sorted(events, key=key)


def timeline(versions, workspace, plans=(), now=None, timezone="UTC", hide_completed=False):
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("时间线时区无效") from None
    view = workspace["view"]
    selected = set(view["selected_games"])
    events = []
    for version in versions:
        if version["game"] not in selected:
            continue
        for event in version["events"]:
            if event.get("ignored") or (hide_completed and event.get("completed")):
                continue
            start = stamp(event["start"].get("at"))
            # Unknown starts are hidden; elapsed estimates remain visibly uncertain.
            if view["hide_future"] and (not start or start > now):
                continue
            events.append({**event, "game": version["game"], "version_name": version["name"]})
    events = sort_events(events, view, now)
    groups, density, seen = {}, {}, set()
    for event in events:
        group = event["game"] if view["group_by"] == "game" else event["category"] if view["group_by"] == "category" else "all"
        groups.setdefault(group, []).append(event["id"])
        end = event["end"]
        if event["id"] not in seen and not event.get("completed") and not event.get("invalid_interval") and event["time_kind"] != "point" and end.get("at") and end["state"] in {"exact", "confirmed", "manual"}:
            seen.add(event["id"])
            day = stamp(end["at"]).astimezone(tz).date().isoformat()
            bucket = density.setdefault(day, {"activities": 0, "exchanges": 0})
            bucket["exchanges" if event["category"] == "exchange" else "activities"] += 1
    visible_plans = [p for p in plans if p.get("game_id") is None or p["game_id"] in selected]
    return {"schema_version": 1, "events": events, "groups": [{"id": key, "event_ids": ids, "collapsed": key in view["collapsed_groups"]} for key, ids in groups.items()], "deadline_density": density, "plans": visible_plans, "timezone": timezone}
