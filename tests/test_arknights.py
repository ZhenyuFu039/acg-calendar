import copy
import datetime as dt
import json
import urllib.parse
import unittest

from app import Application
from acg_calendar.arknights import ArknightsCollector, GAME, VERSION_ID, attach_announcements, catalog_titles, endpoint, make_version, parse_page
from acg_calendar.ical import calendar_bytes
from acg_calendar.parser import ROOT, resolve_versions, stamp
from acg_calendar.reminders import ReminderEngine
from acg_calendar.skland import SklandProbe
from acg_calendar.wiki_http import wiki_url
from test_calendar import TempStoreTest, version

PAGES = json.loads((ROOT / 'tests/fixtures/prts-pages.json').read_text(encoding='utf-8'))['pages']


def page(name):
    return copy.deepcopy(next(p for p in PAGES if p['title'] == name))


def calendar():
    events = parse_page(page('昨日海')) + parse_page(page('矢量突破/03')) + parse_page(page('恒远津梁')) + parse_page(page('稳态测定'))
    attach_announcements(events, [p for p in PAGES if '/活动公告' in p['title']])
    return make_version(events, '2026-10-19T02:00:00Z', 'snapshot')


class PrtsParseTests(unittest.TestCase):
    def test_real_activity_and_exchange_have_different_deadlines(self):
        activity, shop = parse_page(page('昨日海'))
        self.assertEqual(activity['end']['at'], '2026-10-22T19:59:00Z')
        self.assertEqual(shop['end']['at'], '2026-10-29T19:59:00Z')
        self.assertEqual(shop['start']['state'], 'estimated')
        self.assertEqual(activity['requirements'], ['主线关卡1-10'])

    def test_announcement_confirms_shop_start_and_phase_openings(self):
        events = parse_page(page('昨日海'))
        attach_announcements(events, [page('昨日海/活动公告')])
        activity, shop = events[:2]
        self.assertEqual(shop['start']['state'], 'exact')
        self.assertEqual(shop['source']['title'], '昨日海/活动公告')
        self.assertEqual(len(activity['phases']), 2)
        self.assertEqual(activity['phases'][1]['at'], '2026-10-16T08:00:00Z')
        self.assertTrue(any(e['category'] == 'gacha' and '海渊巡游' in e['title'] for e in events))
        self.assertFalse(any('时装' in e['title'] for e in events))

    def test_fixed_revision_source_and_community_label_are_retained(self):
        e = parse_page(page('昨日海'))[0]
        self.assertEqual(e['source']['kind'], 'community_wiki')
        self.assertIn('oldid=', e['source']['revision_url'])
        self.assertEqual(e['source']['revision_id'], page('昨日海')['revisions'][0]['revid'])
        self.assertIn('CC BY-NC-SA', e['source']['license']['name'])

    def test_id_survives_date_and_revision_changes(self):
        original = page('昨日海'); changed = copy.deepcopy(original)
        changed['revisions'][0]['revid'] += 1
        changed['revisions'][0]['slots']['main']['content'] = changed['revisions'][0]['slots']['main']['content'].replace('2026-10-23', '2026-10-24')
        self.assertEqual([e['id'] for e in parse_page(original)], [e['id'] for e in parse_page(changed)])

    def test_cn_fields_take_precedence_and_foreign_fields_are_not_fallbacks(self):
        p = page('昨日海'); s = p['revisions'][0]['slots']['main']['content']
        p['revisions'][0]['slots']['main']['content'] = s.replace('|活动开始时间=', '|活动开始时间cn=') + '\n'
        self.assertEqual(parse_page(p)[0]['start']['at'], '2026-10-09T04:00:00Z')
        p['revisions'][0]['slots']['main']['content'] = s.replace('|活动开始时间=', '|活动开始时间jp=')
        with self.assertRaises(ValueError): parse_page(p)

    def test_unknown_end_and_date_only_are_not_assigned_midnight(self):
        p = page('昨日海')
        p['revisions'][0]['slots']['main']['content'] = p['revisions'][0]['slots']['main']['content'].replace('2026-10-23 03:59', '待公布')
        self.assertIsNone(parse_page(p)[0]['end']['at'])
        self.assertIsNone(endpoint('2026-10-23')['at'])

    def test_invalid_dates_and_reversed_interval_rejected(self):
        with self.assertRaises(ValueError): endpoint('2026-02-31 04:00')
        p = page('昨日海')
        p['revisions'][0]['slots']['main']['content'] = p['revisions'][0]['slots']['main']['content'].replace('2026-10-23 03:59', '2026-10-01 03:59')
        with self.assertRaises(ValueError): parse_page(p)

    def test_catalog_excludes_permanent_categories_but_includes_limited_tasks(self):
        rows = [('常驻玩法', '集成战略'), ('生息演算限时任务', '其他活动'), ('昨日海', '支线故事')]
        html = '<table>' + ''.join('<tr><td>2026-10-09 12:00</td><td><a href="/w/' + t + '">' + t + '</a></td><td><a href="/w/分类:' + c + '">' + c + '</a></td></tr>' for t,c in rows) + '</table>'
        self.assertEqual(catalog_titles(html), ['生息演算限时任务', '昨日海'])

    def test_cover_comes_from_existing_catalog_link_matching_template_filename(self):
        url='https://media.prts.wiki/thumb/9/90/活动预告_昨日海_01.jpg/650px-活动预告_昨日海_01.jpg?v=one'
        html='<table><tr><td>2026-10-09 12:00</td><td><a href="/w/昨日海">昨日海</a></td><td><img src="'+url+'"></td></tr></table>'
        titles, images=catalog_titles(html,with_images=True)
        self.assertEqual(titles,['昨日海'])
        self.assertEqual(images['活动预告 昨日海 01.jpg'],url)

    def test_comments_and_script_instruction_text_are_never_executed(self):
        p = page('昨日海')
        p['revisions'][0]['slots']['main']['content'] = '<!--{{活动信息|名称=假|活动开始时间=2030-01-01 00:00}}-->\n' + p['revisions'][0]['slots']['main']['content']
        self.assertEqual(parse_page(p)[0]['title'], '昨日海')

    def test_source_hosts_cannot_redirect_to_credentials_or_unrelated_paths(self):
        self.assertTrue(wiki_url('https://prts.wiki/api.php?action=query'))
        self.assertTrue(wiki_url('https://zonai.skland.com/web/v1/wiki/item/catalog'))
        for url in ['https://prts.wiki.attacker.test/api.php', 'http://prts.wiki/api.php', 'https://user@prts.wiki/api.php', 'https://zonai.skland.com/api/v1/user/info', 'https://media.prts.wiki/bad.svg']:
            self.assertFalse(wiki_url(url))

    def test_versions_never_borrow_other_games_maintenance(self):
        endfield = version(); ark = calendar()
        ark['maintenance']['start'] = '2026-10-30T22:00:00Z'
        result = resolve_versions([endfield, ark], {})
        e = next(e for v in result if v['game'] == 'endfield-cn' for e in v['events'] if e['end'].get('reference') == 'next_maintenance_start')
        self.assertIsNone(e['end']['at'])

    def test_arknights_export_keeps_game_and_wiki_attribution_without_endfield_currency(self):
        v = calendar()
        self.assertNotIn('reward_summary', v)
        text = calendar_bytes([v], {'reminders_enabled': False}).decode().replace('\r\n ', '')
        self.assertIn('明日方舟国服', text)
        self.assertIn('PRTS', text)
        self.assertIn('CC BY-NC-SA', text)
        self.assertNotIn('嵌晶玉', text)


