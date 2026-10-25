"""Compile official Chinese ZZZ announcements; exact Wiki matches supplement art."""
import copy
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import re
import threading
import urllib.parse

from .artwork import image_kind
from .gamekee import atomic_json
from .network import deferring_images, network_job
from .parser import iso, stamp
from .plugin_media import local_file
from .providers import JsonProvider, bounded_json
from .sync_progress import SyncProgress
from .zzz_http import API, WIKI, ZZZHTTP, allowed

ZONE = dt.timezone(dt.timedelta(hours=8))
DATE = r'20\d{2}[/-]\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?'
VERSION = r'(\d+\.\d+)版本'


class Blocks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.parts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'br', 'h3', 'h4', 'li'}:
            self.parts.append('\n')
        if tag == 'img':
            self.parts.append('\n[IMAGE:' + dict(attrs).get('src', '') + ']\n')

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return '\n'.join(s.strip() for s in ''.join(self.parts).splitlines() if s.strip())


def plain(html):
    return re.sub(r'\[IMAGE:[^\]]*\]', '', Blocks(html).text()).strip()


def exact(raw):
    fields = list(map(int, re.findall(r'\d+', raw)))
    value = dt.datetime(*fields, tzinfo=ZONE)
    return {'at': iso(value), 'state': 'exact', 'raw': raw}


def identity(title):
    return re.sub(r'[\s「」『』！!·\-：:]', '', title)


class WikiRows(HTMLParser):
    """Read the rendered Semantic MediaWiki table, not the #ask template."""
    def __init__(self, html):
        super().__init__()
        self.rows, self.cells, self.cell = [], None, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'tr' and 'data-param1' in a:
            self.cells = []
        elif tag == 'td' and self.cells is not None:
            self.cell = {'text': '', 'links': [], 'images': []}
        elif self.cell is not None:
            if tag == 'a' and a.get('href', '').startswith('/zzz/'):
                self.cell['links'].append(a['href'])
            if tag == 'img' and a.get('src'):
                self.cell['images'].append(a['src'])

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.cells.append(self.cell)
            self.cell = None
        if tag == 'tr' and self.cells is not None:
            if len(self.cells) >= 6:
                c = self.cells
                dates = re.findall(DATE, c[0]['text'])
                if len(dates) == 2 and c[1]['images'] and c[2]['links']:
                    self.rows.append({'title': c[2]['text'].strip(), 'start': exact(dates[0])['at'], 'end': exact(dates[1])['at'],
                                      'image_url': c[1]['images'][0], 'url': 'https://wiki.biligame.com' + c[2]['links'][0], 'version': c[5]['text'].strip()})
            self.cells = None


def maintenance_map(articles):
    versions = {}
    for a in sorted(articles, key=lambda a: a['dtStartTime']):
        if not re.search(r'预下载开启&更新通知|更新公告', a['sTitle']):
            continue
        v = re.search(VERSION, a['sTitle'])
        text = plain(a['sContent'])
        start = re.search(r'【(?:版本更新时间|更新开始时间)】\s*(' + DATE + r')', text)
        if not v or not start:
            continue
        key = v[1]
        own = versions.setdefault(key, {'version': key})
        own.update(start=exact(start[1]), url=news_url(a), title=a['sTitle'])
        duration = re.search(r'预计(?:需要)?\s*(\d{1,2})\s*个?小时', text[start.end():start.end()+100])
        own['ready'] = {'at': iso(stamp(own['start']['at']) + dt.timedelta(hours=int(duration[1]))), 'state': 'estimated', 'raw': '维护预计' + duration[1] + '小时'} if duration else {'at': None, 'state': 'unknown', 'raw': '版本更新后'}
        end = re.search(re.escape(key) + r'版本结束时间为\s*(' + DATE + r')', text)
        if end:
            own['end'] = exact(end[1])
    return versions


def news_url(a):
    return 'https://zenless.hoyoverse.com/zh-cn/news/' + str(a['iInfoId'])


def endpoint(raw, versions):
    m = re.search(DATE, raw)
    if m:
        return exact(m[0])
    v = re.search(VERSION + r'(更新后|结束)', raw)
    value = versions.get(v[1], {}).get('ready' if v[2] == '更新后' else 'end') if v else None
    return {**copy.deepcopy(value), 'raw': raw.strip()} if value else {'at': None, 'state': 'unknown', 'raw': raw.strip()}


def window(raw, versions):
    # Match only the explicitly labelled window. Publication/API expiry dates
    # and the later daily-reset or reward-expiry times never become this range.
    clean = re.sub(r'[（(](?:服务器时间|UTC\+8)[）)]', '', raw)
    parts = re.split(r'\s*[~～至]\s*', clean, maxsplit=1)
    if len(parts) != 2:
        return None
    return endpoint(parts[0], versions), endpoint(parts[1], versions)


