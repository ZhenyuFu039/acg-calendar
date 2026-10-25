"""Historical replay plus adversarial controls for cross-server media matching."""
import copy
import datetime as dt
import json
import random
import shutil
import unittest
from unittest.mock import patch

from acg_calendar.azurlane import AzurLaneProvider, compile_feed
from acg_calendar.azurlane_artwork import (align_artwork, discover_pages, snapshot_candidates, table_windows,
                                          bulletin_visual_references, apply_bulletin_visuals)
from acg_calendar.azurlane_http import allowed
from acg_calendar.parser import ROOT, stamp
from test_calendar import TempStoreTest


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.plugin = ROOT / 'plugins/azurlane-cn'
        self.rules = read(self.plugin / 'rules.json')
        self.rules['artwork'] = []
        self.config = read(self.plugin / 'artwork-alignment.json')
        self.pages = read(self.plugin / 'sources/cn-bulletins.json')['pages']
        self.snapshot = read(ROOT / 'samples/azurlane-artwork-history.json')
        self.catalog = snapshot_candidates(self.snapshot, self.config)
        self.rows = compile_feed(self.pages, self.rules, 'test')['versions'][0]['events']

    def run_alignment(self):
        return align_artwork(self.rows, self.pages, self.catalog, self.config)

    def test_history_against_independently_verified_images_without_old_url_mapping(self):
        report = self.run_alignment()
        expected = {m['title']: m['image_url'] for m in read(self.plugin / 'rules.json')['artwork'] if m.get('language') == 'en'}
        for suffix in [' · 活动商店', ' · 累计奖励领取']:
            expected['幽影迷城' + suffix] = expected['幽影迷城']
        self.assertEqual((report['matched'], report['unmatched']), (8, 12))
        for match in report['results']:
            if match['status'] == 'matched':
                self.assertEqual(match['image_url'], expected[match['title']])
        # An associated image with no independently scoped dates is not guessed.
        self.assertEqual(next(r for r in report['results'] if r['title'] == '异常的日常进行中')['status'], 'unmatched')

    def test_held_out_all_title_aliases_still_matches_real_missions(self):
        self.config['aliases'] = []
        report = self.run_alignment()
        matched = [r for r in report['results'] if r['status'] == 'matched']
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]['title'], '严厉女仆不严厉')
        self.assertEqual(matched[0]['shared_missions'], 15)
        self.assertIn('mission_signature', matched[0]['evidence'])
        self.assertNotIn('known_title_alias', matched[0]['evidence'])

    def test_order_does_not_determine_matching(self):
        before = {r['event_id']: r.get('image_url') for r in self.run_alignment()['results']}
        random.Random(31).shuffle(self.catalog)
        random.Random(79).shuffle(self.rows)
        self.pages.reverse()
        after = {r['event_id']: r.get('image_url') for r in self.run_alignment()['results']}
        self.assertEqual(before, after)

    def test_timing_and_structure_alone_never_identify_event(self):
        self.config.update(aliases=[], entities=[], missions=[])
        report = self.run_alignment()
        self.assertEqual(report['matched'], 0)
        self.assertFalse(any(r.get('image_url') for r in self.rows))

    def test_each_batch_learns_own_offset_and_never_changes_clocks(self):
        for row in self.rows:
            if '9月4日' in row['evidence']['page_title']:
                for role in ('start', 'end'):
                    row[role]['at'] = (stamp(row[role]['at']) + dt.timedelta(days=7)).isoformat().replace('+00:00', 'Z')
        before = [(r['start'].copy(), r['end'].copy()) for r in self.rows]
        report = self.run_alignment()
        self.assertEqual(report['matched'], 8)
        self.assertIn([7], report['batch_offsets'].values())
        self.assertIn([0], report['batch_offsets'].values())
        self.assertEqual(before, [(r['start'], r['end']) for r in self.rows])

    def test_conflicting_anchors_in_same_batch_abstain(self):
        row = next(r for r in self.rows if r['title'].startswith('作战准备'))
        for role in ('start', 'end'):
            row[role]['at'] = (stamp(row[role]['at']) + dt.timedelta(days=5)).isoformat().replace('+00:00', 'Z')
        report = self.run_alignment()
        group = [r for r in report['results'] if r['title'] in ['作战准备：新纪元都市NO.7', '闪耀的“魔法”', 'Fight！都市求职大作战！', '蓝图补完计划 · 那不勒斯']]
        self.assertTrue(all(r.get('reason') == 'conflicting_batch_offsets' for r in group))

    def test_equal_plausible_banners_are_ambiguous(self):
        maid = copy.deepcopy(next(c for c in self.catalog if c['title'].startswith('The Strict Maid')))
        maid['image_url'] = 'https://azurlane.netojuu.com/images/other_Event_Banner_EN.jpg'
        self.catalog.append(maid)
        result = next(r for r in self.run_alignment()['results'] if r['title'] == '严厉女仆不严厉')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertFalse(next(r for r in self.rows if r['title'] == '严厉女仆不严厉').get('image_url'))

    def test_same_banner_claimed_by_two_unrelated_events_is_rejected(self):
        other = copy.deepcopy(next(r for r in self.rows if r['title'] == '严厉女仆不严厉'))
        other['id'] = 'independent-copy'
        self.rows.append(other)
        results = self.run_alignment()['results']
        self.assertEqual(len([r for r in results if r.get('reason') == 'multiple_cn_events_claim_same_banner']), 2)

    def test_children_inherit_only_explicit_parent_and_keep_independent_deadlines(self):
        before = [(r['id'], r['start'].copy(), r['end'].copy()) for r in self.rows]
        self.run_alignment()
        main = next(r for r in self.rows if r['title'] == '幽影迷城')
        children = [r for r in self.rows if r.get('parent_id') == main['id']]
        self.assertEqual(len(children), 2)
        self.assertTrue(all(r['image_url'] == main['image_url'] and r['end'] != main['end'] for r in children))
        self.assertEqual(before, [(r['id'], r['start'], r['end']) for r in self.rows])

    def test_discovery_uses_recent_event_headings_not_ship_and_skin_links(self):
        html = (ROOT / 'samples/azurlane-artwork-index.html').read_text(encoding='utf-8')
        targets = discover_pages(html, self.config, stamp('2026-10-23T12:00:00Z'))
        self.assertEqual(targets[0], {'title': 'Tempesta and the Corridor of Fog', 'batch_date': '2026-10-22'})
        self.assertTrue(any(t['title'] == 'Tales of the Paranormal' for t in targets))
        self.assertFalse(any('/Gallery' in t['title'] for t in targets))
        self.assertEqual(discover_pages('<h1>Checking your browser</h1>', self.config, stamp('2026-10-23T12:00:00Z')), [])

    def test_separate_server_dates_and_reruns_are_not_mixed(self):
        windows = table_windows(['CN/JP Servers', 'Start date', 'July 16th, 2020', 'End date', 'August 12th, 2020',
                                 'EN Servers', 'Start date', 'July 30th, 2020', 'End date', 'August 26th, 2020',
                                 'Rerun All Servers', 'Start date', 'May 20th, 2021', 'End date', 'June 16th, 2021'])
        self.assertEqual([w['start'] for w in windows], ['2020-07-30', '2021-05-20'])

    def test_wrong_year_cannot_be_reused_for_current_rerun(self):
        for candidate in self.catalog:
            candidate['windows'] = [{**w, 'start': w['start'].replace('2026', '2025'), 'end': w['end'].replace('2026', '2025')} for w in candidate['windows']]
        self.assertEqual(self.run_alignment()['matched'], 0)

    def test_english_api_scope_is_read_only(self):
        base = 'https://azurlane.koumakan.jp/w/api.php?'
        self.assertTrue(allowed(base + 'action=parse&page=Recent_News&prop=text%7Crevid&format=json&formatversion=2'))
        self.assertFalse(allowed(base + 'action=edit&page=A&format=json'))
        self.assertFalse(allowed('https://azurlane.koumakan.jp/wiki/Recent_News'))


class CatalogIntegrationTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugin = self.directory / 'plugins/azurlane-cn'
        shutil.copytree(ROOT / 'plugins/azurlane-cn', self.plugin)
        self.provider = AzurLaneProvider(self.plugin, read(self.plugin / 'manifest.json'), self.store)

    def test_new_update_shared_visual_metadata_is_queried_once_and_cached(self):
        pages = read(ROOT / 'samples/azurlane-cn-20261022.json')['pages']
        feed = compile_feed(pages, self.provider.config, 'test')
        cache = self.plugin / self.provider.config['bulletin_artwork']['cache']
        cache.write_text('{"files":{}}', encoding='utf-8')
        payload = read(ROOT / 'samples/azurlane-cn-20261022-imageinfo.json')
        with patch.object(self.provider, 'api', return_value=payload) as api:
            self.assertEqual(self.provider.attach_bulletin_visuals(feed, pages), [])
            self.assertEqual(self.provider.attach_bulletin_visuals(feed, pages), [])
        self.assertEqual(api.call_count, 1)
        self.assertTrue(all(r.get('image_match') == 'cn_bulletin_shared_visual' for r in feed['versions'][0]['events']))
        self.assertEqual(feed['versions'][0]['analysis']['bulletin_shared_artwork_count'], 5)

    def test_normal_sync_includes_new_bulletin_shared_images_in_database(self):
        pages = read(ROOT / 'samples/azurlane-cn-20261022.json')['pages']
        index = {'parse': {'revid': 418571, 'wikitext': '[[' + pages[0]['title'] + ']]'}}
        with patch.object(self.provider, 'api', side_effect=[index, {'parse': pages[0]}]), \
             patch.object(self.provider, 'artwork_catalog', return_value=(None, [])), \
             patch.object(self.provider.http, 'read', side_effect=AssertionError('cached image must not download again')):
            self.provider.collect(now=stamp('2026-10-23T12:00:00Z'))
        rows = [e for v in self.store.versions() for e in v['events'] if e['source']['evidence']['revision_id'] == 418469]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(e['artwork']['shared_update'] for e in rows))
        self.assertIn('含 5 条使用更新主视觉', self.store.get_meta('sync:azurlane-cn')['message'])

    def test_bad_visual_metadata_keeps_schedule_without_importing_image(self):
        pages = read(ROOT / 'samples/azurlane-cn-20261022.json')['pages']
        feed = compile_feed(pages, self.provider.config, 'test')
        (self.plugin / self.provider.config['bulletin_artwork']['cache']).write_text('{"files":{}}', encoding='utf-8')
        payload = read(ROOT / 'samples/azurlane-cn-20261022-imageinfo.json')
        payload['query']['pages'][0]['imageinfo'][0]['height'] = 4000
        with patch.object(self.provider, 'api', return_value=payload):
            warnings = self.provider.attach_bulletin_visuals(feed, pages)
        self.assertTrue(warnings)
        self.assertEqual(len(feed['versions'][0]['events']), 5)
        self.assertFalse(any(r.get('image_url') for r in feed['versions'][0]['events']))

    def test_sync_persists_automatic_images_and_keeps_cn_schedule(self):
        pages = read(self.plugin / 'sources/cn-bulletins.json')['pages']
        catalog = snapshot_candidates(read(ROOT / 'samples/azurlane-artwork-history.json'), self.provider.alignment_rules)
        index = {'parse': {'revid': 1, 'wikitext': '\n'.join('[[' + p['title'] + ']]' for p in pages)}}
        with patch.object(self.provider, 'api', side_effect=[index] + [{'parse': p} for p in pages]), \
             patch.object(self.provider, 'artwork_catalog', return_value=(catalog, [])), \
             patch.object(self.provider.http, 'read', side_effect=AssertionError('must reuse cached media')):
            feed = self.provider.collect(now=stamp('2026-10-19T21:30:00Z'))
        self.assertEqual(feed['versions'][0]['analysis']['artwork_alignment']['matched'], 8)
        maid = next(e for e in self.store.versions()[0]['events'] if e['title'] == '严厉女仆不严厉')
        self.assertEqual(maid['artwork']['match'], 'json_cross_server_alignment')
        self.assertIn('mission_signature', maid['artwork']['alignment']['evidence'])
        self.assertEqual(maid['end']['at'], '2026-11-05T02:00:00Z')
        self.assertIn('自动对齐 8 条', self.store.get_meta('sync:azurlane-cn')['message'])

    def test_english_source_failure_uses_snapshot_without_blocking_cn_dates(self):
        with patch.object(self.provider, 'english_page', side_effect=ValueError('access denied')):
            catalog, warnings = self.provider.artwork_catalog(stamp('2026-10-23T12:00:00Z'))
        self.assertTrue(catalog)
        self.assertIn('沿用缓存', warnings[0])
        feed = compile_feed(read(self.plugin / 'sources/cn-bulletins.json')['pages'], self.provider.config, 'test',
                            artwork_catalog=catalog, alignment_rules=self.provider.alignment_rules)
        self.assertEqual(len(feed['versions'][0]['events']), 20)

    def test_live_catalog_is_bounded_persisted_and_reusable_offline(self):
        index = (ROOT / 'samples/azurlane-artwork-index.html').read_text(encoding='utf-8')
        page = next(p for p in read(ROOT / 'samples/azurlane-artwork-history.json')['pages'] if 'html' in p and p['title'].startswith('The Strict Maid'))
        self.provider.alignment_rules['discovery']['max_pages'] = 1
        def fetch(title):
            if title == 'Recent_News':
                return {'title': title, 'html': index.replace('Tempesta_and_the_Corridor_of_Fog', 'The_Strict_Maid_Isn%E2%80%99t_So_Strict'), 'revision_id': 17}
            return {**page, 'revision_id': 18}
        with patch.object(self.provider, 'english_page', side_effect=fetch):
            catalog, _ = self.provider.artwork_catalog(stamp('2026-10-23T12:00:00Z'))
        cache = self.plugin / self.provider.alignment['live_snapshot']
        self.assertLess(cache.stat().st_size, 2_000_000)
        self.assertTrue(read(cache)['pages'][0]['candidates'])
        self.assertTrue(any(c.get('revision_id') == 18 for c in catalog))
        with patch.object(self.provider, 'english_page', side_effect=OSError('offline')):
            again, _ = self.provider.artwork_catalog(stamp('2026-10-24T12:00:00Z'))
        self.assertTrue(any(c.get('revision_id') == 18 for c in again))

    def test_incomplete_page_does_not_starve_next_candidate(self):
        cache = self.plugin / self.provider.alignment['live_snapshot']
        # Use an isolated empty cache rather than the packaged historical seed.
        cache.write_text('{"pages": []}', encoding='utf-8')
        index = ('<h2>October 15th 2026</h2><b>New Event: <a href="/wiki/Empty_Event">Empty Event</a></b>'
                 '<b>New Event: <a href="/wiki/The_Strict_Maid_Isn%E2%80%99t_So_Strict">Maid</a></b>')
        page = next(p for p in read(ROOT / 'samples/azurlane-artwork-history.json')['pages'] if 'html' in p and p['title'].startswith('The Strict Maid'))
        self.provider.alignment_rules['discovery']['max_pages'] = 1
        visited = []
        def fetch(title):
            visited.append(title)
            if title == 'Recent_News':
                return {'title': title, 'html': index, 'revision_id': 17}
            if title == 'Empty Event':
                return {'title': title, 'html': '<p>Under construction</p>', 'revision_id': 1, 'source_url': 'https://azurlane.koumakan.jp/wiki/Empty_Event'}
            return {**page, 'revision_id': 18}
        with patch.object(self.provider, 'english_page', side_effect=fetch):
            self.provider.artwork_catalog(stamp('2026-10-23T12:00:00Z'))
            self.provider.artwork_catalog(stamp('2026-10-24T12:00:00Z'))
        self.assertEqual(visited, ['Recent_News', 'Empty Event', 'Recent_News', 'The Strict Maid Isn’t So Strict'])


