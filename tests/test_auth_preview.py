from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.auth_preview.app import create_app
from src.auth_preview.config import Config


@pytest.fixture
def app(tmp_path):
    return create_app(Config(data_dir=tmp_path, origin='http://testserver', development=True))


def client(app, persona=None):
    c = TestClient(app)
    if persona:
        assert post(c, '/api/dev/switch', {'user_id': persona}).status_code == 200
    return c


def post(c, url, body=None, **kwargs):
    csrf = c.get('/api/session').json()['csrf_token']
    return c.post(url, json=body or {}, headers={'X-CSRF-Token': csrf, 'Origin': 'http://testserver'}, **kwargs)


def signup(c, username='newstudent', org='org-bau', **extra):
    return post(c, '/api/signup', dict(username=username, password='A long test passphrase 47!',
        confirm_password='A long test passphrase 47!', requested_org_id=org,
        email=extra.pop('email',username+'@example.test'), **extra))


def decision(c, user, action='approve', **extra):
    return post(c, f"/api/admin/users/{user['id']}/decision", dict(action=action, version=user['version'], **extra))


def test_registration_pending_cannot_read_science(app):
    c=client(app)
    r=signup(c); assert r.status_code==201, r.text
    u=r.json()['user']; assert u['status']=='PENDING' and u['org_id'] is None
    assert c.get('/api/workspace/measurements').status_code==403
    assert 'administrator' in r.json()['message'].lower()


@pytest.mark.parametrize('persona', ['pending','rejected','suspended'])
def test_inactive_personas_denied(app,persona):
    c=client(app,persona)
    assert c.get('/api/workspace/measurements').status_code==403


@pytest.mark.parametrize('kind', ['plants','photos','calibrations','analyses','reviews','measurements','masks','instances','growth','scientific_artifacts'])
def test_tenant_ids_and_resource_guesses_fail_closed(app,kind):
    c=client(app,'bau-student')
    r=c.get('/api/workspace/'+kind); assert r.status_code==200
    assert all(x['org_id']=='org-bau' for x in r.json()['items'])
    assert c.get('/api/workspace/'+kind,params={'organization_id':'org-b'}).status_code==403
    assert c.get(f'/api/workspace/{kind}/b-{kind}').status_code==404
    assert c.get(f'/api/workspace/{kind}/bau-{kind}').status_code==200


def test_private_artifact_and_csv(app):
    c=client(app,'bau-student')
    assert c.get('/api/artifacts/b-masks').status_code==404
    r=c.get('/api/artifacts/bau-masks'); assert r.status_code==200
    assert r.headers['content-type'].startswith('image/svg+xml')
    csv=c.get('/api/workspace/export.csv'); assert csv.status_code==200
    assert 'org-bau' in csv.text and ',org-b,' not in csv.text
    assert c.get('/api/workspace/export.csv?organization_id=org-b').status_code==403
    assert c.get('/api/artifacts/../../secrets').status_code==404


def test_member_cannot_approve_and_org_admin_cannot_escalate(app):
    member=client(app,'bau-student'); owner=client(app,'owner')
    u=next(x for x in owner.get('/api/admin/users').json()['users'] if x['id']=='pending')
    assert decision(member,u).status_code==403
    org=client(app,'bau-admin')
    target=org.get('/api/admin/users').json()['users']
    assert all((x['org_id'] or x['requested_org_id'])=='org-bau' for x in target)
    assert post(org,'/api/admin/users/bau-admin/role',{'role':'PLATFORM_ADMIN','version':1}).status_code==403
    assert post(org,'/api/admin/users/b-student/decision',{'action':'suspend','version':1}).status_code==403
    assert post(org,'/api/admin/organizations',{'name':'Illegal'}).status_code==403


def test_owner_approval_promotion_and_audit(app):
    c=client(app); u=signup(c).json()['user']; owner=client(app,'owner')
    approved=decision(owner,u).json()['user']; assert approved['org_id']=='org-bau'
    r=post(owner,f"/api/admin/users/{u['id']}/role",{'role':'ORG_ADMIN','version':approved['version']})
    assert r.status_code==200
    r=post(owner,f"/api/admin/users/{u['id']}/role",{'role':'PLATFORM_ADMIN','version':r.json()['user']['version']})
    assert r.status_code==200
    events=owner.get('/api/admin/audit').json()['events']
    assert sum(e['action']=='role_changed' and e['target_id']==u['id'] for e in events)==2


