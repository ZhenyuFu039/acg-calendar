import base64
import copy
import unittest

from acg_calendar.artwork import ArtworkCache, official_image
from test_calendar import TempStoreTest, version


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII=')


class ArtworkTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.calls = []
        def downloader(url):
            self.calls.append(url)
            return PNG
        self.cache = ArtworkCache(self.directory, downloader)
        self.data = version()
        self.cover = {'cid': '8548', 'title': '「万物更新」特许寻访说明', 'cover': 'https://web.hycdn.cn/upload/image/cover.png'}

    def test_exact_activity_match_does_not_reuse_version_cover(self):
        version_cover = {**self.cover, 'title': '「丹青渡」版本更新说明'}
        self.cache.attach(self.data, [version_cover, self.cover])
        matched = [e for e in self.data['events'] if e.get('artwork')]
        self.assertEqual(len(matched), 1)
        self.assertIn('万物更新', matched[0]['title'])
        self.assertEqual(matched[0]['artwork']['source_url'], 'https://endfield.hypergryph.com/news/8548')

    def test_cached_artwork_survives_list_rotation_and_avoids_repeated_downloads(self):
        self.cache.attach(self.data, [self.cover])
        previous = copy.deepcopy(self.data)
        current = version()
        self.cache.attach(current, [], previous)
        self.assertEqual(current['artwork_summary']['matched'], 1)
        self.cache.attach(current, [self.cover], previous)
        self.assertEqual(len(self.calls), 1)

    def test_image_failure_does_not_drop_events_or_dates(self):
        before = [(e['id'], copy.deepcopy(e['start']), copy.deepcopy(e['end'])) for e in self.data['events']]
        self.cache.downloader = lambda url: (_ for _ in ()).throw(OSError('offline'))
        self.cache.attach(self.data, [self.cover])
        self.assertEqual(self.data['artwork_summary']['errors'], 1)
        self.assertEqual(before, [(e['id'], e['start'], e['end']) for e in self.data['events']])

    def test_bad_mime_and_oversized_images_are_rejected(self):
        for data in (b'<svg onload="alert(1)">', b'\xff\xd8\xff' + b'x' * 5_000_000):
            self.cache.downloader = lambda url, data=data: data
            with self.assertRaises(ValueError): self.cache.cache(self.cover['cover'])

    def test_cdn_allowlist_and_cache_path_cannot_read_arbitrary_files(self):
        for url in ('https://evil.example/upload/image/a.jpg', 'http://web.hycdn.cn/upload/image/a.jpg', 'https://web.hycdn.cn.evil.example/upload/image/a.jpg', 'https://web.hycdn.cn:abc/upload/image/a.jpg'):
            self.assertFalse(official_image(url))
        for key in ('../../VERSION', 'image.svg', '../'+'a'*32+'.jpg'):
            self.assertIsNone(self.cache.path(key))

    def test_content_is_saved_once_and_only_image_key_is_served(self):
        key = self.cache.cache(self.cover['cover'])
        self.assertTrue(key.endswith('.png'))
        self.assertEqual(self.cache.path(key).read_bytes(), PNG)
        self.assertIsNone(self.cache.path('a'*32+'.jpg'))
