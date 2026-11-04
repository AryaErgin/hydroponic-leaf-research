"""Real local Firebase Auth Emulator, synthetic inboxes only; no delivery claim."""
import os
import shutil
from pathlib import Path
import tempfile
from uuid import uuid4
import requests
import pytest
from fastapi.testclient import TestClient
from src.hydroponic.config import ROOT

PROJECT='demo-hydroponic-leaf-research'
PASSWORD='Synthetic Firebase passphrase 47!'
BASE='http://127.0.0.1:9099'


def provider(operation,payload,status=200):
    r=requests.post(BASE+'/identitytoolkit.googleapis.com/v1/accounts:'+operation+'?key=fake-api-key',json=payload,timeout=15)
    assert r.status_code==status,'Firebase fixture HTTP status '+str(r.status_code)
    return r.json()


@pytest.fixture
def site(monkeypatch):
    from src.firebase_preview.app import create_app
    from src.firebase_preview.identity import FirebaseConfig,Gateway
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    parent=ROOT/'.runtime/firebase';parent.mkdir(parents=True,exist_ok=True)
    temp=tempfile.mkdtemp(dir=parent)
    try:
        config=FirebaseConfig(Path(temp)/'auth','http://testserver',development=True,integrated=True,require_verified_email=True)
        gateway=Gateway();app=create_app(config,gateway,Path(temp)/'science')
        service=app.state.service
        with service.db.transaction() as db:
            for org in ('org-bau','org-b'):db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(org,org))
            db.execute('INSERT INTO public_signup VALUES(1,?)',('org-bau',))
        try:
            with TestClient(app,base_url='http://testserver') as client:yield app,client,gateway
        finally:app.state.scientific.close();gateway.close()
    finally:
        resolved=Path(temp).resolve()
        assert resolved.is_relative_to(parent.resolve()) and resolved!=parent.resolve()
        shutil.rmtree('\\\\?\\'+str(resolved) if os.name=='nt' else resolved)


def post(client,path,data):
    state=client.get('/api/session').json()
    return client.post(path,json=data,headers={'Origin':'http://testserver','X-CSRF-Token':state['csrf_token']})


def signup(client):
    email='synthetic-'+uuid4().hex+'@example.test'
    identity=provider('signUp',dict(email=email,password=PASSWORD,returnSecureToken=True))
    r=post(client,'/api/firebase/register',dict(id_token=identity['idToken'],username='student-'+uuid4().hex[:12],requested_org_id='org-bau'))
    assert r.status_code==201
    return identity,r.json()['user']


def test_firebase_registration_stays_pending_and_legacy_auth_is_absent(site):
    app,c,g=site;identity,user=signup(c)
    assert user['status']=='PENDING' and user['role']=='PENDING' and not user['org_id']
    assert c.get('/api/profiles').status_code==403
    for route in ('/api/login','/api/signup','/api/password','/api/recover','/api/email/change','/api/email/verify','/api/recovery/complete'):
        assert post(c,route,{}).status_code in (404,405),route
    with app.state.service.db.transaction() as db:
        row=db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        assert row['password_hash']=='FIREBASE_MANAGED'


def test_unmapped_uid_cannot_inherit_account_by_email_or_claim_role(site):
    app,c,g=site;identity,user=signup(c)
    forged=post(c,'/api/firebase/register',dict(id_token=identity['idToken'],username='forged',requested_org_id='org-bau',role='PLATFORM_OWNER'))
    assert forged.status_code==422
    bad=post(c,'/api/firebase/session',dict(id_token='invalid-token'))
    assert bad.status_code==401


def test_emulator_mode_cannot_run_on_cloud_or_with_existing_runtime(site,monkeypatch):
    from src.firebase_preview.identity import FirebaseConfig,Gateway
    monkeypatch.setenv('K_SERVICE','hydroponic-research')
    with pytest.raises(ValueError):Gateway()
    with pytest.raises(ValueError):FirebaseConfig(ROOT/'.runtime/site/auth','http://testserver',development=True,integrated=True,require_verified_email=True)


def action(email,kind):
    response=requests.get(BASE+'/emulator/v1/projects/'+PROJECT+'/oobCodes',timeout=15)
    assert response.status_code==200
    matches=[c for c in response.json()['oobCodes'] if c['email']==email and c['requestType']==kind]
    assert matches,'Expected an emulator action; this is not email delivery'
    return matches[-1]['oobCode']


def signin(identity,password=PASSWORD):
    return provider('signInWithPassword',dict(email=identity['email'],password=password,returnSecureToken=True))


