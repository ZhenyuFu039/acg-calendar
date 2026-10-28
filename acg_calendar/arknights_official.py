"""Official AK preview dates, split by numbered sections; never executes page scripts."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import re
import urllib.parse

from .artwork import official_image
from .parser import iso

GAME = 'arknights-cn'
VERSION_ID = GAME + '-activities'
CN = dt.timezone(dt.timedelta(hours=8))
QUOTED = re.compile(r'[「〖【]([^」〗】]+)[」〗】]')
DATE = re.compile(r'(?:(?P<year>20\d{2})[年/\-])?(?P<month>\d{1,2})[月/\-](?P<day>\d{1,2})日?\s*(?P<hour>\d{1,2})[:：](?P<minute>\d{2})')


def normalized(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.casefold())


class NewsLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current = None
        self.rows = []
        self.script = None
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.script = []
        if tag == 'a':
            href = urllib.parse.urljoin('https://ak.hypergryph.com/', dict(attrs).get('href', ''))
            parsed = urllib.parse.urlsplit(href)
            match = re.fullmatch(r'/news/(\d{1,10})(?:\.html)?', parsed.path)
            self.current = {'cid': match[1], 'url': href, 'title': ''} if match and parsed.hostname == 'ak.hypergryph.com' and parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in {None, 443} else None

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        elif self.current is not None:
            self.current['title'] += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script)); self.script = None
        if tag == 'a' and self.current is not None:
            self.current['title'] = re.sub(r'\s+', ' ', self.current['title']).strip()
            self.rows.append(self.current)
            self.current = None


def discover(html, config):
    parser = NewsLinks(); parser.feed(html)
    # Prefer labelled activity links over carousel links with no title. No arbitrary URLs.
    # Next's server-rendered news index carries structured initialData rather than links.
    # Decode JSON only; never evaluate JavaScript or fetch executable bundles.
    for script in parser.scripts:
        match = re.fullmatch(r'self\.__next_f\.push\((.*)\);?', script.strip(), re.S)
        if not match:
            continue
        try:
            frame = json.loads(match[1])
            payload = frame[1] if len(frame) > 1 and isinstance(frame[1], str) else ''
            marker = re.search(r'"initialData"\s*:', payload)
            if not marker:
                continue
            data, _ = json.JSONDecoder().raw_decode(payload[marker.end():].lstrip())
        except (ValueError, TypeError, IndexError):
            continue
        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get('cid'), str) and re.fullmatch(r'\d{1,10}', value['cid']) and isinstance(value.get('title'), str):
                    parser.rows.append({'cid': value['cid'], 'url': 'https://ak.hypergryph.com/news/' + value['cid'], 'title': value['title'], 'published': value.get('displayTime', 0)})
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(data)
    rows = {}
    for row in parser.rows:
        if row['cid'] not in rows or len(row['title']) > len(rows[row['cid']]['title']):
            rows[row['cid']] = row
    result = [r for r in rows.values() if re.search(config['activity_pattern'], r['title']) and not re.search(config['skip_section_pattern'], r['title'])]
    if not result:
        raise ValueError('官网新闻目录没有可识别的活动预告，保留已有日历')
    def priority(row):
        published = row.get('published', 0)
        published = published if isinstance(published, (int, float)) else 0
        return (published // 86400, '活动预告' in row['title'], published)
    result.sort(key=priority, reverse=True)
    return result[:config['max_articles']]


class Article(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.capture = False
        self.in_title = False
        self.title = ''
        self.buffer = []
        self.blocks = []
        self.image = None

    def flush(self):
        text = re.sub(r'[ \t\r\f\v\xa0]+', ' ', ''.join(self.buffer)).strip()
        for line in text.splitlines():
            if line.strip():
                self.blocks.append({'text': line.strip(), 'image': self.image})
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'del', 's'}:
            self.skip += 1
        if self.skip:
            return
        if tag == 'title':
            self.in_title = True
        if tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.flush(); self.capture = True
        if tag == 'br' and self.capture:
            self.buffer.append('\n')
        if tag == 'img':
            self.flush()
            url = dict(attrs).get('src')
            self.image = url if official_image(url) else None

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'del', 's'}:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'title':
            self.in_title = False
        if tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.flush(); self.capture = False

    def handle_data(self, data):
        if not self.skip:
            if self.in_title:
                self.title += data
            elif self.capture:
                self.buffer.append(data)


def interval(text, published):
    matches = list(DATE.finditer(text))
    if len(matches) != 2:
        return None
    endpoints = []
    for index, match in enumerate(matches):
        parts = {k: int(v) if v is not None else None for k, v in match.groupdict().items()}
        year = parts['year'] or (endpoints[0].year if endpoints else published.year)
        if not parts['year']:
            reference_month = endpoints[0].month if endpoints else published.month
            if parts['month'] < reference_month - 6:
                year += 1
            elif not index and parts['month'] > reference_month + 6:
                year -= 1
        try:
            endpoints.append(dt.datetime(year, parts['month'], parts['day'], parts['hour'], parts['minute'], tzinfo=CN))
        except ValueError:
            return None
    if endpoints[1] <= endpoints[0]:
        return None
    return [{'at': iso(v), 'state': 'exact', 'reference': None, 'raw': m.group(), 'precision': 'minute'} for v, m in zip(endpoints, matches)]


def section_title(heading, published, config):
    quoted = QUOTED.findall(heading)
    if quoted and not re.search(config['campaign_pattern'], quoted[-1]):
        title = quoted[-1]
        return title + (' · 复刻' if '复刻' in heading and '复刻' not in title else '')
    if quoted:
        suffix = re.split(r'[，,]', heading, maxsplit=1)[-1]
        suffix = re.sub(r'(?:活动)?(?:即将)?(?:开启|开放)$', '', suffix).strip()
        suffix = suffix.removeprefix(quoted[0])
        return quoted[0] + str(published.year) + suffix
    return re.sub(r'(?:活动)?(?:即将)?(?:开启|开放)$', '', re.sub(config['section_pattern'], '', heading)).strip()


def parse_article(html, cid, config):
    if len(html) > 6_000_000:
        raise ValueError('官网公告过大')
    parser = Article(); parser.feed(html); parser.flush()
    title = re.sub(r'\s*-\s*明日方舟\s*$', '', parser.title).strip()
    date = re.search(r'>\s*(20\d{2})\s*//\s*(\d{1,2})\s*/\s*(\d{1,2})\s*<', html)
    if not date:
        raise ValueError('官网公告发布日期不明确，不能推断活动年份')
    published = dt.datetime(*map(int, date.groups()), tzinfo=CN)
    sections = []; current = {'heading': title, 'blocks': [], 'image': None}
    for block in parser.blocks:
        if re.match(config['section_pattern'], block['text']):
            sections.append(current)
            current = {'heading': block['text'], 'blocks': [], 'image': block['image']}
        else:
            current['blocks'].append(block)
            if current['image'] is None:
                current['image'] = block['image']
    sections.append(current)
    events, warnings = [], []
    for section in sections:
        heading, blocks = section['heading'], section['blocks']
        if not re.search(config['activity_pattern'], heading) or re.search(config['skip_section_pattern'], heading):
            continue
        ranges = []
        for index, block in enumerate(blocks):
            text = block['text']
            if not re.search(config['time_label_pattern'], text):
                continue
            date_index = index
            if not DATE.search(text) and index + 1 < len(blocks):
                text += ' ' + blocks[index+1]['text']
                date_index += 1
            values = interval(text, published)
            if not values:
                continue
            following = ' '.join(b['text'] for b in blocks[date_index+1:date_index+3])
            exchange = bool(re.search(config['exchange_pattern'], text) or re.match(r'兑换说明|商店说明', following))
            label = blocks[index-1]['text'] if index else ''
            phase_label = re.match(r'(预选期|招募期|领取时间)', text)
            if phase_label:
                label = phase_label[1]
            if date_index+1 < len(blocks) and blocks[date_index+1]['text'].startswith('开放关卡'):
                label = blocks[date_index+1]['text'].split('：')[-1]
            ranges.append({'start': values[0], 'end': values[1], 'raw': text, 'exchange': exchange, 'label': label})
        main = [r for r in ranges if not r['exchange']]
        if not main:
            # An explicitly announced opening with no closing date remains open-ended.
            # Do not turn its later maintenance note into the activity's deadline.
            for block in blocks:
                text = block['text']
                if not re.search(config['time_label_pattern'], text) or re.search(config['exchange_pattern'], text):
                    continue
                matches = list(DATE.finditer(text))
                if len(matches) != 1 or re.search(r'截止|截至|结束|过期', text):
                    continue
                parts = matches[0].groupdict()
                year = int(parts['year'] or published.year)
                month = int(parts['month'])
                if not parts['year'] and month < published.month - 6:
                    year += 1
                try:
                    value = dt.datetime(year, month, int(parts['day']), int(parts['hour']), int(parts['minute']), tzinfo=CN)
                except ValueError:
                    continue
                begin = {'at': iso(value), 'state': 'exact', 'reference': None, 'raw': text, 'precision': 'minute'}
                finish = {'at': None, 'state': 'unknown', 'reference': None, 'raw': '官方尚未公布结束时间', 'precision': 'unknown'}
                main.append({'start': begin, 'end': finish, 'raw': text, 'exchange': False, 'label': heading})
                break
        if not main:
            if heading != title or len(sections) == 1:
                warnings.append(heading + '：未找到明确的活动起止时间，未猜测日期')
            continue
        name = section_title(heading, published, config)
        category = 'gacha' if '寻访' in heading and not re.search(r'每日赠送|登录|签到', heading) else 'event'
        start = min(main, key=lambda r: r['start']['at'])['start']
        end = max(main, key=lambda r: r['end']['at'] or '9999')['end']
        subtype = '寻访' if category == 'gacha' else '签到' if re.search(r'签到|登录', heading) else '活动'
        def make(role, begin, finish):
            event_title = name + (' · 奖励兑换' if role == 'exchange' else ' · 寻访' if role == 'gacha' else '')
            key = hashlib.sha256((GAME + '|official|' + cid + '|' + normalized(name) + '|' + role).encode()).hexdigest()[:20]
            return {'id': key, 'version_id': VERSION_ID, 'title': event_title, 'parent_title': name if role == 'exchange' else None,
                    'section': heading, 'category': 'exchange' if role == 'exchange' else category, 'subtype': '奖励兑换' if role == 'exchange' else subtype,
                    'time_kind': 'interval', 'start': copy.deepcopy(begin), 'end': copy.deepcopy(finish), 'phases': [], 'requirements': [],
                    'description': '\n'.join(b['text'] for b in blocks)[:3000], 'warnings': [],
                    'source': {'url': 'https://ak.hypergryph.com/news/' + cid, 'cid': cid, 'title': title, 'kind': 'official', 'label': '明日方舟官方公告', 'published_at': iso(published), 'time_label': heading, 'time_text': begin['raw'] + ' → ' + finish['raw'], 'block_text': '\n'.join(b['text'] for b in blocks)[:4000]},
                    'official': {'cid': cid, 'role': role, 'name': name, 'image_url': section['image']}}
        event = make('gacha' if category == 'gacha' else 'activity', start, end)
        if end['at'] is None:
            event['warnings'].append('官网尚未公布截止日期，不猜测结束时间，也不生成截止提醒。')
        if any(re.search(r'维护后|提前或延迟', b['text']) for b in blocks):
            event['start']['state'] = 'estimated'
            event['warnings'].append('公告涉及维护后开放或时间浮动，当前开始时间为预告值，实际开放请参考后续官方公告。')
        event['phases'] = [{'title': re.sub(r'[◆◇<>「」〖〗]', '', r['label']).strip()[:80] + '开放', **r['start'], 'source_url': event['source']['url'], 'raw': r['raw']} for r in main if r['start']['at'] > start['at']]
        events.append(event)
        shops = [r for r in ranges if r['exchange']]
        if shops:
            events.append(make('exchange', min(shops, key=lambda r: r['start']['at'])['start'], max(shops, key=lambda r: r['end']['at'])['end']))
    if not events:
        raise ValueError('官网公告未解析出明确活动，保留已有日历')
    return events, warnings


def reuse_ids(events, previous):
    """Only reuse a unique same-name/same-role identity in the same period; no fuzzy merge."""
    result = []
    for event in events:
        role = event['official']['role']
        candidates = []
        for old in [*previous, *result]:
            old_role = old.get('official', {}).get('role') or old.get('prts', {}).get('role') or ('exchange' if old.get('category') == 'exchange' else 'gacha' if old.get('category') == 'gacha' else 'activity')
            if old_role != role or normalized(old['title']) != normalized(event['title']):
                continue
            old_cids = old.get('official', {}).get('cids', []) + [str(old.get('official', {}).get('cid') or old.get('prts', {}).get('official_cid') or '')]
            same_notice = event['official']['cid'] in old_cids
            overlap = old['start'].get('at') and old['end'].get('at') and event['end'].get('at') and old['start']['at'] < event['end']['at'] and event['start']['at'] < old['end']['at']
            if same_notice or overlap:
                candidates.append(old)
        by_id = {e['id']: e for e in candidates}
        if len(by_id) == 1:
            match = next(iter(by_id.values())); event['id'] = match['id']
            event['official']['cids'] = sorted(set(match.get('official', {}).get('cids', []) + [str(match.get('official', {}).get('cid') or match.get('prts', {}).get('official_cid') or ''), event['official']['cid']]) - {''})
            if not event['official'].get('image_url') and match.get('official', {}).get('image_url'):
                event['official']['image_url'] = match['official']['image_url']
                event['official']['image_source_url'] = match['official'].get('image_source_url') or match['source']['url']
            if not event.get('artwork') and match.get('artwork'):
                event['artwork'] = copy.deepcopy(match['artwork'])
            if match.get('source', {}).get('kind') != 'official' and (match['start'].get('at'), match['end'].get('at')) != (event['start']['at'], event['end']['at']):
                event['warnings'].append('官网时间与此前 Wiki 缓存不同，已采用官网日期；个人时间校正仍保留。')
        elif len(by_id) > 1:
            event['warnings'].append('存在多个旧活动身份候选，未自动合并；旧个人状态保留在原条目。')
        event['warnings'] = list(dict.fromkeys(event['warnings']))
        result.append(event)
    return list({e['id']: e for e in result}.values())
