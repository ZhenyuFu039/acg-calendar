"""Bounded, anonymous reads from the explicitly configured Wiki sources."""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import zlib

from . import __version__
from .network import read_response


def wiki_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in {None, 443}:
            return False
        if p.hostname == 'prts.wiki':
            return p.path == '/api.php' or p.path.startswith('/w/') or p.path == '/robots.txt'
        if p.hostname == 'media.prts.wiki':
            return p.path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
        return p.hostname == 'zonai.skland.com' and p.path in {'/web/v1/wiki/item/catalog', '/web/v1/wiki/activity'}
    except (ValueError, TypeError):
        return False


class WikiRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not wiki_url(newurl) or urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            raise ValueError('Wiki 跳转超出配置来源')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WikiHTTP:
    def __init__(self, interval=1.0, timeout=20):
        self.last = 0
        self.interval = interval
        self.timeout = timeout

    def read(self, url):
        if not wiki_url(url):
            raise ValueError('Wiki 来源不在允许范围内')
        headers = {'User-Agent': f'PlaytimeCalendar/{__version__} (local calendar; https://github.com/ZhenyuFu039/acg-calendar)', 'Accept-Encoding': 'identity', 'Accept': 'application/json,text/html,image/*;q=0.8'}
        if urllib.parse.urlsplit(url).hostname == 'zonai.skland.com':
            headers['Referer'] = 'https://wiki.skland.com/endfield/'
        request = urllib.request.Request(url, headers=headers)
        limit = 6_000_000
        data = read_response(request, WikiRedirect, limit=limit, timeout=self.timeout, image=urllib.parse.urlsplit(url).path.lower().endswith((".jpg", ".png", ".webp")), min_interval=self.interval).data
        if data[:2] == b'\x1f\x8b':
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = decoder.decompress(data, limit + 1)
            if decoder.unconsumed_tail:
                raise ValueError('Wiki 解压后过大')
        if len(data) > limit:
            raise ValueError('Wiki 响应过大，保留现有缓存')
        return data

    def __call__(self, url):
        return self.read(url).decode('utf-8')
