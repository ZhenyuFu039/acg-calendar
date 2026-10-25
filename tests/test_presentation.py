"""Linked presentation, explicit trees and independent local play plans."""
import copy
import datetime as dt
import shutil

from app import Application
from acg_calendar.parser import ROOT, stamp
from acg_calendar.presentation import context, local_instant, in_focus
from acg_calendar.workspace import timeline
from test_calendar import TempStoreTest


class PresentationTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugin = self.directory / 'plugins/sample-game'
        shutil.copytree(ROOT / 'examples/plugins/sample-game', self.plugin)
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.w = self.app.workspace
        self.w.update({'view':{'selected_games':['sample-game'], 'scope':'single'}})
        self.versions = self.app.scoped_versions('sample-game')

    def test_dst_local_gap_and_ambiguous_choices(self):
        with self.assertRaisesRegex(ValueError, '不存在'):
            local_instant('2026-03-08T02:30', 'America/New_York')
        with self.assertRaisesRegex(ValueError, '重复'):
            local_instant('2026-11-01T01:30', 'America/New_York')
        self.assertEqual(local_instant('2026-11-01T01:30','America/New_York',0),'2026-11-01T05:30:00Z')
        self.assertEqual(local_instant('2026-11-01T01:30','America/New_York',1),'2026-11-01T06:30:00Z')
        for fold in (True,2,'1'):
            with self.assertRaises(ValueError): local_instant('2026-11-01T01:30','America/New_York',fold)

    def test_focus_days_cover_dst_23_and_25_hours(self):
        for date,hours in [('2026-03-08',23),('2026-11-01',25)]:
            self.w.update({'view':{'focus':{'date':date,'event_id':None},'focus_timezone':'America/New_York'}})
            b=context(self.versions,self.w.get()['view'])['focus_bounds']
            self.assertEqual((stamp(b['end'])-stamp(b['start'])).total_seconds()/3600,hours)
            event=copy.deepcopy(self.versions[0]['events'][0]);event['start']['at']=b['start'];event['end']['at']=b['end']
            self.assertTrue(in_focus(event,b))
            event['end']['at']=b['start'];event['start']['at']='2026-01-01T00:00:00Z'
            self.assertFalse(in_focus(event,b))

    def test_exact_relationships_only_and_cycle_protection(self):
        versions=copy.deepcopy(self.versions);parent,child=versions[0]['events']
        child.pop('parent_id',None);child['parent_title']=parent['title']
        self.assertEqual(context(versions,self.w.get()['view'])['parents'],{child['id']:parent['id']})
        child['parent_title']=parent['title']+'近似'
        self.assertEqual(context(versions,self.w.get()['view'])['parents'],{})
        child['parent_id']=parent['id'];parent['parent_id']=child['id']
        links=context(versions,self.w.get()['view'])['parents']
        self.assertFalse(links.get(parent['id'])==child['id'] and links.get(child['id'])==parent['id'])
        self.w.update({'view':{'collapsed_parents':[parent['id']]}})
        self.assertEqual(self.app.workspace.get()['view']['collapsed_parents'],[parent['id']])

    def test_density_counts_nodes_once_and_exchanges_separately(self):
        versions=copy.deepcopy(self.versions);parent,child=versions[0]['events']
        parent['phases']=[{'title':'活动结束','at':parent['end']['at'],'state':'exact'}, {'title':'挑战关闭','at':'2026-11-03T01:00:00Z','state':'exact'}, {'title':'预计截止','at':'2026-11-04T01:00:00Z','state':'estimated'}]
        child['end']={'at':'2026-11-03T02:00:00Z','state':'exact'}
        result=timeline(versions,self.w.get(),timezone='UTC')
        self.assertEqual(result['deadline_density']['2026-11-03'],{'activities':1,'exchanges':1})
        self.assertEqual(len(result['presentation']['deadline_nodes']),3)
        self.assertNotIn('2026-11-04',result['deadline_density'])
        parent['completed']=True
        self.assertEqual(len(context(versions,self.w.get()['view'])['deadline_nodes']),1)

    def test_focus_projection_and_density_do_not_depend_on_fold(self):
        parent,child=self.versions[0]['events']
        plan=self.w.plan({'title':'玩一下','local_start':'2026-11-05T18:00','local_end':'2026-11-05T19:00','timezone':'America/New_York','game_id':'sample-game'})
        before=timeline(self.versions,self.w.get(),[plan])
        self.w.update({'view':{'focus':{'date':'2026-11-05','event_id':None},'focus_timezone':'America/New_York','collapsed_parents':[parent['id']]}})
        after=timeline(self.versions,self.w.get(),[plan])
        self.assertEqual([e['id'] for e in after['events']],[child['id']])
        self.assertEqual(after['plans'],[plan])
        self.assertEqual(before['deadline_density'],after['deadline_density'])
        self.assertEqual(self.app.state()['provider_api_version'],1)

    def test_plan_moves_restarts_and_source_changes_are_independent(self):
        event=self.versions[0]['events'][0];original=copy.deepcopy(event)
        plan=self.w.plan({'title':'游玩','local_start':'2026-11-01T01:15','local_end':'2026-11-01T02:15','timezone':'America/New_York','fold_start':0,'game_id':'sample-game','event_id':event['id']})
        moved=self.w.plan({'move_minutes':1440},plan['id'])
        self.assertEqual(stamp(moved['start'])-stamp(plan['start']),dt.timedelta(hours=24))
        self.assertEqual(stamp(moved['end'])-stamp(moved['start']),dt.timedelta(hours=2))
        self.assertEqual(self.app.scoped_versions('sample-game')[0]['events'][0],original)
        self.app.update_event(event['id'],{'end_override':'2026-12-01T00:00:00Z'})
        again=Application(self.directory,auto_sync=False,desktop=False)
        self.assertEqual(again.store.plans(),[moved])
        manifest=self.plugin/'manifest.json';manifest.write_text(manifest.read_text(encoding='utf-8').replace('"api_version": 1','"api_version": 999'),encoding='utf-8')
        removed=Application(self.directory,auto_sync=False,desktop=False)
        self.assertNotIn('sample-game',removed.registry.ids)
        saved=removed.workspace.plan({'title':'来源失效仍可修改'},plan['id'])
        self.assertEqual(saved['event_id'],event['id'])
        self.assertEqual(saved['start'],moved['start'])

    def test_bad_plan_updates_leave_original_intact(self):
        p=self.w.plan({'title':'原计划','start':'2026-10-20T12:00:00Z','end':'2026-10-20T13:00:00Z'})
        for body in ({'start':'bad','move_minutes':60},{'move_minutes':True},{'local_start':'2026-11-01T01:30','timezone':'America/New_York'},{'timezone':'wrong/zone'}):
            with self.assertRaises(ValueError):self.w.plan(body,p['id'])
            self.assertEqual(self.store.plans(),[p])

    def test_scope_changes_preserve_date_and_partial_focus_updates(self):
        self.w.update({'view':{'focus':{'date':'2026-11-05','event_id':None},'focus_timezone':'America/New_York'}})
        self.w.update({'view':{'focus':{'event_id':self.versions[0]['events'][0]['id']}}})
        self.assertEqual(self.w.get()['view']['focus']['date'],'2026-11-05')
        self.w.update({'view':{'scope':'single','selected_games':['arknights-cn']}})
        self.assertEqual(self.w.get()['view']['focus'],{'date':'2026-11-05','event_id':None})
        for focus in ({'unexpected':True},{'date':'20261105'}):
            with self.assertRaises(ValueError):self.w.update({'view':{'focus':focus}})
        self.assertEqual(self.w.get()['view']['focus']['date'],'2026-11-05')