def verify(identity):
    provider('sendOobCode',dict(requestType='VERIFY_EMAIL',idToken=identity['idToken']))
    code=action(identity['email'],'VERIFY_EMAIL')
    provider('update',dict(oobCode=code))
    provider('update',dict(oobCode=code),400)  # Single use.
    return signin(identity)


def activate(app,c,identity,user,org='org-bau',role='MEMBER'):
    identity=verify(identity)
    assert post(c,'/api/firebase/session',dict(id_token=identity['idToken'])).status_code==200
    with app.state.service.db.transaction() as db:
        db.execute('UPDATE users SET status=?,role=?,org_id=? WHERE id=?',('ACTIVE',role,org,user['id']))
    return identity


def test_verification_csrf_admin_gate_and_science_identity(site):
    app,c,g=site;identity,user=signup(c)
    # An ACTIVE flag alone cannot replace verified Firebase email proof.
    with app.state.service.db.transaction() as db:
        db.execute("UPDATE users SET status='ACTIVE',role='MEMBER',org_id='org-bau' WHERE id=?",(user['id'],))
    assert c.get('/api/profiles').status_code==403
    with app.state.service.db.transaction() as db:db.execute("UPDATE users SET status='PENDING' WHERE id=?",(user['id'],))
    identity=verify(identity)
    assert c.post('/api/firebase/session',json={'id_token':identity['idToken']}).status_code==403
    assert post(c,'/api/firebase/session',dict(id_token=identity['idToken'])).status_code==200
    assert c.get('/api/profiles').status_code==403  # Still needs admin approval.
    with app.state.service.db.transaction() as db:db.execute("UPDATE users SET status='ACTIVE' WHERE id=?",(user['id'],))
    state=c.get('/api/session');assert state.json()['user']['id']==user['id']
    cookie=state.request.headers['cookie'];assert 'hydro_firebase_eval_v1=' in cookie
    assert 'hydro_integrated' not in cookie
    assert c.get('/api/profiles').json()==[]
    page=c.get('/analyze');assert page.status_code==200 and '/science-static/app.js' in page.text
    assert c.get('/institution').status_code==200
    assert c.get('/api/dev/mailbox').status_code==404


def test_reset_single_use_old_password_and_sessions_revoked(site):
    from firebase_admin import auth
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    old_cookie=c.cookies.get(app.state.config.cookie)
    # Provider timestamps are seconds; separate issuance from revocation.
    import time;time.sleep(1.1)
    provider('sendOobCode',dict(requestType='PASSWORD_RESET',email=identity['email']))
    code=action(identity['email'],'PASSWORD_RESET');new_password=PASSWORD+' changed'
    provider('resetPassword',dict(oobCode=code,newPassword=new_password))
    provider('resetPassword',dict(oobCode=code,newPassword=new_password),400)
    provider('signInWithPassword',dict(email=identity['email'],password=PASSWORD,returnSecureToken=True),400)
    assert c.get('/api/profiles').status_code==401
    fresh=signin(identity,new_password)
    assert post(c,'/api/firebase/session',dict(id_token=fresh['idToken'])).status_code==200
    assert c.get('/api/profiles').status_code==200
    time.sleep(1.1)
    auth.revoke_refresh_tokens(identity['localId'],app=g.app)
    assert c.get('/api/profiles').status_code==401
    assert old_cookie  # Never print provider JWTs.


def test_verify_before_email_change_preserves_old_address_then_invalidates_session(site):
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    new_email='new-'+uuid4().hex+'@example.test'
    provider('sendOobCode',dict(requestType='VERIFY_AND_CHANGE_EMAIL',idToken=identity['idToken'],newEmail=new_email))
    assert g.inspect_id(identity['idToken'])['email']==identity['email']
    codes=requests.get(BASE+'/emulator/v1/projects/'+PROJECT+'/oobCodes',timeout=15).json()['oobCodes']
    matching=[x for x in codes if x['requestType']=='VERIFY_AND_CHANGE_EMAIL' and x.get('newEmail')==new_email]
    assert matching
    code=matching[-1]['oobCode'];provider('update',dict(oobCode=code))
    assert c.get('/api/profiles').status_code==401
    fresh=provider('signInWithPassword',dict(email=new_email,password=PASSWORD,returnSecureToken=True))
    assert post(c,'/api/firebase/session',dict(id_token=fresh['idToken'])).status_code==200
    current=c.get('/api/session').json()['user']
    assert current['id']==user['id'] and current['email']==new_email and current['email_verified_at']


