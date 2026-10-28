import copy
import datetime as dt
import hashlib
import json
import unittest

from acg_calendar.arknights import ArknightsCollector, VERSION_ID, make_version, rules
from acg_calendar.arknights_official import discover, interval, parse_article, reuse_ids
from acg_calendar.parser import ROOT, resolve_versions
from unittest.mock import patch
from test_calendar import TempStoreTest, version as endfield_version

HTML = (ROOT / 'tests/fixtures/arknights-official-preview.html').read_text(encoding='utf-8')
CONFIG = rules()['official']
INDEX = '<a href="/news/111">[活动预告]2026感谢庆典即将开启</a>'
GACHA = '<title>【将雪投火】限时寻访即将开启 - 明日方舟</title><div>2026 // 10 / 24</div><p>活动时间：11月01日 12:00 - 11月15日 03:59</p>'


def events():
    return parse_article(HTML, '111', CONFIG)[0]


def named(rows, name):
    return next(e for e in rows if e['title'] == name)


class MemoryArtwork:
    def __init__(self, callback=None):
        self.calls, self.keys, self.callback = [], set(), callback

    def path(self, key):
        return key in self.keys

    def cache(self, url):
        self.calls.append(url)
        if self.callback:
            self.callback()
        key = hashlib.sha256(url.encode()).hexdigest()[:32] + '.png'
        self.keys.add(key)
        return key


