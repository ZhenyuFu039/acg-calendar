import copy
import json
from pathlib import Path
import urllib.parse
import unittest

from acg_calendar.artwork import ArtworkCache
from acg_calendar.briefing import BriefingReader, briefing_link, merge_briefing, parse_bundle
from acg_calendar.collector import Collector
from acg_calendar.official import official_url, OfficialRedirect
from acg_calendar.parser import ROOT, resolve_versions
from test_artwork import PNG
from test_calendar import TempStoreTest, version, TEXT, META

PAGE = 'https://endfield.hypergryph.com/version_briefing/latest?source_from=official'
SCRIPT = 'https://web.hycdn.cn/endfield/webview/_version_briefing/v1d6/version-v1d6.fixture.js'
NEWS = {'cid': '4774', 'title': '「丹青渡」新版本导览专题网页上线', 'displayTime': 1791289380}
DETAIL = 'https://endfield.hypergryph.com/news/{cid}'
NOW = '2026-10-19T01:00:00Z'
FIXTURE = json.loads((ROOT / 'tests/fixtures/official-briefing-v1d6.json').read_text(encoding='utf-8'))


def bundle(name='丹青渡', tag='v1d6'):
    config = {'meta': {'version': tag}, 'gameplay': []}
    asset_rows = []
    for index, row in enumerate(FIXTURE['entries']):
        key = f'gameplay.{index}.thumbnail'
        config['gameplay'].append({'id': row['id'], 'name': {'zh-cn': row['title']}, 'timeByServer': {'cn': row['cn'], 'us': {'openTime': '2000/1/1 00:00:00'}}, 'thumbnail': {'assetKey': key}})
        filename = Path(urllib.parse.urlsplit(row['thumbnail_url']).path).name
        asset_rows.append(json.dumps(key) + ':x.p+' + json.dumps('assets/imgs/' + filename))
    label = {'page.title': {'zh-cn': '《明日方舟：终末地》「' + name + '」新版本导览'}}
    return 'const labels=JSON.parse(' + json.dumps(json.dumps(label)) + ');const payload=JSON.parse(' + json.dumps(json.dumps(config)) + ');const images={' + ','.join(asset_rows) + '};'


def guide():
    data = parse_bundle(bundle(), SCRIPT)
    data.update(source_url=PAGE, news_url=DETAIL.format(cid='4774'), news_cid='4774', fetched_at=NOW)
    return data


def source_fetcher(calls, *, rolled=False, only_preview=False):
    def fetch(url):
        calls.append(url)
        if 'api/bulletin' in url:
            articles = [NEWS] if only_preview else [{'cid': '6094', 'title': META['title'], 'displayTime': 1792026000}, NEWS]
            return json.dumps({'code': 0, 'data': {'list': articles}})
        if url.endswith('/news/6094'):
            return '<article>' + ''.join('<p>' + line + '</p>' for line in TEXT.splitlines()) + '</article>'
        if url.endswith('/news/4774'):
            return '<article><a href="' + PAGE + '">专题入口</a></article>'
        if url == PAGE:
            return '<script src="https://third-party.example/tracker.js"></script><script src="' + SCRIPT + '"></script>'
        if url == SCRIPT:
            return bundle('下一版本' if rolled else '丹青渡', 'v1d7' if rolled else 'v1d6')
        raise AssertionError('Unexpected request: ' + url)
    return fetch


class BriefingParseTests(unittest.TestCase):
    def test_official_config_contains_cn_activity_times_and_assets_without_ocr(self):
        parsed = parse_bundle(bundle(), SCRIPT)
        self.assertEqual(parsed['name'], '丹青渡')
        self.assertEqual(len(parsed['entries']), 9)
        carly = next(row for row in parsed['entries'] if '卡莉' in row['title'])
        self.assertEqual(carly['cn']['closeTime'], '2026/11/5 4:00:00')
        self.assertIn('/assets/imgs/thumbnail.', carly['thumbnail_url'])

    def test_unsupported_javascript_is_not_executed(self):
        with self.assertRaises(ValueError):
            parse_bundle('JSON.parse((()=>{throw new Error("never execute")})())', SCRIPT)

    def test_news_link_rejects_third_party_and_credentials(self):
        for href in ('https://endsync.vercel.app/version_briefing/latest', 'https://attacker@endfield.hypergryph.com/version_briefing/latest', 'javascript:alert(1)'):
            with self.assertRaises(ValueError): briefing_link('<a href="' + href + '">link</a>', DETAIL.format(cid='4774'))

    def test_known_official_sources_only_including_redirects(self):
        self.assertTrue(official_url(SCRIPT))
        self.assertFalse(official_url('https://endsync.vercel.app/zh/events/'))
        self.assertFalse(official_url('https://web.hycdn.cn/unrelated/file.js'))
        with self.assertRaises(ValueError): OfficialRedirect().redirect_request(None, None, 302, '', {}, 'https://third-party.example/')


class BriefingIntegrationTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.art = ArtworkCache(self.directory, lambda url: PNG)
        self.data = version()

    def test_merge_matches_nine_configs_to_ten_announcement_rows_and_preserves_ids(self):
        ids = [e['id'] for e in self.data['events']]
        merge_briefing(self.data, guide(), self.art)
        self.assertEqual(self.data['briefing']['matched_events'], 10)
        self.assertEqual(self.data['briefing']['unmatched_activities'], [])
        self.assertEqual(self.data['artwork_summary']['matched'], 10)
        self.assertEqual(ids, [e['id'] for e in self.data['events']])

    def test_after_maintenance_and_before_update_do_not_become_exact_deadlines(self):
        merge_briefing(self.data, guide(), self.art)
        inn = next(e for e in self.data['events'] if '烟火生缘' in e['title'])
        self.assertEqual(inn['start']['state'], 'estimated')
        self.assertEqual(inn['start']['at'], '2026-10-15T04:00:00Z')
        self.assertIsNone(inn['end']['at'])
        self.assertEqual(inn['end']['reference'], 'next_maintenance_start')

    def test_conflicting_precise_time_keeps_announcement_and_records_both_values(self):
        current = guide()
        row = next(e for e in current['entries'] if '卡莉' in e['title'])
        row['cn']['closeTime'] = '2026/11/5 23:59:59'
        merge_briefing(self.data, current, self.art)
        event = next(e for e in self.data['events'] if '卡莉' in e['title'])
        self.assertEqual(event['end']['at'], '2026-11-04T20:00:00Z')
        self.assertEqual(event['briefing']['conflicts'][0]['role'], 'end')
        self.assertIn('不一致', event['warnings'][-1])

    def test_explicit_supplement_only_fills_unpublished_interval_and_manual_override_wins(self):
        event = next(e for e in self.data['events'] if '卡莉' in e['title'])
        event['end'].update(at=None, state='unknown', reference='unpublished')
        merge_briefing(self.data, guide(), self.art)
        self.assertEqual(event['end']['reference'], 'briefing_explicit')
        self.assertEqual(event['end']['at'], '2026-11-04T20:00:00Z')
        resolved = resolve_versions([self.data], {event['id']: {'end_override': '2026-11-06T00:00:00Z', 'completed': True}})[0]
        personal = next(e for e in resolved['events'] if e['id'] == event['id'])
        self.assertTrue(personal['completed'])
        self.assertEqual(personal['end']['state'], 'manual')

    def test_permanent_parent_launch_date_does_not_replace_new_season_times(self):
        before = {e['id']: copy.deepcopy(e['start']) for e in self.data['events'] if e.get('parent_title') and '战争回响' in e['parent_title']}
        merge_briefing(self.data, guide(), self.art)
        self.assertEqual(len(before), 2)
        for e in self.data['events']:
            if e['id'] in before:
                self.assertEqual(e['start'], before[e['id']])
                self.assertEqual(e['briefing']['match'], 'parent_activity')

    def test_asset_network_failure_keeps_all_times_and_rows(self):
        before = [(e['id'], copy.deepcopy(e['end'])) for e in self.data['events']]
        self.art.downloader = lambda url: (_ for _ in ()).throw(OSError('offline'))
        merge_briefing(self.data, guide(), self.art)
        self.assertEqual(before, [(e['id'], e['end']) for e in self.data['events']])
        self.assertGreater(self.data['briefing']['image_errors'], 0)

    def test_latest_rollover_rejected_while_original_snapshot_is_preserved(self):
        reader = BriefingReader(self.directory, source_fetcher([]))
        original = reader.read(NEWS, DETAIL, NOW)
        rolled_reader = BriefingReader(self.directory, source_fetcher([], rolled=True))
        # Force a new immutable script URL, as a real new version would use.
        fetch = rolled_reader.fetcher
        rolled_reader.fetcher = lambda url: fetch(url).replace(SCRIPT, SCRIPT.replace('fixture', 'next')) if url == PAGE else bundle('下一版本', 'v1d7') if url.endswith('version-v1d6.next.js') else fetch(url)
        with self.assertRaisesRegex(ValueError, '切换版本'):
            rolled_reader.read(NEWS, DETAIL, NOW)
        self.assertEqual(rolled_reader.cached()[0]['script_sha256'], original['script_sha256'])

    def test_same_hashed_bundle_is_reused_without_redownload(self):
        calls = []
        first = BriefingReader(self.directory, source_fetcher(calls))
        first.read(NEWS, DETAIL, NOW)
        calls.clear()
        second = BriefingReader(self.directory, source_fetcher(calls))
        result = second.read(NEWS, DETAIL, NOW)
        self.assertTrue(result['cache_reused'])
        self.assertNotIn(SCRIPT, calls)

    def test_collector_discovers_both_sources_and_preserves_personal_state(self):
        self.store.save_version(self.data)
        event = next(e for e in self.data['events'] if '卡莉' in e['title'])
        self.store.update_state(event['id'], {'completed': True, 'notes': '我的计划'})
        calls = []
        collector = Collector(self.store, self.directory, fetcher=source_fetcher(calls))
        collector.artwork = self.art
        output = collector.collect()[0]
        self.assertEqual(output['briefing']['matched_events'], 10)
        self.assertIn(PAGE, calls)
        self.assertFalse(any('third-party.example' in url for url in calls))
        self.assertEqual(self.store.state()[event['id']]['notes'], '我的计划')
        self.assertEqual(self.store.get_meta('sync')['briefing_count'], 1)

    def test_guide_can_be_cached_before_update_announcement_is_published(self):
        collector = Collector(self.store, self.directory, fetcher=source_fetcher([], only_preview=True))
        collector.artwork = self.art
        self.assertEqual(collector.collect(), [])
        self.assertEqual(self.store.get_meta('briefing_previews')[0]['name'], '丹青渡')
        self.assertEqual(self.store.get_meta('briefing_previews')[0]['activity_count'], 9)

    def test_changed_guide_structure_does_not_discard_successful_patch_notice(self):
        original = source_fetcher([])
        collector = Collector(self.store, self.directory, fetcher=lambda url: 'window.changed=true' if url == SCRIPT else original(url))
        collector.artwork = self.art
        result = collector.collect()
        self.assertEqual(len(result[0]['events']), 24)
        self.assertEqual(result[0]['briefing_status'], 'not_found')
        self.assertEqual(len(self.store.get_meta('sync')['briefing_errors']), 1)
