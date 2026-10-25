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
# max_width is retained for schema-1 imports; the web shell now fills the viewport.
DEFAULT_LAYOUT = {"schema_version": 1, "modules": [{"id": m, "visible": True, "collapsed": False} for m in ("sources", "rewards", "deadlines", "calendar", "personal-plans")], "sidebar": {"visible": True, "position": "left", "width": 208}, "max_width": 1920, "desktop_calendar": "left"}
DEFAULT_THEME = {"schema_version": 1, "preset": "light", "accent": "#607D37", "card_opacity": 1.0, "background": "#F5F7F2", "surface": "#FFFFFF", "text": "#27332D", "muted": "#626E63", "border": "#D5DCCF", "font_size": 14, "radius": 8, "spacing": 14}
THEMES = {"light": DEFAULT_THEME, "dark": {**DEFAULT_THEME, "preset": "dark", "accent": "#B8D77A", "background": "#1B221A", "surface": "#252D23", "text": "#E3EBDD", "muted": "#B2BDAA", "border": "#4A5843"}}
# Representative colors come from the bundled APP icons, with contrast-adjusted
# backgrounds/text. Host presets do not change game metadata or plugin manifests.
THEME_NAMES = {'light': '标准 · 游时', 'dark': '深色 · 静夜', 'arknights': '明日方舟 · 青蓝', 'endfield': '终末地 · 晶黄', 'nikke': 'NIKKE · 绯红', 'azurlane': '碧蓝航线 · 海蓝', 'zzz': '绝区零 · 亮橙'}
for _key, _tokens in {
    'arknights': dict(accent='#08788B', background='#EFF7F9', surface='#FFFFFF', text='#20343C', muted='#526975', border='#BED5DD'),
    'endfield': dict(accent='#D0C427', background='#20221B', surface='#2A2D22', text='#F1ECE4', muted='#C1BFA9', border='#606343'),
    'nikke': dict(accent='#A43B33', background='#FCF2EF', surface='#FFFBF9', text='#433037', muted='#805B60', border='#DFC3BF'),
    'azurlane': dict(accent='#266491', background='#EFF5FC', surface='#FCFDFF', text='#283346', muted='#5A6C85', border='#C1D2E4'),
    'zzz': dict(accent='#F38319', background='#20231E', surface='#2B2F27', text='#EEF3F4', muted='#C2C6BA', border='#58614E'),
}.items():
    THEMES[_key] = {**DEFAULT_THEME, 'preset': _key, **_tokens}
LEGACY_THEMES = copy.deepcopy(THEMES)
THEME_COLORS = ('accent', 'accent_text', 'secondary', 'link', 'background', 'surface', 'panel_alt', 'text', 'muted', 'border', 'nav_background', 'nav_text', 'bar_background', 'bar_text', 'event_color', 'gacha_color', 'exchange_color', 'update_color', 'maintenance_color')
DEFAULT_THEME.update(accent_text='#FFFFFF', secondary='#8BA65B', link='#506A30', panel_alt='#EDF1E8', nav_background='#FFFFFF', nav_text='#27332D', bar_background='#E6ECDF', bar_text='#27332D', event_color='#799663', gacha_color='#8C87AC', exchange_color='#B28D54', update_color='#6094A1', maintenance_color='#93978F', control_radius=4, border_width=1, heading_weight=800, texture='none', texture_opacity=3)
for _key, _theme in THEMES.items():
    THEMES[_key] = {**DEFAULT_THEME, **_theme}