def compile_feed(articles, wiki, rules, fetched_at):
    if not articles or len({a['iInfoId'] for a in articles}) != len(articles):
        raise ValueError('官方公告列表为空或 ID 重复，保留旧日历')
    versions = maintenance_map(articles)
    if not versions:
        raise ValueError('未识别版本维护公告，保留旧日历')
    keys = sorted(versions, key=lambda s: tuple(map(int, s.split('.'))))[-rules['collection']['version_count']:]
    periods = {k: {'id': 'version-' + k, 'name': k + '版本', 'source_url': versions[k]['url'], 'maintenance': versions[k], 'events': [], 'analysis': {'strategy': rules['strategy'], 'ai_used': False, 'fetched_at': fetched_at, 'wiki_revision': wiki.get('revid'), 'wiki_latest_end': None}} for k in keys}
    wiki_rows = WikiRows(wiki.get('text', '')).rows
    wiki_latest = max((row['end'] for row in wiki_rows), default=None)

    def add(a, title, raw, category, image, suffix, description=''):
        pair = window(raw, versions)
        if not pair:
            raise ValueError('活动时间格式发生变化：' + a['sTitle'])
        v = re.search(VERSION, raw + '\n' + a['sTitle'])
        candidates = [k for k in versions if pair[0]['at'] and versions[k]['start']['at'] <= pair[0]['at']]
        key = v[1] if v else max(candidates, key=lambda k: versions[k]['start']['at']) if candidates else None
        if key not in periods:
            if key not in versions or not pair[1]['at'] or stamp(pair[1]['at']) < stamp(fetched_at):
                return
            # A long-running activity can outlive the normal version window.
            periods[key] = {'id': 'version-' + key, 'name': key + '版本', 'source_url': versions[key]['url'], 'maintenance': versions[key], 'events': [], 'analysis': {'strategy': rules['strategy'], 'ai_used': False, 'fetched_at': fetched_at, 'wiki_revision': wiki.get('revid')}}
        evidence = {'official_title': a['sTitle'], 'official_id': a['iInfoId'], 'published_at': a['dtStartTime'], 'time_text': raw, 'maintenance_url': versions[key]['url']}
        # Supplement only the identical event AND exact UTC interval. Never use
        # an old rerun's picture solely because its title looks similar.
        if not image and all(p['state'] == 'exact' for p in pair):
            matches = [r for r in wiki_rows if identity(r['title']) == identity(title) and (r['start'], r['end']) == (pair[0]['at'], pair[1]['at'])]
            if len(matches) == 1:
                image = matches[0]['image_url']
                evidence['wiki'] = {**matches[0], 'revision_url': WIKI + 'index.php?oldid=' + str(wiki['revid']), 'revision': wiki['revid'], 'license': rules['sources']['wiki_license']}
        warnings = []
        if any(p['state'] != 'exact' for p in pair):
            warnings.append('版本更新后按官方预计维护结束时间显示；实际开服时间请参考游戏公告。' if pair[0]['state'] == 'estimated' else '版本关联时间尚未明确，暂不生成精确提醒。')
        row = {'id': 'official-' + str(a['iInfoId']) + '-' + suffix, 'title': title, 'category': category, 'subtype': {'event': '活动', 'gacha': '调频', 'maintenance': '版本维护'}[category], 'start': pair[0], 'end': pair[1], 'source_url': news_url(a), 'description': description[:4500], 'warnings': warnings, 'evidence': evidence}
        if image:
            row.update(image_url=image, image_source=evidence.get('wiki', {}).get('url', news_url(a)))
        periods[key]['events'].append(row)

    for a in articles:
        text = plain(a['sContent'])
        title = a['sTitle']
        explicit_version = re.search(VERSION, title)
        if explicit_version and explicit_version[1] not in periods:
            continue
        ext = json.loads(a.get('sExt') or '{}') if isinstance(a.get('sExt'), str) else a.get('sExt', {})
        banner = next((x.get('url') for x in ext.get('news-banner', []) if x.get('url')), None)
        if re.search(rules['articles']['event_title_pattern'], title):
            m = re.search(r'【活动时间】\s*([^\n]+)', text)
            if not m:
                raise ValueError('活动公告缺少时间栏：' + title)
            label = title.removesuffix('活动说明')
            add(a, label, m[1], 'event', banner, 'event', text)
        elif re.search(rules['articles']['frequency_title_pattern'], title):
            m = re.search(r'调频活动时间为[：:]\s*([^\n]+?)(?:，包含|\n|$)', text)
            if not m:
                raise ValueError('限时频段公告缺少共同时间：' + title)
            blocks = Blocks(a['sContent']).text()
            seen = set()
            for match in re.finditer(r'「([^\n]+?)」调频活动\n(.*?)(?=\n「[^\n]+?」调频活动|\n※|$)', blocks, re.S):
                name, body = match.groups()
                if name in seen:
                    continue
                seen.add(name)
                image = re.search(r'\[IMAGE:([^\]]+)\]', body)
                add(a, '「' + name + '」', m[1], 'gacha', image[1] if image else None, hashlib.sha256(name.encode()).hexdigest()[:12], re.sub(r'\[IMAGE:[^\]]*\]', '', body).strip())
            if not seen:
                raise ValueError('未识别调频子活动：' + title)
    for key, period in periods.items():
        own = versions[key]
        period['events'].append({'id': 'maintenance-' + key, 'title': key + '版本更新维护', 'category': 'maintenance', 'subtype': '版本维护', 'start': own['start'], 'end': own['ready'], 'source_url': own['url'], 'description': '维护结束为官方预计时间，实际完成时间以游戏公告为准。', 'warnings': ['预计维护结束时间，不生成精确结束提醒。'], 'evidence': {'official_title': own['title'], 'time_text': own['start']['raw'] + ' 开始；' + own['ready']['raw'], 'maintenance_url': own['url']}})
        period['analysis'].update(event_count=len(period['events']), wiki_latest_end=wiki_latest, wiki_matched=sum('wiki' in e['evidence'] for e in period['events']))
    return {'schema_version': 1, 'versions': list(periods.values())}


