from http.server import ThreadingHTTPServer
import json
import threading
import urllib.error
import urllib.request

from app import Application, handler_for
from test_calendar import TempStoreTest


class StateTagTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, tag=None):
        request = urllib.request.Request(self.base + "/api/state", headers={"If-None-Match": tag} if tag else {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers.get("ETag"), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("ETag"), error.read()

    def test_unchanged_state_answers_304_without_a_body(self):
        status, tag, body = self.get()
        self.assertEqual(status, 200)
        self.assertRegex(tag, r'^"[0-9a-f]{32}"$')
        self.assertIn("server_time", json.loads(body))
        status, again, body = self.get(tag)
        self.assertEqual((status, again, body), (304, tag, b""))

    def test_any_state_change_or_stale_tag_returns_the_full_state(self):
        _, tag, _ = self.get()
        self.app.store.update_settings({"hide_completed": True})
        status, changed, body = self.get(tag)
        self.assertEqual(status, 200)
        self.assertNotEqual(changed, tag)
        self.assertTrue(json.loads(body)["settings"]["hide_completed"])
        self.assertEqual(self.get('"0000"')[0], 200)
        self.app.store.update_settings({"hide_completed": False})
        # Content, not a counter, names the state: returning to the old data matches the old tag.
        self.assertEqual(self.get(tag)[0], 304)

    def test_a_new_process_token_changes_the_tag(self):
        _, tag, _ = self.get()
        self.app.csrf = "another-token"
        status, _, body = self.get(tag)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrf_token"], "another-token")
