"""Owner inbox tests use only disposable artificial approval records."""
import copy
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from sqlalchemy import select
from tests.test_training_registry import RegistryFixtures
from tests import test_webapp as fixtures
import unittest

class TestOwnerNotifications(RegistryFixtures):
    def test_ten_unique_real_approvals_emit_once_no_candidates_or_duplicates(self):
        from src.training.owner_notifications import advance
        state=None
        for i in range(9):
            row,refs=self.approved(i,plant='p'+str(i%2))
            row=self.module('approved_registry').from_measurement(row,refs)
            state,event=advance(state,row,'snapshot',set())
            self.assertFalse(event)
        state,event=advance(state,row,'snapshot',set());self.assertFalse(event)
        self.assertEqual(state['new_eligible_count'],9)
        row,refs=self.approved(10);row=self.module('approved_registry').from_measurement(row,refs)
        state,event=advance(state,row,'snapshot',set());self.assertTrue(event)
        self.assertEqual(state['threshold'],10);self.assertEqual(state['status'],'OWNER_REVIEW_REQUIRED')
        self.assertFalse(state['training_executed']);self.assertFalse(state['promotion_executed'])
        state,event=advance(state,row,'snapshot',set());self.assertFalse(event)
        for changes in [{'new_real_eligible':False},{'review_status':'CANDIDATE'},{'synthetic':True},{'historically_exposed':True}]:
            invalid={**row,**changes};before=copy.deepcopy(state)
            state,event=advance(state,invalid,'snapshot',set());self.assertEqual(state,before);self.assertFalse(event)
    def test_training_snapshot_excludes_already_trained_images_and_resets_batch(self):
        from src.training.owner_notifications import advance
        row,refs=self.approved();row=self.module('approved_registry').from_measurement(row,refs)
        state,event=advance(None,row,'old',set());self.assertEqual(state['new_eligible_count'],1)
        state,event=advance(None,row,'new',{row['assets']['original']['sha256']})
        self.assertEqual(state['new_eligible_count'],0);self.assertFalse(event)
    def test_default_retraining_batch_threshold_is_ten(self):
        import inspect
        from src.training.retrain_lettuce import prepare
        self.assertEqual(inspect.signature(prepare).parameters['minimum_new_real'].default,10)

class TestOwnerIntegration(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    analyze=fixtures.TestWebMVP.analyze
    upload=fixtures.TestWebMVP.upload
    approve=fixtures.TestWebMVP.approve
    def test_durable_inbox_crossing_is_atomic_idempotent_and_owner_only(self):
        from src.training.owner_notifications import record_approval
        s=self.app.state.service;events=[]
        def row(i):return dict(id='fixture-'+str(i),species='lettuce',review_status='HUMAN_APPROVED',
            new_real_eligible=True,data_origin='real',synthetic=False,diagnostic=False,historically_exposed=False,
            assets={'original':{'sha256':__import__('hashlib').sha256(str(i).encode()).hexdigest()}})
        for i in range(10):
            with s.db.engine.begin()as c:events.append(record_approval(s.db,c,row(i),s.models))
        self.assertEqual(sum(e is not None for e in events),1)
        saved=s.db.rows(s.db.owner_notifications)[0]['payload']
        self.assertEqual(saved['new_eligible_count'],10);self.assertEqual(saved['audience'],'PROJECT_OWNER_ONLY')
        with s.db.engine.begin()as c:self.assertIsNone(record_approval(s.db,c,row(9),s.models))
        with self.assertRaisesRegex(ValueError,'rollback'):
            with s.db.engine.begin()as c:
                record_approval(s.db,c,row(11),s.models);raise ValueError('rollback')
        self.assertEqual(s.db.rows(s.db.owner_notifications)[0]['payload'],saved)
    def test_candidate_not_registered_approval_updates_private_inbox(self):
        self.settings.closed_academic_research=True
        p=self.profile();a=self.analyze(p);s=self.app.state.service
        self.assertEqual(s.db.rows(s.db.owner_notifications),[])
        self.assertEqual(s.db.rows(s.db.approved_masks),[])
        # An existing foreground analysis remains eligible for its original
        # cohort. New lamina approvals are covered separately and cannot train it.
        from sqlalchemy import update
        stored=s.db.one(s.db.analyses,a['id'])['payload']
        stored['measurement_target']='visible_plant_foreground'
        with s.db.engine.begin() as c:c.execute(update(s.db.analyses).where(s.db.analyses.c.id==a['id']).values(payload=stored))
        r=self.approve(a);self.assertEqual(r.status_code,201,r.text)
        self.assertTrue(s.db.rows(s.db.approved_masks)[0]['payload']['new_real_eligible'])
        states=s.db.rows(s.db.owner_notifications);self.assertEqual(states[0]['payload']['new_eligible_count'],1)
        self.assertEqual(self.client.get('/api/owner-notifications').status_code,404)
        self.assertNotIn('owner_notifications',r.text)
    def test_expired_browser_cookie_same_key_and_multiple_browsers(self):
        from src.webapp.app import create_app
        from src.webapp.settings import Settings
        from fastapi.testclient import TestClient
        settings=Settings(data_dir=self.base/'expiry',auth_token='a'*64)
        with TestClient(create_app(settings))as first,TestClient(create_app(settings))as second:
            for c in [first,second]:self.assertEqual(c.post('/api/login',json={'token':settings.auth_token}).status_code,200)
            for cookie in first.cookies.jar:cookie.expires=1
            self.assertEqual(first.get('/api/profiles').status_code,401)
            self.assertEqual(second.get('/api/profiles').status_code,200)
            self.assertEqual(first.post('/api/login',json={'token':settings.auth_token}).status_code,200)
            self.assertEqual(first.get('/api/profiles').status_code,200)
