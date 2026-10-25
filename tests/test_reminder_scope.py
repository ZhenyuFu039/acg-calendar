"""F13: reminder games are chosen separately from the page's game combination and filters."""
import datetime as dt

from app import Application
from acg_calendar import backup
from acg_calendar.parser import iso
from test_calendar import TempStoreTest


class ReminderScopeTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.games = sorted(self.app.registry.ids)
        self.assertGreaterEqual(len(self.games), 2)
        # One activity per game ends in two hours (manual corrections), so the 24-hour reminder fires.
        for game in self.games:
            event = next(e for v in self.app.versions() if v['game'] == game for e in v['events'] if e['time_kind'] != 'point')
            self.app.update_event(event['id'], {'start_override': iso(self.now - dt.timedelta(days=1)), 'end_override': iso(self.now + dt.timedelta(hours=2))})
        self.app.update_settings({'reminder_offsets': [1440]})

    def reminded(self):
        self.store.read_notifications()
        with self.store.connection() as conn:
            conn.execute('DELETE FROM notifications')  # Each check starts from a clean inbox.
        return sorted({n['game'] for n in self.app.reminders.tick(self.now)})

    def test_old_settings_keep_reminding_every_installed_game(self):
        saved = self.store.get_meta('settings')
        saved.pop('reminder_scope', None)
        self.store.set_meta('settings', saved)  # Settings written before F13.
        self.assertEqual(self.store.settings()['reminder_scope'], {'mode': 'all', 'games': None})
        self.assertEqual(self.reminded(), self.games)

    def test_custom_list_only_reminds_chosen_games_and_keeps_uninstalled_ids(self):
        self.app.update_settings({'reminder_scope': {'mode': 'custom', 'games': [self.games[0], 'removed-plugin']}})
        self.assertEqual(self.reminded(), [self.games[0]])
        self.app.update_settings({'reminder_scope': {'mode': 'custom', 'games': []}})
        self.assertEqual(self.reminded(), [], 'an empty custom list sends nothing')
        self.app.update_settings({'reminder_scope': {'mode': 'all', 'games': [self.games[0]]}})
        self.assertEqual(self.reminded(), self.games, 'the saved list is kept but ignored in all mode')

    def test_follow_uses_the_game_combination_but_not_page_filters(self):
        self.app.update_settings({'reminder_scope': {'mode': 'follow', 'games': []}})
        self.app.workspace.update({'view': {'scope': 'single', 'selected_games': [self.games[1]]}})
        self.assertEqual(self.reminded(), [self.games[1]])
        # Display-only filters never switch reminders off.
        self.app.update_settings({'hide_completed': True})
        self.app.workspace.update({'view': {'hide_future': True}})
        self.assertEqual(self.reminded(), [self.games[1]])
        self.app.workspace.update({'view': {'scope': 'all', 'selected_games': self.games}})
        self.assertEqual(self.reminded(), self.games)

    def test_invalid_scopes_are_rejected(self):
        for scope in ({'mode': 'some'}, {'mode': 'custom', 'games': 'x'}, {'mode': 'custom', 'games': ['a', 'a']},
                      {'mode': 'custom', 'games': [1]}, {'mode': 'all', 'games': [], 'extra': 1}, 'all'):
            with self.assertRaisesRegex(ValueError, '提醒游戏范围无效'):
                self.app.update_settings({'reminder_scope': scope})

    def test_scope_survives_backup_and_restore(self):
        chosen = {'mode': 'custom', 'games': [self.games[0]]}
        self.app.update_settings({'reminder_scope': chosen})
        made = self.app.create_backup()
        self.app.update_settings({'reminder_scope': {'mode': 'all', 'games': []}})
        self.app.restore_backup({'name': made['name']})
        self.assertEqual(self.store.settings()['reminder_scope'], chosen)
        self.assertEqual(backup.read(self.directory, made['name'])['contents']['settings']['reminder_scope'], chosen)
