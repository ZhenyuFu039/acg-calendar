"""Source rechecks use persisted deadlines, independent of event countdowns."""
import datetime as dt
import threading
from unittest.mock import Mock

from app import Application
from acg_calendar.network import NetworkManager
from acg_calendar.scheduling import SyncSchedule
from acg_calendar.storage import Store
from test_calendar import TempStoreTest
from test_network import Clock


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


class ScheduleTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.schedule = SyncSchedule(self.store, clock=self.clock, uniform=lambda a,b:b)

    def success(self, seconds_ago=0):
        self.store.set_meta('sync', {'last_attempt':iso(self.clock()-seconds_ago-30), 'last_success':iso(self.clock()-seconds_ago), 'error':None})

    def test_new_and_migrated_settings_use_daily_checks_without_touching_user_data(self):
        self.assertEqual(self.store.settings()['sync_interval_minutes'], 1440)
        self.store.set_meta('conservative-sync-v1', False)
        self.store.update_settings({'sync_interval_minutes':180,'reminders_enabled':False})
        SyncSchedule(self.store)
        self.assertEqual(self.store.settings()['sync_interval_minutes'], 1440)
        self.assertFalse(self.store.settings()['reminders_enabled'])
        self.store.update_settings({'sync_interval_minutes':1080})
        SyncSchedule(self.store)
        self.assertEqual(self.store.settings()['sync_interval_minutes'], 1080)

    def test_first_run_is_staggered_and_does_not_fetch_immediately(self):
        due=self.schedule.prepare('sync',1440)['next_at']
        self.assertEqual(due,self.clock()+600)
        self.assertFalse(self.schedule.claim('sync',1440))
        self.clock.advance(600)
        self.assertTrue(self.schedule.claim('sync',1440))
        self.assertFalse(self.schedule.claim('sync',1440))

    def test_success_waits_24_hours_plus_persisted_jitter(self):
        self.success()
        due=self.schedule.prepare('sync',1440)['next_at']
        self.assertEqual(due,self.clock()+26*3600)
        self.clock.advance(24*3600)
        restored=SyncSchedule(self.store,clock=self.clock,uniform=lambda a,b:a)
        self.assertEqual(restored.prepare('sync',1440,startup=True)['next_at'],due)
        self.assertFalse(restored.claim('sync',1440))
        self.clock.advance(2*3600)
        self.assertTrue(restored.claim('sync',1440))

    def test_18_hour_choice_is_available_and_recalculates_from_last_success(self):
        self.success()
        self.schedule.prepare('sync',1440)
        self.assertEqual(self.schedule.prepare('sync',1080)['next_at'],self.clock()+20*3600)

    def test_overdue_restart_staggers_once_instead_of_forcing_a_fetch(self):
        self.success(seconds_ago=3*86400)
        due=self.schedule.prepare('sync',1440,startup=True)['next_at']
        self.assertEqual(due,self.clock()+600)
        self.clock.advance(60)
        restored=SyncSchedule(self.store,clock=self.clock,uniform=lambda a,b:a)
        self.assertEqual(restored.prepare('sync',1440,startup=True)['next_at'],due)

    def test_manual_success_resets_the_daily_check_from_completion(self):
        self.success(seconds_ago=3600)
        previous=self.schedule.prepare('sync',1440)['next_at']
        self.clock.advance(120)
        self.success()
        self.assertGreater(self.schedule.prepare('sync',1440)['next_at'],previous)

    def test_failed_attempt_also_avoids_frequent_retries(self):
        self.store.set_meta('sync',{'last_attempt':iso(self.clock()),'error':'offline'})
        self.assertEqual(self.schedule.prepare('sync',1440)['next_at'],self.clock()+26*3600)
        self.clock.advance(3600)
        self.assertFalse(self.schedule.claim('sync',1440))

    def test_two_services_sharing_database_claim_a_due_check_only_once(self):
        self.schedule.prepare('sync',1440)
        self.clock.advance(600)
        second=SyncSchedule(Store(self.store.path),clock=self.clock,uniform=lambda a,b:b)
        results=[]
        threads=[threading.Thread(target=lambda s=s:results.append(s.claim('sync',1440))) for s in (self.schedule,second)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertEqual(sorted(results),[False,True])

    def test_legacy_request_spacing_is_raised_without_resetting_quota_or_pause(self):
        self.store.set_meta('network-policy',{'policy':{'interval_seconds':2,'hourly_limit':600,'round_limit':100},'paused':True,'sites':{'prts.wiki':{'requests':17,'active':False}}})
        manager=NetworkManager(self.store)
        self.assertEqual(manager.policy['interval_seconds'],10)
        self.assertEqual(manager.policy['hourly_limit'],120)
        self.assertEqual(manager.policy['round_limit'],64)
        self.assertTrue(manager.paused)
        self.assertEqual(manager.sites['prts.wiki']['requests'],17)

    def test_application_startup_keeps_reminders_but_does_not_force_collection(self):
        app=Application(self.directory,auto_sync=True,desktop=False)
        app.collectors={key:Mock() for key in app.collectors}
        app.probes={}
        app.reminders.tick=Mock(side_effect=app.stop.set)
        thread=app.start()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(app.stop.is_set())
        app.reminders.tick.assert_called_once()
        for collector in app.collectors.values():collector.start.assert_not_called()

    def test_host_respects_offline_pause_and_checks_reject_fast_intervals(self):
        app=Application(self.directory,auto_sync=False,desktop=False)
        app.collectors={key:Mock() for key in app.collectors};app.probes={}
        app.schedule=Mock();app.schedule.claim.return_value=True
        app.sync_due();app.schedule.claim.assert_not_called()
        app.auto_sync=True;app.network.paused=True
        app.sync_due();app.schedule.claim.assert_not_called()
        app.network.paused=False;app.sync_due()
        for collector in app.collectors.values():collector.start.assert_called_once_with()
        for value in [10,60,180,360,True]:
            with self.assertRaises(ValueError):app.update_settings({'sync_interval_minutes':value})
        app.update_settings({'sync_interval_minutes':1080})
        self.assertEqual(app.store.settings()['sync_interval_minutes'],1080)
