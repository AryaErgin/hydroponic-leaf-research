"""Local runtime isolation, ownership and atomic persistence regressions."""
import json
import socket
from pathlib import Path
import pytest
from src.local_setup.runtime import (guard_environment, claim_storage, available_ports,
    RuntimeLock, atomic_json, load_profile, require_password)


def test_reject_cloud_configuration_before_touching_storage(monkeypatch):
    for key in ('K_SERVICE', 'HYDRO_FIRESTORE_PROJECT', 'HYDRO_GCS_BUCKET', 'GOOGLE_APPLICATION_CREDENTIALS'):
        monkeypatch.setenv(key, 'foreign-value')
        with pytest.raises(ValueError): guard_environment()
        monkeypatch.delenv(key)


def test_claim_never_adopts_unmarked_existing_data(tmp_path):
    state = tmp_path/'local'; state.mkdir(); (state/'auth.sqlite3').write_bytes(b'protected')
    with pytest.raises(ValueError, match='unrecognized'): claim_storage(state)
    assert (state/'auth.sqlite3').read_bytes() == b'protected'


def test_claim_reuses_only_own_storage(tmp_path):
    state=tmp_path/'local'; first=claim_storage(state)
    assert claim_storage(state)==first
    marker=json.loads((state/'owner.json').read_text())
    marker['application']='other'; (state/'owner.json').write_text(json.dumps(marker))
    with pytest.raises(ValueError): claim_storage(state)


def test_lock_excludes_second_launcher_and_releases(tmp_path):
    with RuntimeLock(tmp_path/'launcher.lock'):
        with pytest.raises(ValueError, match='already'): 
            with RuntimeLock(tmp_path/'launcher.lock'): pass
    with RuntimeLock(tmp_path/'launcher.lock'): pass


def test_collision_does_not_close_foreign_listener():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); listener.listen()
        port=listener.getsockname()[1]
        with pytest.raises(ValueError, match=str(port)): available_ports((port,))
        with socket.create_connection(('127.0.0.1',port)): pass


@pytest.mark.parametrize('password', ['', 'x'*14, 'x'*129])
def test_password_minimum_matches_ui(password):
    with pytest.raises(ValueError): require_password(password)


def test_atomic_json_preserves_existing_on_serialization_failure(tmp_path):
    target=tmp_path/'state.json'; atomic_json(target, {'good':1})
    with pytest.raises(TypeError): atomic_json(target, {'bad':object()})
    assert json.loads(target.read_text())=={'good':1}


def test_profile_rejects_unknown_mode_or_relative_executables(tmp_path):
    path=tmp_path/'runtime-profile.json'
    path.write_text(json.dumps(dict(version=1,backend='windows',python='python',node='node',firebase_tools_root='tools',mode='FULL')))
    with pytest.raises(ValueError): load_profile(path)

