"""Official ZZZ windows, per-frequency art, stale Wiki and refresh isolation."""
import copy
import json
import shutil
import unittest
from unittest.mock import patch

from acg_calendar.parser import ROOT, resolve_versions, stamp
from acg_calendar.providers import ProviderRegistry
from acg_calendar.reminders import ReminderEngine
from acg_calendar.zzz import ZZZProvider, WikiRows, compile_feed, window
from acg_calendar.zzz_http import API, WIKI, allowed
from test_calendar import TempStoreTest

PACKAGE = ROOT / 'plugins/zzz-cn'
NOW = '2026-10-19T23:00:00Z'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.articles = read(PACKAGE / 'sources/official-news.json')['articles']
        self.wiki = read(PACKAGE / 'sources/wiki-calendar.json')
        self.rules = read(PACKAGE / 'rules.json')
        self.feed = compile_feed(self.articles, self.wiki, self.rules, NOW)
        self.events = [e for v in self.feed['versions'] for e in v['events']]

    def event(self, title):
        return next(e for e in self.events if e['title'] == title)

    def test_two_versions_and_twenty_five_distinct_entries(self):
        self.assertEqual([v['name'] for v in self.feed['versions']], ['3.2版本', '3.3版本'])
        self.assertEqual(len(self.events), 25)
        self.assertEqual(len({e['id'] for e in self.events}), 25)
        self.assertFalse(self.feed['versions'][0]['analysis']['ai_used'])
        self.assertFalse(any('商城' in e['title'] or '预下载' in e['title'] or '试镜' in e['title'] for e in self.events))

    def test_official_end_is_not_publication_expiry_or_daily_reset(self):
        row = self.event('「惊喜放映企划」')
        self.assertEqual(row['start']['at'], '2026-09-23T02:00:00Z')
        self.assertEqual(row['end']['at'], '2026-10-19T19:59:00Z')
        self.assertIn('次日04:00', row['description'])
        self.assertNotIn('2035', row['end']['at'])
        self.assertEqual(self.event('「数据悬赏-实战模拟」')['end']['at'], '2026-10-18T19:59:00Z')

    def test_maintenance_opening_estimate_and_explicit_version_end(self):
        row = self.event('「潜能预演·烈火重锤」')
        self.assertEqual(row['start']['at'], '2026-09-09T03:00:00Z')
        self.assertEqual(row['start']['state'], 'estimated')
        self.assertEqual(row['end']['at'], '2026-10-20T22:00:00Z')
        self.assertEqual(row['end']['state'], 'exact')
        next_row = self.event('「倾掷浮生」')
        self.assertEqual(next_row['start']['at'], '2026-10-21T03:00:00Z')
        self.assertEqual(next_row['start']['state'], 'estimated')
        self.assertEqual(next_row['end']['at'], '2026-11-11T03:59:00Z')
        unknown = window('3.3版本更新后 ~ 3.3版本结束', {})
        self.assertEqual([p['state'] for p in unknown], ['unknown', 'unknown'])
        self.assertTrue(all(p['at'] is None for p in unknown))

    def test_four_banners_get_individual_heading_images(self):
        children = [e for e in self.feed['versions'][1]['events'] if e['category'] == 'gacha']
        self.assertEqual(len(children), 4)
        self.assertEqual(len({e['image_url'] for e in children}), 4)
        self.assertTrue(all('/nap/166926/' in e['image_url'] for e in children))
        self.assertIn('菲欧妮', self.event('「倾掷浮生」')['description'])
        self.assertIn('维琳娜', self.event('「凛风吟仪」')['description'])

    def test_stale_wiki_never_becomes_current_or_overrides_times(self):
        self.assertGreater(len(WikiRows(self.wiki['text']).rows), 20)
        self.assertEqual(self.feed['versions'][0]['analysis']['wiki_latest_end'], '2026-09-13T19:59:00Z')
        self.assertFalse(any('wiki' in e['evidence'] for e in self.events))
        self.assertFalse(any('见习邮差' in e['title'] for e in self.events))

    def test_wiki_image_requires_unique_name_and_identical_interval(self):
        article = next(a for a in self.articles if a['iInfoId'] == 166191)
        article['sExt'] = '{}'
        def wiki(end, duplicate=False):
            row = '<tr data-param1="常规"><td>2026/09/23 10:00~2026/10/20 ' + end + '</td><td><img src="https://patchwiki.biligame.com/images/zzz/a/a/banner.png"></td><td><a href="/zzz/活动">惊喜放映企划</a></td><td></td><td></td><td>3.2</td></tr>'
            return {**self.wiki, 'text': '<table>' + row * (2 if duplicate else 1) + '</table>'}
        def event(w):
            feed = compile_feed(self.articles, w, self.rules, NOW)
            return next(e for v in feed['versions'] for e in v['events'] if e['title'] == '「惊喜放映企划」')
        row = event(wiki('03:59'))
        self.assertIn('wiki', row['evidence'])
        self.assertEqual(row['end']['at'], '2026-10-19T19:59:00Z')
        self.assertNotIn('image_url', event(wiki('04:00')))
        self.assertNotIn('image_url', event(wiki('03:59', True)))

    def test_content_edits_keep_ids_and_missing_label_fails_closed(self):
        for a in self.articles:
            a['sContent'] = a['sContent'].replace('可以', '可')
        after = compile_feed(self.articles, self.wiki, self.rules, NOW)
        self.assertEqual([e['id'] for e in self.events], [e['id'] for v in after['versions'] for e in v['events']])
        a = next(a for a in self.articles if a['iInfoId'] == 166191)
        a['sContent'] = a['sContent'].replace('【活动时间】', '【时间格式变更】')
        with self.assertRaises(ValueError):
            compile_feed(self.articles, self.wiki, self.rules, NOW)

    def test_network_scope_excludes_wrong_games_and_redirect_targets(self):
        self.assertTrue(allowed(API + 'getContentList?iChanId=296'))
        self.assertTrue(allowed(WIKI + 'api.php?action=parse'))
        self.assertTrue(allowed('https://fastcdn.hoyoverse.com/content-v2/nap/1/image.png', True))
        for url in ['http://wiki.biligame.com/zzz/api.php', 'https://wiki.biligame.com/sr/api.php', API+'getContent', 'https://localhost/zzz/api.php', 'https://fastcdn.hoyoverse.com/content-v2/nap/../secret.png']:
            self.assertFalse(allowed(url))
            self.assertFalse(allowed(url, True))


class ProviderTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugin = self.directory / 'plugins/zzz-cn'
        shutil.copytree(PACKAGE, self.plugin)
        self.provider = ZZZProvider(self.plugin, read(self.plugin / 'manifest.json'), self.store)
        self.provider.seed()

    def test_discovery_local_images_and_exact_endpoint_reminders(self):
        registry = ProviderRegistry()
        registry.discover(self.directory / 'plugins', self.store)
        self.assertEqual(registry.errors, [])
        self.assertIn('zzz-cn', registry.ids)
        self.assertTrue(registry.games[0]['icon'].startswith('/api/artwork/'))
        versions = resolve_versions(self.store.versions(), self.store.state())
        rows = [e for v in versions for e in v['events']]
        self.assertEqual(sum(bool(e.get('artwork')) for e in rows), 23)
        estimated = next(e for e in rows if e['title'] == '「倾掷浮生」')
        self.assertEqual(estimated['start']['state'], 'estimated')
        self.store.update_settings({'native_notifications': False, 'reminders_enabled': True, 'reminder_offsets': [60]})
        engine = ReminderEngine(self.store)
        notices = engine.tick(stamp('2026-11-11T03:30:00Z'))
        self.assertEqual(len(notices), 4)
        notices = engine.tick(stamp('2026-10-21T02:30:00Z'))
        self.assertFalse(any('维护' in n['title'] for n in notices))

    def test_invalid_sync_preserves_snapshot_and_personal_state(self):
        before = self.store.versions()
        event = before[0]['events'][0]
        self.store.update_state(event['id'], {'starred': True, 'notes': '我的计划'})
        with patch.object(self.provider.http, 'read', return_value=b'{"retcode":0,"data":{"list":[]}}'):
            self.provider.lock.acquire()
            self.provider._run()
        self.assertEqual(self.store.versions(), before)
        self.assertTrue(self.store.state()[event['id']]['starred'])
        self.assertIn('同步失败', self.store.get_meta(self.provider.sync_key)['message'])

    def test_optional_wiki_failure_does_not_block_official_refresh(self):
        articles = read(self.plugin / 'sources/official-news.json')['articles']
        def response(url, image=False):
            if url.startswith(API):
                return json.dumps({'retcode': 0, 'data': {'list': articles}}).encode()
            raise OSError('Wiki unavailable')
        with patch.object(self.provider.http, 'read', side_effect=response):
            feed = self.provider.collect(stamp(NOW))
        self.assertEqual(sum(len(v['events']) for v in feed['versions']), 25)
        status = self.store.get_meta(self.provider.sync_key)
        self.assertIsNone(status['error'])
        self.assertTrue(any('Wiki 暂不可用' in w for w in status['warnings']))


if __name__ == '__main__':
    unittest.main()