def test_no_public_role_or_verified_org_assignment(app):
    c=client(app)
    assert signup(c,role='PLATFORM_OWNER').status_code==422
    assert signup(c,org_id='org-b').status_code==422
    assert signup(c,org='nonexistent').status_code==400
    # New institution onboarding is deferred by the user's latest instruction.
    assert signup(c,org=None,institution_request='New University').status_code==400
    assert signup(c,org='org-b').status_code==400


def test_duplicate_signup_case_insensitive(app):
    c=client(app); assert signup(c,'DupUser').status_code==201
    assert signup(c,'dupuser').status_code==409


def test_concurrent_approval_compare_and_swap(app):
    u=signup(client(app)).json()['user']
    owners=[client(app,'owner'),client(app,'owner')]
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda c:decision(c,u).status_code,owners))
    assert sorted(results)==[200,409]
    events=owners[0].get('/api/admin/audit').json()['events']
    assert sum(x['action']=='account_approved' and x['target_id']==u['id'] for x in events)==1


def test_suspension_and_role_changes_revoke_sessions(app):
    member=client(app,'bau-student'); owner=client(app,'owner')
    u=next(x for x in owner.get('/api/admin/users').json()['users'] if x['id']=='bau-student')
    assert decision(owner,u,'suspend').status_code==200
    assert member.get('/api/workspace/plants').status_code==401
    assert post(member,'/api/workspace/plants',{'name':'x'}).status_code==401


def test_logout_revokes_copied_cookie(app):
    c=client(app,'bau-student'); stolen=c.cookies.get('hydro_auth_preview_v1')
    assert post(c,'/api/logout').status_code==200
    replay=client(app); replay.cookies.set('hydro_auth_preview_v1',stolen)
    assert replay.get('/api/workspace/plants').status_code==401


def test_csrf_required_including_login_signup_and_persona(app):
    c=client(app)
    for url in ['/api/signup','/api/login','/api/logout','/api/dev/switch']:
        assert c.post(url,json={}).status_code==403
    csrf=c.get('/api/session').json()['csrf_token']
    assert c.post('/api/dev/switch',json={'user_id':'owner'},headers={'X-CSRF-Token':csrf,'Origin':'http://evil.test'}).status_code==403
    assert c.get('/api/session',headers={'Host':'evil.test'}).status_code==400


def test_real_password_login_change_and_recovery(app):
    c=client(app); u=signup(c).json()['user']; owner=client(app,'owner'); decision(owner,u)
    assert post(c,'/api/login',{'username':'newstudent','password':'wrong'}).status_code==401
    assert post(c,'/api/login',{'username':'newstudent','password':'A long test passphrase 47!'}).status_code==200
    old_cookie=c.cookies.get('hydro_auth_preview_v1')
    assert post(c,'/api/password',{'current_password':'A long test passphrase 47!','new_password':'A new long passphrase 55!'}).status_code==200
    replay=client(app); replay.cookies.set('hydro_auth_preview_v1',old_cookie)
    assert replay.get('/api/workspace/plants').status_code==401
    assert post(c,'/api/login',{'username':'newstudent','password':'A new long passphrase 55!'}).status_code==200
    with sqlite3.connect(app.state.config.database) as db:
        password=db.execute('select password_hash from users where id=?',(u['id'],)).fetchone()[0]
        assert password.startswith('scrypt$131072$8$1$')


def test_rejected_login_and_lockout_registration_limits(app):
    c=client(app); u=signup(c).json()['user']; owner=client(app,'owner'); decision(owner,u,'reject')
    assert post(c,'/api/login',{'username':'newstudent','password':'A long test passphrase 47!'}).status_code==403
    codes=[post(c,'/api/login',{'username':'no-user','password':'wrong'}).status_code for _ in range(10)]
    assert 429 in codes
    c=client(app)
    codes=[signup(c,'limited'+str(i)).status_code for i in range(7)]
    assert 429 in codes


