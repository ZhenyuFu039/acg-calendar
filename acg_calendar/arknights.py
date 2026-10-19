"""PRTS CN calendar adapter. Wiki markup is parsed as data, never executed."""
from __future__ import annotations

import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import urllib.parse

from .artwork import ArtworkCache
from .arknights_artwork import OfficialArknightsArtwork
from .parser import ROOT, iso, stamp
from .wiki_http import WikiHTTP, wiki_url

GAME = 'arknights-cn'
VERSION_ID = GAME + '-activities'
CN = dt.timezone(dt.timedelta(hours=8))
LICENSE = {'name': 'CC BY-NC-SA 4.0（Wiki 编辑内容；游戏素材另属原权利人）', 'url': 'https://creativecommons.org/licenses/by-nc-sa/4.0/'}


def rules():
    return json.loads((ROOT / 'rules/arknights-cn.json').read_text(encoding='utf-8'))


def page_url(title):
    return 'https://prts.wiki/w/' + urllib.parse.quote(title.replace(' ', '_'), safe='/')


def api_url(**params):
    return 'https://prts.wiki/api.php?' + urllib.parse.urlencode({'format': 'json', 'formatversion': 2, 'maxlag': 5, **params})


def clean(value):
    value = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]+)\]\]', r'\1', value)
    value = re.sub(r'<[^>]*>', '', value)
    return value.replace("'''", '').replace("''", '').strip()


def split_fields(text):
    parts, start, braces, links, i = [], 0, 0, 0, 0
    while i < len(text):
        pair = text[i:i+2]
        if pair in {'{{', '[[', '}}', ']]'}:
            braces += 1 if pair == '{{' else -1 if pair == '}}' else 0
            links += 1 if pair == '[[' else -1 if pair == ']]' else 0
            i += 2
            continue
        if text[i] == '|' and not braces and not links:
            parts.append(text[start:i]); start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def info_template(text):
    if len(text) > 2_000_000:
        raise ValueError('PRTS 页面过大')
    # Ignore comments without changing line numbers used by evidence.
    text = re.sub(r'<!--[\s\S]*?-->', lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    stack, i, candidates = [], 0, []
    while i < len(text) - 1:
        if text[i:i+2] == '{{':
            stack.append(i)
            if len(stack) > 100:
                raise ValueError('PRTS 模板嵌套过深')
            i += 2
        elif text[i:i+2] == '}}' and stack:
            start = stack.pop()
            raw = text[start:i+2]
            name = raw[2:].split('|', 1)[0].strip().removeprefix('模板:')
            if name in {'活动信息', '活动信息/info'}:
                fields = {}
                for part in split_fields(raw[2:-2])[1:]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        fields[key.strip()] = value.strip()
                candidates.append((start, fields, raw, text[:start].count('\n') + 1))
            i += 2
        else:
            i += 1
    if not candidates:
        raise ValueError('未找到 PRTS 活动信息模板')
    return sorted(candidates, key=lambda x: x[0])[0][1:]


def endpoint(value):
    match = re.fullmatch(r'\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*', value or '')
    if not match:
        return {'at': None, 'state': 'unknown', 'reference': 'unpublished', 'raw': value or '', 'precision': None}
    year, month, day, hour, minute, second = match.groups()
    date = dt.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0), tzinfo=CN)
    return {'at': iso(date), 'state': 'exact', 'reference': None, 'raw': value, 'precision': 'second' if second else 'minute'}


def cn_field(fields, name):
    return fields.get(name + 'cn', fields.get(name, ''))