class OfficialParserTests(unittest.TestCase):
    def test_phases_exchange_and_official_timezone(self):
        rows, notes = parse_article(HTML, '111', CONFIG)
        main = named(rows, '时间啊，向前！')
        self.assertEqual(main['start']['at'], '2026-11-01T04:00:00Z')
        self.assertEqual(main['end']['at'], '2026-11-21T19:59:00Z')
        self.assertEqual([p['at'] for p in main['phases']], ['2026-11-08T08:00:00Z', '2026-11-15T08:00:00Z'])
        self.assertEqual(named(rows, '时间啊，向前！ · 奖励兑换')['end']['at'], '2026-11-28T19:59:00Z')
        museum = named(rows, '视相博物馆：演化')
        self.assertEqual(museum['start']['at'], '2026-10-24T13:30:00Z')
        self.assertEqual(named(rows, '视相博物馆：演化 · 奖励兑换')['end']['at'], '2026-11-09T19:59:00Z')
        self.assertEqual(main['source']['kind'], 'official')
        self.assertTrue(main['official']['image_url'].endswith('/story.jpg'))
        self.assertTrue(museum['official']['image_url'].endswith('/museum.jpg'))
        self.assertTrue(any('尚未定时' in w for w in notes))
        self.assertFalse(any('时装' in e['title'] or '恶意活动' in e['title'] for e in rows))

    def test_free_daily_pulls_are_not_a_second_gacha(self):
        rows = events()
        self.assertEqual(named(rows, '将雪投火 · 寻访')['category'], 'gacha')
        self.assertEqual(named(rows, '感谢庆典2026限定寻访每日赠送')['category'], 'event')
        self.assertEqual(named(rows, '感谢庆典2026前瞻签到')['subtype'], '签到')

    def test_separate_date_label_paragraph_keeps_exchange_role(self):
        split = HTML.replace('开放时间：11月01日 12:00 - 11月29日 03:59', '开放时间：</p><p>11月01日 12:00 - 11月29日 03:59')
        rows, _ = parse_article(split, '111', CONFIG)
        self.assertEqual(named(rows, '时间啊，向前！')['end']['at'], '2026-11-21T19:59:00Z')
        self.assertEqual(named(rows, '时间啊，向前！ · 奖励兑换')['end']['at'], '2026-11-28T19:59:00Z')

    def test_start_only_and_maintenance_note_do_not_fabricate_end(self):
        html = '<title>「卫戍协议·规程·序列I」活动开启 - 明日方舟</title><div>2026 // 10 / 24</div><p>活动开启时间：11月08日 16:00（赛季更新时间请参照后续官方公告）</p><p>注意：11月08日 16:00 - 17:00维护，维护后开启，可能提前或延迟。</p>'
        rows, _ = parse_article(html, '333', CONFIG)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start']['at'], '2026-11-08T08:00:00Z')
        self.assertEqual(rows[0]['start']['state'], 'estimated')
        self.assertIsNone(rows[0]['end']['at'])
        self.assertEqual(rows[0]['end']['state'], 'unknown')
        self.assertTrue(rows[0]['warnings'])

    def test_index_accepts_only_official_numeric_links_and_filters_commerce(self):
        html = INDEX + '<a href="https://evil.example/news/222">活动开启</a><a href="/news/abc">活动开启</a><a href="/news/333">时装活动</a>' + INDEX
        self.assertEqual([r['cid'] for r in discover(html, CONFIG)], ['111'])

    def test_next_flight_index_is_json_data_never_executed(self):
        data = {'LATEST': {'list': [{'cid': '111', 'title': '[活动预告]感谢庆典', 'displayTime': 1792845000}, {'cid': '../bad', 'title': '活动'}]}}
        frame = [1, 'e:["$",null,{"initialData": ' + json.dumps(data, ensure_ascii=False) + '}]\n']
        html = '<script>self.__next_f.push(' + json.dumps(frame, ensure_ascii=False) + ')</script>'
        self.assertEqual(discover(html, CONFIG)[0]['url'], 'https://ak.hypergryph.com/news/111')
        with self.assertRaises(ValueError):
            discover('<script>self.__next_f.push(not_json())</script>', CONFIG)

    def test_missing_year_invalid_or_reversed_dates_are_not_guessed(self):
        with self.assertRaises(ValueError):
            parse_article(HTML.replace('2026 // 10 / 24', ''), '111', CONFIG)
        published = dt.datetime(2026, 12, 25)
        self.assertEqual(interval('12月29日 12:00 - 01月05日 03:59', published)[1]['at'], '2027-01-04T19:59:00Z')
        self.assertIsNone(interval('11月05日 12:00 - 11月01日 03:59', published))
        self.assertIsNone(interval('02月30日 12:00 - 03月01日 03:59', published))

    def test_identity_does_not_merge_previous_year_or_ambiguous_records(self):
        event = events()[0]
        old = copy.deepcopy(event); old['id'] = 'last-year'; old.pop('official')
        old['start']['at'] = '2025-11-01T04:00:00Z'; old['end']['at'] = '2025-11-21T19:59:00Z'
        self.assertNotEqual(reuse_ids([copy.deepcopy(event)], [old])[0]['id'], old['id'])
        old['start'] = event['start']; old['end'] = event['end']
        duplicate = {**old, 'id': 'another-old'}
        self.assertNotIn(reuse_ids([copy.deepcopy(event)], [old, duplicate])[0]['id'], {'last-year', 'another-old'})


