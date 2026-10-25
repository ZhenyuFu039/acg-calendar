"""Conservative, JSON-driven CN/EN artwork alignment. Never changes event clocks."""
from collections import Counter
import datetime as dt
from html.parser import HTMLParser
import re
import unicodedata
import urllib.parse

from .azurlane_http import allowed

EN = 'https://azurlane.koumakan.jp'
MONTHS = {name.lower(): n for n, name in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'], 1)}
DATE = re.compile(r'(?:(?P<m1>' + '|'.join(MONTHS) + r')\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?|'
                  r'(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m2>' + '|'.join(MONTHS) + r'))'
                  r'(?:,?\s+(?P<year>20\d{2}))?', re.I)


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text)).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


def dates(text, year=None):
    result = []
    for m in DATE.finditer(text):
        y = int(m['year']) if m['year'] else year
        if y is None:
            continue
        try:
            result.append(dt.date(y, MONTHS[(m['m1'] or m['m2']).lower()], int(m['d1'] or m['d2'])))
        except ValueError:
            continue
    return result


class Node:
    def __init__(self, tag='', attrs=None, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, attrs or {}, parent, []

    def text(self):
        return ' '.join(x.text() if isinstance(x, Node) else x for x in self.children).strip()

    def nodes(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.nodes(tag)

    def ancestor(self, tag):
        current = self.parent
        while current and current.tag != tag:
            current = current.parent
        return current


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        child = Node(tag, dict(attrs), self.current)
        self.current.children.append(child)
        if tag not in {'img', 'br', 'hr', 'input', 'meta', 'link', 'wbr', 'source'}:
            self.current = child

    def handle_endtag(self, tag):
        current = self.current
        while current.parent:
            if current.tag == tag:
                self.current = current.parent
                return
            current = current.parent

    def handle_data(self, text):
        if self.current.tag not in {'script', 'style'}:
            self.current.children.append(text)


def original_image(url):
    url = urllib.parse.urljoin(EN, url)
    p = urllib.parse.urlsplit(url)
    parts = p.path.split('/')
    if len(parts) == 7 and parts[1:3] == ['images', 'thumb']:
        url = urllib.parse.urlunsplit((p.scheme, p.netloc, '/images/' + '/'.join(parts[3:6]), '', ''))
    return url if allowed(url, True) else None


def discover_pages(html, config, now):
    """Only linked event headings in dated update batches; never crawl all links."""
    doc = Document(html)
    batch, result, seen = None, [], set()
    cutoff = now.date() - dt.timedelta(days=config['discovery']['history_days'])
    for node in doc.root.nodes():
        if node.tag == 'h2':
            found = dates(node.text())
            batch = found[0] if len(found) == 1 else None
        if node.tag != 'b' or not batch or batch < cutoff or batch > now.date() + dt.timedelta(days=7):
            continue
        if not any(node.text().startswith(prefix) for prefix in config['discovery']['heading_prefixes']):
            continue
        for link in node.nodes('a'):
            href = urllib.parse.urlsplit(urllib.parse.urljoin(EN, link.attrs.get('href', '')))
            title = urllib.parse.unquote(href.path.removeprefix('/wiki/')).replace('_', ' ')
            if href.hostname != 'azurlane.koumakan.jp' or not href.path.startswith('/wiki/') or ':' in title or '/' in title:
                continue
            if title not in seen:
                seen.add(title)
                result.append({'title': title, 'batch_date': batch.isoformat()})
            break
    return result[:config['discovery'].get('max_candidates', 24)]


def table_windows(lines):
    """Keep each server/run date pair together; do not mix original and rerun."""
    server, start, result = '', None, []
    for i, line in enumerate(lines):
        if re.search(r'\bServers?\b', line, re.I):
            server, start = line, None
        if normalized(line) in {'startdate', 'rerunstart'} and i + 1 < len(lines):
            found = dates(lines[i + 1])
            start = found[0] if len(found) == 1 else None
        if normalized(line) in {'enddate', 'rerunend'} and i + 1 < len(lines):
            found = dates(lines[i + 1])
            if start and len(found) == 1 and start <= found[0] and ('all servers' in server.lower() or 'en servers' in server.lower()):
                result.append({'start': start.isoformat(), 'end': found[0].isoformat(), 'servers': server})
            start = None
    return result


def page_candidates(page, config):
    """Use a banner's own table, not the first date or image anywhere on a page."""
    doc = Document(page['html'])
    result, seen = [], set()
    for table in doc.root.nodes('table'):
        rows = [r for r in table.nodes('tr') if r.ancestor('table') is table]
        lines = [c.text() for r in rows for c in r.children if isinstance(c, Node) and c.tag in {'th', 'td'}]
        windows = table_windows(lines)
        if not windows:
            continue
        for img in table.nodes('img'):
            if img.ancestor('table') is not table:
                continue
            src = img.attrs.get('src', '')
            filename = urllib.parse.unquote(src.split('/')[-2] if '/thumb/' in src else src.split('/')[-1])
            if not re.search(config['banner_filename_pattern'], filename, re.I):
                continue
            url = original_image(src)
            if not url or url in seen:
                continue
            seen.add(url)
            title = re.sub(config['banner_filename_pattern'], '', filename, flags=re.I).replace('_', ' ')
            # Small event pages contain missions outside the infobox. On large
            # catalog pages do not mix every ship's missions into each candidate.
            context = doc.root.text() if len(list(doc.root.nodes('table'))) < 15 else table.text()
            result.append({'title': title, 'page_title': page['title'], 'image_url': url,
                           'source_url': page['source_url'], 'revision_id': page.get('revision_id'),
                           'windows': windows, 'context': context[:60000]})
    return result


def snapshot_candidates(snapshot, config):
    """Compatibility with previously captured, visibly verified Wiki tables."""
    result = []
    for page in snapshot.get('pages', []):
        if 'candidates' in page:
            result.extend(c for c in page['candidates'] if allowed(c.get('image_url', ''), True))
            continue
        if 'html' in page:
            result.extend(page_candidates(page, config))
            continue
        windows = table_windows([x.strip() for x in re.split(r'[\n\t]+', page.get('table', '')) if x.strip()])
        for img in page.get('images', []):
            url = original_image(img.get('src', ''))
            filename = urllib.parse.unquote((url or '').split('/')[-1])
            title = re.sub(config['banner_filename_pattern'], '', filename, flags=re.I).replace('_', ' ')
            # A second image without its own dates is intentionally not assigned
            # the main event's time window.
            if not url or normalized(title) != normalized(page['title']) or not windows:
                continue
            result.append({'title': title, 'page_title': page['title'], 'image_url': url,
                           'source_url': page['source_url'], 'windows': windows,
                           'context': page.get('table', '')})
    # Same file found in live and historical tables is one candidate, not ambiguity.
    unique = {}
    for row in result:
        key = (normalized(row['title']), row['image_url'])
        if key not in unique:
            unique[key] = row
        else:
            unique[key]['windows'] += [w for w in row['windows'] if w not in unique[key]['windows']]
            if len(row.get('context', '')) > len(unique[key].get('context', '')):
                unique[key]['context'] = row['context']
            if row.get('revision_id'):
                unique[key]['revision_id'] = row['revision_id']
                unique[key]['source_url'] = row['source_url']
    return list(unique.values())


def signatures(text, language, config):
    result = Counter()
    for rule in config['missions']:
        for pattern in rule[language]:
            for m in re.finditer(pattern, text, re.I):
                result[(rule['action'], int(m.groupdict().get('count') or 1))] += 1
    return result


def event_context(row, pages):
    page = next((p for p in pages if p['title'] == row['evidence']['page_title']), None)
    if not page:
        return row['title']
    lines = page['wikitext'].splitlines()
    start = max(0, row['evidence']['line'] - 1)
    end = next((i for i in range(start + 1, len(lines)) if re.match(r'\s*(?:\d+\.\s*|[二三]、)', lines[i])), len(lines))
    # Import locally to avoid an azurlane <-> artwork import cycle.
    from .azurlane import plain
    return plain('\n'.join(lines[start:end]))


def local_day(row, role):
    at = row[role].get('at')
    if not at:
        return None
    return dt.datetime.fromisoformat(at.replace('Z', '+00:00')).astimezone(dt.timezone(dt.timedelta(hours=8))).date()


def bulletin_visual_references(pages, rows, config):
    """Only a uniquely identified footer visual from the event's own bulletin."""
    references = {}
    if not config.get('enabled'):
        return references
    for page in pages:
        source = page['wikitext']
        marker = config['footer_marker']
        if marker not in source:
            continue
        footer = source.rsplit(marker, 1)[1]
        files = list(dict.fromkeys(m['file'].strip() for m in re.finditer(config['file_pattern'], footer, re.I)))
        page_rows = [r for r in rows if r['evidence']['page_title'] == page['title'] and not r.get('parent_id')]
        if config.get('require_activity_title', True):
            files = [f for f in files if any(len(normalized(r['title'])) >= 4 and
                     normalized(r['title']) in normalized(f.rsplit('.', 1)[0]) for r in page_rows)]
        if len(files) != 1:
            continue
        references[page['title']] = {'file': files[0], 'page_title': page['title'], 'revision_id': page['revid']}
    return references


def apply_bulletin_visuals(feed, references, media):
    count = 0
    for version in feed['versions']:
        for row in version['events']:
            ref = references.get(row['evidence']['page_title'])
            image = media.get(ref['file']) if ref else None
            if row.get('image_url') or not image:
                continue
            proof = {**ref, 'file_url': image.get('descriptionurl'), 'shared': True,
                     'role': 'update_bulletin_visual', 'image_url': image['url']}
            row.update(image_url=image['url'], image_source=row['evidence']['revision_url'],
                       image_match='cn_bulletin_shared_visual', image_bulletin_visual=proof)
            row['description'] += '\n头图为本次国服更新公告的共用主视觉，并非该条目的独立宣传图。'
            count += 1
        version['analysis']['bulletin_shared_artwork_count'] = sum(
            r.get('image_match') == 'cn_bulletin_shared_visual' for r in version['events'])
    return count


def identity(row, candidate, context, config):
    evidence, points = [], 0
    alias = next((a for a in config['aliases'] if normalized(row['title']) in {normalized(v) for v in a['cn']}
                  and normalized(candidate['title']) in {normalized(v) for v in a['en']}), None)
    if alias:
        evidence.append('known_title_alias')
        points = config['scores']['alias']
    cn, en = signatures(context, 'cn', config), signatures(candidate.get('context', ''), 'en', config)
    shared = sum((cn & en).values())
    union = sum((cn | en).values())
    ratio = shared / union if union else 0
    if shared >= config['minimum_shared_missions'] and ratio >= config['minimum_mission_similarity']:
        evidence.append('mission_signature')
        points = max(points, config['scores']['missions'])
    entities = []
    for item in config['entities']:
        if any(normalized(v) in normalized(context) for v in item['cn']) and any(
                normalized(v) in normalized(candidate.get('context', '')) for v in item['en']):
            entities.append(item['id'])
    if len(entities) >= config['minimum_shared_entities']:
        evidence.append('shared_reward_entities')
        points = max(points, config['scores']['entities'])
    kinds = []
    for rule in config['activity_types']:
        a = any(re.search(p, row['title'], re.I) for p in rule['cn'])
        b = any(re.search(p, candidate['title'], re.I) for p in rule['en'])
        if a and b:
            kinds.append(rule['id'])
        elif a != b:
            return {'points': 0, 'evidence': ['activity_type_conflict']}
    if kinds:
        points += config['scores']['structure']
        evidence.append('activity_type:' + ','.join(kinds))
    return {'points': points, 'evidence': evidence, 'shared_missions': shared,
            'mission_similarity': round(ratio, 4), 'entities': entities,
            'identity': bool(alias or 'mission_signature' in evidence or 'shared_reward_entities' in evidence)}


def align_artwork(rows, pages, catalog, config):
    """Return an auditable report. Timing alone and position alone never match."""
    if config.get('schema_version') != 1:
        raise ValueError('不支持的碧蓝航线头图对齐规则版本')
    contexts = {r['id']: event_context(r, pages) for r in rows}
    pairs, offsets = {}, {}
    for row in rows:
        if row.get('parent_id'):
            continue
        start = local_day(row, 'start')
        for i, candidate in enumerate(catalog):
            proof = identity(row, candidate, contexts[row['id']], config)
            pairs[row['id'], i] = proof
            if not start or not proof.get('identity'):
                continue
            end = local_day(row, 'end')
            options = {int((start - dt.date.fromisoformat(w['start'])).days) for w in candidate['windows']
                       if end and abs((end - dt.date.fromisoformat(w['end'])).days -
                                      (start - dt.date.fromisoformat(w['start'])).days) <= config['end_tolerance_days']}
            options = {x for x in options if abs(x) <= config['max_offset_days']}
            if len(options) == 1:
                batch = row['evidence']['page_title']
                offsets.setdefault(batch, set()).update(options)
    report = {'schema_version': 1, 'strategy': 'batch_date_and_independent_identity',
              'english_dates_used_for_schedule': False, 'candidate_count': len(catalog),
              'batch_offsets': {k: list(sorted(v)) for k, v in offsets.items()}, 'results': []}
    assigned = {}
    for row in rows:
        if row.get('parent_id'):
            continue
        start, end = local_day(row, 'start'), local_day(row, 'end')
        batch_offsets = offsets.get(row['evidence']['page_title'], set())
        ranked = []
        for i, candidate in enumerate(catalog):
            proof = pairs[row['id'], i]
            if not proof.get('identity') or not start or not end or len(batch_offsets) != 1:
                continue
            offset = next(iter(batch_offsets))
            compatible = [w for w in candidate['windows'] if
                          abs((start - dt.date.fromisoformat(w['start'])).days - offset) <= config['start_tolerance_days'] and
                          abs((end - dt.date.fromisoformat(w['end'])).days - offset) <= config['end_tolerance_days']]
            if compatible:
                ranked.append({**proof, 'score': proof['points'] + config['scores']['dates'],
                               'candidate': candidate, 'offset_days': offset, 'window': compatible[0]})
        ranked.sort(key=lambda x: x['score'], reverse=True)
        result = {'event_id': row['id'], 'title': row['title'], 'status': 'unmatched'}
        if len(batch_offsets) > 1:
            result['reason'] = 'conflicting_batch_offsets'
        elif ranked and ranked[0]['score'] >= config['minimum_score']:
            best = ranked[0]
            if len(ranked) > 1 and best['score'] - ranked[1]['score'] < config['minimum_margin']:
                result.update(status='ambiguous', reason='candidate_scores_too_close')
            else:
                candidate = best.pop('candidate')
                proof = {k: v for k, v in best.items() if k != 'points'}
                result.update(status='matched', image_url=candidate['image_url'],
                              source_url=candidate['source_url'], candidate_title=candidate['title'],
                              revision_id=candidate.get('revision_id'), **proof)
                assigned[row['id']] = result
        result['candidates_considered'] = len(ranked)
        report['results'].append(result)
    claims = Counter(r['image_url'] for r in assigned.values())
    for key, result in list(assigned.items()):
        if claims[result['image_url']] > 1:
            result.update(status='ambiguous', reason='multiple_cn_events_claim_same_banner')
            del assigned[key]
    # One independently matched image may be inherited only by explicit child IDs.
    for row in rows:
        parent = assigned.get(row.get('parent_id'))
        if parent:
            result = {**parent, 'event_id': row['id'], 'title': row['title'], 'inherited_from': row['parent_id']}
            report['results'].append(result)
            assigned[row['id']] = result
        elif row.get('parent_id'):
            report['results'].append({'event_id': row['id'], 'title': row['title'], 'status': 'unmatched'})
    for row in rows:
        match = assigned.get(row['id'])
        # Explicit CN media wins. Known EN URLs remain a fallback when automatic
        # evidence is insufficient, and are never counted as algorithm successes.
        if match and (not row.get('image_url') or row.get('image_match') == 'verified_cn_en_alias'):
            row.update(image_url=match['image_url'], image_source=match['source_url'],
                       image_match='json_cross_server_alignment', image_alignment=match)
            if '不采用其外服时间' not in row.get('description', ''):
                row['description'] = row.get('description', '') + '\n英文 Wiki 仅补充算法核对头图，不采用其外服时间。图内日期/文案可能属于外服。'
    report['matched'] = sum(r['status'] == 'matched' for r in report['results'])
    report['ambiguous'] = sum(r['status'] == 'ambiguous' for r in report['results'])
    report['unmatched'] = len(rows) - report['matched'] - report['ambiguous']
    return report