def parse_page(page):
    title = page['title']
    revision = page['revisions'][0]
    content = revision['slots']['main']['content']
    fields, raw, line = info_template(content)
    start, end = endpoint(cn_field(fields, '活动开始时间')), endpoint(cn_field(fields, '活动结束时间'))
    if not start['at']:
        raise ValueError('国服开始时间未明确，保留已有条目')
    if end['at'] and stamp(end['at']) <= stamp(start['at']):
        raise ValueError('PRTS 活动时间顺序异常')
    kind = clean(fields.get('类型', '活动'))
    if kind in {'集成战略', '生息演算'} and not end['at']:
        raise ValueError('常驻玩法不作为限时活动导入')
    name = clean(fields.get('名称缩短') or fields.get('名称') or title)
    source = {'url': page_url(title), 'title': title, 'page_id': page.get('pageid'), 'revision_id': revision['revid'], 'revision_url': 'https://prts.wiki/index.php?' + urllib.parse.urlencode({'title': title, 'oldid': revision['revid']}), 'revised_at': revision.get('timestamp'), 'kind': 'community_wiki', 'label': 'PRTS 社区 Wiki', 'license': LICENSE, 'line': line, 'time_label': '活动信息模板 · 国服', 'time_text': start['raw'] + ' → ' + end['raw'], 'block_text': raw}
    def make(suffix, event_title, category, begin, finish):
        key = hashlib.sha256((GAME + '|' + title + '|' + suffix).encode()).hexdigest()[:20]
        return {'id': key, 'version_id': VERSION_ID, 'title': event_title, 'parent_title': None if suffix == 'activity' else name, 'section': kind, 'category': category, 'subtype': '签到' if '登录' in kind else '复刻' if '复刻' in name else kind, 'time_kind': 'interval', 'start': begin, 'end': finish, 'phases': [], 'description': 'PRTS 国服活动时间；关卡开放与奖励兑换分别列出。', 'requirements': [clean(fields['参加条件关卡'])] if fields.get('参加条件关卡') else [], 'source': dict(source), 'warnings': [], 'prts': {'page': title, 'image_title': clean(fields.get('标题图文件名', '')), 'announcement': clean(fields.get('公告', '')).split('#')[0], 'official_cid': fields.get('官网链接', '').strip() if re.fullmatch(r'\d{1,10}', fields.get('官网链接', '').strip()) else None, 'role': suffix}}
    event = make('activity', name, 'event', start, end)
    events = [event]
    shop = endpoint(cn_field(fields, '兑换结束时间'))
    if shop['at']:
        if stamp(shop['at']) <= stamp(start['at']):
            event['warnings'].append('兑换截止早于活动开始，未导入兑换条目')
        else:
            shop_start = {**start, 'state': 'estimated', 'reference': 'activity_start', 'raw': '兑换开始未单列，暂按活动开始展示'}
            events.append(make('exchange', name + ' · 奖励兑换', 'exchange', shop_start, shop))
    return events


