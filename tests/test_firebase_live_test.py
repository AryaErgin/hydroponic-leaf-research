"""Local live-test safety contracts; no real user or email creation in unit tests."""
import json
from pathlib import Path
import tempfile

import pytest

from src.hydroponic.config import ROOT


@pytest.fixture
def live_paths(monkeypatch):
    monkeypatch.delenv('K_SERVICE',raising=False)
    monkeypatch.delenv('FIREBASE_AUTH_EMULATOR_HOST',raising=False)
    monkeypatch.delenv('FIRESTORE_EMULATOR_HOST',raising=False)
    parent=ROOT/'.runtime/firebase/live';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as temp:
        base=Path(temp)
        (base/'web.json').write_text(json.dumps(dict(projectId='hydroponic-leaf-research-aryae',
            appId='1:242618885776:web:synthetic-test-app',authDomain='hydroponic-leaf-research-aryae.firebaseapp.com',
            apiKey='UNIT_TEST_PUBLIC_CONFIG_NOT_A_CREDENTIAL')))
        (base/'inboxes.json').write_text(json.dumps({'receiving_inboxes':[]}))
        yield base


def config(base):
    from src.firebase_preview.live_test import LiveTestConfig
    return LiveTestConfig(base/'auth','http://testserver',development=True,integrated=True,require_verified_email=True,
        web_config_path=base/'web.json',inboxes_path=base/'inboxes.json')


def test_live_mode_no_emulator_and_no_inboxes_by_default(live_paths):
    c=config(live_paths);public=c.public_config()
    assert public['projectId']=='hydroponic-leaf-research-aryae'
    assert public['identity_mode']=='google_live_test' and public['local_only'] is True
    assert not public.get('emulator') and public['test_inboxes_configured'] is False
    assert c.cookie=='hydro_firebase_live_test_v1'
    with pytest.raises(ValueError):c.require_receiving_inbox('invented@example.test')


@pytest.mark.parametrize('env',['K_SERVICE','FIREBASE_AUTH_EMULATOR_HOST','FIRESTORE_EMULATOR_HOST'])
def test_live_mode_refuses_cloud_and_emulator_environment(live_paths,monkeypatch,env):
    monkeypatch.setenv(env,'forbidden')
    with pytest.raises(ValueError):config(live_paths)


def test_live_mode_refuses_wrong_project_or_app_number(live_paths):
    data=json.loads((live_paths/'web.json').read_text())
    data['projectId']='unrelated-project';(live_paths/'web.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):config(live_paths)
    data['projectId']='hydroponic-leaf-research-aryae';data['appId']='1:999:web:bad'
    (live_paths/'web.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):config(live_paths)


def test_only_explicit_receiving_inboxes_allowed_and_never_publicly_listed(live_paths):
    # These strings test parsing only; no provider call/email is made.
    (live_paths/'inboxes.json').write_text(json.dumps({'receiving_inboxes':['Controlled-A@example.test','controlled-b@example.test']}))
    c=config(live_paths)
    assert c.require_receiving_inbox('controlled-a@example.test')=='controlled-a@example.test'
    with pytest.raises(ValueError):c.require_receiving_inbox('not-controlled@example.test')
    public=c.public_config();assert public['test_inboxes_configured']
    assert 'receiving_inboxes' not in public and 'controlled-a' not in json.dumps(public)


def test_one_receiving_inbox_is_sufficient_for_signup_and_password_reset(live_paths):
    (live_paths/'inboxes.json').write_text(json.dumps({'receiving_inboxes':['one-controlled@example.test']}))
    c=config(live_paths)
    assert c.public_config()['test_inboxes_configured'] is True
    assert c.require_receiving_inbox('one-controlled@example.test')=='one-controlled@example.test'


def test_live_mode_rejects_existing_local_auth_and_shared_science_store(live_paths):
    from dataclasses import replace
    c=config(live_paths)
    with pytest.raises(ValueError):replace(c,data_dir=ROOT/'.runtime/site/auth')
    with pytest.raises(ValueError):replace(c,firestore_project='hydroponic-leaf-research-aryae')
    with pytest.raises(ValueError):replace(c,phone_review_owner_id='anything')


def test_live_factory_has_no_emulator_and_requires_inbox_intent_with_csrf(live_paths):
    from fastapi.testclient import TestClient
    from src.firebase_preview.app import create_app
    class UnusedGateway:
        def inspect_id(self,*args):raise AssertionError('No provider call is needed before inbox configuration')
    c=config(live_paths);app=create_app(c,UnusedGateway(),live_paths/'science')
    with TestClient(app,base_url='http://testserver') as client:
        state=client.get('/api/session').json()
        assert client.get('/api/firebase/config').json()['identity_mode']=='google_live_test'
        page=client.get('/signup').text
        assert 'Google-managed email testing' in page and 'emulator and do not send mail' not in page
        assert client.post('/api/firebase/test-intent',json={'email':'unconfigured@example.test'}).status_code==403
        response=client.post('/api/firebase/test-intent',json={'email':'unconfigured@example.test'},
            headers={'Origin':'http://testserver','X-CSRF-Token':state['csrf_token']})
        assert response.status_code==400
        assert client.get('/api/profiles').status_code==401
        assert client.post('/api/login',json={}).status_code in (403,404,405)
        assert client.get('/health').json()['identity']=='google_live_test'
    app.state.scientific.close()
