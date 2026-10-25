"""F12: named sync stages, waiting reasons, calendar published before images, round results."""
import json
import shutil
from unittest.mock import patch
import urllib.request

from acg_calendar import network
from acg_calendar.network import ImageDeferred, NetworkManager, deferring_images, read_response, reporting
from acg_calendar.parser import ROOT, stamp
from acg_calendar.sync_progress import SyncProgress, calendar_changes
from acg_calendar.zzz import ZZZProvider
from acg_calendar.zzz_http import API
from test_calendar import TempStoreTest

PACKAGE = ROOT / 'plugins/zzz-cn'
NOW = '2026-10-19T23:00:00Z'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class NetworkStageTests(TempStoreTest):
    def test_deferred_images_raise_before_any_request_but_text_is_not_affected(self):
        request = urllib.request.Request('https://example.org/a.png')
        with patch.object(NetworkManager, 'read', return_value='text') as manager_read:
            with deferring_images():
                with self.assertRaises(ImageDeferred):
                    read_response(request, None, image=True)
                self.assertEqual(read_response(request, None), 'text')
            self.assertEqual(manager_read.call_count, 1)
            read_response(request, None, image=True)  # Outside the block images load again.
            self.assertEqual(manager_read.call_count, 2)

    def test_waiting_for_site_interval_is_reported_once_per_request(self):
        clock = {'t': 1000.0}
        manager = NetworkManager(clock=lambda: clock['t'], waiter=lambda s: clock.__setitem__('t', clock['t'] + s), uniform=lambda a, b: 0)
        reports = []
        with manager.job(), reporting(lambda site, seconds: reports.append((site, round(seconds)))):
            manager._claim('example.org')
            manager._finish('example.org')
            manager._claim('example.org')  # Has to wait the 10-second interval.
            manager._finish('example.org')
        self.assertEqual(reports, [('example.org', 10)])

    def test_progress_records_stage_wait_changes_and_final_cleanup(self):
        progress = SyncProgress(self.store, 'sync:test')
        progress.stage('fetch')
        progress.wait('example.org', 9.6)
        meta = self.store.get_meta('sync:test')
        self.assertEqual((meta['stage']['name'], meta['stage']['label']), ('fetch', '读取公告与来源'))
        self.assertEqual(meta['waiting'], {**meta['waiting'], 'site': 'example.org', 'seconds': 10})
        progress.stage('parse')
        self.assertIsNone(self.store.get_meta('sync:test')['waiting'], 'a new stage clears the old wait')
        old = [{'events': [{'id': 'a', 'title': 'A', 'start': {'at': '2026-01-01T00:00:00Z'}, 'end': {'at': None}},
                           {'id': 'b', 'title': 'B', 'start': {'at': None}, 'end': {'at': None}}]}]
        new = [{'events': [{'id': 'a', 'title': 'A', 'start': {'at': '2026-01-02T00:00:00Z'}, 'end': {'at': None}},
                           {'id': 'c', 'title': 'C', 'start': {'at': None}, 'end': {'at': None}, 'artwork': {'file': 'x'}}]}]
        self.assertEqual(calendar_changes(old, new), {'added': 1, 'removed': 1, 'updated': 1, 'total': 2, 'added_ids': ['c']})
        progress.calendar_saved(old, new)
        progress.images_done(new + [{'events': [{'id': 'd', 'artwork': {'file': 'y'}}]}], failed=2)
        final = progress.finish({'running': False, 'message': 'ok'})
        self.assertEqual((final['stage'], final['waiting'], final['running']), (None, None, False))
        self.assertEqual(final['result']['images'], {'ready': 2, 'added': 1, 'failed': 2, 'notes': []})

    def test_phase_changes_count_as_updates_but_images_do_not(self):
        def version(phase_at, artwork=None):
            event = {'id': 'a', 'title': 'A', 'category': 'event', 'start': {'at': '2026-10-01T00:00:00Z', 'state': 'exact'},
                     'end': {'at': '2026-11-04T01:00:00Z', 'state': 'exact'}, 'phases': [{'title': '挑战关闭', 'at': phase_at, 'state': 'exact'}]}
            if artwork:
                event['artwork'] = artwork
            return [{'events': [event]}]
        same = calendar_changes(version('2026-11-03T01:00:00Z'), version('2026-11-03T01:00:00Z', {'file': 'x'}))
        self.assertEqual(same['updated'], 0, 'an added image is not a date change')
        moved = calendar_changes(version('2026-11-03T01:00:00Z'), version('2026-11-02T01:00:00Z'))
        self.assertEqual(moved['updated'], 1)
        dropped = version('2026-11-03T01:00:00Z'); dropped[0]['events'][0]['phases'] = []
        self.assertEqual(calendar_changes(version('2026-11-03T01:00:00Z'), dropped)['updated'], 1)


class TwoStageCollectorTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugin = self.directory / 'plugins/zzz-cn'
        shutil.copytree(PACKAGE, self.plugin)
        self.provider = ZZZProvider(self.plugin, read(self.plugin / 'manifest.json'), self.store)
        self.provider.seed()

    def test_dates_are_published_before_missing_images_and_text_is_read_once(self):
        removed = sorted((self.plugin / 'assets').glob('*'))[:3]
        cached = {p.name: p.read_bytes() for p in removed}
        for path in removed:
            path.unlink()
        articles = read(self.plugin / 'sources/official-news.json')['articles']
        wiki = read(self.plugin / 'sources/wiki-calendar.json')
        calls, saves, stages = [], [], []

        def response(url, image=False):
            # Same contract as the real clients, whose image reads go through read_response.
            if image and network._defer_images.get():
                raise ImageDeferred('活动图片稍后补齐')
            calls.append('image' if image else url.split('?')[0])
            if image:
                return next(iter(cached.values()))
            if url.startswith(API):
                return json.dumps({'retcode': 0, 'data': {'list': articles}}).encode()
            return json.dumps({'parse': wiki}).encode()
        original_save, original_meta = self.store.replace_game_versions, self.store.set_meta

        def save(game, versions):
            saves.append(sum(bool(e.get('artwork')) for v in versions for e in v['events']))
            return original_save(game, versions)

        def meta(key, value):
            if key == self.provider.sync_key and value.get('stage'):
                stages.append(value['stage']['name'])
            return original_meta(key, value)
        with patch.object(self.provider.http, 'read', side_effect=response), \
             patch.object(self.store, 'replace_game_versions', side_effect=save), \
             patch.object(self.store, 'set_meta', side_effect=meta):
            self.provider.collect(stamp(NOW))
        self.assertEqual(len(saves), 2, 'calendar first, then the version with filled images')
        self.assertLess(saves[0], saves[1])
        self.assertEqual([c for c in calls if c != 'image'], [API + 'getContentList', network_wiki()], 'text sources are not fetched twice')
        self.assertGreaterEqual(calls.count('image'), 1)
        self.assertEqual(list(dict.fromkeys(stages)), ['fetch', 'parse', 'images'])
        status = self.store.get_meta(self.provider.sync_key)
        self.assertIsNone(status['stage'])
        self.assertFalse(status['running'])
        self.assertEqual(status['result']['images']['added'], saves[1] - saves[0])
        self.assertEqual(status['result']['calendar']['total'], 25)
        self.assertIn('calendar_saved_at', status)

    def test_live_wiki_index_is_read_after_dates_are_published(self):
        articles = read(self.plugin / 'sources/official-news.json')['articles']
        wiki = read(self.plugin / 'sources/wiki-calendar.json')
        seen = {}
        original_save = self.store.replace_game_versions
        saved_ids = []

        def save(game, versions):
            saved_ids.append(sorted(e['id'] for v in versions for e in v['events']))
            return original_save(game, versions)

        def response(url, image=False):
            if image:
                raise ImageDeferred('no images in this test')
            if url.startswith(API):
                return json.dumps({'retcode': 0, 'data': {'list': articles}}).encode()
            # The optional Wiki is only requested once the official dates are already stored.
            seen['saves_before_wiki'] = len(saved_ids)
            seen['stage'] = self.store.get_meta(self.provider.sync_key)['stage']['name']
            return json.dumps({'parse': wiki}).encode()
        with patch.object(self.provider.http, 'read', side_effect=response), patch.object(self.store, 'replace_game_versions', side_effect=save):
            self.provider.collect(stamp(NOW))
        self.assertEqual(seen, {'saves_before_wiki': 1, 'stage': 'images'})
        self.assertEqual(len(saved_ids), 2)
        self.assertEqual(saved_ids[0], saved_ids[1], 'activity identities do not change when the Wiki arrives')


class ArknightsImageNoteTests(TempStoreTest):
    def test_official_artwork_outage_is_a_note_not_a_sync_error(self):
        from acg_calendar.arknights import ArknightsCollector
        from test_arknights import PrtsPersistenceTests

        class OfficialOffline:
            def attach(self, version):
                version['official_artwork'] = {'errors': ['官网公告 1 连接失败，使用已缓存横幅索引', '官网头图 2：offline']}
        collector = ArknightsCollector(self.store, self.directory, PrtsPersistenceTests.fetcher.__get__(self), official_artwork=OfficialOffline())
        collector.collect()
        status = self.store.get_meta(collector.meta_key)
        self.assertIsNone(status['error'])
        self.assertEqual(status['result']['images']['failed'], 0, 'source problems are not counted as missing images')
        self.assertEqual(status['result']['images']['notes'], ['明日方舟官网头图暂不可用（2 项记录），已沿用 PRTS 图片或缓存'])

        class OfficialOk:
            def attach(self, version):
                version['official_artwork'] = {'errors': []}
        ArknightsCollector(self.store, self.directory, PrtsPersistenceTests.fetcher.__get__(self), official_artwork=OfficialOk()).collect()
        self.assertEqual(self.store.get_meta(collector.meta_key)['result']['images']['notes'], [])


def network_wiki():
    from acg_calendar.zzz_http import WIKI
    return WIKI + 'api.php'


class InterruptedSyncTests(TempStoreTest):
    def test_restart_clears_a_sync_that_was_cut_off(self):
        from app import Application
        Application(self.directory, auto_sync=False, desktop=False)
        self.store.set_meta('sync', {'running': True, 'stage': {'name': 'images', 'label': '补齐活动图片'}, 'waiting': {'site': 'x'}, 'last_success': '2026-10-01T00:00:00Z'})
        Application(self.directory, auto_sync=False, desktop=False)
        status = self.store.get_meta('sync')
        self.assertEqual((status['running'], status['stage'], status['waiting']), (False, None, None))
        self.assertEqual(status['last_success'], '2026-10-01T00:00:00Z')
        self.assertIn('中断', status['message'])