class Catalog(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.row = None
        self.rows = []
        self.images = {}
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'img' and wiki_url(attrs.get('src', '')):
            image_url = attrs['src']
            parsed = urllib.parse.urlsplit(image_url)
            if parsed.hostname == 'media.prts.wiki':
                parts = urllib.parse.unquote(parsed.path).split('/')
                filename = parts[-2] if '/thumb/' in parsed.path and len(parts) > 2 else parts[-1]
                self.images[filename.replace('_', ' ')] = image_url
        if tag in {'script', 'style'}:
            self.skip += 1
        if tag == 'tr':
            self.row = {'text': '', 'titles': [], 'categories': []}
        if self.row is not None and tag == 'a' and attrs.get('href', '').startswith('/w/'):
            target = urllib.parse.unquote(attrs['href'][3:]).replace('_', ' ')
            if target.startswith(('分类:', 'Category:')):
                self.row['categories'].append(target.split(':', 1)[1])
            if ':' not in target and '#' not in target and target not in self.row['titles']:
                self.row['titles'].append(target)

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.skip = max(0, self.skip - 1)
        if tag == 'tr' and self.row is not None:
            match = re.match(r'\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})', self.row['text'])
            if match and self.row['titles']:
                self.rows.append({'title': self.row['titles'][0], 'start': match.group(1), 'text': self.row['text'].strip(), 'categories': self.row['categories']})
            self.row = None

    def handle_data(self, data):
        if self.row is not None and not self.skip:
            self.row['text'] += data


def catalog_titles(html, config=None, with_images=False):
    config = config or rules()
    parser = Catalog(); parser.feed(html)
    titles = []
    for row in parser.rows:
        if any(kind in row['categories'] for kind in config['permanent_types']):
            continue
        if row['title'] not in titles:
            titles.append(row['title'])
        if len(titles) >= config['max_activity_pages']:
            break
    if not titles:
        raise ValueError('PRTS 活动目录结构变化，保留本地日历')
    return (titles, parser.images) if with_images else titles


def revisions(fetcher, titles):
    if not titles:
        return []
    data = json.loads(fetcher(api_url(action='query', prop='revisions', titles='|'.join(titles), rvprop='content|timestamp|ids', rvslots='main', redirects=1)))
    if 'error' in data or not isinstance(data.get('query', {}).get('pages'), list):
        raise ValueError('PRTS 原文接口不可用或繁忙，保留本地日历')
    return data['query']['pages']


def attach_announcements(events, pages, config=None):
    config = config or rules()
    date_re = re.compile(config['announcement_datetime_pattern'])
    extra, seen = [], set()
    for page in pages:
        if not page.get('revisions'):
            continue
        owners = [e for e in events if e.get('prts', {}).get('announcement') == page['title'] and e['prts']['role'] == 'activity']
        if not owners:
            continue
        owner = next((e for e in owners if page['title'] == e['prts']['page'] + '/活动公告'), next((e for e in owners if e['subtype'] != '签到'), owners[0]))
        base = stamp(owner['start']['at']).astimezone(CN)
        revision = page['revisions'][0]
        section, last_label = '', ''
        lines = revision['slots']['main']['content'].splitlines()
        for number, raw in enumerate(lines, 1):
            text = clean(raw)
            if re.match(r'^={2,}', text):
                section = text.strip('= '); last_label = ''; continue
            if re.fullmatch(r'【[^】]+】', text):
                last_label = text
            matches = list(date_re.finditer(text))
            if len(matches) != 2 or not re.search(r'时间|开启', text):
                continue
            values = []
            try:
                for match in matches:
                    parts = match.groupdict()
                    year = int(parts.get('year') or base.year)
                    if not parts.get('year') and int(parts['month']) < base.month - 6:
                        year += 1
                    values.append(endpoint(f"{year}-{int(parts['month']):02}-{int(parts['day']):02} {parts['hour']}:{parts['minute']}"))
            except ValueError:
                continue
            begin, finish = values
            if not begin['at'] or not finish['at'] or stamp(finish['at']) <= stamp(begin['at']):
                continue
            evidence = {**owner['source'], 'page_id': page.get('pageid'), 'url': page_url(page['title']), 'title': page['title'], 'revision_id': revision['revid'], 'revision_url': 'https://prts.wiki/index.php?' + urllib.parse.urlencode({'title': page['title'], 'oldid': revision['revid']}), 'line': number, 'time_label': section or last_label or '开放时间', 'time_text': text, 'block_text': raw}
            # Only explicitly named stage openings inside the owning activity.
            phase = re.search(r'[◆◇]\s*「(.+?)」开启时间', text)
            if phase and stamp(begin['at']) >= stamp(owner['start']['at']) and (not owner['end']['at'] or stamp(finish['at']) <= stamp(owner['end']['at'])):
                owner['phases'].append({**begin, 'title': phase.group(1) + '开放', 'source_line': number, 'source_url': evidence['url'], 'raw': text})
            if '开放时间' in text and any('兑换说明' in clean(line) for line in lines[number:number+4]):
                for shop in events:
                    if shop.get('prts', {}).get('page') == owner['prts']['page'] and shop['prts']['role'] == 'exchange' and shop['end']['at'] == finish['at']:
                        shop['start'] = begin; shop['source'] = evidence
            if '寻访' in section and re.search(r'限时|标准|中坚', section) and '活动时间' in text:
                signature = (page['title'], section)
                if signature in seen:
                    continue
                seen.add(signature)
                name = re.search(r'【([^】]+)】', section)
                title = (name.group(1) if name else section) + ' · 寻访'
                key = hashlib.sha256((GAME + '|' + page['title'] + '|gacha|' + section).encode()).hexdigest()[:20]
                extra.append({**owner, 'id': key, 'title': title, 'parent_title': owner['title'], 'category': 'gacha', 'subtype': '寻访', 'description': '从 PRTS 所转载活动公告提取的独立寻访时间。', 'start': begin, 'end': finish, 'phases': [], 'source': evidence, 'prts': {**owner['prts'], 'role': 'gacha', 'image_title': ''}})
    for event in events:
        event['phases'] = list({(p['title'], p['at']): p for p in event['phases']}.values())
    events.extend(extra)


def make_version(events, fetched_at, origin='live', errors=None):
    return {'schema_version': 1, 'ruleset_version': rules()['ruleset_version'], 'id': VERSION_ID, 'name': '近期活动 · PRTS', 'game': GAME, 'timezone': 'Asia/Shanghai', 'source': {'cid': 'prts', 'url': page_url('活动一览'), 'title': 'PRTS 活动一览', 'kind': 'community_wiki', 'label': 'PRTS 社区 Wiki', 'license': LICENSE, 'published_at': None, 'fetched_at': fetched_at, 'origin': origin}, 'maintenance': {'start': None, 'planned_end': None, 'actual_end': None}, 'events': events, 'warnings': errors or [], 'analysis': {'strategy': 'prts_activity_templates', 'ai_used': False, 'event_count': len(events)}}


class ArknightsCollector:
    meta_key = 'sync:arknights-cn'

    def __init__(self, store, data_dir: Path, fetcher=None, artwork=None, official_artwork=None):
        self.store, self.data_dir = store, data_dir
        self.fetcher = fetcher or WikiHTTP()
        self.lock = threading.Lock()
        self.last_attempt = None
        def image_allowed(url):
            return wiki_url(url) and urllib.parse.urlsplit(url).hostname == 'media.prts.wiki'
        self.artwork = artwork or ArtworkCache(data_dir, downloader=WikiHTTP(timeout=8).read, validator=image_allowed)
        self.official_artwork = official_artwork or (OfficialArknightsArtwork(data_dir) if fetcher is None else None)

    def seed(self):
        if any(v.get('game') == GAME for v in self.store.versions()):
            return
        version = json.loads((ROOT / 'samples/arknights-cn-calendar.json').read_text(encoding='utf-8'))
        self.store.save_version(version)
        self.store.set_meta(self.meta_key, {'running': False, 'mode': 'snapshot', 'last_success': None, 'last_attempt': None, 'error': None, 'message': '内置 PRTS 活动快照，联网后更新'})

    def start(self, force=False):
        if not self.lock.acquire(blocking=False):
            return False
        now = dt.datetime.now(dt.timezone.utc)
        if not force and self.last_attempt and (now - self.last_attempt).total_seconds() < 30:
            self.lock.release(); return False
        self.last_attempt = now
        previous = self.store.get_meta(self.meta_key, {})
        self.store.set_meta(self.meta_key, {**previous, 'running': True, 'last_attempt': iso(now), 'error': None})
        threading.Thread(target=self._run, daemon=True, name='prts-sync').start()
        return True

    def _run(self):
        try:
            self.collect()
        except Exception as error:
            previous = self.store.get_meta(self.meta_key, {})
            self.store.set_meta(self.meta_key, {**previous, 'running': False, 'error': str(error)[:300], 'message': 'PRTS 同步失败，保留本地日历'})
            # Official pictures may refresh even when the independent date source is offline.
            if self.official_artwork:
                version = next((v for v in self.store.versions() if v['id'] == VERSION_ID), None)
                if version:
                    self.enrich(version)
                    self.store.save_version(version)
        finally:
            self.lock.release()

    def enrich(self, version):
        if self.official_artwork:
            try:
                self.official_artwork.attach(version)
            except Exception as error:
                version['official_artwork'] = {'errors': ['官网图片补充失败：' + str(error)[:160]]}

    def collect(self):
        config = rules()
        titles, image_urls = catalog_titles(self.fetcher(config['catalog_url']), config, with_images=True)
        pages = revisions(self.fetcher, titles)
        previous = next((v for v in self.store.versions() if v['id'] == VERSION_ID), None)
        old = (previous or {}).get('events', [])
        events, errors = [], []
        source_dir = self.data_dir / 'sources' / 'prts'; source_dir.mkdir(parents=True, exist_ok=True)
        parsed = 0
        for page in pages:
            try:
                rows = parse_page(page)
                events.extend(rows); parsed += 1
                (source_dir / (str(page['pageid']) + '.json')).write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding='utf-8')
            except (ValueError, KeyError, IndexError, TypeError) as error:
                errors.append(page.get('title', '?') + '：' + str(error))
                events.extend(e for e in old if e.get('prts', {}).get('page') == page.get('title'))
        returned = {p.get('title') for p in pages}
        for title in titles:
            if title not in returned:
                errors.append(title + '：接口未返回，保留已有条目')
                events.extend(e for e in old if e.get('prts', {}).get('page') == title)
        if not parsed:
            raise ValueError('PRTS 未解析出有效活动，保留本地日历')
        announcements = list(dict.fromkeys(e['prts']['announcement'] for e in events if e.get('prts', {}).get('announcement')))[:config['max_announcement_pages']]
        try:
            announcement_pages = revisions(self.fetcher, announcements)
            if announcements and not any(p.get('revisions') for p in announcement_pages):
                raise ValueError('接口未返回活动公告，保留已有补充信息')
            attach_announcements(events, announcement_pages, config)
            for page in announcement_pages:
                if page.get('revisions'):
                    (source_dir / (str(page['pageid']) + '.json')).write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as error:
            errors.append('公告补充失败：' + str(error)[:180])
            existing = {e['id'] for e in events}
            events.extend(e for e in old if e['id'] not in existing and e.get('prts', {}).get('role') == 'gacha' and e['prts']['page'] in titles)
            saved_phases = {e['id']: e for e in old}
            for event in events:
                saved = saved_phases.get(event['id'])
                if saved and not event['phases'] and event['start']['at'] == saved['start']['at'] and event['end']['at'] == saved['end']['at']:
                    event['phases'] = saved['phases']
        # Use images already linked from the catalog, no thumbnail-generation API.
        saved = {e['id']: e for e in old}
        for event in events:
            image = saved.get(event['id'], {}).get('artwork')
            if image and self.artwork.path(image.get('key', '')):
                event['artwork'] = image
        now = dt.datetime.now(dt.timezone.utc)
        fetched = iso(now)
        version = make_version(list({e['id']: e for e in events}.values()), fetched, errors=errors)
        self.store.save_version(version)
        self.enrich(version)
        image_candidates = sorted(events, key=lambda e: (bool(e['end']['at']) and stamp(e['end']['at']) < now, -(stamp(e['start']['at']).timestamp() if e['start']['at'] else 0)))
        images = list(dict.fromkeys(e['prts']['image_title'] for e in image_candidates if e['prts'].get('image_title') and not e.get('artwork')))[:config['max_images']]
        image_errors = 0
        for filename in images:
            url = image_urls.get(filename.replace('_', ' '))
            if not url:
                continue
            try:
                key = self.artwork.cache(url)
                for event in events:
                    if key and event['prts'].get('image_title') == filename and not event.get('artwork'):
                        event['artwork'] = {'key': key, 'path': '/api/artwork/' + key, 'url': url, 'source_url': event['source']['url'], 'source_title': '文件:' + filename, 'match': 'prts_template_image'}
            except Exception as error:
                errors.append('图片未更新（' + filename + '）：' + str(error)[:120])
                image_errors += 1
                if image_errors >= 2:
                    break
        version['warnings'] = errors
        self.store.save_version(version)
        self.store.set_meta(self.meta_key, {'running': False, 'mode': 'live', 'last_attempt': fetched, 'last_success': fetched, 'error': None, 'message': '已同步 PRTS 社区 Wiki', 'page_count': parsed, 'warnings': errors})
        return version
