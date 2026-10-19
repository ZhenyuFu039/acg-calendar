import copy
import unittest

from app import Application
from acg_calendar.briefing import BriefingReader, merge_briefing
from acg_calendar.parser import resolve_versions
from acg_calendar.rewards import summarize_rewards
from test_calendar import TempStoreTest, version
from test_briefing import guide, source_fetcher, NEWS, DETAIL, NOW


class NoArtwork:
    def cache(self, url):
        return None


class RewardsTests(unittest.TestCase):
    def test_real_announcement_total_has_thirteen_unique_claims(self):
        v = version()
        r = v['reward_summary']
        self.assertEqual(r['verified_total'], 11000)
        self.assertEqual(len(r['claims']), 13)
        self.assertEqual(len({c['id'] for c in r['claims']}), 13)
        self.assertTrue(all(c['line'] and c['url'].endswith('/6094') for c in r['claims']))

    def test_exchange_is_shared_not_another_two_thousand(self):
        v = version()
        shop = next(e for e in v['events'] if e['category'] == 'exchange')
        parent = next(e for e in v['events'] if e['id'] == shop['reward']['shared_with'])
        self.assertEqual(shop['reward']['amount'], 2000)
        self.assertEqual(shop['reward']['state'], 'shared')
        self.assertEqual(parent['reward']['amount'], 2000)
        summarize_rewards(v)
        self.assertEqual(v['reward_summary']['verified_total'], 11000)

    def test_maintenance_and_unspecified_battle_pass_are_separate(self):
        v = version()
        other = v['reward_summary']['other_mentions']
        self.assertEqual(sum(c['amount'] or 0 for c in other if c['reason'] == '维护补偿'), 1800)
        self.assertTrue(any(c['amount'] is None and '通行证' in c['reason'] for c in other))
        self.assertEqual(v['events'][0]['reward']['state'], 'excluded')

    def test_two_seasons_are_distinct_and_not_multiplied_by_cycles(self):
        v = version()
        seasons = [e for e in v['events'] if '赛季' in e['title']]
        self.assertEqual([e['reward']['amount'] for e in seasons], [300, 300])
        self.assertNotEqual(seasons[0]['reward']['claims'][0]['id'], seasons[1]['reward']['claims'][0]['id'])
        puzzle = next(e for e in v['events'] if '密境行者' in e['title'])
        self.assertEqual(puzzle['reward']['amount'], 1200)

    def test_trial_rewards_do_not_convert_tickets(self):
        v = version()
        banners = [e for e in v['events'] if e['category'] == 'gacha']
        self.assertEqual(sum(e['reward']['amount'] or 0 for e in banners), 200)
        self.assertEqual(sum(e['reward']['amount'] is not None for e in banners), 2)
        sign = next(e for e in v['events'] if '签到' in e['title'])
        self.assertIsNone(sign['reward']['amount'])
        self.assertEqual(sign['reward']['state'], 'unlisted')

    def test_guide_one_is_not_counted_as_one_gem(self):
        v = version()
        merge_briefing(v, guide(), NoArtwork())
        self.assertEqual(v['reward_summary']['verified_total'], 11000)
        self.assertEqual(v['reward_summary']['conflicts'], [])
        self.assertTrue(any(r.get('quantity') == 1 for e in v['events'] for r in e.get('briefing', {}).get('reward_items', [])))
        ghost = next(e for e in v['events'] if '予众魂' in e['title'])
        self.assertEqual(ghost['reward']['amount'], 300)

    def test_guide_can_supply_precise_missing_amount_and_reports_conflict(self):
        v = version()
        g = guide()
        row = next(x for x in g['entries'] if '卡莉' in x['title'])
        row['rewards'] = [{'id': 'item_diamond', 'quantity': 1700}]
        merge_briefing(v, g, NoArtwork())
        self.assertEqual(v['reward_summary']['verified_total'], 11000)
        self.assertEqual(v['reward_summary']['conflicts'][0]['announcement'], 1500)
        event = next(e for e in v['events'] if '卡莉' in e['title'])
        event['source']['reward_lines'] = []
        summarize_rewards(v)
        self.assertEqual(event['reward']['amount'], 1700)
        self.assertEqual(v['reward_summary']['verified_total'], 11200)
        self.assertEqual(event['reward']['claims'][0]['source'], 'briefing')

    def test_local_completion_and_time_overrides_do_not_change_total(self):
        v = version()
        state = {e['id']: {'completed': True, 'ignored': True} for e in v['events']}
        resolved = resolve_versions([v], state)[0]
        summarize_rewards(resolved)
        self.assertEqual(resolved['reward_summary']['verified_total'], 11000)

    def test_map_credit_excluded_but_named_event_in_new_area_is_included(self):
        v = version()
        for i, title in enumerate(['新地图探索奖励', '信用商店']):
            e = copy.deepcopy(v['events'][7])
            e.update(id='excluded-' + str(i), title=title)
            e['source']['reward_lines'] = [{'line': 900+i, 'text': '奖励【嵌晶玉】×10000'}]
            v['events'].append(e)
        summarize_rewards(v)
        self.assertEqual(v['reward_summary']['verified_total'], 11000)
        self.assertEqual(v['events'][-1]['reward']['note'], '信用商店购买不计入')
        intro = next(e for e in v['events'] if '墨分诸彩' in e['title'])
        self.assertEqual(intro['reward']['amount'], 1200)

    def test_same_reward_source_line_on_two_rows_is_counted_once(self):
        v = version()
        e = copy.deepcopy(v['events'][7])
        e['id'] = 'second-deadline'
        v['events'].append(e)
        summarize_rewards(v)
        self.assertEqual(e['reward']['state'], 'shared')
        self.assertEqual(v['reward_summary']['verified_total'], 11000)

    def test_per_day_amount_is_not_claimed_as_a_full_event_total(self):
        v = version()
        e = v['events'][7]
        e['source']['reward_lines'] = [{'line': 999, 'text': '每日完成任务可领取【嵌晶玉】×100'}]
        summarize_rewards(v)
        self.assertIsNone(e['reward']['amount'])
        self.assertEqual(v['reward_summary']['verified_total'], 10000)

    def test_pre_version_guide_reward_is_separate_from_in_version_activities(self):
        v = version()
        g = guide()
        g.update(news_reward_mentions=[{'line': 33, 'text': '浏览即可领取【嵌晶玉】×1000'}], news_reward_before_update=True)
        merge_briefing(v, g, NoArtwork())
        self.assertEqual(v['reward_summary']['verified_total'], 11000)
        other = v['reward_summary']['other_mentions'][-1]
        self.assertEqual(other['amount'], 1000)
        self.assertIn('本版本维护前截止', other['reason'])
        self.assertTrue(other['url'].endswith('/4774'))


