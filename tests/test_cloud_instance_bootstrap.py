"""Real auth statement interpreter with an offline transactional document store."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from src.auth_preview.firestore_database import Connection,opaque
from test_cloud_instance import manifest,clean_environment


class OfflineConnection(Connection):
    def __enter__(self):
        self.rows=deepcopy(self.database.rows)
        return self
    def __exit__(self,kind,error,traceback):
        if kind is None:self.database.rows=self.rows
    def raw(self,table,key):return deepcopy(self.rows.get((table,str(key))))
    def put(self,table,key,row):
        if row is None:self.rows.pop((table,str(key)),None)
        else:self.rows[(table,str(key))]=deepcopy(row)
    def scan(self,table,field=None,value=None,op='==',cap=250,order=None,descending=False,limit=None):
        rows=[deepcopy(row) for (t,key),row in self.rows.items() if t==table and (field is None or row.get(field)==value)]
        if order:rows.sort(key=lambda row:row[order],reverse=descending)
        return rows[:limit] if limit else rows


class OfflineDatabase:
    durable=True
    credential_authority='firebase-v1'
    def __init__(self,config):
        self.prefix=config.firestore_prefix;self.client=SimpleNamespace(project=config.firestore_project)
        self.rows={}
    def transaction(self):return OfflineConnection(self)


@pytest.fixture
def service():
    from src.cloud_instance.config import CloudInstanceConfig
    from src.cloud_instance.service import IndependentService
    config=CloudInstanceConfig.from_mapping(manifest()).auth_config()
    gateway=SimpleNamespace(config=config,lookup_owner=lambda uid,email:dict(uid=uid,email=email,email_verified=True,disabled=False))
    return IndependentService(config,gateway,database=OfflineDatabase(config))


def pending(service,uid='verified-owner',email='owner@example.test',org=None):
    with service.db.transaction() as db:
        db.execute('INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('account-'+uid,uid,'Owner','FIREBASE_MANAGED','PENDING','PENDING',org or service.config.instance.organization_id,'2026-11-15',email,'verified'))
        db.execute('INSERT INTO firebase_identities VALUES(?,?)',(uid,'account-'+uid))
    return dict(uid=uid,email=email,email_verified=True)


def test_empty_installation_initializes_institution_but_never_owner(service):
    with service.db.transaction() as db:
        assert not db.scan('users')
        assert db.raw('organizations',service.config.instance.organization_id)['name']=='Independent University'
        assert db.raw('public_signup','1')['org_id']==service.config.instance.organization_id


def test_first_and_additional_institution_registrations_stay_pending(service):
    from fastapi import HTTPException
    service.gateway.cookie=lambda token,seconds:'provider-session'
    with service.db.transaction() as db:
        db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',('new-university','Second University'))
    for index,org in enumerate((service.config.instance.organization_id,'new-university')):
        identity=dict(uid='uid-'+str(index),email=f'member{index}@example.test',email_verified=True,stamp='stamp')
        service.gateway.inspect_id=lambda token:identity
        service.gateway.inspect_cookie=lambda token:identity
        token,result=service.register(SimpleNamespace(id_token='proof',username='member-'+str(index),
            display_name='Member',requested_org_id=org),'')
        assert result['user']['status']=='PENDING' and result['user']['role']=='PENDING'
        assert result['user']['org_id'] is None
        with service.db.transaction() as db:
            assert not db.raw('meta','independent-owner')
            with pytest.raises(HTTPException) as error:service.actor(db,token)
            assert error.value.status_code==403


def test_exact_verified_pending_bootstrap_atomic_and_idempotent(service):
    from src.cloud_instance.service import bootstrap_owner
    identity=pending(service)
    first=bootstrap_owner(service,identity)
    assert first['role']=='PLATFORM_OWNER' and first['status']=='ACTIVE'
    with service.db.transaction() as db:
        version=db.raw('users',first['id'])['version'];audit=len(db.scan('audit'))
    assert bootstrap_owner(service,identity)==first
    with service.db.transaction() as db:
        assert db.raw('users',first['id'])['version']==version
        assert len(db.scan('audit'))==audit
    other=pending(service,'second-owner','other@example.test')
    with pytest.raises(ValueError,match='already'):bootstrap_owner(service,other)


@pytest.mark.parametrize('change',[dict(email_verified=False),dict(email='mismatch@example.test'),dict(uid='missing')])
def test_bootstrap_rejects_unverified_mismatched_or_unregistered(service,change):
    from src.cloud_instance.service import bootstrap_owner
    identity=pending(service);identity.update(change)
    before=deepcopy(service.db.rows)
    with pytest.raises(ValueError):bootstrap_owner(service,identity)
    assert service.db.rows==before


def test_bootstrap_rechecks_provider_and_rolls_back_if_audit_fails(service):
    from src.cloud_instance.service import bootstrap_owner
    identity=pending(service)
    service.gateway.lookup_owner=lambda *args:dict(identity,disabled=True)
    with pytest.raises(ValueError):bootstrap_owner(service,identity)
    service.gateway.lookup_owner=lambda *args:dict(identity,disabled=False)
    before=deepcopy(service.db.rows)
    service.audit=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('audit failed'))
    with pytest.raises(RuntimeError,match='audit failed'):bootstrap_owner(service,identity)
    assert service.db.rows==before


def test_dynamic_enabled_institutions_accept_registration_and_disabled_refused(service):
    with service.db.transaction() as db:
        db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',('new-university','Second University'))
        assert service.registration_organization(db,'new-university')['id']=='new-university'
    assert {row['id'] for row in service.signup_institutions()}=={service.config.instance.organization_id,'new-university'}
    with service.db.transaction() as db:
        db.update('organizations','new-university',dict(enabled=False))
        with pytest.raises(Exception):service.registration_organization(db,'new-university')
    assert len(service.signup_institutions())==1


def test_independent_app_reuses_integrated_routes_without_test_or_owner_endpoint(service,monkeypatch):
    from fastapi.testclient import TestClient
    from src.cloud_instance.gateway import IndependentGateway
    from src.firebase_preview.app import create_app
    gateway=object.__new__(IndependentGateway);gateway.config=service.config
    monkeypatch.setattr('src.cloud_instance.service.IndependentService',lambda *args,**kwargs:service)
    settings=service.config.instance.scientific_settings('s'*48)
    app=create_app(service.config,gateway,settings.data_dir,scientific_settings=settings)
    paths={getattr(route,'path','') for route in app.routes}
    assert '/api/firebase/register' in paths and '/api/analyses/{id}/approve' in paths
    assert not any('/api/dev' in path or 'bootstrap' in path or 'test-intent' in path for path in paths)
    with TestClient(app,base_url=service.config.origin) as client:
        assert client.get('/api/firebase/config').json()['identity_mode']=='independent_cloud'
        assert client.get('/api/session').json()['independent_cloud'] is True
        assert client.get('/health').json()['scientific_ready'] is False
        app.state.independent_model_status={'status':'PASSED','device':'cpu'}
        assert client.get('/health').json()['scientific_ready'] is True
        assert client.get('/api/dev/personas').status_code==404
