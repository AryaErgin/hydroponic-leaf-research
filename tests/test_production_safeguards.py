"""Production regression contracts with synthetic fixtures only."""
import hashlib, json, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest
from sqlalchemy import update
from tests import test_webapp as fixtures

class TestProductionSafeguards(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    upload=fixtures.TestWebMVP.upload
    analyze=fixtures.TestWebMVP.analyze
    approve=fixtures.TestWebMVP.approve

    def test_production_rejects_model_selection_and_hides_research_registry(self):
        self.settings.cloud_run=True
        upload=self.upload(self.profile()).json()
        r=self.client.post('/api/analyses/'+upload['id']+'/analyze',params={'engine':'classical'})
        self.assertEqual(r.status_code,400,r.text)
        self.assertEqual(self.client.get('/api/models').status_code,404)
    def test_selected_missing_model_fails_closed(self):
        upload=self.upload(self.profile()).json()
        from src.webapp.service import process_single_plant_rgb
        with patch('src.webapp.service.process_single_plant_rgb',wraps=process_single_plant_rgb) as classical:
            r=self.client.post('/api/analyses/'+upload['id']+'/analyze',params={'engine':'learned:missing'})
        self.assertEqual(r.status_code,503,r.text);classical.assert_not_called()
        row=self.app.state.service.analysis(upload['id'])
        self.assertEqual(row['status'],'ERROR');self.assertNotIn('candidate_mask',row['assets'])
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.measurements),[])

    def test_auto_without_champion_fails_closed(self):
        upload=self.upload(self.profile()).json()
        r=self.client.post('/api/analyses/'+upload['id']+'/analyze')
        self.assertEqual(r.status_code,503,r.text)
        self.assertIn('promoted',r.json()['detail'])

    def test_resume_only_latest_and_current_context_is_hidden(self):
        p=self.profile();first=self.upload(p).json();second=self.upload(p).json()
        rows=self.client.get('/api/profiles/'+p['id']+'/analyses').json()
        self.assertEqual([r['id']for r in rows],[second['id']])
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/analyses',params={'active_id':second['id']}).json(),[])
        self.assertEqual(self.app.state.service.analysis(first['id'])['status'],'UPLOADED')

    def test_expired_session_is_deleted_but_scientific_records_remain(self):
        p=self.profile();a=self.analyze(p);s=self.app.state.service
        table=s.db.review_sessions
        row=s.db.one(table,p['id'])
        with s.db.engine.begin()as c:
            c.execute(update(table).where(table.c.id==p['id']).values(payload={**row['payload'],'updated_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()}))
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/analyses').json(),[])
        self.assertEqual(s.db.rows(table),[])
        self.assertEqual(s.analysis(a['id'])['status'],'ANALYZED')
        self.assertEqual(s.profile(p['id'])['cm_per_pixel'],.1)

    def test_stale_analyzing_recovers_to_error_and_leaves_picker(self):
        p=self.profile();a=self.upload(p).json();s=self.app.state.service
        row=s.db.one(s.db.analyses,a['id'])
        with s.db.engine.begin()as c:
            c.execute(update(s.db.analyses).where(s.db.analyses.c.id==a['id']).values(status='ANALYZING',payload={**row['payload'],'analysis_started_at':(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat(),'analysis_token':'expired'}))
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/analyses').json(),[])
        self.assertEqual(s.analysis(a['id'])['status'],'ERROR')

    def test_approval_removes_session_but_not_measurement_or_registry(self):
        p=self.profile();a=self.analyze(p);r=self.approve(a)
        self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/analyses').json(),[])
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.review_sessions),[])
        self.assertEqual(len(self.app.state.service.db.rows(self.app.state.service.db.approved_masks)),1)

    def test_same_key_login_logout_relogin_and_restart_cookie(self):
        from src.webapp.app import create_app
        from src.webapp.settings import Settings
        from fastapi.testclient import TestClient
        settings=Settings(data_dir=self.base/'auth',auth_token='x'*64)
        with TestClient(create_app(settings)) as c:
            r=c.post('/api/login',json={'token':settings.auth_token})
            self.assertIn('Max-Age=43200',r.headers['set-cookie']);cookie=c.cookies.get('hydro_session')
            self.assertEqual(c.post('/api/logout').status_code,200)
            self.assertEqual(c.get('/api/profiles').status_code,401)
            self.assertEqual(c.post('/api/login',json={'token':settings.auth_token}).status_code,200)
        with TestClient(create_app(settings))as c:
            c.cookies.set('hydro_session',cookie)
            self.assertEqual(c.get('/api/profiles').status_code,200)
            self.assertEqual(c.post('/api/login',json={'token':settings.auth_token}).status_code,200)

    def test_professor_page_contains_no_model_picker_or_validation_explanation(self):
        html=self.client.get('/').text;js=self.client.get('/static/app.js').text
        for value in ['candidate-engine','model-note','qc-banner','id="pending"']:
            self.assertNotIn(value,html)
        self.assertIn('Resume unfinished review',html)
        self.assertNotIn('candidateOptions',js)

    def test_predict_version_serializes_cold_load_and_inference(self):
        import numpy as np
        from src.training import inference
        config=self.base/'config.json';config.write_text(json.dumps(dict(architecture='segformer_b0',input_size=16,threshold=.5)))
        class Registry:
            root=self.base
            def get(self,version):return dict(version=version,species='lettuce',architecture='segformer_b0',artifacts=dict(config={'path':'config.json'},checkpoint={'path':'model.safetensors','sha256':'fixture'}))
        active=0;maximum=0;guard=threading.Lock()
        def load(*a,**k):
            nonlocal active,maximum
            with guard:active+=1;maximum=max(maximum,active)
            time.sleep(.02)
            with guard:active-=1
            return lambda canvas:np.full((16,16),.7,np.float32)
        with patch.object(inference,'load_predictor',side_effect=load),ThreadPoolExecutor(4)as ex:
            results=list(ex.map(lambda _:inference.predict_version(self.rgb,'fixture',Registry()),range(4)))
        self.assertEqual(maximum,1)
        self.assertEqual(len({hashlib.sha256(r['mask'].tobytes()).hexdigest()for r in results}),1)