def test_backend_suspension_logout_and_provider_disabled_fail_closed(site):
    from firebase_admin import auth
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    cookie=c.cookies.get(app.state.config.cookie)
    assert post(c,'/api/logout',{}).status_code==200
    c.cookies.set(app.state.config.cookie,cookie,domain='testserver.local',path='/')
    assert c.get('/api/profiles').status_code==401
    assert post(c,'/api/firebase/session',dict(id_token=signin(identity)['idToken'])).status_code==200
    with app.state.service.db.transaction() as db:db.execute("UPDATE users SET status='SUSPENDED' WHERE id=?",(user['id'],))
    assert c.get('/api/profiles').status_code==403
    assert post(c,'/api/firebase/session',dict(id_token=signin(identity)['idToken'])).status_code==403
    auth.update_user(identity['localId'],disabled=True,app=g.app)
    assert c.get('/api/profiles').status_code==401


def test_concurrent_signin_sessions_do_not_collide_or_share_logout(site):
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    with TestClient(app,base_url='http://testserver') as second:
        assert post(second,'/api/firebase/session',dict(id_token=identity['idToken'])).status_code==200
        assert second.cookies.get(app.state.config.cookie)!=c.cookies.get(app.state.config.cookie)
        assert post(second,'/api/logout',{}).status_code==200
        assert c.get('/api/profiles').status_code==200


def test_invalid_action_codes_and_unmapped_uid_cannot_signin(site):
    app,c,g=site
    identity=provider('signUp',dict(email='unmapped-'+uuid4().hex+'@example.test',password=PASSWORD,returnSecureToken=True))
    assert post(c,'/api/firebase/session',dict(id_token=identity['idToken'])).status_code==403
    provider('resetPassword',dict(oobCode='invalid-code',newPassword=PASSWORD),400)
    provider('update',dict(oobCode='invalid-code'),400)


