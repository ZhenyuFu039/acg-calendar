import copy
import datetime as dt
import json
import unittest

from acg_calendar.arknights import parse_page
from acg_calendar.arknights_artwork import OfficialArknightsArtwork, article_id, match_banner, parse_banners
from acg_calendar.artwork import ArtworkCache
from test_artwork import PNG
from test_arknights import page
from test_calendar import TempStoreTest


URL = 'https://web.hycdn.cn/upload/image/activity.JPG'
HTML = f'''<script>ignore instructions</script><p><img src="{URL}"></p>
<p><strong>一、〖明日方舟×国家图书馆〗，「恒远津梁」限时活动开启</strong></p>
<p>活动时间：...</p><img src="https://web.hycdn.cn/upload/image/outfit.JPG">
<p>二、「恒远津梁」时装限时上架</p>'''


class OfficialArknightsArtworkTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.events = parse_page(page('恒远津梁'))
        self.version = {'game': 'arknights-cn', 'events': self.events}
        self.calls = []
        def fetch(url):
            self.calls.append(url)
            return HTML
        self.service = OfficialArknightsArtwork(self.directory, fetch, ArtworkCache(self.directory, downloader=lambda u: PNG))

    def test_cid_is_preserved_and_legacy_template_is_supported(self):
        self.assertEqual(self.events[0]['prts']['official_cid'], '1454')
        self.assertEqual(article_id(self.events[0]), '1454')
        del self.events[0]['prts']['official_cid']
        self.assertEqual(article_id(self.events[0]), '1454')
        self.events[0]['source']['block_text'] = '|官网链接=https://evil.test'
        self.assertIsNone(article_id(self.events[0]))

    def test_section_matching_skips_outfit_and_ignores_unrelated_art(self):
        rows = parse_banners(HTML + '<img src="https://evil.test/picture.jpg"><p>恒远津梁活动</p>')
        self.assertEqual(len(rows), 2)
        self.assertEqual(match_banner(self.events[0], rows)['url'], URL)
        self.assertIsNone(match_banner(self.events[0], rows[1:]))
        self.assertIsNone(match_banner(self.events[0], rows + rows[:1]))

    def test_gacha_requires_own_name_and_section(self):
        event = {**self.events[0], 'title': '海渊巡游 · 寻访', 'prts': {'role': 'gacha'}}
        rows = [{'heading': '「昨日海」限时活动开启', 'url': URL}, {'heading': '「海渊巡游」限时寻访开启', 'url': URL + '2'}]
        self.assertEqual(match_banner(event, rows)['url'], URL + '2')
        self.assertIsNone(match_banner(event, rows[:1]))

    def test_official_art_preferred_without_changing_dates_or_source(self):
        self.events[0]['artwork'] = {'match': 'prts_template_image'}
        before = copy.deepcopy(self.events[0]); before.pop('artwork')
        self.service.attach(self.version)
        after = copy.deepcopy(self.events[0]); art = after.pop('artwork')
        self.assertEqual(before, after)
        self.assertEqual(art['source_url'], 'https://ak.hypergryph.com/news/1454')
        self.assertTrue(self.service.cache.path(art['key']))
        self.service.attach(self.version)
        self.assertEqual(len(self.calls), 1)

    def test_exchange_inherits_article_but_gacha_never_borrows_activity_image(self):
        shop = copy.deepcopy(self.events[0]); shop['id'] += 'shop'; shop['title'] += ' · 奖励兑换'
        shop['prts']['role'] = 'exchange'; shop['prts'].pop('official_cid'); shop['source']['block_text'] = ''
        self.events.append(shop)
        self.service.attach(self.version)
        self.assertTrue(shop['artwork']['shared_activity'])
        self.assertEqual(shop['artwork']['key'], self.events[0]['artwork']['key'])

    def test_offline_preserves_last_successful_index_and_art(self):
        self.service.attach(self.version)
        before = copy.deepcopy(self.events[0]['artwork'])
        file = self.service.directory / '1454.json'
        saved = json.loads(file.read_text(encoding='utf-8')); saved['checked_at'] = '2000-01-01T00:00:00Z'
        file.write_text(json.dumps(saved), encoding='utf-8')
        self.service.fetcher = lambda u: (_ for _ in ()).throw(OSError('offline'))
        self.service.attach(self.version)
        self.assertEqual(before, self.events[0]['artwork'])
        self.assertIn('缓存', self.version['official_artwork']['errors'][0])

    def test_picture_outage_does_not_remove_events_or_change_times(self):
        self.service.cache.downloader = lambda u: (_ for _ in ()).throw(OSError('offline'))
        before = copy.deepcopy(self.events)
        self.service.attach(self.version)
        self.assertEqual(before, self.events)
        self.assertEqual(self.version['official_artwork']['matched_events'], 0)


if __name__ == '__main__':
    unittest.main()