THEMES['dark'].update(accent_text='#182010', secondary='#7DBDA7', link='#B8D77A', panel_alt='#303A2C', nav_background='#151C14', nav_text='#E3EBDD', bar_background='#36412F', bar_text='#E3EBDD')
THEMES['endfield'].update(accent='#FFFA00', accent_text='#111111', secondary='#00A6AE', link='#595700', background='#FAFAF8', surface='#FFFFFF', panel_alt='#F0F0EC', text='#111111', muted='#62625D', border='#D3D3CC', nav_background='#FFFFFF', nav_text='#171717', bar_background='#ECEDE7', bar_text='#22231F', radius=0, control_radius=0, spacing=16, heading_weight=900, texture='grid', texture_opacity=3)
THEMES['arknights'].update(accent='#00C8E8', accent_text='#091013', secondary='#A8DDE5', link='#38D6ED', background='#101214', surface='#1B1E22', panel_alt='#252A2F', text='#F0F4F6', muted='#ABB6BF', border='#424B52', nav_background='#0A0C0E', nav_text='#F0F4F6', bar_background='#30383D', bar_text='#F0F4F6', radius=0, control_radius=2, spacing=14, heading_weight=800, texture='grid', texture_opacity=5)
THEMES['zzz'].update(accent='#D8FA00', accent_text='#11130A', secondary='#F38319', link='#4A5800', background='#E8EAE4', surface='#FAFBF6', panel_alt='#EEF0E7', text='#171A12', muted='#59604D', border='#BFC6B1', nav_background='#10120D', nav_text='#F5F9E9', bar_background='#E1E6D5', bar_text='#242A18', radius=22, control_radius=32, spacing=18, heading_weight=900, texture='diagonal', texture_opacity=4)
THEMES['nikke'].update(accent_text='#FFFFFF', secondary='#D2A49D', link='#A43B33', panel_alt='#F6E9E6', nav_background='#433037', nav_text='#FFF6F0', bar_background='#F0DED9', bar_text='#433037', radius=6, control_radius=6)
THEMES['azurlane'].update(accent_text='#FFFFFF', secondary='#CB9CAB', link='#266491', panel_alt='#E5EEF9', nav_background='#193D5B', nav_text='#F2F8FF', bar_background='#DEE8F3', bar_text='#283346', radius=10, control_radius=10)
THEME_NAMES.update(endfield='终末地 · 工业白黄', arknights='明日方舟 · 战术黑青', zzz='绝区零 · 街头荧光')
# Neutral host default shares the industrial palette, with quieter geometry.
DEFAULT_THEME.update({k:v for k,v in THEMES['endfield'].items() if k != 'preset'})
DEFAULT_THEME.update(radius=4, control_radius=2, spacing=14, heading_weight=800, texture='none', texture_opacity=0)
THEMES['light'] = copy.deepcopy(DEFAULT_THEME)
# Native widgets have smaller controls than the web workspace. Old schema-1
# themes receive the preset's native geometry without changing their web values.
for _theme in THEMES.values():
    _theme.update(desktop_radius=min(_theme['radius'], 10), desktop_control_radius=min(_theme['control_radius'], 8))
THEMES['zzz'].update(desktop_radius=8, desktop_control_radius=6)
DEFAULT_THEME.update(desktop_radius=4, desktop_control_radius=2)


def readable_ink(color):
    rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    return '#111111' if contrast(rgb, (17, 17, 17)) >= contrast(rgb, (255, 255, 255)) else '#FFFFFF'


def complete_theme(saved):
    """Extend schema 1; upgrade unchanged factory skins, preserve custom colors."""
    base = THEMES.get(saved.get('preset'), DEFAULT_THEME)
    old = LEGACY_THEMES.get(saved.get('preset'), {})
    same = lambda a,b: a.upper() == b.upper() if isinstance(a,str) and isinstance(b,str) and a.startswith('#') and b.startswith('#') else a == b
    if 'accent_text' not in saved and saved and all(k in old and same(old[k],v) for k, v in saved.items()):
        return copy.deepcopy(base)
    result = {**base, **saved}
    if 'accent_text' not in saved:
        result['accent_text'] = readable_ink(result['accent'])
        if saved:
            result.update(nav_background=result['surface'], nav_text=result['text'], panel_alt=result['surface'], bar_background=result['surface'], bar_text=result['text'], link=result['accent'])
    for key in THEME_COLORS:
        if isinstance(result.get(key),str) and re.fullmatch(r'#[0-9a-fA-F]{6}',result[key]): result[key] = result[key].upper()
    return result
DEFAULT_VIEW = {"selected_games": [], "scope": "single", "sort": "start", "starred_first": True, "hide_future": False, "group_by": "category", "collapsed_groups": [], "collapsed_parents": [], "focus_timezone": "UTC", "focus": {"date": None, "event_id": None}}


def contrast(first, second):
    def light(rgb):
        channels = [c / 255 for c in rgb]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((light(first), light(second)))
    return (b + .05) / (a + .05)