class BulletinVisualTests(unittest.TestCase):
    def setUp(self):
        self.rules = read(ROOT / 'plugins/azurlane-cn/rules.json')
        self.pages = read(ROOT / 'samples/azurlane-cn-20261022.json')['pages']
        self.feed = compile_feed(self.pages, self.rules, 'test')
        self.rows = self.feed['versions'][0]['events']
        self.media = read(ROOT / 'plugins/azurlane-cn/sources/cn-bulletin-artwork.json')['files']

    def test_real_footer_supplies_all_five_update_entries_without_changing_times(self):
        before = [(r['id'], copy.deepcopy(r['start']), copy.deepcopy(r['end'])) for r in self.rows]
        refs = bulletin_visual_references(self.pages, self.rows, self.rules['bulletin_artwork'])
        self.assertEqual(list(refs.values())[0]['file'], '飓风与雾霭回廊.jpg')
        self.assertEqual(apply_bulletin_visuals(self.feed, refs, self.media), 5)
        self.assertEqual(len({r['image_url'] for r in self.rows}), 1)
        self.assertTrue(all('oldid=418469' in r['image_source'] for r in self.rows))
        self.assertTrue(all(r['image_bulletin_visual']['shared'] for r in self.rows))
        self.assertEqual(before, [(r['id'], r['start'], r['end']) for r in self.rows])
        self.assertTrue(any('共用主视觉' in r['description'] for r in self.rows))

    def test_independent_artwork_wins_and_other_bulletins_do_not_inherit(self):
        self.rows[0].update(image_url='https://patchwiki.biligame.com/images/blhx/independent.jpg', image_match='exact_cn_activity')
        unrelated = copy.deepcopy(self.rows[1])
        unrelated.update(id='unrelated', evidence={**unrelated['evidence'], 'page_title': 'Different bulletin'})
        self.rows.append(unrelated)
        refs = bulletin_visual_references(self.pages, self.rows, self.rules['bulletin_artwork'])
        self.assertEqual(apply_bulletin_visuals(self.feed, refs, self.media), 4)
        self.assertEqual(self.rows[0]['image_match'], 'exact_cn_activity')
        self.assertNotIn('image_url', unrelated)

    def test_non_footer_or_ambiguous_visuals_are_not_used(self):
        self.pages[0]['wikitext'] = self.pages[0]['wikitext'].replace('From 司令部', 'No footer marker')
        self.assertEqual(bulletin_visual_references(self.pages, self.rows, self.rules['bulletin_artwork']), {})
        self.pages[0]['wikitext'] += '\nFrom 司令部\n[[File:飓风与雾霭回廊.jpg]][[File:飓风与雾霭回廊第二张.jpg]]'
        self.assertEqual(bulletin_visual_references(self.pages, self.rows, self.rules['bulletin_artwork']), {})
