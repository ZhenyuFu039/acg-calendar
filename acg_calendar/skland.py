"""Check official Skland public availability without account/device credentials."""
import datetime as dt
import json

from .parser import iso, stamp
from .wiki_http import WikiHTTP

CATALOG = 'https://wiki.skland.com/endfield/catalog?mainTypeId=1&subTypeId=9&filterIds=&header=0'
API = 'https://zonai.skland.com/web/v1/wiki/item/catalog?typeMainId=1&typeSubId=9'


class SklandProbe:
    def __init__(self, store, fetcher=None):
        self.store = store
        self.fetcher = fetcher or WikiHTTP()

    def check(self, force=False):
        previous = self.store.get_meta('source:skland', {})
        now = dt.datetime.now(dt.timezone.utc)
        if not force and previous.get('checked_at') and (now - stamp(previous['checked_at'])).total_seconds() < 86400:
            return previous
        result = {'name': '森空岛终末地 Wiki', 'url': CATALOG, 'api_url': API, 'kind': 'official_wiki', 'checked_at': iso(now), 'used_for_calendar': False}
        try:
            data = json.loads(self.fetcher(API))
            result['code'] = data.get('code')
            if data.get('code') == 10001:
                result.update(status='device_validation_required', message='公开接口返回设备信息无效；未参与排期，继续使用官方公告和导览')
            elif data.get('code') == 0:
                result.update(status='available_needs_mapping', message='公开接口可读，尚需核对具体活动字段；暂不覆盖已验证排期')
            else:
                result.update(status='unavailable', message=str(data.get('message') or '接口结构变化')[:180])
        except Exception as error:
            result.update(status='unavailable', message=str(error)[:180])
        self.store.set_meta('source:skland', result)
        return result
