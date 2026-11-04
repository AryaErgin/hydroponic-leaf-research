"""Actual promoted inference + Firebase/Auth/Firestore emulators, synthetic science."""
import os
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
from firebase_admin import auth
from google.cloud import firestore
from src.auth_preview.firestore_database import TABLES
from src.firebase_preview.app import create_app
from src.firebase_preview.identity import Gateway
from src.firebase_preview.modes import DurableEmulatorConfig,LIVE_PROJECT
from src.hydroponic.config import ROOT
from src.webapp.settings import Settings
from tests.test_firebase_identity import post,provider,verify,signin
from tests.test_preproduction_isolation import MemoryGCS,real_workflow
from tests.test_integrated_science_workflow import _asset_hashes


def test_durable_firebase_real_model_approval_admin_gate_and_restart(monkeypatch):
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST','127.0.0.1:8791')
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    monkeypatch.delenv('K_SERVICE',raising=False)
    base=ROOT/'.runtime/firebase'/('d'+uuid4().hex[:8])
    prefix='firebase_test_'+uuid4().hex
    config=DurableEmulatorConfig(base/'a','http://testserver',development=True,integrated=True,
        require_verified_email=True,firestore_project=LIVE_PROJECT,firestore_prefix=prefix)
    settings=Settings(data_dir=base/'s',firestore_project=LIVE_PROJECT,
        firestore_prefix=prefix+'_science',gcs_bucket='synthetic-preproduction-private-bucket',
        closed_academic_research=False,max_image_pixels=20000)
    media=MemoryGCS();monkeypatch.setattr('google.cloud.storage.Client',lambda **kwargs:media)
    gateways=[Gateway()];apps=[];uids=[];collections={prefix+'_'+table for table in TABLES}
    try:
        app=create_app(config,gateways[0],settings.data_dir,scientific_settings=settings);apps.append(app)
        with app.state.service.db.transaction() as db:
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',('org-bau','Synthetic university'))
            db.execute('INSERT INTO public_signup VALUES(1,?)',('org-bau',))
        owner=provider('signUp',dict(email='owner-'+uuid4().hex+'@example.test',
            password='Synthetic Firebase passphrase 47!',returnSecureToken=True));uids.append(owner['localId'])
        owner=verify(owner)
        with app.state.service.db.transaction() as db:
            db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                ('synthetic-owner','synthetic-owner','FIREBASE_MANAGED','ACTIVE','PLATFORM_OWNER','org-bau','org-bau','2026-10-28'))
            db.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?',(owner['email'],'synthetic-owner'))
            db.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?',('verified','synthetic-owner'))
            db.execute('INSERT INTO firebase_identities VALUES(?,?)',(owner['localId'],'synthetic-owner'))
        with TestClient(app,base_url='http://testserver') as client,TestClient(app,base_url='http://testserver') as admin:
            assert post(admin,'/api/firebase/session',dict(id_token=owner['idToken'])).status_code==200
            identity=provider('signUp',dict(email='member-'+uuid4().hex+'@example.test',
                password='Synthetic Firebase passphrase 47!',returnSecureToken=True));uids.append(identity['localId'])
            result=post(client,'/api/firebase/register',dict(id_token=identity['idToken'],
                username='synthetic-member',requested_org_id='org-bau'))
            assert result.status_code==201 and client.get('/api/profiles').status_code==403
            identity=verify(identity)
            result=post(client,'/api/firebase/session',dict(id_token=identity['idToken']))
            member=result.json()['user'];assert client.get('/api/profiles').status_code==403
            assert post(admin,'/api/admin/users/'+member['id']+'/decision',
                dict(action='approve',version=member['version'],verified_org_id='org-bau')).status_code==200
            assert post(client,'/api/firebase/session',dict(id_token=signin(identity)['idToken'])).status_code==200
            client.headers.update({'Origin':'http://testserver','X-CSRF-Token':client.get('/api/session').json()['csrf_token']})
            profile,measurement,history,hashes=real_workflow(app,client)
            gateways.append(Gateway())
            reopened=create_app(config,gateways[-1],settings.data_dir,scientific_settings=settings);apps.append(reopened)
            with TestClient(reopened,base_url='http://testserver') as refreshed:
                refreshed.cookies.update(client.cookies)
                assert refreshed.get('/api/profiles/'+profile['id']+'/history').json()==history
                assert refreshed.get('/api/measurements/'+measurement['id']).json()==measurement
                assert _asset_hashes(refreshed,{str(n):url for n,url in enumerate(hashes)})==hashes
                assert refreshed.get('/api/profiles/'+profile['id']+'/export.csv').status_code==200
    finally:
        for app in apps:
            for tenant in app.state.scientific.services.values():
                collections.update(tenant.db.engine.prefix+'_'+table for table in tenant.db.engine.tables)
            app.state.scientific.close();app.state.service.db.close()
        cleanup=firestore.Client(project=LIVE_PROJECT)
        try:
            for collection in collections:
                for snapshot in cleanup.collection(collection).stream():snapshot.reference.delete()
        finally:cleanup.close()
        for uid in uids:auth.delete_user(uid,app=gateways[0].app)
        for gateway in gateways:gateway.close()
        assert base.resolve().is_relative_to((ROOT/'.runtime/firebase').resolve())
        shutil.rmtree('\\\\?\\'+str(base.resolve()) if os.name=='nt' else base)