def test_synthetic_import_metadata_preserves_id_but_emulator_cannot_verify_standard_scrypt(site):
    """Import metadata is testable; emulator's fake hash is NOT scrypt parity proof."""
    from firebase_admin import auth
    from src.auth_preview.passwords import hash_password
    app,c,g=site;id='existing-synthetic-'+uuid4().hex;uid='mapped-'+uuid4().hex
    email='import-'+uuid4().hex+'@example.test'
    raw=hash_password(PASSWORD).split('$')
    record=auth.ImportUserRecord(uid,email=email,email_verified=True,
        password_hash=bytes.fromhex(raw[5]),password_salt=bytes.fromhex(raw[4]))
    result=auth.import_users([record],hash_alg=auth.UserImportHash.standard_scrypt(
        memory_cost=131072,parallelization=1,block_size=8,derived_key_length=32),app=g.app)
    assert result.success_count==1 and result.failure_count==0
    with app.state.service.db.transaction() as db:
        db.execute('INSERT INTO users(id,username,password_hash,status,role,org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (id,id,'FIREBASE_MANAGED','ACTIVE','MEMBER','org-bau','2026-10-27',email,'2026-10-27'))
        app.state.service.bind_for_local_test(db,id,uid)
    # Official emulator uses hashPassword(password,salt), ignoring imported hash
    # algorithms (firebase-tools15.22.4 operations.js). Record this boundary.
    provider('signInWithPassword',dict(email=email,password=PASSWORD,returnSecureToken=True),400)
    with app.state.service.db.transaction() as db:
        assert db.execute('SELECT user_id FROM firebase_identities WHERE uid=?',(uid,)).fetchone()[0]==id


def test_scientific_workflow_preserves_calibration_history_and_tenant_acl(site,monkeypatch):
    from tests.test_integrated_app import plant
    from tests.test_integrated_science_workflow import (_calibrate,_mock_segformer,_analyze,
        _editor_payload,_approve,_asset_hashes)
    from fastapi.testclient import TestClient
    app,c,g=site;identity,user=signup(c);activate(app,c,identity,user)
    c.headers.update({'Origin':'http://testserver','X-CSRF-Token':c.get('/api/session').json()['csrf_token']})
    profile=plant(c,'Synthetic Firebase scientific fixture')
    calibration=_calibrate(c,profile).json()['calibration']
    _mock_segformer(app,monkeypatch)
    analysis=_analyze(c,profile,'2026-10-27')
    payload,labels=_editor_payload(analysis)
    draft=c.post('/api/analyses/'+analysis['id']+'/draft',json=payload)
    assert draft.status_code==200
    approved=_approve(c,analysis,payload);assert approved.status_code==201
    measurement=approved.json()
    assert measurement['leaf_count']==3 and measurement['median_leaf_area_cm2']>0
    before=_asset_hashes(c,measurement['assets'])
    assert _calibrate(c,profile,12).status_code==201
    history=c.get('/api/profiles/'+profile['id']+'/history').json()['measurements']
    assert history[0]['id']==measurement['id']
    assert history[0]['total_projected_area_cm2']==measurement['total_projected_area_cm2']
    assert _asset_hashes(c,measurement['assets'])==before
    csv=c.get('/api/profiles/'+profile['id']+'/export.csv')
    assert csv.status_code==200 and 'median_leaf_area_cm2' in csv.text
    # A second verified UID has a different backend organization, not a client grant.
    with TestClient(app,base_url='http://testserver') as second:
        other,other_user=signup(second);activate(app,second,other,other_user,'org-b')
        assert second.get('/api/profiles').json()==[]
        assert second.get('/api/profiles/'+profile['id']+'/history').status_code==404
        for url in measurement['assets'].values():assert second.get(url).status_code==404
        assert second.get('/api/profiles/'+profile['id']+'/export.csv').status_code==404
        assert second.get('/api/calibrations').json()==[]
        assert second.get('/api/profiles',params={'organization_id':'org-bau'}).status_code==403


@pytest.mark.parametrize('changed',[
    {'aud':'another-project'}, {'iss':'https://securetoken.google.com/another-project'},
    {'exp':1}, {'auth_time':1},
])
def test_emulator_claim_validation_rejects_wrong_project_expired_and_stale_proof(site,changed):
    # Unsigned emulator tokens are ONLY useful for claim-contract tests. This is
    # explicitly not a production signature-verification test.
    import jwt
    app,c,g=site;identity,user=signup(c)
    claims=jwt.decode(identity['idToken'],options={'verify_signature':False})
    claims.update(changed)
    forged=jwt.encode(claims,key='',algorithm='none')
    assert post(c,'/api/firebase/session',dict(id_token=forged)).status_code==401


def test_provider_password_change_invalidates_old_sessions(site):
    import time
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    time.sleep(1.1)
    provider('update',dict(idToken=identity['idToken'],password=PASSWORD+' next',returnSecureToken=True))
    assert c.get('/api/profiles').status_code==401
    fresh=signin(identity,PASSWORD+' next')
    assert post(c,'/api/firebase/session',dict(id_token=fresh['idToken'])).status_code==200
    assert c.get('/api/session').json()['user']['id']==user['id']


def test_admin_approval_role_changes_and_scope_remain_backend_authoritative(site):
    from fastapi.testclient import TestClient
    app,owner,g=site;identity,user=signup(owner);activate(app,owner,identity,user,role='PLATFORM_OWNER')
    with TestClient(app,base_url='http://testserver') as member:
        other,target=signup(member)
        assert post(owner,'/api/admin/users/'+target['id']+'/decision',dict(action='approve',version=1,verified_org_id='org-bau')).status_code==409
        other=verify(other)
        refreshed=post(member,'/api/firebase/session',dict(id_token=other['idToken']))
        assert refreshed.status_code==200
        version=refreshed.json()['user']['version']
        assert version==target['version']+1
        # A form opened before the identity proof changed must be refreshed.
        assert post(owner,'/api/admin/users/'+target['id']+'/decision',dict(action='approve',version=target['version'],verified_org_id='org-bau')).status_code==409
        result=post(owner,'/api/admin/users/'+target['id']+'/decision',dict(action='approve',version=version,verified_org_id='org-bau'))
        assert result.status_code==200
        # Approval revokes the pending session; fresh provider proof restores it.
        assert member.get('/api/profiles').status_code==401
        assert post(member,'/api/firebase/session',dict(id_token=signin(other)['idToken'])).status_code==200
        assert member.get('/api/profiles').status_code==200
        assert member.get('/api/admin/users').status_code==403
        assert post(member,'/api/admin/users/'+user['id']+'/role',dict(role='PLATFORM_ADMIN',version=1)).status_code==403
        assert post(owner,'/api/admin/users/'+target['id']+'/role',dict(role='ORG_ADMIN',version=result.json()['user']['version'])).status_code==200
        assert member.get('/api/profiles').status_code==401


def test_session_cookie_flags_and_backend_rate_limit(site):
    app,c,g=site;identity,user=signup(c)
    # Development is loopback HTTP; Secure is a separately required HTTPS gate.
    c.cookies.clear();response=c.get('/api/session')
    header=response.headers['set-cookie'].lower()
    assert 'httponly' in header and 'samesite=strict' in header and 'path=/' in header
    assert 'secure' not in header
    with app.state.service.db.transaction() as db:
        db.execute('INSERT OR REPLACE INTO limits VALUES(?,?,?)',('firebase-exchange-attempts:testclient',240,9999999999))
    assert post(c,'/api/firebase/session',dict(id_token='invalid')).status_code==429


def test_factory_refuses_unrestricted_legacy_config(site):
    from src.auth_preview.config import Config
    from src.firebase_preview.app import create_app
    app,c,g=site;config=app.state.config
    unrestricted=Config(config.data_dir,config.origin,development=True,integrated=True,require_verified_email=True)
    with pytest.raises(ValueError):create_app(unrestricted,g,config.data_dir.parent/'science')


def test_repeated_registration_cannot_reuse_stale_email_verification(site):
    from firebase_admin import auth
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    auth.update_user(identity['localId'],email_verified=False,app=g.app)
    fresh=signin(identity)
    assert c.get('/api/profiles').status_code==401
    result=post(c,'/api/firebase/register',dict(id_token=fresh['idToken'],username=user['username'],requested_org_id='org-bau'))
    assert result.status_code==201
    assert c.get('/api/profiles').status_code==403
    assert not c.get('/api/session').json()['user']['email_verified_at']


def test_pending_institution_route_stays_in_firebase_review_ui(site):
    app,c,g=site;identity,user=signup(c)
    response=c.get('/institution',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/verify-email'


def test_preapproved_administrator_needs_email_verification_not_approval(site):
    app,c,g=site;identity,user=signup(c)
    with app.state.service.db.transaction() as db:
        db.execute('UPDATE users SET status=?,role=?,org_id=? WHERE id=?',('ACTIVE','ORG_ADMIN','org-bau',user['id']))
    response=c.get('/institution',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/verify-email'
    assert c.get('/verify-email').status_code==200
    assert c.get('/api/profiles').status_code==403
    identity=verify(identity)
    result=post(c,'/api/firebase/session',dict(id_token=identity['idToken']))
    assert result.status_code==200 and result.json()['user']['role']=='ORG_ADMIN'
    assert c.get('/institution').status_code==200


def test_firebase_exchange_limit_is_per_verified_uid_not_shared_proxy(site):
    import hashlib
    app,c,g=site;first,first_user=signup(c);second,second_user=signup(c)
    with app.state.service.db.transaction() as db:
        key='firebase-exchange-uid:'+hashlib.sha256(first['localId'].encode()).hexdigest()
        db.execute('INSERT OR REPLACE INTO limits VALUES(?,?,?)',(key,20,9999999999))
    assert post(c,'/api/firebase/session',dict(id_token=first['idToken'])).status_code==429
    assert post(c,'/api/firebase/session',dict(id_token=second['idToken'])).status_code==200
    with app.state.service.db.transaction() as db:
        db.execute('INSERT OR REPLACE INTO limits VALUES(?,?,?)',('firebase-exchange:testclient',20,9999999999))
    assert post(c,'/api/firebase/session',dict(id_token=second['idToken'])).status_code==200


def test_firebase_signin_reclaims_only_anonymous_capacity_and_stable_proof_version(site):
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    service=app.state.service;service.session_capacity=4
    version=c.get('/api/session').json()['user']['version']
    retained=c.cookies.get(app.state.config.cookie)
    with service.db.transaction() as db:
        for _ in range(3):service.new_session(db)
    with TestClient(app,base_url='http://testserver') as second:
        assert post(second,'/api/firebase/session',dict(id_token=signin(identity)['idToken'])).status_code==200
        assert second.get('/api/session').json()['user']['version']==version
        assert c.get('/api/profiles').status_code==200
    with service.db.transaction() as db:
        assert service.session(db,retained)
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==4


def test_provider_proof_revocation_invalidates_admin_snapshot_version(site):
    from firebase_admin import auth
    app,c,g=site;identity,user=signup(c);identity=activate(app,c,identity,user)
    version=c.get('/api/session').json()['user']['version']
    auth.update_user(identity['localId'],email_verified=False,app=g.app)
    result=post(c,'/api/firebase/session',dict(id_token=signin(identity)['idToken']))
    assert result.status_code==200
    assert result.json()['user']['version']==version+1
    assert result.json()['user']['email_verified_at'] is None
    assert c.get('/api/profiles').status_code==403