class ZZZProvider(JsonProvider):
    def __init__(self, directory, manifest, store, http=None):
        super().__init__(directory, manifest, store)
        self.config = bounded_json(local_file(directory, manifest['source'].get('rules', 'rules.json')))
        if self.descriptor['id'] != 'zzz-cn' or self.descriptor['timezone'] != 'Asia/Shanghai' or self.config.get('schema_version') != 1:
            raise ValueError('绝区零采集引擎仅适用于国服和规则版本 1')
        c = self.config['collection']
        if type(c['page_size']) is not int or not 20 <= c['page_size'] <= 50 or type(c['version_count']) is not int or not 1 <= c['version_count'] <= 3:
            raise ValueError('官方采集范围超出上限')
        self.http, self.lock, self.last_attempt = http or ZZZHTTP(), threading.Lock(), None
        self.descriptor.update(plugin_kind='zzz-official-json', source_kind='official_announcements', capabilities=['sync', 'rules', 'artwork'])

    def rules(self):
        return copy.deepcopy(self.config)

    def normalize(self, feed):
        versions = super().normalize(feed)
        for version, raw in zip(versions, feed['versions']):
            version['analysis'] = raw['analysis']
            # Preserve event-specific estimates; the shared resolver must not
            # upgrade a planned opening time to confirmed just because it passed.
            for event, row in zip(version['events'], raw['events']):
                event['source'].update(kind='zzz_official', title=row['evidence']['official_title'], time_text=row['evidence']['time_text'], time_label='国服公告时间（UTC+8）', block_text=row['evidence']['time_text'], evidence=row['evidence'])
                event['warnings'] = row.get('warnings', [])
                for role in ('start', 'end'):
                    event[role]['raw'] = row[role].get('raw', '')
                if event.get('artwork'):
                    event['artwork']['match'] = 'wiki_exact_interval' if 'wiki' in row['evidence'] else 'official_activity_heading'
        return versions

    def seed(self):
        if not any(v.get('game') == 'zzz-cn' for v in self.store.versions()):
            JsonProvider.start(self)

    def start(self, force=False):
        if not self.lock.acquire(False):
            return False
        now = dt.datetime.now(dt.timezone.utc)
        if not force and self.last_attempt and (now - self.last_attempt).total_seconds() < 30:
            self.lock.release()
            return False
        self.last_attempt = now
        self.store.set_meta(self.sync_key, {**self.store.get_meta(self.sync_key, {}), 'running': True, 'last_attempt': iso(now)})
        threading.Thread(target=self._run, daemon=True, name='zzz-official').start()
        return True

    def _run(self):
        try:
            self.collect()
        except Exception as error:
            self.store.set_meta(self.sync_key, {**self.store.get_meta(self.sync_key, {}), 'running': False, 'stage': None, 'waiting': None, 'error': str(error)[:400], 'message': '同步失败，保留上次成功日历'})
        finally:
            self.lock.release()

    @network_job
    def collect(self, now=None):
        progress = SyncProgress(self.store, self.sync_key)
        with progress.active():
            return self._collect(progress, now)

    def _collect(self, progress, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        progress.stage('fetch')
        result = json.loads(self.http.read(API + 'getContentList?' + urllib.parse.urlencode({'iChanId': 296, 'iPageSize': self.config['collection']['page_size'], 'iPage': 1, 'sLangKey': 'zh-cn'})))
        if result.get('retcode') != 0 or not isinstance(result.get('data', {}).get('list'), list):
            raise ValueError('官方公告接口格式发生变化')
        articles = result['data']['list']
        if not articles or len(articles) > self.config['collection']['page_size']:
            raise ValueError('官方公告列表为空或超出请求范围')
        # Keep source bulletins of still-open cross-version events, even after
        # they fall off the latest page. Fresh copies always replace cached IDs.
        cached = self.directory / 'sources/official-news.json'
        fresh_ids = {a['iInfoId'] for a in articles}
        if cached.is_file():
            old = bounded_json(cached).get('articles', [])
            existing = bounded_json(self.directory / 'events.json')
            active = {e['evidence'].get('official_id') for v in existing['versions'] for e in v['events'] if not e['end']['at'] or stamp(e['end']['at']) >= now}
            context = {a['iInfoId'] for a in old if re.search(r'预下载开启&更新通知|更新公告', a['sTitle'])}
            articles += [a for a in old if a['iInfoId'] in active | context and a['iInfoId'] not in fresh_ids]
        if len(articles) > 100:
            raise ValueError('保留公告超出上限')
        warnings = []
        cached_wiki = self.directory / 'sources/wiki-calendar.json'
        # The live Wiki index only adds exactly matched images, so dates are first published
        # from the official bulletins with the last cached index; the live index is read once,
        # in the image stage.
        local_wiki = bounded_json(cached_wiki) if cached_wiki.is_file() else {}
        progress.stage('parse')
        old = [v for v in self.store.versions() if v.get('game') == 'zzz-cn']
        draft = compile_feed(articles, local_wiki, self.config, iso(now))
        with deferring_images():
            self.attach_images(draft)
        versions = self.normalize(draft)
        atomic_json(self.directory / 'sources/official-news.json', {'articles': articles, 'fetched_at': iso(now)})
        atomic_json(self.directory / 'events.json', draft)
        self.store.replace_game_versions('zzz-cn', versions)
        progress.calendar_saved(old, versions)
        progress.stage('images')
        try:
            wiki = json.loads(self.http.read(WIKI + 'api.php?' + urllib.parse.urlencode({'action': 'parse', 'page': '活动一览', 'prop': 'text|revid', 'format': 'json', 'formatversion': 2})))['parse']
            atomic_json(cached_wiki, wiki)
        except (OSError, ValueError, KeyError) as error:
            wiki = local_wiki
            warnings.append('Wiki 暂不可用，继续采用官方公告：' + str(error)[:100])
        feed = compile_feed(articles, wiki, self.config, iso(now))
        image_errors = self.attach_images(feed)
        warnings += image_errors
        versions = self.normalize(feed)
        atomic_json(self.directory / 'events.json', feed)
        self.store.replace_game_versions('zzz-cn', versions)
        progress.images_done(versions, len(image_errors))
        latest = feed['versions'][0]['analysis']['wiki_latest_end']
        if latest and stamp(latest) < now - dt.timedelta(days=14):
            warnings.append('Wiki 最新活动已滞后，未将历史条目当作当前活动。')
        self.store.set_meta(self.sync_key, progress.finish({'running': False, 'last_success': iso(now), 'last_attempt': iso(now), 'mode': 'official', 'error': None, 'message': '绝区零官网公告已同步；Wiki 仅补充完全匹配的头图', 'warnings': warnings}))
        return feed

    def attach_images(self, feed):
        errors = []
        for version in feed['versions']:
            for row in version['events']:
                url = row.get('image_url')
                if not url:
                    continue
                try:
                    if not allowed(url, True):
                        raise ValueError('图片来源超出限定 CDN')
                    key = hashlib.sha256(url.encode()).hexdigest()[:32]
                    path = next(iter(sorted((self.directory / 'assets').glob(key + '.*'))), None)
                    if not path:
                        data = self.http.read(url, image=True)
                        path = local_file(self.directory, 'assets/' + key + '.' + image_kind(data))
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temp = path.with_suffix(path.suffix + '.tmp')
                        temp.write_bytes(data)
                        temp.replace(path)
                    row['artwork'] = {'file': 'assets/' + path.name, 'source_url': row['image_source']}
                except (OSError, ValueError) as error:
                    errors.append(row['title'] + '：' + str(error)[:100])
            version['analysis']['artwork_count'] = sum(bool(e.get('artwork')) for e in version['events'])
        return errors
