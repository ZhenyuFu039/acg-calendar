"""Match official AK article banners by named section, independently of dates."""
from __future__ import annotations

import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from .artwork import ArtworkCache, official_image
from .official import fetch_text
from .parser import iso, stamp


def normalize(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.casefold())


class ArticleBanners(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current = None
        self.paragraph = []
        self.skip = 0

    def flush(self):
        text = ''.join(self.paragraph).strip()
        if self.current and text and not self.current['heading']:
            self.current['heading'] = text[:600]
        self.paragraph = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript'}:
            self.skip += 1
        if self.skip:
            return
        if tag in {'p', 'h1', 'h2', 'h3'}:
            self.flush()
        if tag == 'img':
            self.flush()
            self.current = None
            if official_image(attrs.get('src')):
                self.current = {'url': attrs['src'], 'heading': ''}
                self.rows.append(self.current)

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in {'p', 'h1', 'h2', 'h3'}:
            self.flush()

    def handle_data(self, data):
        if not self.skip and self.current:
            self.paragraph.append(data)


def parse_banners(html):
    parser = ArticleBanners(); parser.feed(html); parser.flush()
    return list({(r['url'], r['heading']): r for r in parser.rows if r['heading']}.values())


def article_id(event):
    value = event.get('prts', {}).get('official_cid')
    if value and re.fullmatch(r'\d{1,10}', str(value)):
        return str(value)
    match = re.search(r'(?:^|\n)\s*\|\s*官网链接\s*=\s*(\d{1,10})\s*(?:\n|\})', event.get('source', {}).get('block_text', ''))
    return match.group(1) if match else None


def match_banner(event, rows):
    title = re.sub(r'\s*·\s*(?:奖励兑换|寻访)$', '', event['title'])
    target = normalize(title)
    if len(target) < 2:
        return None
    gacha = event.get('prts', {}).get('role') == 'gacha'
    matches = []
    for row in rows:
        heading = row['heading']
        if target not in normalize(heading):
            continue
        if gacha and '寻访' not in heading:
            continue
        if not gacha and re.search(r'寻访|时装|礼包|家具上架|信赖获取', heading):
            continue
        if not re.search(r'活动|寻访|作战|签到', heading):
            continue
        matches.append(row)
    return matches[0] if len(matches) == 1 else None


class OfficialArknightsArtwork:
    def __init__(self, data_dir: Path, fetcher=fetch_text, cache=None, max_articles=6):
        self.directory = data_dir / 'sources' / 'arknights-official'
        self.fetcher = fetcher
        self.cache = cache or ArtworkCache(data_dir)
        self.max_articles = max_articles

    def read(self, cid, now):
        file = self.directory / (cid + '.json')
        previous = None
        try:
            previous = json.loads(file.read_text(encoding='utf-8'))
            if previous.get('cid') != cid or not isinstance(previous.get('banners'), list):
                previous = None
            if previous and previous.get('checked_at') and 0 <= (now - stamp(previous['checked_at'])).total_seconds() < 86400:
                return previous['banners'], False
        except (OSError, ValueError, KeyError, TypeError):
            previous = None
        url = 'https://ak.hypergryph.com/news/' + cid
        try:
            banners = parse_banners(self.fetcher(url))
            if not banners:
                raise ValueError('官方公告中未找到具名横幅')
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = file.with_suffix('.tmp')
            temporary.write_text(json.dumps({'cid': cid, 'url': url, 'checked_at': iso(now), 'banners': banners}, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(file)
            return banners, False
        except Exception:
            if previous and isinstance(previous.get('banners'), list):
                return previous['banners'], True
            raise

    def attach(self, version):
        if version.get('game') != 'arknights-cn':
            return
        now = dt.datetime.now(dt.timezone.utc)
        events = version['events']
        ids_by_page = {e.get('prts', {}).get('page'): article_id(e) for e in events if article_id(e)}
        ordered = sorted(events, key=lambda e: (bool(e['end']['at']) and stamp(e['end']['at']) < now, -(stamp(e['start']['at']).timestamp() if e['start']['at'] else 0)))
        cids = list(dict.fromkeys(article_id(e) or ids_by_page.get(e.get('prts', {}).get('page')) for e in ordered))
        cids = [cid for cid in cids if cid][:self.max_articles]
        articles, errors = {}, []
        for cid in cids:
            try:
                rows, stale = self.read(cid, now)
                articles[cid] = rows
                if stale:
                    errors.append('官网公告 ' + cid + ' 连接失败，使用已缓存横幅索引')
            except Exception as error:
                errors.append('官网头图 ' + cid + '：' + str(error)[:160])
        for event in events:
            cid = article_id(event) or ids_by_page.get(event.get('prts', {}).get('page'))
            banner = match_banner(event, articles.get(cid, []))
            if not banner:
                continue
            try:
                key = self.cache.cache(banner['url'])
                if key:
                    event['artwork'] = {'key': key, 'path': '/api/artwork/' + key, 'url': banner['url'], 'source_url': 'https://ak.hypergryph.com/news/' + cid, 'source_title': banner['heading'], 'match': 'official_activity_heading', 'shared_activity': event.get('prts', {}).get('role') == 'exchange'}
            except Exception as error:
                errors.append('官网图片未缓存：' + str(error)[:160])
        version['official_artwork'] = {'checked_at': iso(now), 'articles': len(articles), 'matched_events': sum(e.get('artwork', {}).get('match') == 'official_activity_heading' for e in events), 'errors': errors}
