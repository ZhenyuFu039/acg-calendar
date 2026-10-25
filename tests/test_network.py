"""No live traffic: quotas, redirects, conditional cache, pause and cooldown."""
import io
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from acg_calendar.network import NetworkManager, NetworkDeferred, site_for
from acg_calendar.wiki_http import WikiRedirect
from test_calendar import TempStoreTest


class Clock:
    def __init__(self): self.now = 1790000000.
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class Reply(io.BytesIO):
    def __init__(self, data=b'{}', headers=None):
        super().__init__(data)
        self.headers = headers or {'Content-Type': 'application/json', 'ETag': 'v1'}


class Opener:
    def __init__(self, responses=None): self.responses, self.calls = list(responses or []), []
    def open(self, req, timeout):
        self.calls.append(req)
        item = self.responses.pop(0) if self.responses else Reply()
        if isinstance(item, Exception): raise item
        return item


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.opener = Clock(), Opener()
        self.manager = NetworkManager(clock=self.clock, waiter=self.clock.advance, opener=self.opener, uniform=lambda a,b:0)

    def read(self, suffix='', host='wiki.biligame.com', **kwargs):
        return self.manager.read(urllib.request.Request('https://' + host + '/zzz/api.php?' + suffix), WikiRedirect, **kwargs)

    def test_shared_site_interval_across_plugins_and_media(self):
        self.assertEqual(site_for('https://patchwiki.biligame.com/images/zzz/a.png'), 'biligame.com')
        with self.manager.job():
            self.read('one')
            start = self.clock()
            self.read('two', host='patchwiki.biligame.com')
        self.assertGreaterEqual(self.clock() - start, 2)
        self.assertEqual(self.manager.snapshot()['sites'][0]['requests'], 2)

    def test_round_budget_and_hourly_limit_survive_new_sync_jobs(self):
        self.manager.policy.update(round_limit=2, hourly_limit=3)
        with self.manager.job():
            self.read('one'); self.read('two')
            with self.assertRaises(NetworkDeferred): self.read('three')
        self.read('three')
        with self.assertRaises(NetworkDeferred): self.read('four')
        self.clock.advance(3600)
        self.read('four')
        self.assertEqual(len(self.opener.calls), 4)

    def test_retry_after_does_not_retry_or_allow_manual_bypass(self):
        self.opener.responses = [urllib.error.HTTPError('https://wiki.biligame.com', 429, 'busy', {'Retry-After': '120'}, None), Reply()]
        with self.assertRaises(urllib.error.HTTPError): self.read('one')
        self.assertEqual(len(self.opener.calls), 1)
        self.manager.update({'paused': False})
        self.clock.advance(119)
        with self.assertRaises(NetworkDeferred): self.read('two')
        self.clock.advance(1)
        self.read('two')
        self.assertEqual(len(self.opener.calls), 2)

    def test_server_http_date_is_respected(self):
        from email.utils import formatdate
        retry = formatdate(self.clock() + 600, usegmt=True)
        self.opener.responses = [urllib.error.HTTPError('https://wiki.biligame.com', 503, 'busy', {'Retry-After': retry}, None)]
        with self.assertRaises(urllib.error.HTTPError): self.read('one')
        self.assertEqual(self.manager.sites['biligame.com']['cooldown_until'], self.clock() + 600)

    def test_access_denied_and_html_challenge_pause_the_site(self):
        self.opener.responses = [urllib.error.HTTPError('https://wiki.biligame.com',403,'forbidden',{},None)]
        with self.assertRaises(urllib.error.HTTPError): self.read('one')
        self.assertTrue(self.manager.snapshot()['sites'][0]['paused'])
        self.manager.update({'site': 'biligame.com', 'paused': False})
        self.clock.advance(31)
        self.opener.responses = [Reply(b'<html><title>Just a moment...</title>', {'Content-Type': 'text/html'})]
        with self.assertRaises(ValueError): self.read('two')
        self.assertTrue(self.manager.sites['biligame.com']['paused'])

    def test_three_network_failures_open_circuit(self):
        self.opener.responses = [OSError('offline') for _ in range(3)]
        for i in range(3):
            with self.assertRaises(OSError): self.read(str(i))
            self.clock.advance(121)
        self.assertTrue(self.manager.sites['biligame.com']['paused'])
        with self.assertRaises(NetworkDeferred): self.read('four')
        self.assertEqual(len(self.opener.calls),3)

    def test_fresh_cache_works_while_paused_and_does_not_consume_quota(self):
        data = self.read('one').data
        self.manager.update({'paused': True})
        self.assertEqual(self.read('one').data,data)
        with self.assertRaises(NetworkDeferred): self.read('new')
        self.assertEqual(len(self.opener.calls),1)
        self.assertEqual(self.manager.snapshot()['cache_hits'],1)

    def test_conditional_get_reuses_304_body_with_varying_headers(self):
        self.opener.responses = [Reply(b'{"a":1}', {'Content-Type':'application/json; charset=utf-8','ETag':'"revision"','Last-Modified':'Mon, 19 Oct 2026 10:00:00 GMT'}), urllib.error.HTTPError('url',304,'unchanged',{},None)]
        self.read('one'); self.clock.advance(301)
        response=self.read('one')
        self.assertEqual(response.data,b'{"a":1}')
        self.assertEqual(response.charset,'utf-8')
        self.assertEqual(self.opener.calls[-1].get_header('If-none-match'),'"revision"')
        self.assertIsNotNone(self.opener.calls[-1].get_header('If-modified-since'))

    def test_redirects_are_rate_limited_and_charge_the_same_round(self):
        calls=[]
        def factory(handler):
            class RedirectOpener:
                def open(inner, req, timeout):
                    calls.append(req.full_url)
                    if len(calls)==1:
                        return handler.redirect_request(req,io.BytesIO(),302,'redirect',{},'https://prts.wiki/api.php?target')
                    return Reply()
            return RedirectOpener()
        self.manager.opener=None
        with patch('urllib.request.build_opener',side_effect=factory): self.manager.read('https://prts.wiki/api.php?redirect',WikiRedirect)
        self.assertEqual(len(calls),2)
        self.assertGreaterEqual(self.clock(),1790000002)
        self.assertEqual(self.manager.sites['prts.wiki']['requests'],2)

    def test_response_size_and_settings_cannot_disable_limits(self):
        self.opener.responses=[Reply(b'0123456789')]
        with self.assertRaises(ValueError): self.read('one',limit=5)
        for p in [{'policy':{'interval_seconds':0}},{'policy':{'interval_seconds':9}},{'policy':{'hourly_limit':121}},{'policy':{'round_limit':65}},{'policy':{'round_limit':True}},{'site':'unknown','paused':False},{'paused':'false'}]:
            with self.assertRaises(ValueError): self.manager.update(p)

    def test_source_interval_and_tighter_policy_take_effect_immediately(self):
        self.read('one'); start=self.clock()
        self.manager.update({'policy':{'interval_seconds':10}})
        self.read('two');self.assertEqual(self.clock()-start,10)
        start=self.clock();self.read('three',min_interval=20)
        self.assertEqual(self.clock()-start,20)

    def test_request_jitter_is_chosen_once_and_survives_status_reads(self):
        self.manager.uniform=lambda a,b:b
        self.read('one');start=self.clock()
        self.assertEqual(self.manager.sites['biligame.com']['next_at'],start+15)
        self.manager.snapshot();self.manager.snapshot()
        self.read('two')
        self.assertEqual(self.clock()-start,15)

    def test_no_store_no_cache_and_public_metadata_only(self):
        self.opener.responses=[Reply(b'private',{'Cache-Control':'no-store'}),Reply(b'new'),Reply(b'validate',{'Cache-Control':'no-cache','ETag':'version','Set-Cookie':'not-retained'}),urllib.error.HTTPError('url',304,'unchanged',{},None)]
        self.read('one');self.read('one')
        self.read('two');self.read('two')
        self.assertEqual(len(self.opener.calls),4)
        self.assertEqual(self.opener.calls[-1].get_header('If-none-match'),'version')
        self.assertFalse(any('set-cookie' in row['headers'] for row in self.manager.memory.values()))

    def test_unbounded_retry_header_cannot_break_status_json(self):
        self.opener.responses=[urllib.error.HTTPError('url',503,'busy',{'Retry-After':'Infinity'},None)]
        with self.assertRaises(urllib.error.HTTPError):self.read('one')
        status=self.manager.snapshot()['sites'][0]
        self.assertTrue(status['paused'])
        self.assertIsInstance(status['next_allowed_at'],str)

    def test_pause_cancels_queued_requests_without_extra_network_traffic(self):
        manager=NetworkManager(opener=self.opener)
        manager.policy['interval_seconds']=60
        manager.read('https://wiki.biligame.com/zzz/api.php?first',WikiRedirect)
        errors=[]
        def run():
            try: manager.read('https://wiki.biligame.com/zzz/api.php?second',WikiRedirect)
            except Exception as e: errors.append(e)
        thread=threading.Thread(target=run);thread.start()
        for _ in range(30):
            if manager.snapshot()['waiting']: break
            time.sleep(.01)
        manager.update({'paused':True});thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0],NetworkDeferred)
        self.assertEqual(len(self.opener.calls),1)

    def test_site_concurrency_is_one_but_different_sites_can_run(self):
        entered=threading.Event();release=threading.Event();counts={'active':0,'peak':0};lock=threading.Lock()
        class BlockingOpener:
            def open(inner,req,timeout):
                with lock: counts['active']+=1;counts['peak']=max(counts['peak'],counts['active'])
                entered.set();release.wait(2)
                with lock: counts['active']-=1
                return Reply()
        manager=NetworkManager(opener=BlockingOpener(), uniform=lambda a,b:0);manager.policy['interval_seconds']=.01
        def run(url):manager.read(url,WikiRedirect)
        a=threading.Thread(target=run,args=('https://wiki.biligame.com/zzz/api.php?a',));b=threading.Thread(target=run,args=('https://patchwiki.biligame.com/images/b.png',))
        a.start();self.assertTrue(entered.wait(1));b.start();time.sleep(.05)
        self.assertEqual(counts['peak'],1)
        release.set();a.join(2);b.join(2);self.assertFalse(a.is_alive() or b.is_alive())


class PersistenceTests(TempStoreTest):
    def test_restart_preserves_cache_quota_and_server_cooldown(self):
        clock=Clock();opener=Opener([Reply(b'cached'),urllib.error.HTTPError('url',503,'busy',{'Retry-After':'600'},None)])
        manager=NetworkManager(self.store,clock=clock,waiter=clock.advance,opener=opener)
        manager.read('https://wiki.biligame.com/zzz/api.php?cache',WikiRedirect)
        with self.assertRaises(urllib.error.HTTPError):manager.read('https://wiki.biligame.com/zzz/api.php?busy',WikiRedirect)
        restored=NetworkManager(self.store,clock=clock,waiter=clock.advance,opener=Opener())
        self.assertEqual(restored.read('https://wiki.biligame.com/zzz/api.php?cache',WikiRedirect).data,b'cached')
        with self.assertRaises(NetworkDeferred):restored.read('https://wiki.biligame.com/zzz/api.php?new',WikiRedirect)
        self.assertEqual(restored.snapshot()['sites'][0]['requests'],2)


if __name__=='__main__': unittest.main()
