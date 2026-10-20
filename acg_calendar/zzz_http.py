"""Anonymous, bounded access to the configured ZZZ official API and BWIKI."""
import time
import urllib.parse
import urllib.request

from . import __version__
from .network import read_response

API = 'https://sg-public-api-static.hoyoverse.com/content_v2_user/app/3e9196a4b9274bd7/'
WIKI = 'https://wiki.biligame.com/zzz/'


def allowed(url, image=False):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or '..' in urllib.parse.unquote(p.path).split('/'):
            return False
        if image:
            return ((p.hostname == 'fastcdn.hoyoverse.com' and p.path.startswith('/content-v2/nap/')) or
                    (p.hostname == 'patchwiki.biligame.com' and p.path.startswith('/images/zzz/'))) and p.path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
        return url.split('?')[0] in {API + 'getContentList', WIKI + 'api.php'}
    except (TypeError, ValueError):
        return False


class ZZZHTTP:
    def __init__(self):
        self.last = 0

    def read(self, url, image=False):
        if not allowed(url, image):
            raise ValueError('绝区零来源超出配置范围')
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not allowed(newurl, image) or urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
                    raise ValueError('绝区零来源跳转超出配置范围')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        req = urllib.request.Request(url, headers={'User-Agent': f'PlaytimeCalendar/{__version__} (https://github.com/ZhenyuFu039/acg-calendar)', 'Accept-Encoding': 'identity'})
        limit = 5_000_000 if image else 2_000_000
        result = read_response(req, Redirect, limit=limit, timeout=20, image=image).data
        if len(result) > limit:
            raise ValueError('绝区零响应超过大小限制')
        return result
