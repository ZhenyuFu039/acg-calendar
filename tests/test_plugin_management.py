"""F15: list plugins, turn them off without touching personal records, apply after restart."""
import json
import shutil

from app import Application
from acg_calendar import backup
from acg_calendar.parser import ROOT
from test_calendar import TempStoreTest

SAMPLE = ROOT / 'examples/plugins/sample-game'


class PluginManagementTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        plugins = self.directory / 'plugins'
        shutil.copytree(SAMPLE, plugins / 'sample-game')
        off = plugins / 'manifest-off'
        shutil.copytree(SAMPLE, off)
        manifest = json.loads((off / 'manifest.json').read_text(encoding='utf-8'))
        manifest.update(enabled=False, game={**manifest['game'], 'id': 'manifest-off'})
        (off / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        broken = plugins / 'broken'
        broken.mkdir()
        (broken / 'manifest.json').write_text('{"api_version": 99}', encoding='utf-8')

    def start(self):
        return Application(self.directory, auto_sync=False, desktop=False)

    def rows(self, app):
        return {r['folder']: r for r in app.plugin_report()['plugins']}

    def test_report_lists_every_folder_with_its_state(self):
        app = self.start()
        rows = self.rows(app)
        self.assertEqual(rows['sample-game']['status'], 'loaded')
        self.assertEqual(rows['sample-game']['name'], '示例游戏（虚构）')
        self.assertEqual(rows['manifest-off']['status'], 'manifest_disabled')
        self.assertEqual(rows['broken']['status'], 'error')
        self.assertIn('api_version', rows['broken']['error'])
        self.assertFalse(any(r['restart_required'] for r in rows.values()))
        self.assertEqual({g['id'] for g in app.plugin_report()['builtins']}, {'endfield-cn', 'arknights-cn'})

    def test_disable_applies_after_restart_and_keeps_personal_records(self):
        app = self.start()
        event = next(e for v in app.versions() if v['game'] == 'sample-game' for e in v['events'])
        app.update_event(event['id'], {'starred': True, 'notes': '保留我'})
        plan = app.workspace.plan({'title': '计划', 'start': '2026-11-01T00:00:00Z', 'end': '2026-11-01T01:00:00Z', 'timezone': 'UTC', 'game_id': 'sample-game'})
        app.update_settings({'disabled_plugins': ['sample-game']})
        row = self.rows(app)['sample-game']
        self.assertEqual((row['status'], row['enabled_setting'], row['restart_required']), ('loaded', False, True))
        self.assertIn('sample-game', app.registry.ids, 'nothing changes until restart')

        app = self.start()
        self.assertNotIn('sample-game', app.registry.ids)
        self.assertFalse(any(e['game'] == 'sample-game' for e in app.state()['events']))
        self.assertEqual(self.rows(app)['sample-game']['status'], 'disabled')
        self.assertEqual(self.store.state()[event['id']]['notes'], '保留我')
        self.assertEqual([p['id'] for p in self.store.plans()], [plan['id']])

        app.update_settings({'disabled_plugins': []})
        self.assertTrue(self.rows(app)['sample-game']['restart_required'])
        app = self.start()
        restored = next(e for e in app.state()['events'] if e['id'] == event['id'])
        self.assertTrue(restored['starred'])
        self.assertEqual(restored['notes'], '保留我')

    def test_disabled_list_is_validated(self):
        app = self.start()
        for value in ('sample-game', ['..'], ['a/b'], ['x', 'x'], [1], ['a' * 101]):
            with self.assertRaisesRegex(ValueError, '停用插件列表无效'):
                app.update_settings({'disabled_plugins': value})
        app.update_settings({'disabled_plugins': ['not-installed']})  # Harmless if the folder is gone.
        self.assertEqual(self.store.settings()['disabled_plugins'], ['not-installed'])


class PluginToggleEdgeTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugins = self.directory / 'plugins'

    def install(self, folder, game_id):
        target = self.plugins / folder
        shutil.copytree(SAMPLE, target)
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        manifest['game']['id'] = game_id
        (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    def start(self):
        return Application(self.directory, auto_sync=False, desktop=False)

    def row(self, app, folder):
        return next(r for r in app.plugin_report()['plugins'] if r['folder'] == folder)

    def test_chinese_and_spaced_folder_names_can_be_turned_off_and_on(self):
        self.install('中文 日历', 'cn-sample')
        app = self.start()
        self.assertEqual(self.row(app, '中文 日历')['status'], 'loaded')
        app.set_plugin_enabled({'folder': '中文 日历', 'enabled': False})
        app = self.start()
        self.assertNotIn('cn-sample', app.registry.ids)
        self.assertEqual(self.row(app, '中文 日历')['status'], 'disabled')
        app.set_plugin_enabled({'folder': '中文 日历', 'enabled': True})
        self.assertIn('cn-sample', self.start().registry.ids)

    def test_disabled_broken_manifest_stays_off_after_restart(self):
        broken = self.plugins / 'broken-json'
        broken.mkdir(parents=True)
        (broken / 'manifest.json').write_text('{not json', encoding='utf-8')
        app = self.start()
        self.assertEqual(self.row(app, 'broken-json')['status'], 'error')
        app.set_plugin_enabled({'folder': 'broken-json', 'enabled': False})
        for _ in range(2):  # Stays settled on every later start.
            app = self.start()
            row = self.row(app, 'broken-json')
            self.assertEqual((row['status'], row['restart_required']), ('disabled', False))
            self.assertIn('error', row, 'the reason is kept for diagnosis')
            self.assertEqual(app.registry.errors, [])
        app.set_plugin_enabled({'folder': 'broken-json', 'enabled': True})
        self.assertTrue(self.row(app, 'broken-json')['restart_required'])
        self.assertEqual(self.row(self.start(), 'broken-json')['status'], 'error')

    def test_concurrent_toggles_of_different_plugins_all_persist(self):
        import threading
        app = self.start()
        folders = ['plugin-%02d' % i for i in range(20)]
        threads = [threading.Thread(target=app.set_plugin_enabled, args=({'folder': f, 'enabled': False},)) for f in folders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.store.settings()['disabled_plugins'], sorted(folders))

    def test_toggle_requests_are_validated(self):
        app = self.start()
        for payload in ({'folder': '../x', 'enabled': False}, {'folder': 'a\\b', 'enabled': False}, {'folder': 'a:b', 'enabled': False},
                        {'folder': '.', 'enabled': True}, {'folder': 'ok', 'enabled': 'no'}, {'folder': 'ok'}, {'folder': ' padded', 'enabled': False}):
            with self.assertRaises(ValueError):
                app.set_plugin_enabled(payload)
        self.assertEqual(self.store.settings()['disabled_plugins'], [])


class ClearGameRecordsTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        shutil.copytree(SAMPLE, self.directory / 'plugins' / 'sample-game')
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.sample = [e for v in self.app.versions() if v['game'] == 'sample-game' for e in v['events']]
        self.other = next(e for v in self.app.versions() if v['game'] == 'endfield-cn' for e in v['events'])
        self.app.update_event(self.sample[0]['id'], {'starred': True, 'notes': '示例备注'})
        self.app.update_event(self.sample[1]['id'], {'completed': True})
        self.app.update_event(self.other['id'], {'starred': True, 'notes': '其他游戏'})
        self.plan = self.app.workspace.plan({'title': '示例计划', 'start': '2026-11-01T00:00:00Z', 'end': '2026-11-01T01:00:00Z', 'timezone': 'UTC', 'game_id': 'sample-game'})
        self.free = self.app.workspace.plan({'title': '未关联计划', 'start': '2026-11-01T00:00:00Z', 'end': '2026-11-01T01:00:00Z', 'timezone': 'UTC'})

    def test_preview_counts_and_clear_touches_only_that_game(self):
        preview = self.app.preview_clear({'game': 'sample-game'})
        self.assertEqual({k: preview[k] for k in ('marks', 'starred', 'completed', 'notes', 'plans')}, {'marks': 2, 'starred': 1, 'completed': 1, 'notes': 1, 'plans': 1})
        before = len(backup.listing(self.directory))
        result = self.app.clear_game_records({'game': 'sample-game'})
        state = self.store.state()
        self.assertNotIn(self.sample[0]['id'], state)
        self.assertNotIn(self.sample[1]['id'], state)
        self.assertEqual(state[self.other['id']]['notes'], '其他游戏')
        self.assertEqual([p['id'] for p in self.store.plans()], [self.free['id']])
        self.assertEqual(len(backup.listing(self.directory)), before + 1)
        self.assertEqual(backup.listing(self.directory)[0]['reason'], 'before-clear')
        # The automatic backup brings everything back.
        self.app.restore_backup({'name': result['safety_backup']})
        self.assertEqual(self.store.state()[self.sample[0]['id']]['notes'], '示例备注')
        self.assertIn(self.plan['id'], [p['id'] for p in self.store.plans()])

    def test_disabled_game_can_be_cleared_and_unknown_games_are_rejected(self):
        self.app.set_plugin_enabled({'folder': 'sample-game', 'enabled': False})
        app = Application(self.directory, auto_sync=False, desktop=False)
        self.assertNotIn('sample-game', app.registry.ids)
        self.assertEqual(app.clear_game_records({'game': 'sample-game'})['marks'], 2)
        with self.assertRaises(LookupError):
            app.preview_clear({'game': 'no-such-game'})
        for payload in ({}, {'game': 1}, {'game': 'sample-game', 'extra': 1}):
            with self.assertRaises(ValueError):
                app.preview_clear(payload)