def validate_theme(theme):
    if type(theme['schema_version']) is not int or theme['schema_version'] != 1 or theme['preset'] not in THEMES or type(theme['card_opacity']) not in {int, float} or not .35 <= theme['card_opacity'] <= 1:
        raise ValueError('主题值无效')
    colors = THEME_COLORS
    if any(not isinstance(theme[k], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', theme[k]) for k in colors):
        raise ValueError('主题颜色必须为 #RRGGBB，不接受 CSS 或脚本')
    for key in colors: theme[key] = theme[key].upper()
    for key, (low, high) in {'font_size': (12, 18), 'radius': (0, 28), 'control_radius': (0, 40), 'desktop_radius': (0, 16), 'desktop_control_radius': (0, 12), 'spacing': (8, 24), 'border_width': (0, 2), 'heading_weight': (600, 900), 'texture_opacity': (0, 12)}.items():
        if type(theme[key]) is not int or not low <= theme[key] <= high:
            raise ValueError('字号、圆角或间距超出范围')
    rgb = lambda key: tuple(int(theme[key][i:i+2], 16) for i in (1, 3, 5))
    if not isinstance(theme['texture'], str) or theme['texture'] not in {'none', 'grid', 'diagonal', 'dots'}:
        raise ValueError('纹理需为无、网格、斜线或点阵')
    page, paper = rgb('background'), rgb('surface')
    blended = tuple(a * theme['card_opacity'] + b * (1 - theme['card_opacity']) for a, b in zip(paper, page))
    if min(contrast(rgb('text'), bg) for bg in (page, paper, blended, rgb('panel_alt'))) < 4.5 or min(contrast(rgb('muted'), bg) for bg in (page, paper, blended, rgb('panel_alt'))) < 3 or min(contrast(rgb('link'), bg) for bg in (page, paper)) < 3 or contrast(rgb('accent'), rgb('accent_text')) < 4.5 or contrast(rgb('nav_background'), rgb('nav_text')) < 4.5 or contrast(rgb('bar_background'), rgb('bar_text')) < 4.5:
        raise ValueError('文字或主色对比度不足，请调整文字、背景或透明度，保证日期与状态清晰')


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
        theme = saved.get("theme", {})
        layout = {**copy.deepcopy(DEFAULT_LAYOUT), **copy.deepcopy(saved.get("layout", {}))}
        return {"schema_version": 1, "view": view, "layout": layout, "theme": complete_theme(theme)}

    def defaults(self):
        return copy.deepcopy({"layout": DEFAULT_LAYOUT, "theme": DEFAULT_THEME, "themes": THEMES, "theme_names": THEME_NAMES})

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
            if 'focus' in p:
                if not isinstance(p['focus'],dict) or set(p['focus'])-{'date','event_id'}:
                    raise ValueError('焦点格式无效')
                v['focus'] = {**value['view']['focus'], **p['focus']}
            elif {'scope','selected_games'} & set(p):
                v['focus'] = {**value['view']['focus'], 'event_id':None}
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
            if not isinstance(v['collapsed_parents'], list) or len(v['collapsed_parents']) > 1000 or any(not isinstance(g,str) or len(g)>200 for g in v['collapsed_parents']):
                raise ValueError('父活动折叠列表无效')
            try:
                ZoneInfo(v['focus_timezone'])
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                raise ValueError('日期聚焦时区无效') from None
            focus = v["focus"]
            if not isinstance(focus, dict) or set(focus) != {"date", "event_id"}:
                raise ValueError("焦点格式无效")
            if focus["date"] is not None:
                try:
                    if not isinstance(focus['date'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',focus['date']):
                        raise ValueError()
                    dt.date.fromisoformat(focus["date"])
                except (TypeError, ValueError):
                    raise ValueError("焦点日期无效") from None
            if focus["event_id"] is not None and (not isinstance(focus["event_id"], str) or len(focus["event_id"]) > 200):
                raise ValueError("焦点活动 ID 无效")
            value["view"] = v
        if "layout" in patch:
            layout = patch["layout"]
            if not isinstance(layout, dict) or set(layout) - set(DEFAULT_LAYOUT) or not {"schema_version", "modules"} <= set(layout) or type(layout["schema_version"]) is not int or layout["schema_version"] != 1 or not isinstance(layout["modules"], list):
                raise ValueError("布局格式无效")
            rows = layout["modules"]
            if len(rows) != len(MODULES) or any(not isinstance(r, dict) or set(r) != {"id", "visible", "collapsed"} or r["id"] not in MODULES or type(r["visible"]) is not bool or type(r["collapsed"]) is not bool for r in rows) or len({r["id"] for r in rows}) != len(MODULES):
                raise ValueError("布局需包含每个已知模块且只能出现一次")
            if not next(r["visible"] for r in rows if r["id"] == "calendar"):
                raise ValueError("日历主模块必须保留")
            layout = {**copy.deepcopy(DEFAULT_LAYOUT), **layout}
            sidebar = layout['sidebar']
            if not isinstance(sidebar, dict) or set(sidebar) != {'visible', 'position', 'width'} or type(sidebar['visible']) is not bool or sidebar['position'] not in {'left', 'right'} or type(sidebar['width']) is not int or not 180 <= sidebar['width'] <= 360:
                raise ValueError('侧栏需要有效的位置、显隐和 180–360 像素宽度')
            if type(layout['max_width']) is not int or not 800 <= layout['max_width'] <= 2400 or layout['desktop_calendar'] not in {'left', 'top'}:
                raise ValueError('页面宽度应为 800–2400，桌面月历位置为 left / top')
            value["layout"] = layout
        if "theme" in patch:
            p = patch["theme"]
            if not isinstance(p, dict) or set(p) - set(DEFAULT_THEME):
                raise ValueError("主题字段无效")
            base = THEMES.get(p.get('preset'), value['theme']) if p.get('preset') != value['theme']['preset'] else value['theme']
            theme = {**base, **p}
            if 'accent_text' not in p and {'preset', 'background', 'surface', 'text'} <= set(p):
                theme = complete_theme(p)
            if 'accent' in p and 'accent_text' not in p:
                theme['accent_text'] = readable_ink(theme['accent'])
            validate_theme(theme)
            value["theme"] = theme
        self.store.set_meta("workspace", value)
        return value

    def plan(self, payload, key=None):
        with self.store.lock:
            return self._plan(payload, key)

    def _plan(self, payload, key=None):
        allowed = {"title", "start", "end", "timezone", "game_id", "event_id", "notes", "local_start", "local_end", "fold_start", "fold_end", "move_minutes"}
        if set(payload) - allowed:
            raise ValueError("不支持的个人计划字段")
        existing = next((p for p in self.store.plans() if p["id"] == key), None)
        if key and not existing:
            raise LookupError("个人计划不存在")
        from .presentation import local_instant
        fields = {k:v for k,v in payload.items() if k not in {'local_start','local_end','fold_start','fold_end','move_minutes'}}
        plan = {**(existing or {"schema_version": 1, "id": "plan-" + uuid.uuid4().hex, "game_id": None, "event_id": None, "notes": "", "timezone": "UTC"}), **fields}
        for role in ('start','end'):
            if 'local_'+role in payload:
                if role in payload:
                    raise ValueError('当地时间和 UTC 时间不能同时填写')
                plan[role] = local_instant(payload['local_'+role], plan['timezone'], payload.get('fold_'+role))
        if 'move_minutes' in payload:
            delta = payload['move_minutes']
            if type(delta) is not int or abs(delta)>525600 or not existing:
                raise ValueError('移动量需为一年内的整数分钟，且计划已保存')
            for role in ('start','end'):
                try:
                    plan[role] = iso(stamp(date_value(plan.get(role))) + dt.timedelta(minutes=delta))
                except OverflowError:
                    raise ValueError('移动后的计划日期超出支持范围') from None
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
        if plan["game_id"] is not None and (not isinstance(plan["game_id"], str) or (plan["game_id"] not in self.registry.ids and (not existing or plan['game_id']!=existing['game_id']))):
            raise ValueError("计划关联游戏不存在")
        if not isinstance(plan["notes"], str) or len(plan["notes"]) > 2000:
            raise ValueError("计划备注最多 2000 字")
        if plan["event_id"] is not None:
            event = next((e for v in self.store.versions() for e in v["events"] if e["id"] == plan["event_id"]), None)
            if not event and (not existing or plan['event_id'] != existing['event_id']):
                raise ValueError("关联活动不存在")
            if event:
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
    from .presentation import context, in_focus
    presentation = context(versions, view)
    eligible = {e['id'] for e in events}
    density = {}
    for node in presentation['deadline_nodes']:
        if node['event_id'] not in eligible:
            continue
        day = stamp(node['at']).astimezone(tz).date().isoformat()
        bucket = density.setdefault(day, {'activities':0, 'exchanges':0})
        bucket['exchanges' if node['kind']=='exchange' else 'activities'] += 1
    events = [e for e in events if in_focus(e, presentation['focus_bounds'])]
    groups = {}
    for event in events:
        group = event["game"] if view["group_by"] == "game" else event["category"] if view["group_by"] == "category" else "all"
        groups.setdefault(group, []).append(event["id"])
    bounds = presentation['focus_bounds']
    visible_plans = [p for p in plans if (p.get('game_id') is None or p['game_id'] in selected) and (not bounds or (stamp(p['start']) < stamp(bounds['end']) and stamp(p['end']) > stamp(bounds['start'])))]
    return {"schema_version": 1, "events": events, "groups": [{"id": key, "event_ids": ids, "collapsed": key in view["collapsed_groups"]} for key, ids in groups.items()], "deadline_density": density, "plans": visible_plans, "timezone": timezone, 'presentation':presentation}
