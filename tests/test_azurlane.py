"""CN scheduling authority, cross-Wiki artwork and safe plugin refresh."""
import copy
import json
import shutil
import unittest
from unittest.mock import patch

from app import Application
from acg_calendar.azurlane import AzurLaneProvider, BulletinParser, calendar_titles, compile_feed
from acg_calendar.azurlane_http import allowed
from acg_calendar.parser import ROOT, stamp, maintenance_start_estimate, resolve_versions
from acg_calendar.reminders import ReminderEngine
from test_calendar import TempStoreTest

PACKAGE = ROOT / 'plugins/azurlane-cn'
NOW = stamp('2026-10-19T21:30:00Z')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class BulletinTests(unittest.TestCase):
    def setUp(self):
        self.rules = read(PACKAGE / 'rules.json')
        self.pages = read(PACKAGE / 'sources/cn-bulletins.json')['pages']
        self.feed = compile_feed(self.pages, self.rules, '2026-10-19T21:30:00Z')
        self.events = self.feed['versions'][0]['events']

    def event(self, title):
        return next(e for e in self.events if e['title'] == title)

    def test_twenty_cn_entries_and_provisional_maintenance_deadlines(self):
        self.assertEqual(len(self.events), 20)
        maid = self.event('严厉女仆不严厉')
        self.assertEqual(maid['start']['at'], '2026-10-15T07:00:00Z')
        self.assertEqual(maid['start']['state'], 'estimated')
        self.assertEqual(maid['end']['at'], '2026-11-05T02:00:00Z')
        self.assertEqual(maid['end']['state'], 'estimated')
        self.assertEqual(maid['end']['raw'], '11月5日维护')
        self.assertIn('不采用其外服时间', maid['description'])
        self.assertTrue(maid['image_url'].startswith('https://azurlane.netojuu.com/'))
        self.assertEqual(self.event('信标·META · 萨拉托加·META')['end']['raw'], '12月10日维护')
        self.assertEqual(self.event('信标·META · 萨拉托加·META')['end']['at'], '2026-12-10T02:00:00Z')

    def test_published_maintenance_overrides_ten_am_and_undated_text_stays_unknown(self):
        parser = BulletinParser(self.rules)
        start, end = parser.window('10月15日维护后~11月5日维护', 2026, {'2026-11-05': {'start': '2026-11-05T01:00:00Z'}})
        self.assertEqual(end['at'], '2026-11-05T01:00:00Z')
        self.assertEqual(end['state'], 'exact')
        self.assertIsNone(maintenance_start_estimate('下次版本更新维护前', 2026, parser.zone))
        self.assertIsNone(maintenance_start_estimate('11月5日维护后', 2026, parser.zone))

    def test_known_next_maintenance_and_exact_cn_pass_deadline(self):
        rerun = self.event('自选轻量复刻')
        self.assertEqual(rerun['end']['at'], '2026-10-15T02:00:00Z')
        self.assertEqual(rerun['end']['state'], 'exact')
        cruise = self.event('世界巡游 · 第三十一期')
        self.assertEqual(cruise['start']['at'], '2026-09-30T16:00:00Z')
        self.assertEqual(cruise['end']['at'], '2026-11-30T15:59:00Z')
        self.assertTrue(cruise['image_url'].startswith('https://patchwiki.biligame.com/'))

    def test_shop_claim_and_conditional_rerun_choices(self):
        main = self.event('幽影迷城')
        for title in ['幽影迷城 · 活动商店', '幽影迷城 · 累计奖励领取']:
            child = self.event(title)
            self.assertEqual(child['parent_id'], main['id'])
            self.assertEqual(child['end']['at'], '2026-10-07T15:59:00Z')
        self.assertEqual(main['end']['at'], '2026-09-30T15:59:00Z')
        self.assertFalse(any('铁翼擎风' == e['title'] or '高塔上的蔷薇' == e['title'] for e in self.events))
        self.assertFalse(any('礼包' in e['title'] for e in self.events))

    def test_revision_changes_keep_event_ids_and_year_rollover(self):
        pages = copy.deepcopy(self.pages)
        for page in pages:
            page['revid'] += 1
        after = compile_feed(pages, self.rules, 'updated')['versions'][0]['events']
        self.assertEqual([e['id'] for e in self.events], [e['id'] for e in after])
        parser = BulletinParser(self.rules)
        start, end = parser.window('活动时间1月1日00:00~1月31日23:59', 2026, {}, reference_month=12)
        self.assertEqual(start['at'], '2026-12-31T16:00:00Z')
        self.assertEqual(end['at'], '2027-01-31T15:59:00Z')

    def test_index_and_remote_image_scope(self):
        text = '[[2026年10月15日10:00港区改建|改建]] [[2025年10月15日10:00港区改建|旧]]'
        self.assertEqual(calendar_titles(text, self.rules, NOW), ['2026年10月15日10:00港区改建'])
        for url in ['http://127.0.0.1/test.png', 'https://patchwiki.biligame.com/images/another-game/a.png', 'https://azurlane.netojuu.com/images/%2e%2e/private.png', 'https://azurlane.netojuu.com/images/a.svg']:
            self.assertFalse(allowed(url, True))
        self.assertTrue(allowed('https://azurlane.netojuu.com/images/a.png', True))
        self.assertFalse(allowed('https://wiki.biligame.com/other/api.php'))


class PluginTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugin = self.directory / 'plugins/azurlane-cn'
        shutil.copytree(PACKAGE, self.plugin)
        self.provider = AzurLaneProvider(self.plugin, read(self.plugin / 'manifest.json'), self.store)

    def test_offline_discovery_media_and_source_attribution(self):
        app = Application(self.directory, auto_sync=False, desktop=False)
        self.assertEqual(app.registry.errors, [])
        game = next(g for g in app.state()['games'] if g['id'] == 'azurlane-cn')
        self.assertNotIn('currency', game)
        rows = [e for e in app.state()['events'] if e['game'] == 'azurlane-cn']
        self.assertEqual(len(rows), 20)
        self.assertEqual(sum(bool(e.get('artwork')) for e in rows), 12)
        self.assertTrue(all(e['source']['kind'] == 'azurlane_wiki' for e in rows))
        self.assertTrue(all('oldid=' in e['source']['evidence']['revision_url'] for e in rows))

    def test_unavailable_source_does_not_replace_snapshot(self):
        self.provider.seed()
        before = self.store.versions()
        with patch.object(self.provider, 'api', side_effect=OSError('unavailable')):
            self.assertTrue(self.provider.lock.acquire(False))
            self.provider._run()
        self.assertEqual(self.store.versions(), before)
        self.assertTrue(self.store.get_meta('sync:azurlane-cn')['error'])

    def test_failed_image_download_keeps_all_schedule_rows(self):
        # Existing local pictures are enough offline; emulate a new image URL.
        feed = read(self.plugin / 'events.json')
        row = next(e for e in feed['versions'][0]['events'] if e.get('image_url'))
        row.pop('artwork', None)
        row['image_url'] = 'https://azurlane.netojuu.com/images/new.png'
        with patch.object(self.provider.http, 'read', side_effect=OSError('offline')):
            errors = self.provider.attach_images(feed)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(self.provider.normalize(feed)[0]['events']), 20)

    def test_personal_data_and_other_games_survive_refresh(self):
        app = Application(self.directory, auto_sync=False, desktop=False)
        original = app.scoped_versions('azurlane-cn')[0]['events'][0]
        app.update_event(original['id'], {'starred': True, 'notes': '保留备注'})
        other = app.scoped_versions('endfield-cn')
        self.store.replace_game_versions('azurlane-cn', self.provider.normalize(read(self.plugin / 'events.json')))
        restored = next(e for e in app.state()['events'] if e['id'] == original['id'])
        self.assertTrue(restored['starred'])
        self.assertEqual(restored['notes'], '保留备注')
        self.assertEqual(other, app.scoped_versions('endfield-cn'))

    def test_estimated_deadlines_do_not_remind_but_exact_cn_deadlines_do(self):
        self.provider.seed()
        engine = ReminderEngine(self.store, sender=lambda *_: False)
        notices = engine.tick(stamp('2026-11-30T15:30:00Z'))
        self.assertTrue(any('世界巡游' in n['title'] for n in notices))
        self.assertFalse(any('严厉女仆' in n['title'] for n in engine.tick(stamp('2026-11-05T01:30:00Z'))))

    def test_provisional_end_preserves_manual_override(self):
        self.provider.seed()
        version = self.store.versions()[0]
        maid = next(e for e in version['events'] if e['title'] == '严厉女仆不严厉')
        resolved = resolve_versions([version], {maid['id']: {'end_override': '2026-11-05T03:00:00Z'}})
        end = next(e for e in resolved[0]['events'] if e['id'] == maid['id'])['end']
        self.assertEqual(end['at'], '2026-11-05T03:00:00Z')
        self.assertEqual(end['state'], 'manual')

    def test_shared_projection_resolves_old_unknown_and_prefers_known_maintenance(self):
        self.provider.seed()
        version = self.store.versions()[0]
        maid = next(e for e in version['events'] if e['title'] == '严厉女仆不严厉')
        maid['end'].update(at=None, state='unknown', reference=None)
        resolved = resolve_versions([version], {})[0]
        self.assertEqual(next(e for e in resolved['events'] if e['id'] == maid['id'])['end']['at'], '2026-11-05T02:00:00Z')
        next_version = {**copy.deepcopy(version), 'id': 'next-period', 'events': [], 'maintenance': {'start': '2026-11-05T01:00:00Z', 'planned_end': None}}
        result = resolve_versions([version, next_version], {})
        end = next(e for v in result for e in v['events'] if e['id'] == maid['id'])['end']
        self.assertEqual(end['at'], '2026-11-05T01:00:00Z')
        self.assertEqual(end['state'], 'exact')

    def test_long_meta_bulletin_is_retained_beyond_index_lookback(self):
        recent = read(self.plugin / 'sources/cn-bulletins.json')['pages'][0]
        index = {'parse': {'revid': 1, 'wikitext': '[[2026年10月15日10:00港区改建|改建]]'}}
        with patch.object(self.provider, 'api', side_effect=[index, {'parse': recent}]), patch.object(self.provider, 'attach_images', return_value=[]):
            feed = self.provider.collect(now=stamp('2026-11-25T00:00:00Z'))
        self.assertTrue(any('萨拉托加' in e['title'] for e in feed['versions'][0]['events']))
