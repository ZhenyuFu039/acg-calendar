"""CN bulletin scheduling; optional JSON-aligned English Wiki artwork."""
import copy
import datetime as dt
import hashlib
from html import unescape
import json
import re
import threading
import urllib.parse

from .artwork import image_kind
from .azurlane_http import AzurLaneHTTP, allowed
from .azurlane_artwork import (EN, align_artwork, discover_pages, snapshot_candidates, page_candidates,
                               bulletin_visual_references, apply_bulletin_visuals)
from .gamekee import atomic_json
from .network import deferring_images, network_job
from .parser import iso, stamp, maintenance_start_estimate
from .plugin_media import local_file
from .providers import JsonProvider, bounded_json
from .sync_progress import SyncProgress

CN = 'https://wiki.biligame.com/blhx/'


def page_url(title):
    return CN + urllib.parse.quote(title, safe='')


def plain(value):
    value = re.sub(r'<s\b[^>]*>.*?</s>', '', value, flags=re.S)
    value = re.sub(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', lambda m: m[2] or m[1], value)
    value = re.sub(r'\{\{(?:小图标2?|道具2?)\|([^{}|]+)(?:\|[^{}]*)?\}\}', r'\1', value)
    value = re.sub(r'<[^>]+>', '', value)
    return unescape(value.replace("'''", '').replace("''", '')).strip()


def calendar_titles(text, rules, now):
    result = []
    cutoff = now.date() - dt.timedelta(days=rules['collection']['history_days'])
    for title in re.findall(r'\[\[(20\d{2}年\d{1,2}月\d{1,2}日\d{1,2}:\d{2}港区改建)(?:\||\]\])', text):
        y, m, d = map(int, re.findall(r'\d+', title)[:3])
        if dt.date(y, m, d) >= cutoff and title not in result:
            result.append(title)
    if not result or len(result) > rules['collection']['max_announcements']:
        raise ValueError('国服改建目录为空或超出采集范围，保留上次日历')
    return result


class BulletinParser:
    def __init__(self, rules):
        self.rules = rules
        self.dates = re.compile(rules['time']['date_pattern'])
        self.zone = dt.timezone(dt.timedelta(hours=8))

    def maintenance(self, page):
        year = int(page['title'][:4])
        m = re.search(r'司令部将于(\d{1,2})月(\d{1,2})日(\d{1,2}):(\d{2})[~～](\d{1,2}):(\d{2})', plain(page['wikitext']))
        if not m:
            raise ValueError('未识别国服公告维护时间：' + page['title'])
        month, day, h, minute, endh, endm = map(int, m.groups())
        start = dt.datetime(year, month, day, h, minute, tzinfo=self.zone)
        end = start.replace(hour=endh, minute=endm)
        if end <= start:
            end += dt.timedelta(days=1)
        return {'date': start.date().isoformat(), 'start': iso(start), 'planned_end': iso(end)}

    def endpoint(self, match, year, maintenance, role):
        fields = match.groupdict()
        date = dt.date(year, int(fields['month']), int(fields['day']))
        reference = fields.get('reference')
        if reference:
            item = maintenance.get(date.isoformat())
            after = '后' in reference
            at = item.get('planned_end' if after else 'start') if item else None
            if not at and not after and role == 'end':
                return maintenance_start_estimate(match[0], year, self.zone, self.rules['time'].get('provisional_maintenance_hour', 10))
            return {'at': at, 'state': ('estimated' if after else 'exact') if at else 'unknown', 'raw': match[0], 'precision': 'minute', 'reference': None}
        if fields.get('hour') is None:
            return {'at': None, 'state': 'unknown', 'raw': match[0], 'precision': 'day', 'reference': None}
        value = dt.datetime(year, date.month, date.day, int(fields['hour']), int(fields['minute']), int(fields.get('second') or 0), tzinfo=self.zone)
        return {'at': iso(value), 'state': 'exact', 'raw': match[0], 'precision': 'second' if fields.get('second') else 'minute', 'reference': None}

    def window(self, text, year, maintenance, reference_month=1):
        matches = list(self.dates.finditer(text))
        if len(matches) != 2:
            return None
        if reference_month >= 11 and int(matches[0]['month']) <= 2:
            year += 1
        end_year = year + (int(matches[1]['month']) < int(matches[0]['month']))
        return self.endpoint(matches[0], year, maintenance, 'start'), self.endpoint(matches[1], end_year, maintenance, 'end')

    def parse(self, page, maintenance):
        result, main, section = [], None, None
        in_content = False
        for line_no, raw in enumerate(page['wikitext'].splitlines(), 1):
            text = plain(raw)
            if text.startswith('一、内容新增'):
                in_content = True
                continue
            if re.match(r'[二三]、', text):
                break
            if not in_content:
                continue
            heading = re.match(r'^(\d+)\.\s*(.+)', text)
            if heading:
                section, main = heading[1], None
            if not section:
                continue
            window = self.window(text, int(page['title'][:4]), maintenance, int(re.search(r'年(\d+)月', page['title'])[1]))
            label = re.search(r'开启.*?活动「([^」]+)」', text)
            if '开启新一期「世界巡游」' in text:
                label = re.search(r'「([^」]+)」', text)
            title = label[1] if label else None
            if '「信标·META」限时开放' in text:
                ship = re.search(r'限时开放「([^」]+)」', text)
                title = '信标·META · ' + ship[1] if ship else None
            if '蓝图补完计划「' in text:
                ship = re.search(r'蓝图补完计划「([^」]+)」', text)
                title = '蓝图补完计划 · ' + ship[1] if ship else None
            if '限时兵装训练' in text and '活动时间' in text:
                title = '限时兵装训练'
            if title and window:
                start, end = window
                category = 'gacha' if '建造' in title else 'event'
                title = self.rules.get('title_overrides', {}).get(title + '|' + (start['at'] or ''), title)
                event = {'id': f"page-{page['pageid']}-section-{section}", 'title': title, 'category': category, 'subtype': '限时建造' if category == 'gacha' else '国服限时活动', 'start': start, 'end': end, 'source_url': page_url(page['title']), 'description': '', 'evidence': {'page_title': page['title'], 'revision_id': page['revid'], 'revision_url': CN + 'index.php?' + urllib.parse.urlencode({'title': page['title'], 'oldid': page['revid']}), 'line': line_no, 'time_text': text, 'timezone': 'Asia/Shanghai'}, 'warnings': []}
                if start['state'] == 'estimated':
                    event['warnings'].append('开始为公告预计维护结束，实际开服请以游戏通知为准')
                if end['state'] == 'unknown':
                    event['warnings'].append('截止为' + end['raw'] + '，尚无该次维护的明确时刻；不触发精确截止提醒')
                if end.get('reference') == 'maintenance_start_fallback':
                    event['warnings'].append('截止暂按该维护日国服时间 10:00（预计），具体活动情况请参考游戏官方时间；不触发精确截止提醒')
                result.append(event)
                main = event
            elif main and text.startswith('兑换开放时间：') and window:
                result.append({**copy.deepcopy(main), 'id': main['id'] + '-shop', 'parent_id': main['id'], 'title': main['title'] + ' · 活动商店', 'category': 'exchange', 'subtype': '奖励兑换', 'start': window[0], 'end': window[1], 'evidence': {**main['evidence'], 'line': line_no, 'time_text': text}})
            elif main and '奖励领取界面将保留至' in text and not any(e['id'] == main['id'] + '-claim' for e in result):
                matches = list(self.dates.finditer(text))
                if len(matches) == 1:
                    end = self.endpoint(matches[0], int(page['title'][:4]), maintenance, 'end')
                    result.append({**copy.deepcopy(main), 'id': main['id'] + '-claim', 'parent_id': main['id'], 'title': main['title'] + ' · 累计奖励领取', 'category': 'exchange', 'subtype': '奖励领取', 'end': end, 'evidence': {**main['evidence'], 'line': line_no, 'time_text': text}})
        return result


def compile_feed(pages, rules, fetched_at, *, artwork_catalog=None, alignment_rules=None):
    parser = BulletinParser(rules)
    maintenance = {value['date']: value for page in pages for value in [parser.maintenance(page)]}
    rows = [event for page in pages for event in parser.parse(page, maintenance)]
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('未识别有效活动或活动 ID 重复')
    for row in rows:
        evidence = row['evidence']
        notes = ['国服公告时间：' + evidence['time_text']]
        mapping = next((m for m in rules['artwork'] if row['title'] == m['title'] and row['start']['raw'].startswith(m.get('start_prefix', ''))), None)
        if mapping:
            row['image_url'] = mapping['image_url']
            row['image_source'] = mapping['source_url']
            row['image_match'] = 'verified_cn_en_alias' if mapping.get('language') == 'en' else 'exact_cn_activity'
            if mapping.get('language') == 'en':
                notes.append('英文 Wiki 仅补充已核对头图，不采用其外服时间。图内日期/文案可能属于外服。')
        row['description'] = '\n'.join(notes)
    analysis = {'strategy': rules['strategy'], 'ai_used': False, 'event_count': len(rows), 'announcements': len(pages), 'fetched_at': fetched_at, 'timezone': 'Asia/Shanghai', 'english_dates_used': False}
    if artwork_catalog is not None and alignment_rules:
        analysis['artwork_alignment'] = align_artwork(rows, pages, artwork_catalog, alignment_rules)
    return {'schema_version': 1, 'versions': [{'id': 'recent-cn-activities', 'name': '国服近期活动', 'source_url': rules['sources']['calendar_url'], 'events': rows, 'analysis': analysis}]}


class AzurLaneProvider(JsonProvider):
    def __init__(self, directory, manifest, store, http=None):
        super().__init__(directory, manifest, store)
        self.config = bounded_json(local_file(directory, manifest['source'].get('rules', 'rules.json')))
        if self.descriptor['id'] != 'azurlane-cn' or self.descriptor['timezone'] != 'Asia/Shanghai' or self.config.get('schema_version') != 1:
            raise ValueError('碧蓝航线采集引擎仅适用于国服及规则版本 1')
        n = self.config['collection']['max_announcements']
        if type(n) is not int or not 1 <= n <= 16 or not 1 <= self.config['collection']['history_days'] <= 100:
            raise ValueError('Wiki 采集范围超出上限')
        hour = self.config['time'].get('provisional_maintenance_hour', 10)
        if type(hour) is not int or not 0 <= hour <= 23:
            raise ValueError('暂定维护小时应在 0–23 之间')
        self.http = http or AzurLaneHTTP()
        self.alignment = self.config.get('artwork_alignment')
        self.alignment_rules = bounded_json(local_file(directory, self.alignment['rules'])) if self.alignment else None
        self.lock, self.last_attempt = threading.Lock(), None
        self.descriptor.update(plugin_kind='azurlane-wiki-json', source_kind='community_calendar', capabilities=['sync', 'rules', 'artwork'])

    def rules(self):
        return copy.deepcopy(self.config)

    def normalize(self, feed):
        versions = super().normalize(feed)
        for version, raw in zip(versions, feed['versions']):
            version['analysis'] = raw.get('analysis', version['analysis'])
            for event, row in zip(version['events'], raw['events']):
                evidence = row.get('evidence', {})
                if not isinstance(evidence, dict):
                    raise ValueError('国服公告依据格式无效')
                event['source'].update(kind='azurlane_wiki', title=evidence.get('page_title', self.descriptor['source_label']), time_text=evidence.get('time_text', ''), time_label='国服公告时间', block_text=evidence.get('time_text', ''), line=evidence.get('line'), evidence=evidence)
                warnings = row.get('warnings', [])
                if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
                    raise ValueError('公告警告应为文本数组')
                event['warnings'] = warnings
                for role in ('start', 'end'):
                    event[role]['raw'] = str(row[role].get('raw', ''))[:2000]
                    reference = row[role].get('reference')
                    if reference not in {None, 'maintenance_start_fallback'}:
                        raise ValueError('不支持的国服公告时间关联')
                    event[role]['reference'] = reference
                if event.get('artwork'):
                    event['artwork'].update(match=row.get('image_match', 'plugin_local_asset'), source_title=row['title'])
                    if row.get('image_alignment'):
                        event['artwork']['alignment'] = copy.deepcopy(row['image_alignment'])
                    if row.get('image_bulletin_visual'):
                        event['artwork'].update(shared_update=True, bulletin_visual=copy.deepcopy(row['image_bulletin_visual']))
        return versions

    def seed(self):
        if any(v.get('game') == self.descriptor['id'] for v in self.store.versions()):
            return
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
        threading.Thread(target=self._run, daemon=True, name='azurlane-wiki').start()
        return True

    def _run(self):
        try:
            self.collect()
        except Exception as error:
            self.store.set_meta(self.sync_key, {**self.store.get_meta(self.sync_key, {}), 'running': False, 'stage': None, 'waiting': None, 'error': str(error)[:400], 'message': '同步失败，保留上次成功日历'})
        finally:
            self.lock.release()

    def api(self, params):
        result = json.loads(self.http.read(CN + 'api.php?' + urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': 2})))
        if 'error' in result:
            raise ValueError('Wiki 接口错误：' + str(result['error'].get('code')))
        return result

    def english_page(self, title):
        url = EN + '/w/api.php?' + urllib.parse.urlencode({'action': 'parse', 'page': title,
            'prop': 'text|revid', 'format': 'json', 'formatversion': 2})
        payload = json.loads(self.http.read(url))
        page = payload.get('parse', {}) if isinstance(payload, dict) else {}
        if not isinstance(page, dict) or not isinstance(page.get('text'), str) or not isinstance(page.get('revid'), int) or not isinstance(page.get('title'), str):
            raise ValueError('英文 Wiki 未返回有效活动页面；保留头图缓存')
        return {'title': page['title'], 'html': page['text'], 'revision_id': page['revid'],
                'source_url': EN + '/wiki/' + urllib.parse.quote(page['title'].replace(' ', '_'), safe='')}

    def artwork_catalog(self, now, online=True):
        """Best-effort optional media refresh, using the host's shared limiter.

        With online=False only the local snapshots are used, so the calendar is not held back.
        """
        if not self.alignment:
            return None, []
        snapshots, warnings = [], []
        for key in ('snapshot', 'live_snapshot'):
            path = local_file(self.directory, self.alignment[key])
            if path.is_file():
                try:
                    snapshots.append(bounded_json(path))
                except (OSError, ValueError) as error:
                    warnings.append('头图缓存不可读：' + str(error)[:100])
        cached = {p['title']: p for s in snapshots for p in s.get('pages', []) if 'html' in p or 'candidates' in p}
        if not online:
            return snapshot_candidates({'pages': [p for s in snapshots for p in s.get('pages', [])]}, self.alignment_rules), warnings
        try:
            index = self.english_page('Recent_News')
            targets = discover_pages(index['html'], self.alignment_rules, now)
            if not targets:
                raise ValueError('英文 Wiki 更新目录没有识别到活动；保留头图缓存')
            targets.sort(key=lambda p: cached.get(p['title'], {}).get('fetched_at', ''))
            changed = False
            for target in targets[:self.alignment_rules['discovery']['max_pages']]:
                try:
                    page = self.english_page(target['title'])
                    candidates = page_candidates(page, self.alignment_rules)
                    if not candidates:
                        warnings.append('英文专题尚无可用的独立头图与日期：' + target['title'])
                        # Record negative checks too, so incomplete pages do not
                        # consume every future round and starve later candidates.
                        cached[page['title']] = {**cached.get(page['title'], {
                            'title': page['title'], 'source_url': page['source_url'],
                            'revision_id': page['revision_id'], 'candidates': []}), 'fetched_at': iso(now)}
                        changed = True
                        continue
                    page = {k: v for k, v in page.items() if k != 'html'}
                    page.update(candidates=candidates, batch_date=target['batch_date'], fetched_at=iso(now))
                    cached[page['title']] = page
                    changed = True
                except (OSError, ValueError) as error:
                    warnings.append('英文头图采集暂停，沿用缓存：' + str(error)[:150])
                    break
            if changed:
                latest = sorted(cached.values(), key=lambda p: p.get('fetched_at', ''), reverse=True)[:32]
                snapshot = {'schema_version': 1, 'fetched_at': iso(now), 'index_revision': index['revision_id'], 'pages': latest}
                while len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > 1_500_000 and latest:
                    latest.pop()
                atomic_json(local_file(self.directory, self.alignment['live_snapshot']), snapshot)
                snapshots.append(snapshot)
        except (OSError, ValueError) as error:
            warnings.append('英文头图来源暂不可用，沿用缓存：' + str(error)[:150])
        return snapshot_candidates({'pages': [p for s in snapshots for p in s.get('pages', [])]}, self.alignment_rules), warnings

    def attach_bulletin_visuals(self, feed, pages, online=True):
        config = self.config.get('bulletin_artwork', {})
        rows = [r for v in feed['versions'] for r in v['events']]
        references = bulletin_visual_references(pages, rows, config)
        if not references:
            return []
        cache_path = local_file(self.directory, config['cache'])
        warnings = []
        try:
            media = bounded_json(cache_path).get('files', {}) if cache_path.is_file() else {}
        except (OSError, ValueError):
            media = {}
        if not isinstance(media, dict):
            media = {}
        def valid(image):
            if not isinstance(image, dict) or any(type(image.get(k)) not in (int, float) for k in ('width', 'height', 'size')):
                return False
            width, height = image.get('width', 0), image.get('height', 0)
            return (allowed(image.get('url', ''), True) and width >= config['minimum_width'] and height > 0 and
                    config['minimum_aspect_ratio'] <= width / height <= config['maximum_aspect_ratio'] and
                    0 < image.get('size', 0) <= 5_000_000)
        files = list(dict.fromkeys(r['file'] for r in references.values()))
        needed = [f for f in files if not valid(media.get(f, {}))]
        if needed and online:
            try:
                result = self.api({'action': 'query', 'titles': '|'.join('File:' + f for f in needed),
                                   'prop': 'imageinfo', 'iiprop': 'url|size'})
                for page in result.get('query', {}).get('pages', []):
                    name = page.get('title', '').split(':', 1)[-1]
                    info = next(iter(page.get('imageinfo', [])), {})
                    if name in needed and valid(info):
                        media[name] = info
                atomic_json(cache_path, {'schema_version': 1, 'files': {f: media[f] for f in files if f in media}})
            except (OSError, ValueError) as error:
                warnings.append('公告共用头图暂不可用：' + str(error)[:150])
        apply_bulletin_visuals(feed, references, {f: media[f] for f in files if valid(media.get(f, {}))})
        missing = [f for f in files if not valid(media.get(f, {}))]
        if missing:
            warnings.append('公告主视觉未取得有效横图：' + '、'.join(missing))
        return warnings

    @network_job
    def collect(self, now=None):
        progress = SyncProgress(self.store, self.sync_key)
        with progress.active():
            return self._collect(progress, now)

    def _collect(self, progress, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        progress.stage('fetch')
        index = self.api({'action': 'parse', 'page': '游戏活动表', 'prop': 'wikitext|revid'})['parse']
        titles = calendar_titles(index['wikitext'], self.config, now)
        pages = []
        for title in titles:
            page = self.api({'action': 'parse', 'page': title, 'prop': 'wikitext|revid'})['parse']
            if page['title'] != title:
                raise ValueError('国服公告发生重定向，待人工核对')
            pages.append(page)
        # A long META/season may remain open after its bulletin leaves the index
        # lookback. Keep that cached bulletin until its announced deadline passes.
        cached = local_file(self.directory, 'sources/cn-bulletins.json')
        if cached.is_file():
            parser = BulletinParser(self.config)
            for page in bounded_json(cached).get('pages', []):
                if page['title'] in titles:
                    continue
                own = parser.maintenance(page)
                for event in parser.parse(page, {own['date']: own}):
                    end = event['end']
                    matches = list(parser.dates.finditer(end.get('raw', '')))
                    deadline = stamp(end['at']).date() if end.get('at') else dt.date(int(page['title'][:4]), int(matches[0]['month']), int(matches[0]['day'])) if matches else None
                    if deadline and deadline >= now.date():
                        pages.append(page)
                        break
        if len(pages) > 24:
            raise ValueError('仍在开放的历史公告超出保留上限，未替换日历')
        progress.stage('parse')
        old = [v for v in self.store.versions() if v.get('game') == self.descriptor['id']]
        # First publish CN dates with cached artwork evidence only; English Wiki pages,
        # image metadata and missing files are fetched afterwards and never delay dates.
        catalog, _ = self.artwork_catalog(now, online=False)
        draft = compile_feed(pages, self.config, iso(now), artwork_catalog=catalog, alignment_rules=self.alignment_rules)
        self.attach_bulletin_visuals(draft, pages, online=False)
        with deferring_images():
            self.attach_images(draft)
        versions = self.normalize(draft)
        atomic_json(self.directory / 'sources/cn-bulletins.json', {'calendar_revision': index['revid'], 'pages': pages, 'fetched_at': iso(now)})
        atomic_json(self.directory / 'events.json', draft)
        self.store.replace_game_versions(self.descriptor['id'], versions)
        progress.calendar_saved(old, versions)
        progress.stage('images')
        catalog, media_warnings = self.artwork_catalog(now)
        feed = compile_feed(pages, self.config, iso(now), artwork_catalog=catalog, alignment_rules=self.alignment_rules)
        media_warnings += self.attach_bulletin_visuals(feed, pages)
        image_errors = self.attach_images(feed)
        errors = media_warnings + image_errors
        versions = self.normalize(feed)
        atomic_json(self.directory / 'events.json', feed)
        self.store.replace_game_versions(self.descriptor['id'], versions)
        progress.images_done(versions, len(image_errors))
        report = feed['versions'][0]['analysis'].get('artwork_alignment')
        media_status = (f"头图自动对齐 {report['matched']} 条，{report['unmatched'] + report['ambiguous']} 条未自动匹配"
                        if report else f'{len(errors)} 张头图暂不可用')
        shared = sum(bool(r.get('artwork')) and r.get('image_match') == 'cn_bulletin_shared_visual'
                     for r in feed['versions'][0]['events'])
        if shared:
            media_status = (f"{sum(bool(r.get('artwork')) for r in feed['versions'][0]['events'])} 条已有头图"
                            f"（含 {shared} 条使用更新主视觉）")
        self.store.set_meta(self.sync_key, progress.finish({'running': False, 'last_success': iso(now), 'last_attempt': iso(now), 'mode': 'bwiki', 'error': None, 'message': '国服公告已同步；' + media_status, 'warnings': errors}))
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
                        temporary = path.with_suffix(path.suffix + '.tmp')
                        temporary.write_bytes(data)
                        temporary.replace(path)
                    row['artwork'] = {'file': 'assets/' + path.name, 'source_url': row['image_source']}
                except (OSError, ValueError) as error:
                    errors.append(row['title'] + '：' + str(error)[:100])
            version['analysis']['artwork_count'] = sum(bool(e.get('artwork')) for e in version['events'])
        return errors
