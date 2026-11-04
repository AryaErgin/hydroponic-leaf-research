"""Real scientific routes under one account session; isolated synthetic data only."""
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import io
import os
import shutil
import tempfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.auth_preview.config import Config
from src.auth_preview.passwords import hash_password
from src.hydroponic.config import ROOT
from src.webapp.settings import Settings

PASSWORD='Synthetic integration passphrase 42!'


@pytest.fixture
def application():
    from src.integrated.app import create_app
    temp=tempfile.mkdtemp(dir=ROOT)
    try:
        base=Path(temp)
        app=create_app(Config(base/'auth','http://testserver',development=True,integrated=True,
            require_verified_email=True,email_hmac_key=b'test-only-key-32-bytes-long-value'),
            Settings(data_dir=base/'science',closed_academic_research=False,max_image_pixels=20000))
        with app.state.service.db.transaction() as db:
            for org in ('org-bau','org-b'):
                db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(org,org))
                for role in ('MEMBER','ORG_ADMIN','PLATFORM_OWNER'):
                    uid=org+'-'+role
                    db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uid,uid,hash_password(PASSWORD),'ACTIVE',role,org,org,'2026-10-27',uid.lower()+'@example.test','2026-10-27'))
            db.execute('INSERT INTO public_signup VALUES(1,?)',('org-bau',))
        yield app
        app.state.scientific.close()
    finally:
        path=Path(temp).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path!=ROOT.resolve()
        shutil.rmtree('\\\\?\\'+str(path) if os.name=='nt' else path)


def login(app,org='org-bau',role='MEMBER'):
    client=TestClient(app,base_url='http://testserver')
    state=client.get('/api/session').json()
    r=client.post('/api/login',headers={'X-CSRF-Token':state['csrf_token'],'Origin':'http://testserver'},
        json={'username':org+'-'+role,'password':PASSWORD})
    assert r.status_code==200,r.text
    client.headers.update({'X-CSRF-Token':client.get('/api/session').json()['csrf_token'],'Origin':'http://testserver'})
    return client


def plant(client,name='Synthetic plant'):
    r=client.post('/api/plants',json=dict(name=name,species='lettuce',calibration_choice='none',request_id=str(uuid4())))
    assert r.status_code==201,r.text
    return r.json()


def image():
    stream=io.BytesIO();rgb=np.full((100,120,3),40,np.uint8);rgb[20:70,20:90]=[70,160,35]
    Image.fromarray(rgb).save(stream,'PNG');return stream.getvalue()


def test_single_session_real_scientific_page_and_no_dev_routes(application):
    c=login(application)
    assert c.get('/api/session').json()['integrated'] is True
    page=c.get('/analyze');assert page.status_code==200
    assert '/science-static/app.js' in page.text and 'data-integrated="true"' in page.text
    assert c.get('/science-static/mask-editor.js').status_code==200
    for url in ('/api/dev/personas','/api/dev/mailbox','/static/dev.js','/api/workspace/plants','/api/artifacts/bau-masks'):
        assert c.get(url).status_code==404,url


def test_real_plant_calibration_upload_assets_and_tenant_isolation(application):
    a=login(application);b=login(application,'org-b');p=plant(a)
    assert b.get('/api/profiles').json()==[]
    assert b.get('/api/profiles/'+p['id']+'/history').status_code==404
    raw=image()
    r=a.post('/api/plants/'+p['id']+'/calibration/two-point',
        data={'diameter_cm':'8','points':'[[20,80],[100,80]]','reviewed':'true'},files={'image':('synthetic.png',raw,'image/png')})
    assert r.status_code==201,r.text
    cal=r.json()['calibration'];assert cal['cm_per_pixel']==pytest.approx(.1)
    assert b.put('/api/plants/'+plant(b)['id']+'/calibration',json={'calibration_id':cal['id']}).status_code==404
    r=a.post('/api/uploads',data={'profile_id':p['id'],'date':'2026-10-27','geometry_unchanged':'true'},files={'image':('synthetic.png',raw,'image/png')})
    assert r.status_code==201,r.text
    upload=r.json();original=upload['assets']['original']
    assert a.get(original).content==raw
    assert b.get(original).status_code==404
    assert b.post('/api/uploads',data={'profile_id':p['id'],'date':'2026-10-27'},files={'image':('synthetic.png',raw,'image/png')}).status_code==404
    assert a.get('/api/profiles',params={'organization_id':'org-b'}).status_code==403
    assert a.get('/api/profiles',headers={'X-Organization-ID':'org-b'}).status_code==403
    assert a.get('/api/calibrations').json()[0]['id']==cal['id']
    assert b.get('/api/calibrations').json()==[]


@pytest.mark.parametrize('state',['PENDING','SUSPENDED','UNVERIFIED','ORG_DISABLED'])
def test_access_gates_inactive_unverified_and_disabled_org(application,state):
    c=login(application)
    with application.state.service.db.transaction() as db:
        if state=='UNVERIFIED':db.execute('UPDATE users SET email_verified_at=NULL WHERE id=?',('org-bau-MEMBER',))
        elif state=='ORG_DISABLED':db.execute('UPDATE organizations SET enabled=0 WHERE id=?',('org-bau',))
        else:db.execute('UPDATE users SET status=? WHERE id=?',(state,'org-bau-MEMBER'))
    assert c.get('/api/profiles').status_code==403
    assert c.post('/api/plants',json={'name':'Blocked','species':'lettuce'}).status_code==403


def test_logout_revocation_csrf_and_owner_cannot_override_org(application):
    c=login(application,role='PLATFORM_OWNER');plant(c)
    assert c.get('/api/profiles?organization_id=org-b').status_code==403
    assert c.post('/api/plants',json={'name':'Forged','species':'lettuce'},headers={'X-CSRF-Token':'wrong'}).status_code==403
    previous=c.cookies.get(application.state.config.cookie)
    assert c.post('/api/logout',json={}).status_code==200
    assert c.get('/api/profiles').status_code==401
    c.cookies.set(application.state.config.cookie,previous)
    assert c.get('/api/profiles').status_code==401


def test_large_science_upload_not_limited_by_auth_json_size(application):
    c=login(application)
    rng=np.random.default_rng(17);stream=io.BytesIO()
    Image.fromarray(rng.integers(0,255,(100,120,3),dtype=np.uint8)).save(stream,'PNG')
    assert len(stream.getvalue())>16384
    r=c.post('/api/images/prepare',files={'image':('synthetic.png',stream.getvalue(),'image/png')})
    assert r.status_code==200,r.text


def test_gcs_namespaces_prevent_identical_key_cross_tenant_reads(application):
    c=login(application);plant(c)
    stores=application.state.scientific.services
    bau=stores['org-bau'];other=application.state.scientific.for_organization('org-b')
    assert bau.media.prefix!=other.media.prefix
    assert bau.settings.firestore_prefix!=other.settings.firestore_prefix
    assert bau.existing_calibration(import_it=True) is None
