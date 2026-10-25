"""Host projections for linked views; never edits publisher data."""
import datetime as dt
import re
from collections import defaultdict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .parser import iso, stamp

CONFIRMED = {'exact', 'confirmed', 'manual'}


def local_instant(value, timezone, fold=None):
    try:
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',value):
            raise ValueError()
        naive = dt.datetime.fromisoformat(value)
        zone = ZoneInfo(timezone)
        if naive.tzinfo or naive.second or naive.microsecond:
            raise ValueError()
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ValueError('请输入有效的当地日期、分钟和 IANA 时区') from None
    choices = {}
    for occurrence in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=occurrence)
        if aware.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            choices[occurrence] = aware.astimezone(dt.timezone.utc)
    if not choices:
        raise ValueError('该当地时刻因夏令时切换不存在，请调整时间')
    if len(set(choices.values())) > 1 and fold is None:
        raise ValueError('该当地时刻因夏令时重复，请选择较早或较晚的一次')
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise ValueError('夏令时重复时刻选项无效')
    return iso(choices.get(fold, next(iter(choices.values()))))


def context(versions, view):
    events = [{**e, 'game':v['game']} for v in versions for e in v['events']]
    by_id = {e['id']:e for e in events}
    titles = defaultdict(list)
    for e in events:
        titles[(e['game'], e['version_id'], e['title'])].append(e['id'])
    parents, children = {}, defaultdict(list)
    for e in events:
        parent = e.get('parent_id')
        if not parent and e.get('parent_title'):
            matches = titles[(e['game'], e['version_id'], e['parent_title'])]
            if len(matches) == 1:
                parent = matches[0]
        if parent in by_id and parent != e['id'] and by_id[parent]['game'] == e['game'] and by_id[parent]['version_id'] == e['version_id']:
            parents[e['id']] = parent
    # Protect legacy adapters as well as validated plugin relationships.
    for child in list(parents):
        seen, current = {child}, parents[child]
        while current in parents:
            if current in seen:
                parents.pop(child, None)
                break
            seen.add(current); current = parents[current]
    for child, parent in parents.items():
        children[parent].append(child)
    nodes, uncertain, seen = [], [], set()
    for e in events:
        if e.get('ignored') or e.get('completed') or e.get('invalid_interval') or e['time_kind'] == 'point':
            continue
        end = e['end']
        if not end.get('at') or end['state'] not in CONFIRMED:
            uncertain.append(e['id'])
        candidates = [(e['title'], end)]
        candidates += [(p['title'], p) for p in e.get('phases', []) if any(t in p['title'] for t in ('截止', '结束', '关闭'))]
        for title, point in candidates:
            key = (e['id'], point.get('at'))
            if not point.get('at') or point.get('state') not in CONFIRMED or key in seen:
                continue
            seen.add(key)
            nodes.append({'event_id':e['id'], 'title':title, 'at':point['at'], 'kind':'exchange' if e['category']=='exchange' else 'activity'})
    focus = view['focus']; bounds = None
    if focus['date']:
        zone = ZoneInfo(view.get('focus_timezone', 'UTC'))
        day = dt.date.fromisoformat(focus['date'])
        lo = dt.datetime.combine(day, dt.time(), zone)
        hi = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), zone)
        bounds = {'start':iso(lo), 'end':iso(hi), 'timezone':zone.key}
    return {'parents':parents, 'children':dict(children), 'deadline_nodes':nodes, 'uncertain_deadlines':uncertain, 'focus_bounds':bounds}


def in_focus(event, bounds):
    if not bounds:
        return True
    start = stamp(event['start'].get('at'))
    if not start or event.get('invalid_interval'):
        return False
    lo, hi = stamp(bounds['start']), stamp(bounds['end'])
    if event['time_kind'] == 'point':
        return lo <= start < hi
    end = stamp(event['end'].get('at'))
    return start < hi and (end is None or end > lo)
