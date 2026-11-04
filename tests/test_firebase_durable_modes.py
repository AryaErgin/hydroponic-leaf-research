"""Configuration and transactional boundary tests; no real cloud access."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from src.hydroponic.config import ROOT
from src.firebase_preview.identity import FirebaseConfig


WEB=dict(projectId='hydroponic-leaf-research-aryae',
    appId='1:242618885776:web:cd700b2282539597081a15',
    authDomain='hydroponic-leaf-research-aryae.firebaseapp.com',apiKey='public-fixture-key')


def production(monkeypatch, **overrides):
    from src.firebase_preview.modes import ProductionFirebaseConfig
    monkeypatch.delenv('FIRESTORE_EMULATOR_HOST',raising=False)
    monkeypatch.delenv('FIREBASE_AUTH_EMULATOR_HOST',raising=False)
    values=dict(data_dir=ROOT/'.runtime/firebase/production-config-test'/uuid4().hex,
                origin='https://hydroponic-research-242618885776.us-central1.run.app',
                integrated=True,require_verified_email=True,require_durable_auth=True,
                firestore_project=WEB['projectId'],firestore_prefix='hydroponic_firebase_auth_test',
                web_config=WEB)
    return ProductionFirebaseConfig(**dict(values,**overrides))


def test_explicit_production_config_is_durable_and_has_distinct_host_cookie(monkeypatch):
    config=production(monkeypatch)
    assert config.cookie=='__Host-hydro_firebase'
    assert config.public_config()['identity_mode']=='firebase_production'
    assert config.public_config()['local_only'] is False
    assert 'emulator' not in config.public_config()
    assert config.public_config()['origin']==config.origin


@pytest.mark.parametrize('changes',[
    {'origin':'http://localhost:8896'}, {'development':True},
    {'firestore_project':'unrelated-project'}, {'firestore_prefix':'hydroponic'},
    {'firestore_prefix':'hydroponic_phone_review_001_auth'},
    {'require_verified_email':False}, {'require_durable_auth':False},
    {'phone_review_owner_id':'owner-exception'},
    {'web_config':dict(WEB,projectId='unrelated-project')},
    {'web_config':dict(WEB,authDomain='untrusted.example')},
    {'web_config':dict(WEB,appId='1:242618885776:web:unapproved')},
])
def test_production_rejects_unsafe_modes_and_namespace_reuse(monkeypatch,changes):
    with pytest.raises(ValueError):production(monkeypatch,**changes)


def test_production_guard_refuses_emulator_after_configuration(monkeypatch):
    config=production(monkeypatch)
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    with pytest.raises(ValueError):config.guard()


def test_durable_emulator_is_loopback_and_existing_eval_guard_is_unchanged(monkeypatch):
    from src.firebase_preview.modes import DurableEmulatorConfig
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST','127.0.0.1:8791')
    values=dict(data_dir=ROOT/'.runtime/firebase/durable-config-test'/uuid4().hex,
        origin='http://testserver',development=True,integrated=True,require_verified_email=True,
        firestore_project=WEB['projectId'],firestore_prefix='firebase_test_'+uuid4().hex)
    assert DurableEmulatorConfig(**values).cookie=='hydro_firebase_durable_eval_v1'
    with pytest.raises(ValueError):FirebaseConfig(**values)
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST','remote.example:8791')
    with pytest.raises(ValueError):DurableEmulatorConfig(**values)


@pytest.mark.parametrize('fault',['missing_binding','wrong_account','wrong_uid','wrong_stamp','provider_revoked','cached_revocation'])
def test_atomic_scientific_commit_rechecks_firebase_binding_and_provider(fault):
    from src.integrated.science import ScientificServices,_current
    registry=object.__new__(ScientificServices)
    rows={'sessions':dict(user_id='member',expires=9999999999),
          'users':dict(id='member',status='ACTIVE',org_id='org',email_verified_at='verified'),
          'organizations':dict(enabled=True),
          'firebase_sessions':dict(uid='uid',security_stamp='fresh'),
          'firebase_identities':dict(uid='uid',user_id='member')}
    if fault=='missing_binding':rows['firebase_sessions']=None
    if fault=='wrong_account':rows['firebase_identities']['user_id']='someone-else'
    def ref(table,key):
        return SimpleNamespace(get=lambda **kwargs:SimpleNamespace(to_dict=lambda:rows[table]))
    def inspect_cookie(token):
        if fault=='cached_revocation':
            from src.firebase_preview.identity import request_cache
            if request_cache.get() is None:raise ValueError('Revoked after initial request authorization')
        if fault=='provider_revoked':raise ValueError('Revoked')
        return dict(uid='other' if fault=='wrong_uid' else 'uid',
                    stamp='old' if fault=='wrong_stamp' else 'fresh')
    registry.auth=SimpleNamespace(db=SimpleNamespace(ref=ref),credential_authority='firebase-v1',
        verified_for_workspace=lambda user:bool(user['email_verified_at']),
        gateway=SimpleNamespace(inspect_cookie=inspect_cookie))
    marker=_current.set((registry,'synthetic-cookie','org'))
    from src.firebase_preview.identity import request_cache
    cache_marker=request_cache.set({})
    try:
        with pytest.raises(HTTPException) as error:registry.authorize_commit(object())
        assert error.value.status_code==401
    finally:
        request_cache.reset(cache_marker)
        _current.reset(marker)


def test_atomic_scientific_commit_accepts_current_firebase_identity():
    from src.integrated.science import ScientificServices,_current
    registry=object.__new__(ScientificServices)
    rows={'sessions':dict(user_id='member',expires=9999999999),
          'users':dict(id='member',status='ACTIVE',org_id='org',email_verified_at='verified'),
          'organizations':dict(enabled=True),
          'firebase_sessions':dict(uid='uid',security_stamp='fresh'),
          'firebase_identities':dict(uid='uid',user_id='member')}
    registry.auth=SimpleNamespace(db=SimpleNamespace(ref=lambda table,key:SimpleNamespace(
        get=lambda **kwargs:SimpleNamespace(to_dict=lambda:rows[table]))),
        credential_authority='firebase-v1',verified_for_workspace=lambda user:True,
        gateway=SimpleNamespace(inspect_cookie=lambda token:dict(uid='uid',stamp='fresh')))
    marker=_current.set((registry,'synthetic-cookie','org'))
    try:assert registry.authorize_commit(object()) is None
    finally:_current.reset(marker)
