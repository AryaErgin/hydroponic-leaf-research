"""Offline boundaries for independently owned installations; never cloud calls."""
from copy import deepcopy
from dataclasses import replace
import importlib
import os
from pathlib import Path

import pytest


def manifest():
    return dict(project_id='developer-research-123',project_number='123456789012',
        installation_id='a'*32,region='us-central1',service_name='hydroponic-leaf-research',
        firestore_database='(default)',bucket='developer-research-123-hlr-science',
        auth_prefix='hlr_'+'a'*16+'_auth',science_prefix='hlr_'+'a'*16+'_science',
        origin='https://hydroponic-leaf-research-123456789012.us-central1.run.app',
        firebase_web=dict(projectId='developer-research-123',appId='1:123456789012:web:abc123',
            authDomain='developer-research-123.firebaseapp.com',apiKey='public-browser-key'),
        institution_name='Independent University')


def module():
    assert importlib.util.find_spec('src.cloud_instance') is not None, 'Independent cloud configuration is missing'
    return importlib.import_module('src.cloud_instance.config')


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('HYDRO_','FIREBASE_','FIRESTORE_')) or key in ('K_SERVICE','GOOGLE_CLOUD_PROJECT','GCLOUD_PROJECT','GOOGLE_CLOUD_QUOTA_PROJECT','GOOGLE_APPLICATION_CREDENTIALS'):
            monkeypatch.delenv(key,raising=False)


def test_explicit_instance_config_binds_all_resources_and_public_web():
    config=module().CloudInstanceConfig.from_mapping(manifest())
    assert config.project_id=='developer-research-123'
    auth=config.auth_config()
    assert auth.firestore_project==config.project_id and auth.require_durable_auth
    assert auth.public_config()['identity_mode']=='independent_cloud'
    assert auth.public_config()['local_only'] is False
    settings=config.scientific_settings('x'*48)
    assert settings.validate().gcs_bucket==config.bucket
    assert settings.cloud_run and settings.inference_device=='cpu'


@pytest.mark.parametrize('field,value',[
    ('project_id','hydroponic-leaf-research-aryae'),('project_number','242618885776'),
    ('bucket','hydroponic-leaf-research-aryae-science'),('auth_prefix','hydroponic_firebase_auth_v1'),
    ('science_prefix','tenant_shared'),('origin','https://unrelated.example.org'),
    ('origin','http://localhost:8896'),('installation_id','../escape'),
    ('firestore_database','other'),('institution_name',' '),
])
def test_instance_rejects_unsafe_or_mixed_manifest(field,value):
    data=manifest();data[field]=value
    with pytest.raises(ValueError):module().CloudInstanceConfig.from_mapping(data)


@pytest.mark.parametrize('field,value',[
    ('projectId','unrelated-project'),('appId','1:999999999999:web:abc'),
    ('authDomain','hydroponic-leaf-research-aryae.firebaseapp.com'),('emulator','http://localhost:9099')])
def test_web_identity_cannot_cross_project(field,value):
    data=manifest();data['firebase_web'][field]=value
    with pytest.raises(ValueError):module().CloudInstanceConfig.from_mapping(data)


@pytest.mark.parametrize('key,value',[
    ('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099'),('FIRESTORE_EMULATOR_HOST','127.0.0.1:8791'),
    ('GOOGLE_CLOUD_PROJECT','other-project'),('GCLOUD_PROJECT','hydroponic-leaf-research-aryae'),
    ('HYDRO_FIRESTORE_PROJECT','other-project'),('HYDRO_GCS_BUCKET','other-bucket'),
    ('GOOGLE_APPLICATION_CREDENTIALS','private-key.json')])
def test_runtime_environment_must_not_redirect_instance(monkeypatch,key,value):
    config=module().CloudInstanceConfig.from_mapping(manifest())
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError):config.guard()


def test_opt_in_settings_reject_cross_project_storage_and_legacy_remains_guarded():
    from src.webapp.settings import Settings
    config=module().CloudInstanceConfig.from_mapping(manifest())
    settings=config.scientific_settings('s'*48)
    for update in [dict(firestore_project='other-project'),dict(gcs_bucket='other-bucket'),
                   dict(firestore_prefix='shared'),dict(auth_token=None),dict(cloud_run=False),
                   dict(inference_device='cuda'),dict(database_url='sqlite:///fallback')]:
        with pytest.raises(ValueError):replace(settings,**update).validate()
    with pytest.raises(ValueError):Settings(firestore_project=config.project_id).validate()


def test_instance_auth_options_cannot_relax_identity_or_durable_boundary():
    auth=module().CloudInstanceConfig.from_mapping(manifest()).auth_config()
    for update in [dict(development=True),dict(integrated=False),dict(require_verified_email=False),
                   dict(require_durable_auth=False),dict(firestore_project='other-project'),
                   dict(firestore_prefix='shared_auth'),dict(origin='https://other.example'),
                   dict(phone_review_owner_id='auto-owner')]:
        with pytest.raises(ValueError):replace(auth,**update)


def test_startup_model_probe_propagates_failure_without_fallback(monkeypatch):
    module()
    runtime=importlib.import_module('src.cloud_instance.runtime')
    def fail(*args):raise ValueError('tampered model')
    monkeypatch.setattr('src.local_setup.diagnostics.model_smoke',fail)
    with pytest.raises(ValueError,match='tampered model'):runtime.verify_scientific_runtime()


def test_runtime_adc_rejects_cached_user_credentials_and_other_service_account(monkeypatch):
    from src.cloud_instance.gateway import require_runtime_adc
    from google.oauth2.credentials import Credentials
    from google.auth.compute_engine import Credentials as MetadataCredentials
    from types import SimpleNamespace
    config=module().CloudInstanceConfig.from_mapping(manifest()).auth_config()
    monkeypatch.setattr('firebase_admin.credentials.ApplicationDefault',lambda:SimpleNamespace(
        project_id=config.firestore_project,get_credential=lambda:Credentials(token='local-user-token')))
    with pytest.raises(ValueError,match='metadata'):require_runtime_adc(config)
    credential=MetadataCredentials(service_account_email='runtime@foreign-project.iam.gserviceaccount.com')
    monkeypatch.setattr(credential,'refresh',lambda request:None)
    monkeypatch.setattr('firebase_admin.credentials.ApplicationDefault',lambda:SimpleNamespace(
        project_id=config.firestore_project,get_credential=lambda:credential))
    with pytest.raises(ValueError,match='service account'):require_runtime_adc(config)


def test_runtime_does_not_trust_public_forwarded_addresses_for_rate_limits(monkeypatch):
    from src.cloud_instance import runtime
    calls=[];application=object()
    monkeypatch.setattr(runtime,'build',lambda:application)
    monkeypatch.setattr('uvicorn.run',lambda app,**options:calls.append((app,options)))
    runtime.main()
    assert calls[0][0] is application
    assert calls[0][1]['proxy_headers'] is False
    assert calls[0][1].get('forwarded_allow_ips')!='*'


@pytest.mark.parametrize('change',[
    dict(project_id='goog-owned-example'),dict(institution_name='University\nInjected'),
    dict(institution_name=' University '),dict(api_key='browser key with spaces')])
def test_runtime_matches_strict_plan_manifest_contract(change):
    data=manifest()
    if 'api_key' in change:data['firebase_web']['apiKey']=change['api_key']
    else:data.update(change)
    with pytest.raises(ValueError):module().CloudInstanceConfig.from_mapping(data)