def test_owner_and_platform_admin_boundaries(app):
    owner=client(app,'owner'); admin=client(app,'platform-admin')
    for c in [owner,admin]:
        assert post(c,'/api/admin/users/owner/decision',{'action':'suspend','version':1}).status_code==403
        assert post(c,'/api/admin/users/owner/role',{'role':'MEMBER','version':1}).status_code==403
    assert post(admin,'/api/admin/users/bau-student/role',{'role':'PLATFORM_ADMIN','version':1}).status_code==403


def test_disabled_organization_blocks_existing_session(app):
    c=client(app,'bau-student'); owner=client(app,'owner')
    org=next(o for o in owner.get('/api/organizations').json()['organizations'] if o['id']=='org-bau')
    r=post(owner,'/api/admin/organizations/org-bau',{'name':org['name'],'enabled':False,'version':org['version'],'approvals_enabled':True})
    assert r.status_code==200
    assert c.get('/api/workspace/plants').status_code==403


def test_writes_bind_tenant_and_parent(app):
    c=client(app,'bau-student')
    assert post(c,'/api/workspace/plants',{'name':'My plant','organization_id':'org-b'}).status_code==422
    assert post(c,'/api/workspace/photos',{'name':'Photo','plant_id':'b-plants'}).status_code==404
    r=post(c,'/api/workspace/plants',{'name':'My plant'})
    assert r.status_code==201 and r.json()['item']['org_id']=='org-bau'


def test_legacy_unassigned_records_never_visible_or_modified(app):
    db=sqlite3.connect(app.state.config.database)
    before=db.execute("select * from resources where id='legacy-unassigned'").fetchone()
    assert before
    for p in ['bau-student','b-student','owner']:
        c=client(app,p); assert c.get('/api/workspace/measurements/legacy-unassigned').status_code in [403,404]
    after=db.execute("select * from resources where id='legacy-unassigned'").fetchone()
    assert before==after
    db.close()


def test_production_has_no_personas_assets_or_fixtures(tmp_path):
    app=create_app(Config(data_dir=tmp_path,origin='https://auth.example.test',development=False))
    c=TestClient(app,base_url='https://auth.example.test')
    for path in ['/api/dev/personas','/api/dev/switch','/static/dev.js','/static/../dev.js']:
        assert c.get(path).status_code==404
    assert 'persona' not in c.get('/').text.lower()
    assert 'DEVELOPMENT PREVIEW' not in c.get('/').text
    with sqlite3.connect(app.state.config.database) as db:
        assert db.execute('select count(*) from users').fetchone()[0]==0
        assert db.execute('select count(*) from resources').fetchone()[0]==0
    cookie=c.get('/api/session').headers['set-cookie']
    assert '__Host-hydro_auth=' in cookie and 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie


def test_preview_cookie_does_not_touch_legacy_cookie(app):
    c=client(app); c.cookies.set('hydro_session','untouched-existing-session')
    post(c,'/api/dev/switch',{'user_id':'owner'}); post(c,'/api/logout')
    assert c.cookies.get('hydro_session')=='untouched-existing-session'


def test_only_bahcesehir_is_offered_for_signup(app):
    c=client(app)
    choices=c.get('/api/signup-institutions').json()['organizations']
    assert [o['id'] for o in choices]==['org-bau']
    assert choices[0]['name']=='Bahçeşehir University'


def test_production_dev_script_and_runtime_mode_reuse_forbidden(tmp_path):
    production=create_app(Config(data_dir=tmp_path,origin='https://auth.example.test'))
    assert TestClient(production,base_url='https://auth.example.test').get('/dev.js').status_code==404
    with pytest.raises(ValueError,match='runtime mode'):
        create_app(Config(data_dir=tmp_path,origin='http://testserver',development=True))


def test_production_candidate_package_excludes_development(tmp_path):
    from src.auth_preview.package import build_bundle
    from zipfile import ZipFile
    package=build_bundle(tmp_path/'candidate.zip')
    with ZipFile(package) as archive:
        assert not any('development' in x or x.endswith('/dev.js') or x.endswith('/__main__.py') for x in archive.namelist())
        assert all(b'University B' not in archive.read(x) and b'dev-persona' not in archive.read(x) for x in archive.namelist())