class RewardUpgradeTests(TempStoreTest):
    def test_bad_legacy_source_does_not_break_saved_calendar(self):
        v = version()
        v.pop('reward_mentions')
        for e in v['events']:
            e['source'].pop('reward_lines', None)
        self.store.save_version(v)
        source = self.directory / 'sources'
        source.mkdir()
        (source / (v['id'] + '.txt')).write_text('incomplete cached response', encoding='utf-8')
        app = Application(self.directory, auto_sync=False, desktop=False)
        state = app.state()
        self.assertEqual(len([e for e in state['events'] if e['game'] == 'endfield-cn']), 24)
        self.assertEqual(state['versions'][0]['reward_summary']['verified_total'], 11000)

    def test_legacy_snapshot_gets_rewards_without_losing_personal_state(self):
        v = version()
        for key in ('reward_mentions', 'reward_summary', 'reward_policy'):
            v.pop(key)
        for e in v['events']:
            e.pop('reward')
            e['source'].pop('reward_lines', None)
        self.store.save_version(v)
        target = v['events'][7]['id']
        self.store.update_state(target, {'completed': True, 'notes': 'keep this'})
        app = Application(self.directory, auto_sync=False, desktop=False)
        state = app.state()
        self.assertEqual(state['versions'][0]['reward_summary']['verified_total'], 11000)
        e = next(e for e in state['events'] if e['id'] == target)
        self.assertTrue(e['completed'])
        self.assertEqual(e['notes'], 'keep this')
        self.assertEqual(e['reward']['amount'], 1000)

    def test_reader_retains_news_reward_and_before_update_deadline_on_cache_hit(self):
        fetch = source_fetcher([])
        def with_reward(url):
            html = fetch(url)
            if url.endswith('/news/4774'):
                return html + '<p>浏览即可领取【嵌晶玉】×1000</p><p>至对应游戏版本更新维护前</p>'
            return html
        reader = BriefingReader(self.directory, with_reward)
        first = reader.read(NEWS, DETAIL, NOW)
        cached = reader.read(NEWS, DETAIL, NOW)
        self.assertTrue(cached['cache_reused'])
        self.assertTrue(cached['news_reward_before_update'])
        self.assertEqual(first['news_reward_mentions'], cached['news_reward_mentions'])
        self.assertEqual(len(cached['news_reward_mentions']), 1)


if __name__ == '__main__':
    unittest.main()
