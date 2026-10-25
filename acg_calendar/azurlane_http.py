"""Bounded public BWIKI/English Wiki parse APIs and two image CDNs."""
import time
import urllib.parse
import urllib.request

from . import __version__
from .network import read_response


def allowed(url, image=False):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or '..' in urllib.parse.unquote(p.path).split('/'):
            return False
        if image:
            return ((p.hostname == 'patchwiki.biligame.com' and p.path.startswith('/images/blhx/')) or
                    (p.hostname == 'azurlane.netojuu.com' and p.path.startswith('/images/'))) and p.path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
        if p.hostname == 'wiki.biligame.com' and p.path == '/blhx/api.php':
            return True
        if p.hostname == 'azurlane.koumakan.jp' and p.path == '/w/api.php':
            query = urllib.parse.parse_qs(p.query)
            return (set(query) <= {'action', 'page', 'prop', 'format', 'formatversion'} and
                    query.get('action') == ['parse'] and query.get('format') == ['json'] and
                    query.get('prop') == ['text|revid'] and len(query.get('page', [''])[0]) <= 200)
        return False
    except (ValueError, TypeError):
        return False


class AzurLaneHTTP:
    def __init__(self):
        self.last = 0

    def read(self, url, image=False):
        if not allowed(url, image):
            raise ValueError('碧蓝航线来源超出配置范围')
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not allowed(newurl, image) or urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
                    raise ValueError('Wiki 跳转超出配置范围')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        request = urllib.request.Request(url, headers={'User-Agent': f'PlaytimeCalendar/{__version__} (https://github.com/ZhenyuFu039/acg-calendar)', 'Accept-Encoding': 'identity'})
        limit = 5_000_000 if image else 2_000_000
        result = read_response(request, Redirect, limit=limit, timeout=20, image=image).data
        if len(result) > limit:
            raise ValueError('Wiki 响应超过大小限制')
        return result