def test_real_emulator_identity_password_uid_and_session_survive_restart(tmp_path, monkeypatch):
    """Opt-in real pinned emulator, isolated synthetic identity; no provider mocks."""
    import os, subprocess, sys, sqlite3
    from src.local_setup import runtime
    tools=os.getenv('HYDRO_TEST_FIREBASE_TOOLS_ROOT')
    if not tools: pytest.skip('Set HYDRO_TEST_FIREBASE_TOOLS_ROOT to installed pinned local Firebase Tools.')
    runtime.available_ports((9099,))
    state=tmp_path/'local'; runtime.claim_storage(state)
    env=runtime.sanitized_environment(); env['HYDRO_FIREBASE_TOOLS_ROOT']=tools
    node=os.getenv('HYDRO_TEST_NODE','node')
    def launch():
        proc=subprocess.Popen([node,str(runtime.ROOT/'scripts/local_auth_emulator.cjs')],env=env,
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
        runtime.wait_auth(proc); return proc
    def stop(proc):
        proc.stdin.write('stop\n'); proc.stdin.flush(); proc.wait(timeout=15)
    process=launch()
    try:
        account=runtime.auth_request('/identitytoolkit.googleapis.com/v1/accounts:signUp?key=fake-api-key',
            dict(email='persistence@example.test',password='Temporary-only-qa-9917',returnSecureToken=True))
        runtime.auth_request('/identitytoolkit.googleapis.com/v1/accounts:update?key=fake-api-key',
            dict(localId=account['localId'],emailVerified=True))
        signed=runtime.auth_request('/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake-api-key',
            dict(email='persistence@example.test',password='Temporary-only-qa-9917',returnSecureToken=True))
        cookie=runtime.auth_request(f'/identitytoolkit.googleapis.com/v1/projects/{runtime.PROJECT}:createSessionCookie',
            dict(idToken=signed['idToken'],validDuration=43200))['sessionCookie']
        (state/'auth').mkdir()
        with sqlite3.connect(state/'auth/auth.sqlite3') as db:
            db.execute('CREATE TABLE firebase_identities(uid TEXT PRIMARY KEY,user_id TEXT)')
            db.execute('INSERT INTO firebase_identities VALUES(?,?)',(account['localId'],'local-binding'))
            db.execute('CREATE TABLE sessions(token TEXT)'); db.execute('INSERT INTO sessions VALUES(?)',(cookie,))
        runtime.snapshot_auth(state)
        exported=json.loads((state/'identity-state.json').read_text())
        assert exported['sqlite_backups']['auth/auth.sqlite3']
        assert exported['accounts']['users'][0]['passwordHash']
        stop(process); process=launch(); runtime.import_auth(state)
        restarted=runtime.auth_request('/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake-api-key',
            dict(email='persistence@example.test',password='Temporary-only-qa-9917',returnSecureToken=True))
        assert restarted['localId']==account['localId']
        after=runtime.auth_request(f'/identitytoolkit.googleapis.com/v1/projects/{runtime.PROJECT}/accounts:batchGet?maxResults=-1')['users'][0]
        before=exported['accounts']['users'][0]
        assert after['validSince']==before['validSince']
        assert after['emailVerified'] is True
        with sqlite3.connect(state/'auth/auth.sqlite3') as db:
            assert db.execute('SELECT token FROM sessions').fetchone()[0]==cookie
    finally:
        if process.poll() is None: stop(process)

def test_local_app_real_owner_org_and_session_restart(monkeypatch):
    import os, subprocess, secrets, shutil
    from src.local_setup import runtime
    tools=os.getenv('HYDRO_TEST_FIREBASE_TOOLS_ROOT')
    if not tools: pytest.skip('Explicit opt-in local emulator tools required.')
    pytest.importorskip('firebase_admin')
    from src.local_setup import app as local
    from fastapi.testclient import TestClient
    state=runtime.ROOT/'.runtime'/('local-tests-'+secrets.token_hex(5))
    runtime.claim_storage(state)
    monkeypatch.setattr(runtime,'STATE',state); monkeypatch.setattr(local,'STATE',state)
    # Persistence must be tied to this explicit isolated test namespace.
    original_snapshot=runtime.snapshot_auth
    monkeypatch.setattr(runtime,'snapshot_auth',lambda path=None: original_snapshot(path or state))
    monkeypatch.setattr(local,'snapshot_auth',lambda: original_snapshot(state))
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    monkeypatch.setenv('HYDRO_LOCAL_OWNER_EMAIL','runtime-owner@example.test')
    password=secrets.token_urlsafe(24)
    monkeypatch.setenv('HYDRO_LOCAL_OWNER_PASSWORD',password)
    env=runtime.sanitized_environment(); env['HYDRO_FIREBASE_TOOLS_ROOT']=tools
    def launch():
        runtime.available_ports((9099,))
        proc=subprocess.Popen([os.getenv('HYDRO_TEST_NODE','node'),str(runtime.ROOT/'scripts/local_auth_emulator.cjs')],env=env,
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
        runtime.wait_auth(proc); return proc
    def stop(proc): proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=15)
    process=launch(); first=None; second=None
    try:
        first=local.build(mode='DEMO')
        assert runtime.bootstrap_owner(first)
        with TestClient(first,base_url=runtime.ORIGIN) as client:
            session=client.get('/api/session').json()
            signed=runtime.auth_request('/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake-api-key',dict(email='runtime-owner@example.test',password=password,returnSecureToken=True))
            response=client.post('/api/firebase/session',json={'id_token':signed['idToken']},headers={'X-CSRF-Token':session['csrf_token']})
            assert response.status_code==200,response.text
            assert response.json()['user']['role']=='PLATFORM_OWNER'
            csrf=response.json()['csrf_token']
            org=client.post('/api/admin/organizations',json={'name':'Independent local institution'},headers={'X-CSRF-Token':csrf})
            assert org.status_code==201,org.text
            assert len(client.get('/api/signup-institutions').json()['organizations'])==2
            assert 'LOCAL / DEMO' in client.get('/organizations').text
            assert client.get('/health/local').json()['mode']=='DEMO'
            cookies=dict(client.cookies)
            owner_uid=signed['localId']
        original_snapshot(state);first.state.service.gateway.close();first=None
        stop(process);process=launch();runtime.import_auth(state)
        second=local.build(mode='DEMO')
        assert not runtime.bootstrap_owner(second)
        with TestClient(second,base_url=runtime.ORIGIN,cookies=cookies) as client:
            current=client.get('/api/session').json()
            assert current['user']['role']=='PLATFORM_OWNER'
            assert len(client.get('/api/signup-institutions').json()['organizations'])==2
            assert client.get('/api/admin/users').status_code==200
            rebound=runtime.auth_request('/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake-api-key',dict(email='runtime-owner@example.test',password=password,returnSecureToken=True))
            assert rebound['localId']==owner_uid
    finally:
        for instance in (first,second):
            if instance:
                instance.state.scientific.close();instance.state.service.gateway.close()
        if process.poll() is None:stop(process)
        # This exact generated test namespace is owned by this test only.
        if state.parent==runtime.ROOT/'.runtime' and state.name.startswith('local-tests-'):shutil.rmtree(state)