class PrtsPersistenceTests(TempStoreTest):
    def fetcher(self, url):
        if '/w/' in url:
            return '<table>' + ''.join('<tr><td>2026-10-09 12:00</td><td><a href="/w/' + p['title'] + '">' + p['title'] + '</a></td></tr>' for p in PAGES if '/活动公告' not in p['title']) + '</table>'
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if params.get('prop') == ['imageinfo']:
            return json.dumps({'query': {'pages': []}})
        titles = params['titles'][0].split('|')
        return json.dumps({'query': {'pages': [p for p in PAGES if p['title'] in titles]}})

    def test_collector_updates_without_losing_completion_or_endfield(self):
        self.store.save_version(version())
        collector = ArknightsCollector(self.store, self.directory, self.fetcher)
        v = collector.collect(); target = v['events'][0]['id']
        self.store.update_state(target, {'completed': True, 'notes': 'keep'})
        collector.collect()
        self.assertEqual(len(self.store.versions()), 2)
        self.assertTrue(self.store.state()[target]['completed'])
        self.assertEqual(self.store.state()[target]['notes'], 'keep')
        self.assertEqual(self.store.get_meta(collector.meta_key)['mode'], 'live')

    def test_changed_index_preserves_all_saved_activities(self):
        collector = ArknightsCollector(self.store, self.directory, self.fetcher)
        collector.collect(); before = self.store.versions()
        collector.fetcher = lambda url: '<html>changed</html>'
        with self.assertRaises(ValueError): collector.collect()
        self.assertEqual(self.store.versions(), before)

    def test_announcement_outage_preserves_existing_gacha_and_phase_rows(self):
        collector=ArknightsCollector(self.store,self.directory,self.fetcher)
        original=collector.collect()
        base=self.fetcher
        def unavailable(url):
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if '/活动公告' in params.get('titles',[''])[0]:
                return json.dumps({'query':{'pages':[]}})
            return base(url)
        collector.fetcher=unavailable
        refreshed=collector.collect()
        self.assertEqual({e['id'] for e in original['events']},{e['id'] for e in refreshed['events']})
        self.assertEqual(sum(len(e['phases']) for e in original['events']),sum(len(e['phases']) for e in refreshed['events']))

    def test_reminders_cover_arknights_without_waiting_for_selected_game(self):
        self.store.save_version(calendar())
        target = next(e for e in calendar()['events'] if e['title'] == '昨日海')
        when = stamp(target['end']['at']) - dt.timedelta(minutes=30)
        engine = ReminderEngine(self.store)
        first = engine.tick(when)
        self.assertTrue(any(n['title'] == '昨日海' for n in first))
        self.assertTrue(all(n['game'] == GAME for n in first))
        self.assertEqual(engine.tick(when), [])

    def test_application_exposes_both_games_and_validates_scoped_settings(self):
        app = Application(self.directory, auto_sync=False, desktop=False)
        self.assertEqual({e['game'] for e in app.state()['events']}, {'endfield-cn', GAME})
        app.update_settings({'active_game': GAME})
        self.assertEqual(app.store.settings()['active_game'], GAME)
        self.assertEqual({v['game'] for v in app.scoped_versions(GAME)}, {GAME})
        with self.assertRaises(ValueError): app.update_settings({'active_game': 'foreign'})

    def test_skland_device_validation_failure_never_overwrites_calendar(self):
        self.store.save_version(version()); before = self.store.versions(); calls = []
        def fetch(url): calls.append(url); return '{"code":10001,"message":"设备信息无效"}'
        probe = SklandProbe(self.store, fetch)
        result = probe.check(force=True)
        self.assertEqual(result['status'], 'device_validation_required')
        self.assertFalse(result['used_for_calendar'])
        self.assertEqual(self.store.versions(), before)
        probe.check()
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
