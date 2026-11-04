"""Durable Firebase grants against loopback demo emulators; never live cloud."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import time

import pytest
from fastapi import HTTPException
from firebase_admin import auth

from src.auth_preview.config import Config
from src.auth_preview.firestore_database import FirestoreDatabase, TABLES, opaque
from src.auth_preview.passwords import token_hash
from src.firebase_preview.identity import Gateway, PROJECT
from src.firebase_preview.service import FirebaseService
from src.hydroponic.config import ROOT
from test_firebase_identity import provider, signin, verify


@pytest.fixture
def repository(monkeypatch):
    for port in (8791,9099):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=1):pass
        except OSError:pytest.skip('Requires real loopback Firestore8791 and Auth9099 emulators')
    monkeypatch.delenv('K_SERVICE',raising=False)
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST','127.0.0.1:8791')
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    parent=ROOT/'.runtime/firebase/repository-tests';parent.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(dir=parent))
    config=Config(directory,'http://testserver',development=True,integrated=True,
        require_verified_email=True,firestore_project=PROJECT,firestore_prefix='firebase_test_'+uuid4().hex)
    gateway=Gateway();service=None;created_uids=set()
    try:
        service=FirebaseService(config,gateway)
        service._repository_fixture_uids=created_uids
        with service.db.transaction() as db:
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',('org','Synthetic University'))
            db.execute('INSERT INTO public_signup VALUES(1,?)',('org',))
        yield service
    finally:
        try:
            if service:
                try:
                    for collection in service.db.collections():
                        for snapshot in collection.stream():snapshot.reference.delete()
                finally:service.db.close()
        finally:
            try:
                gateway.guard()
                # Explicit IDs returned by this fixture's signUp calls only.
                # Unmapped and failed-registration identities are included;
                # technical binding-only IDs were never provider accounts.
                for uid in created_uids:
                    auth.delete_user(uid,app=gateway.app)
                    with pytest.raises(auth.UserNotFoundError):auth.get_user(uid,app=gateway.app)
            finally:
                gateway.close()
                resolved=directory.resolve()
                assert resolved.is_relative_to(parent.resolve()) and resolved!=parent.resolve()
                shutil.rmtree(resolved)


def provider_identity(service,prefix='repository'):
    identity=provider('signUp',dict(email=prefix+'-'+uuid4().hex+'@example.test',
        password='Synthetic Firebase passphrase 47!',returnSecureToken=True))
    service._repository_fixture_uids.add(identity['localId'])
    return identity


def register(service):
    identity=provider_identity(service)
    cookie,response=service.register(SimpleNamespace(id_token=identity['idToken'],
        username='synthetic-'+uuid4().hex[:12],display_name='Synthetic',requested_org_id='org'),'')
    return identity,cookie,response['user']


def user(db,id):
    db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (id,id,'FIREBASE_MANAGED','PENDING','PENDING','org',None,'2026-10-28'))


def test_repository_registration_restart_hashed_storage_and_logout_cascade(repository):
    service=repository;identity,cookie,registered=register(service)
    restarted=FirebaseService(service.config,service.gateway)
    try:
        with restarted.db.transaction() as db:
            assert restarted.actor(db,cookie,active=False)['id']==registered['id']
            row=db.execute('SELECT * FROM users WHERE id=?',(registered['id'],)).fetchone()
            assert row['password_hash']=='FIREBASE_MANAGED'
            assert row['status']=='PENDING' and row['version']==1
            binding=db.execute('SELECT f.*,i.user_id FROM firebase_sessions f JOIN firebase_identities i ON i.uid=f.uid WHERE f.token_hash=?',
                (token_hash(cookie),)).fetchone()
            assert binding['uid']==identity['localId'] and binding['user_id']==registered['id']
            assert set(db.raw('firebase_sessions',token_hash(cookie)))=={'token_hash','uid','security_stamp'}
        serialized=json.dumps([snap.to_dict() for collection in service.db.collections() for snap in collection.stream()])
        assert cookie not in serialized and identity['idToken'] not in serialized and identity['refreshToken'] not in serialized
        with restarted.db.transaction() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(cookie),))
            assert not db.raw('firebase_sessions',token_hash(cookie))
            assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        with service.db.transaction() as db:assert not service.session(db,cookie)
    finally:restarted.db.close()


def test_identity_unique_case_sensitive_immutable_and_rollback(repository):
    service=repository
    with service.db.transaction() as db:
        for id in ('a','b','c'):user(db,id)
        db.execute('INSERT INTO firebase_identities VALUES(?,?)',('CaseSensitive','a'))
        db.execute('INSERT INTO firebase_identities VALUES(?,?)',('casesensitive','b'))
    for uid,id in [('CaseSensitive','c'),('new-uid','a')]:
        with pytest.raises(sqlite3.IntegrityError):
            with service.db.transaction() as db:db.execute('INSERT INTO firebase_identities VALUES(?,?)',(uid,id))
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.transaction() as db:db.save('firebase_identities',dict(uid='CaseSensitive',user_id='c'))
    with pytest.raises(RuntimeError):
        with service.db.transaction() as db:
            user(db,'rolled-back')
            db.execute('INSERT INTO firebase_identities VALUES(?,?)',('rolled-back','rolled-back'))
            raise RuntimeError('synthetic rollback')
    with service.db.transaction() as db:
        assert db.execute('SELECT user_id FROM firebase_identities WHERE uid=?',('CaseSensitive',)).fetchone()['user_id']=='a'
        assert not db.raw('firebase_identities','rolled-back') and not db.raw('users','rolled-back')


@pytest.mark.parametrize('shared',['uid','user'])
def test_concurrent_identity_reservation_is_atomic(repository,shared):
    service=repository
    with service.db.transaction() as db:
        user(db,'a');user(db,'b')
    def bind(n):
        try:
            with service.db.transaction() as db:
                db.execute('INSERT INTO firebase_identities VALUES(?,?)',
                    ('same' if shared=='uid' else str(n),'a' if shared=='user' else ('a','b')[n]))
            return 200
        except (HTTPException,sqlite3.IntegrityError):return 409
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(bind,range(2)))==[200,409]
    with service.db.transaction() as db:assert len(db.scan('firebase_identities'))==1


def test_orphan_identity_session_and_cross_account_bindings_rejected(repository):
    service=repository
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.transaction() as db:db.execute('INSERT INTO firebase_identities VALUES(?,?)',('orphan','absent'))
    with service.db.transaction() as db:
        user(db,'a');user(db,'b');db.execute('INSERT INTO firebase_identities VALUES(?,?)',('known','a'))
    for digest,uid,owner in [('missing-session','known',None),('missing-identity','absent','a'),('cross-account','known','b')]:
        with pytest.raises(sqlite3.IntegrityError):
            with service.db.transaction() as db:
                if owner:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(digest,owner,'csrf',time.time()+100,0))
                db.execute('INSERT INTO firebase_sessions VALUES(?,?,?)',(digest,uid,'stamp'))
    with service.db.transaction() as db:assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0


def test_revoke_expiry_provider_revocation_and_bound_session_counter(repository):
    service=repository;identity,cookie,registered=register(service)
    second=service.exchange(identity['idToken'],'')[0]
    with service.db.transaction() as db:
        service.revoke(db,registered['id'])
        assert not db.raw('firebase_sessions',token_hash(cookie)) and not db.raw('firebase_sessions',token_hash(second))
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
    expired=service.exchange(identity['idToken'],'')[0]
    with service.db.transaction() as db:
        row=db.raw('sessions',token_hash(expired));row['expires']=0;db.save('sessions',row)
        assert not service.session(db,expired)
        db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        assert not db.raw('firebase_sessions',token_hash(expired))
    current=service.exchange(identity['idToken'],'')[0]
    auth.update_user(identity['localId'],disabled=True,app=service.gateway.app)
    restarted=FirebaseService(service.config,service.gateway)
    try:
        with restarted.db.transaction() as db:assert not restarted.session(db,current)
    finally:restarted.db.close()


def test_proof_update_versions_and_no_automatic_email_link(repository):
    service=repository;identity,cookie,registered=register(service)
    fresh=verify(identity)
    cookie,result=service.exchange(fresh['idToken'],cookie)
    assert result['user']['version']==registered['version']+1 and result['user']['email_verified_at']
    cookie,unchanged=service.exchange(fresh['idToken'],cookie)
    assert unchanged['user']['version']==result['user']['version']
    auth.update_user(identity['localId'],email_verified=False,app=service.gateway.app)
    fresh=signin(identity)
    cookie,changed=service.exchange(fresh['idToken'],cookie)
    assert changed['user']['version']==unchanged['user']['version']+1 and changed['user']['email_verified_at'] is None
    with service.db.transaction() as db:
        db.execute('DELETE FROM sessions WHERE user_id=?',(registered['id'],))
        # A provider UID remains explicitly bound, even if another account has a
        # matching email; repository never derives a UID mapping from email.
        assert db.execute('SELECT user_id FROM firebase_identities WHERE uid=?',(identity['localId'],)).fetchone()[0]==registered['id']
    unmapped=provider_identity(service,'unmapped')
    with pytest.raises(HTTPException) as error:service.exchange(unmapped['idToken'],'')
    assert error.value.status_code==403


def test_session_pool_and_per_user_bound_are_preserved(repository):
    service=repository;identity,cookie,registered=register(service);service.session_capacity=3
    with service.db.transaction() as db:
        for _ in range(2):service.new_session(db)
    second=service.exchange(identity['idToken'],'')[0]
    with service.db.transaction() as db:
        assert service.session(db,cookie) and service.session(db,second)
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==3
    service.session_capacity=1000
    with service.db.transaction() as db:
        for n in range(48):
            digest='synthetic-'+str(n)
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(digest,registered['id'],'csrf',time.time()+100,0))
            db.execute('INSERT INTO firebase_sessions VALUES(?,?,?)',(digest,identity['localId'],'synthetic-stamp'))
    with pytest.raises(HTTPException) as error:service.exchange(identity['idToken'],'')
    assert error.value.status_code==429
    with service.db.transaction() as db:assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==51


def test_namespace_authority_rejects_legacy_and_unsupported_sql(repository):
    service=repository
    with pytest.raises(ValueError,match='credential authority'):FirestoreDatabase(service.config)
    with service.db.transaction() as db:
        assert db.raw('meta','credential-authority')['value']=='firebase-v1'
        for statement in ('DELETE FROM firebase_identities','INSERT OR REPLACE INTO firebase_identities VALUES(?,?)',
                          'UPDATE firebase_sessions SET uid=? WHERE token_hash=?'):
            with pytest.raises(ValueError):db.execute(statement,('a','b'))
    prefix=service.config.firestore_prefix+'_legacy'
    from dataclasses import replace
    legacy=FirestoreDatabase(replace(service.config,firestore_prefix=prefix))
    try:
        with pytest.raises(ValueError,match='credential authority'):FirebaseService(replace(service.config,firestore_prefix=prefix),service.gateway)
    finally:
        for collection in legacy.collections():
            for snapshot in collection.stream():snapshot.reference.delete()
        legacy.close()

@pytest.mark.parametrize('populated',['users','organizations','sessions','runtime_mode','firebase_identities'])
def test_firebase_cannot_claim_any_unmarked_populated_namespace(repository,populated):
    from dataclasses import replace
    service=repository;config=replace(service.config,firestore_prefix=service.config.firestore_prefix+'_unmarked')
    # Deliberately create old/corrupt unmarked metadata in this owned emulator
    # fixture only; no service is permitted to adopt it as an identity migration.
    ref=service.db.client.collection(config.firestore_prefix+'_'+populated).document('fixture')
    ref.set({'synthetic_fixture':True})
    try:
        with pytest.raises(ValueError,match='credential authority'):FirebaseService(config,service.gateway)
        marker=service.db.client.collection(config.firestore_prefix+'_meta').document(opaque('credential-authority'))
        assert not marker.get().exists
    finally:
        for table in TABLES:
            for snapshot in service.db.client.collection(config.firestore_prefix+'_'+table).stream():snapshot.reference.delete()


def test_existing_email_requires_explicit_identity_link_and_failed_registration_rolls_back(repository):
    service=repository
    identity=provider_identity(service,'legacy')
    with service.db.transaction() as db:
        user(db,'legacy-user')
        db.execute('UPDATE users SET email=?,email_verified_at=?,version=version+1 WHERE id=?',
            (identity['email'],'2026-10-28','legacy-user'))
    with pytest.raises(HTTPException) as error:service.exchange(identity['idToken'],'')
    assert error.value.status_code==403
    with pytest.raises(HTTPException) as error:
        service.register(SimpleNamespace(id_token=identity['idToken'],username='new-'+uuid4().hex,
            display_name='',requested_org_id='org'),'')
    assert error.value.status_code==409
    with service.db.transaction() as db:
        assert len(db.scan('users'))==1 and not db.scan('firebase_identities') and not db.scan('sessions')
        assert not db.scan('audit')


def test_session_binding_is_immutable_and_user_identifiers_are_case_sensitive(repository):
    service=repository
    with service.db.transaction() as db:
        user(db,'Exact');user(db,'exact-other')
        # Backend IDs, unlike usernames, have exact case semantics too.
        row=db.raw('users','exact-other');row['id']='exact';row['username']='another-username'
        db.save('users',row,insert=True)
        db.execute('INSERT INTO firebase_identities VALUES(?,?)',('uid-Exact','Exact'))
        db.execute('INSERT INTO firebase_identities VALUES(?,?)',('uid-exact','exact'))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',('hash','Exact','csrf',time.time()+100,0))
        db.execute('INSERT INTO firebase_sessions VALUES(?,?,?)',('hash','uid-Exact','stamp'))
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.transaction() as db:db.save('firebase_sessions',dict(token_hash='hash',uid='uid-exact',security_stamp='stamp'))
    with service.db.transaction() as db:
        assert db.raw('firebase_sessions','hash')['uid']=='uid-Exact'
        assert db.raw('firebase_identities','uid-Exact')['user_id']=='Exact'
        assert db.raw('firebase_identities','uid-exact')['user_id']=='exact'