class OfficialCollectorTests(TempStoreTest):
    def collector(self, responses=None, cache=None):
        responses = responses or {'https://ak.hypergryph.com/news': INDEX, 'https://ak.hypergryph.com/news/111': HTML}
        self.calls = []
        def fetch(url):
            self.calls.append(url)
            response = responses[url]
            if isinstance(response, Exception):
                raise response
            return response
        return ArknightsCollector(self.store, self.directory, official_fetcher=fetch, official_cache=cache or MemoryArtwork())

    def test_default_source_is_official_and_never_contacts_prts(self):
        self.assertEqual(ArknightsCollector(self.store, self.directory).source_mode, 'official')
        result = self.collector().collect()
        self.assertEqual(result['source']['kind'], 'official')
        self.assertEqual(result['id'], VERSION_ID)
        self.assertTrue(all(url.startswith('https://ak.hypergryph.com/') for url in self.calls))
        self.assertEqual(self.store.get_meta('sync:arknights-cn')['source_mode'], 'official')

    def test_dates_are_saved_before_pictures_and_image_outage_is_nonfatal(self):
        def on_image():
            saved = next(v for v in self.store.versions() if v['id'] == VERSION_ID)
            self.assertEqual(named(saved['events'], '视相博物馆：演化')['start']['at'], '2026-10-24T13:30:00Z')
            raise OSError('image offline')
        cache = MemoryArtwork(on_image)
        result = self.collector(cache=cache).collect()
        self.assertEqual(len(cache.calls), 2)
        self.assertTrue(any('image offline' in w for w in result['warnings']))
        self.assertIsNone(self.store.get_meta('sync:arknights-cn')['error'])

    def test_wiki_identity_personal_state_and_other_game_survive_migration(self):
        legacy = copy.deepcopy(named(events(), '视相博物馆：演化'))
        legacy.update(id='saved-prts-id', source={'kind': 'community_wiki'}, prts={'role': 'activity', 'official_cid': '111'})
        legacy.pop('official'); legacy['start']['at'] = '2026-10-24T08:00:00Z'
        old = make_version([legacy], '2026-10-26T00:00:00Z'); self.store.save_version(old)
        endfield = endfield_version(); self.store.save_version(endfield)
        personal = {'completed': True, 'starred': True, 'notes': 'keep', 'start_override': '2026-10-24T14:00:00Z'}
        self.store.update_state(legacy['id'], personal)
        plan = {'id': 'plan-keep', 'title': '游玩安排', 'event_ids': [legacy['id']], 'start': '2026-10-25T14:00:00Z', 'end': '2026-10-25T15:00:00Z'}
        self.store.save_plan(plan)
        result = self.collector().collect()
        museum = named(result['events'], '视相博物馆：演化')
        self.assertEqual(museum['id'], legacy['id'])
        self.assertEqual(museum['start']['at'], '2026-10-24T13:30:00Z')
        self.assertTrue(any('Wiki' in w for w in museum['warnings']))
        self.assertEqual(len(museum['warnings']), len(set(museum['warnings'])))
        self.assertEqual(self.store.state()[legacy['id']]['notes'], 'keep')
        self.assertTrue(self.store.state()[legacy['id']]['completed'])
        self.assertTrue(self.store.state()[legacy['id']]['starred'])
        self.assertEqual(self.store.plans(), [plan])
        self.assertEqual(next(v for v in self.store.versions() if v['id'] == endfield['id']), endfield)
        resolved = named(resolve_versions([result], self.store.state())[0]['events'], '视相博物馆：演化')
        self.assertEqual(resolved['start']['at'], personal['start_override'])
        self.assertEqual(resolved['start']['state'], 'manual')

    def test_preview_and_dedicated_notice_share_one_stable_gacha(self):
        responses = {'https://ak.hypergryph.com/news': INDEX + '<a href="/news/222">【将雪投火】限时寻访即将开启</a>', 'https://ak.hypergryph.com/news/111': HTML, 'https://ak.hypergryph.com/news/222': GACHA}
        collector = self.collector(responses)
        first = collector.collect(); second = collector.collect()
        self.assertEqual(sum(e['title'] == '将雪投火 · 寻访' for e in second['events']), 1)
        self.assertEqual({e['id'] for e in first['events']}, {e['id'] for e in second['events']})
        gacha = named(second['events'], '将雪投火 · 寻访')
        self.assertTrue(gacha['artwork']['url'].endswith('/gacha.jpg'))
        self.assertEqual(gacha['artwork']['source_url'], 'https://ak.hypergryph.com/news/111')

    def test_all_nine_sign_in_banners_fit_official_budget(self):
        html = '<title>签到活动预告 - 明日方舟</title><div>2026 // 10 / 24</div>'
        for i, number in enumerate('一二三四五六七八九'):
            html += f'<img src="https://web.hycdn.cn/upload/image/sign-{i}.jpg"><p>{number}、「签到{i}」签到活动开启</p><p>活动时间：10月24日 21:30 - 11月03日 03:59</p>'
        cache = MemoryArtwork()
        result = self.collector({'https://ak.hypergryph.com/news': INDEX, 'https://ak.hypergryph.com/news/111': html}, cache).collect()
        self.assertEqual(len(result['events']), 9)
        self.assertTrue(all(e.get('artwork') for e in result['events']))
        self.assertEqual(len(cache.calls), 9)
        self.assertEqual(self.store.get_meta('sync:arknights-cn')['pending_images'], 0)

    def test_image_budget_is_shared_and_next_sync_resumes_missing_images(self):
        cache = MemoryArtwork()
        responses = {'https://ak.hypergryph.com/news': INDEX + '<a href="/news/222">【将雪投火】限时寻访即将开启</a>', 'https://ak.hypergryph.com/news/111': HTML, 'https://ak.hypergryph.com/news/222': GACHA}
        collector = self.collector(responses, cache=cache)
        config = rules(); config['official']['max_images'] = 2
        with patch('acg_calendar.arknights.rules', return_value=config):
            first = collector.collect()
            self.assertEqual(len(cache.calls), 2)
            self.assertEqual(self.store.get_meta(collector.meta_key)['pending_images'], 3)
            self.assertTrue(any('待补齐' in note for note in self.store.get_meta(collector.meta_key)['result']['images']['notes']))
            second = collector.collect()
            self.assertEqual(len(cache.calls), 4)
            self.assertEqual(self.store.get_meta(collector.meta_key)['pending_images'], 1)
            added = self.store.get_meta(collector.meta_key)['result']['images']['added']
            self.assertEqual(added, sum(bool(e.get('artwork')) for e in second['events']) - sum(bool(e.get('artwork')) for e in first['events']))
            third = collector.collect()
        self.assertEqual(len(cache.calls), 5)
        self.assertTrue(all(e.get('artwork') for e in third['events']))
        self.assertEqual(self.store.get_meta(collector.meta_key)['pending_images'], 0)

    def test_official_outage_preserves_calendar_and_stops_requests(self):
        collector = self.collector(); collector.collect(); before = self.store.versions()
        failing = self.collector({'https://ak.hypergryph.com/news': INDEX + '<a href="/news/222">另一活动开启</a>', 'https://ak.hypergryph.com/news/111': OSError('timed out')})
        failing.lock.acquire(); failing._run()
        self.assertEqual(self.store.versions(), before)
        self.assertEqual(len(self.calls), 2)
        self.assertIn('timed out', self.store.get_meta(failing.meta_key)['error'])
        self.assertFalse(self.store.get_meta(failing.meta_key)['running'])

    def test_partial_outage_keeps_new_dates_and_historical_cache(self):
        old = copy.deepcopy(events()[0]); old.update(id='cached-old-event', title='历史活动')
        self.store.save_version(make_version([old], '2026-10-26T00:00:00Z'))
        def fetch(url):
            if url.endswith('/news'):
                return INDEX + '<a href="/news/222">另一活动开启</a>'
            if url.endswith('/111'):
                return HTML
            self.assertTrue(any(e['title'] == '视相博物馆：演化' for v in self.store.versions() for e in v['events']))
            raise OSError('second notice offline')
        cache = MemoryArtwork()
        collector = ArknightsCollector(self.store, self.directory, official_fetcher=fetch, official_cache=cache)
        result = collector.collect()
        self.assertIn('cached-old-event', {e['id'] for e in result['events']})
        self.assertIsNotNone(self.store.get_meta(collector.meta_key)['last_success'])
        self.assertTrue(cache.calls)
        self.assertTrue(all(e.get('artwork') for e in result['events'] if e.get('official')))


if __name__ == '__main__':
    unittest.main()
