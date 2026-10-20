"""Layout/theme compatibility and isolation from plugin and personal records."""
import copy

from app import Application
from acg_calendar.workspace import DEFAULT_LAYOUT, DEFAULT_THEME, THEMES, THEME_NAMES, LEGACY_THEMES, validate_theme
from test_calendar import TempStoreTest


class PreferencesTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app=Application(self.directory,auto_sync=False,desktop=False)
        self.workspace=self.app.workspace

    def test_legacy_schema_one_is_upgraded_without_changing_plugin_api(self):
        old={'schema_version':1,'layout':{'schema_version':1,'modules':copy.deepcopy(DEFAULT_LAYOUT['modules'])},'theme':{'schema_version':1,'preset':'dark','accent':'#B8D77A','card_opacity':.8}}
        self.store.set_meta('workspace',old)
        value=self.workspace.get()
        self.assertEqual(value['layout']['sidebar']['position'],'left')
        self.assertEqual(value['theme']['surface'],THEMES['dark']['surface'])
        self.assertEqual(value['theme']['card_opacity'],.8)
        self.assertEqual(self.app.state()['provider_api_version'],1)

    def test_layout_reorder_hide_width_and_position_roundtrip(self):
        layout=copy.deepcopy(DEFAULT_LAYOUT);layout['modules'].reverse();layout['modules'][0]['collapsed']=True
        layout['sidebar']={'visible':False,'position':'right','width':290};layout['desktop_calendar']='top';layout['max_width']=1200
        self.workspace.update({'layout':layout})
        self.assertEqual(self.workspace.get()['layout'],layout)
        theme=self.workspace.get()['theme']
        self.workspace.update({'layout':copy.deepcopy(DEFAULT_LAYOUT)})
        self.assertEqual(self.workspace.get()['theme'],theme)

    def test_theme_preset_and_custom_settings_do_not_reset_layout_or_marks(self):
        event=self.app.versions()[0]['events'][0]
        self.store.update_state(event['id'],{'starred':True,'notes':'自己的安排'})
        before=self.store.versions();layout=self.workspace.get()['layout'];layout['sidebar']['width']=300
        self.workspace.update({'layout':layout})
        self.workspace.update({'theme':{'preset':'dark','font_size':17,'radius':16,'spacing':20,'card_opacity':.65}})
        value=self.workspace.get()
        self.assertEqual(value['theme']['text'],THEMES['dark']['text'])
        self.assertEqual(value['layout']['sidebar']['width'],300)
        self.assertEqual(self.store.versions(),before)
        self.assertTrue(self.store.state()[event['id']]['starred'])
        self.workspace.update({'theme':copy.deepcopy(DEFAULT_THEME)})
        self.assertEqual(self.workspace.get()['layout'],layout)

    def test_bad_contrast_injection_and_invalid_layout_leave_previous_settings(self):
        before=self.workspace.get()
        for patch in [{'theme':{'text':'#FFFFFF'}},{'theme':{'background':'url(https://example.com)'}},{'theme':{'font_size':True}},{'theme':{'script':'alert(1)'}},{'theme':{'muted':'#FFFFFF'}}]:
            with self.assertRaises(ValueError):self.workspace.update(patch)
            self.assertEqual(self.workspace.get(),before)
        for field,value in [('sidebar',{'visible':True,'position':'center','width':200}),('max_width',99999),('desktop_calendar','floating')]:
            layout=copy.deepcopy(DEFAULT_LAYOUT);layout[field]=value
            with self.assertRaises(ValueError):self.workspace.update({'layout':layout})
        layout=copy.deepcopy(DEFAULT_LAYOUT);next(r for r in layout['modules'] if r['id']=='calendar')['visible']=False
        with self.assertRaises(ValueError):self.workspace.update({'layout':layout})

    def test_defaults_are_deep_copies(self):
        defaults=self.workspace.defaults();defaults['theme']['accent']='#FFFFFF';defaults['layout']['modules'].clear()
        self.assertEqual(self.workspace.defaults()['theme'],DEFAULT_THEME)
        self.assertTrue(self.workspace.defaults()['layout']['modules'])

    def test_native_rounding_migrates_and_remains_independent_of_web(self):
        old = copy.deepcopy(THEMES['zzz'])
        del old['desktop_radius']; del old['desktop_control_radius']
        self.store.set_meta('workspace', {'theme': old})
        migrated = self.workspace.get()['theme']
        self.assertEqual((migrated['radius'], migrated['control_radius']), (22, 32))
        self.assertEqual((migrated['desktop_radius'], migrated['desktop_control_radius']), (8, 6))
        self.workspace.update({'theme': {'desktop_radius': 5, 'desktop_control_radius': 3}})
        self.workspace.update({'theme': {'radius': 26, 'control_radius': 36}})
        theme = self.workspace.get()['theme']
        self.assertEqual((theme['desktop_radius'], theme['desktop_control_radius']), (5, 3))
        before = self.workspace.get()
        for patch in [{'desktop_radius': 17}, {'desktop_control_radius': 13}, {'desktop_radius': True}]:
            with self.assertRaises(ValueError): self.workspace.update({'theme': patch})
            self.assertEqual(self.workspace.get(), before)

    def test_all_game_presets_pass_contrast_and_preserve_layout_and_plugin_api(self):
        before=self.workspace.get()['layout']
        self.assertEqual(set(THEMES),set(THEME_NAMES))
        self.assertEqual(len(THEMES),7)
        for key,theme in THEMES.items():
            validate_theme(theme)
            self.workspace.update({'theme':theme})
            self.assertEqual(self.workspace.get()['theme'],theme)
            self.assertEqual(self.workspace.get()['layout'],before)
        self.workspace.update({'theme':THEMES['light']})
        self.assertEqual(self.workspace.get()['theme'],DEFAULT_THEME)
        self.assertEqual(self.app.state()['provider_api_version'],1)

    def test_unchanged_legacy_game_preset_migrates_and_custom_colors_remain(self):
        self.store.set_meta('workspace',{'theme':copy.deepcopy(LEGACY_THEMES['endfield'])})
        self.assertEqual(self.workspace.get()['theme'],THEMES['endfield'])
        custom={**LEGACY_THEMES['endfield'],'font_size':17,'background':'#25271E'}
        self.store.set_meta('workspace',{'theme':custom})
        migrated=self.workspace.get()['theme']
        for key,value in custom.items():self.assertEqual(migrated[key],value)
        self.assertEqual(migrated['nav_background'],custom['surface'])
        validate_theme(migrated)

    def test_luminous_accent_uses_its_own_text_contrast(self):
        self.workspace.update({'theme':THEMES['endfield']})
        self.assertEqual(self.workspace.get()['theme']['accent'],'#FFFA00')
        before=self.workspace.get()
        for patch in [{'accent_text':'#FFFFFF'},{'nav_text':'#FFFFFF'},{'bar_text':'#ECEDE7'},{'texture':'url(evil)'},{'control_radius':41},{'heading_weight':True},{'texture_opacity':13},{'event_color':'<script>'}]:
            with self.assertRaises(ValueError):self.workspace.update({'theme':patch})
            self.assertEqual(self.workspace.get(),before)

    def test_legacy_json_import_is_completed_with_semantic_tokens(self):
        old={**LEGACY_THEMES['nikke'],'font_size':16}
        self.workspace.update({'theme':old})
        theme=self.workspace.get()['theme']
        self.assertEqual(theme['font_size'],16)
        self.assertEqual(theme['nav_text'],old['text'])
        self.assertEqual(theme['bar_text'],old['text'])
        validate_theme(theme)
