"""F20: manual corrections and maintenance times typed in the display time zone, stored in UTC."""
from http.server import ThreadingHTTPServer
import json
import threading
import urllib.error
import urllib.request

from app import Application, handler_for
from test_calendar import TempStoreTest


class LocalCorrectionTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.event = next(e for v in self.app.versions() if v['game'] == 'endfield-cn' for e in v['events']
                          if e['time_kind'] != 'point' and e['start']['at'])

    def stored(self):
        return next(e for v in self.app.versions() for e in v['events'] if e['id'] == self.event['id'])

    def test_only_the_changed_endpoint_is_updated_and_seconds_survive(self):
        self.app.update_event(self.event['id'], {'start_override': '2026-01-01T00:00:59Z'})
        self.app.update_event(self.event['id'], {'end_local': '2027-01-01T08:00', 'timezone': 'Asia/Shanghai'})
        event = self.stored()
        self.assertEqual(event['start']['at'], '2026-01-01T00:00:59Z')
        self.assertEqual(event['end']['at'], '2027-01-01T00:00:00Z')

    def test_repeated_and_missing_local_times_follow_the_plan_rules(self):
        self.app.update_event(self.event['id'], {'start_override': '2026-01-01T00:00:00Z'})
        with self.assertRaisesRegex(ValueError, '重复'):
            self.app.update_event(self.event['id'], {'end_local': '2026-11-01T01:30', 'timezone': 'America/New_York'})
        self.app.update_event(self.event['id'], {'end_local': '2026-11-01T01:30', 'timezone': 'America/New_York', 'fold_end': 0})
        self.assertEqual(self.stored()['end']['at'], '2026-11-01T05:30:00Z')
        self.app.update_event(self.event['id'], {'end_local': '2026-11-01T01:30', 'timezone': 'America/New_York', 'fold_end': 1})
        self.assertEqual(self.stored()['end']['at'], '2026-11-01T06:30:00Z')
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.app.update_event(self.event['id'], {'end_local': '2026-03-08T02:30', 'timezone': 'America/New_York'})

    def test_invalid_combinations_are_rejected_and_null_clears(self):
        for payload, message in (({'end_local': '2027-01-01T08:00', 'end_override': '2027-01-01T00:00:00Z', 'timezone': 'UTC'}, '不能同时'),
                                 ({'end_local': '2027-01-01T08:00'}, 'IANA'), ({'end_local': '2027-01-01 08:00', 'timezone': 'UTC'}, '当地日期'),
                                 ({'end_local': '2027-01-01T08:00', 'timezone': 'Mars/Base'}, 'IANA')):
            with self.assertRaisesRegex(ValueError, message):
                self.app.update_event(self.event['id'], payload)
        original = self.stored()['end']['at']
        self.app.update_event(self.event['id'], {'end_local': '2027-01-01T08:00', 'timezone': 'UTC'})
        self.app.update_event(self.event['id'], {'end_local': None})
        self.assertEqual(self.stored()['end']['at'], original)


class LocalMaintenanceTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, payload):
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(), headers={"X-Calendar-Token": self.app.csrf})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_actual_reopening_time_in_display_zone(self):
        version = next(v for v in self.app.versions() if v['maintenance'].get('start'))
        status, body = self.post(f"/api/versions/{version['id']}/maintenance", {'actual_local': '2026-10-15T20:15', 'timezone': 'Asia/Shanghai'})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.app.store.state()['version:' + version['id']]['actual_end'], '2026-10-15T12:15:00Z')
        status, body = self.post(f"/api/versions/{version['id']}/maintenance", {'actual_local': '2026-10-15T20:15', 'timezone': 'Asia/Shanghai', 'extra': 1})
        self.assertEqual(status, 400)
        status, _ = self.post(f"/api/versions/{version['id']}/maintenance", {'actual_local': None})
        self.assertEqual(status, 200)
        self.assertIsNone(self.app.store.state()['version:' + version['id']]['actual_end'])

    def test_malformed_json_gets_a_plain_message(self):
        request = urllib.request.Request(self.base + '/api/settings', data=b'not json', headers={"X-Calendar-Token": self.app.csrf})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(json.loads(caught.exception.read()), {'error': '请求内容不是有效的 JSON'})
