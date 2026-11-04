"""Offline review and LOCAL synthetic restore rehearsal, never a migration tool.

Actual Firestore/Auth emulators; GCS transport double and inference double are
explicit. No real approval, credentials, production reads or cloud fallback.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.firebase_preview.migration_preflight import digest, validate_release_mapping
from src.hydroponic.config import ROOT


def reviewed():
    return dict(review_status='REVIEWED', reviewed_by='synthetic-reviewer',
        reviewed_at_utc='2026-10-28T00:00:00Z', evidence_reference='synthetic-proof')


def mapping_fixture():
    expected = [dict(source_record_id='source-a:row:profiles:p', source_id='source-a',
        original_id='p', kind='profiles', expected_sha256='a'*64,
        parent_source_record_ids=[]), dict(source_record_id='source-a:row:measurements:m',
        source_id='source-a', original_id='m', kind='measurements', expected_sha256='b'*64,
        parent_source_record_ids=['source-a:row:profiles:p'])]
    records = [dict(row, target_org_id='institution-a', **reviewed()) for row in expected]
    bindings = [dict(firebase_uid='synthetic-real-owner-contract', backend_user_id='owner',
        backend_auth_source='authoritative-source', firebase_project='hydroponic-leaf-research-aryae',
        verified_org_id='institution-a', role='PLATFORM_OWNER', status='ACTIVE',
        identity_kind='REAL_OWNER', expected_account_version=2,
        expected_account_sha256='c'*64, provider_and_recovery_proof_reference='synthetic-proof', **reviewed())]
    return expected, records, bindings


def validate(expected, records, bindings, **kwargs):
    return validate_release_mapping(expected, records, bindings,
        verified_org_ids=('institution-a', 'institution-b'), **kwargs)


def test_offline_mapping_accepts_only_explicit_review_contract():
    result = validate(*mapping_fixture())
    assert result['status'] == 'PASS_OFFLINE_REVIEW_ONLY'
    assert not result['migration_performed'] and not result['writes_performed']


@pytest.mark.parametrize('case', ['unreviewed', 'hash', 'source', 'duplicate-record',
    'parent', 'duplicate-uid', 'duplicate-backend', 'no-owner', 'two-owners',
    'synthetic-owner', 'unknown-owner-source', 'unknown-org', 'missing-record', 'no-account-hash'])
def test_offline_mapping_rejects_unresolved_or_conflicting_attribution(case):
    expected, records, bindings = mapping_fixture()
    if case == 'unreviewed':records[0]['review_status'] = 'UNASSIGNED'
    elif case == 'hash':records[0]['expected_sha256'] = 'd'*64
    elif case == 'source':records[0]['source_id'] = 'wrong-source'
    elif case == 'duplicate-record':records.append(copy.deepcopy(records[0]))
    elif case == 'parent':records[0]['target_org_id'] = 'institution-b'
    elif case == 'duplicate-uid':bindings.append(dict(bindings[0], backend_user_id='other', role='MEMBER'))
    elif case == 'duplicate-backend':bindings.append(dict(bindings[0], firebase_uid='other', role='MEMBER'))
    elif case == 'no-owner':bindings[0]['role'] = 'MEMBER'
    elif case == 'two-owners':bindings.append(dict(bindings[0], firebase_uid='other', backend_user_id='other'))
    elif case == 'synthetic-owner':bindings[0]['identity_kind'] = 'EMAIL_DELIVERY_TEST'
    elif case == 'unknown-owner-source':bindings[0]['backend_auth_source'] = 'UNSET'
    elif case == 'unknown-org':bindings[0]['verified_org_id'] = 'UNSET'
    elif case == 'missing-record':records.pop()
    elif case == 'no-account-hash':bindings[0]['expected_account_sha256'] = None
    result = validate(expected, records, bindings)
    assert result['status'] == 'BLOCKED' and result['issue_count'] > 0


def test_same_receiver_test_uid_cannot_be_promoted_to_owner():
    expected, records, bindings = mapping_fixture()
    result = validate(expected, records, bindings, forbidden_uids=[bindings[0]['firebase_uid']])
    assert any(x['reason'] == 'synthetic_or_unproven_owner' for x in result['issues'])


@pytest.mark.parametrize('role', ['PLATFORM_OWNER', 'MEMBER'])
def test_offline_mapping_rejects_wrong_firebase_project_for_every_binding(role):
    expected, records, bindings = mapping_fixture()
    if role == 'MEMBER':
        bindings.append(dict(bindings[0], firebase_uid='member', backend_user_id='member', role=role))
    bindings[-1]['firebase_project'] = 'other-project'
    result = validate(expected, records, bindings)
    assert result['status'] == 'BLOCKED'
    assert any(x['reason'] == 'firebase_project_mismatch' for x in result['issues'])


@pytest.mark.parametrize('field,value,reason', [
    ('role', 'ARBITRARY_ROLE', 'invalid_binding_role'),
    ('status', 'ARBITRARY_STATUS', 'invalid_binding_status'),
    ('role', 'PENDING', 'active_membership_required'),
    ('verified_org_id', None, 'active_membership_required'),
    ('verified_org_id', 'UNSET', 'active_membership_required'),
    ('verified_org_id', '', 'active_membership_required'),
    ('verified_org_id', 'unverified-institution', 'active_membership_required'),
])
def test_offline_mapping_validates_member_grants_with_stable_owner(field, value, reason):
    expected, records, bindings = mapping_fixture()
    bindings.append(dict(bindings[0], firebase_uid='member', backend_user_id='member', role='MEMBER'))
    bindings[-1][field] = value
    # Even a malformed operator allowlist cannot make missing membership ACTIVE.
    result = validate_release_mapping(expected, records, bindings,
        verified_org_ids=('institution-a', None, 'UNSET', ''))
    assert result['status'] == 'BLOCKED'
    assert any(x['reason'] == reason for x in result['issues'])


def test_offline_mapping_repeat_preserves_same_owner_and_all_inputs():
    inputs = mapping_fixture()
    frozen = copy.deepcopy(inputs)
    first = validate(*inputs)
    second = validate(*inputs)
    assert first == second and first['status'] == 'PASS_OFFLINE_REVIEW_ONLY'
    assert inputs == frozen


EPHEMERAL = {'sessions', 'firebase_sessions', 'recovery', 'email_challenges', 'limits', 'meta'}


def logical_backup(app, transport):
    """Known synthetic namespaces only; no provider credentials or session proofs."""
    database = app.state.service.db
    assert database.prefix.startswith('firebase_test_restore_')
    auth = {}
    for collection in database.collections():
        table = collection.id[len(database.prefix)+1:]
        if table not in EPHEMERAL:
            auth[table] = {doc.id: doc.to_dict() for doc in collection.stream()}
    science, objects = {}, {}
    for org in ('org-bau', 'org-b'):
        tenant = app.state.scientific.for_organization(org)
        engine = tenant.db.engine
        science[org] = {table: {doc.id: doc.to_dict() for doc in
            engine.client.collection(engine.prefix+'_'+table).stream()} for table in engine.tables}
        objects[org] = {key[len(tenant.media.prefix):]: bytes(value) for key, value in
            transport.objects.items() if key.startswith(tenant.media.prefix)}
    return dict(auth=auth, science=science, objects=objects)


def private_backup_roundtrip(directory, backup):
    """Create-only disk backup, then restore input is loaded from captured bytes."""
    directory.mkdir(parents=True)
    files = directory/'bytes';files.mkdir()
    manifest = {}
    for org, objects in backup['objects'].items():
        manifest[org] = {}
        for key, value in objects.items():
            sha = hashlib.sha256(value).hexdigest()
            manifest[org][key] = sha
            target = files/sha
            if not target.exists():target.write_bytes(value)
    logical = {key:value for key,value in backup.items() if key != 'objects'}
    logical['objects'] = manifest
    (directory/'logical.json').write_text(json.dumps(logical, sort_keys=True), encoding='utf-8')
    loaded = json.loads((directory/'logical.json').read_text(encoding='utf-8'))
    loaded['objects'] = {org:{key:(files/sha).read_bytes() for key,sha in objects.items()}
        for org,objects in loaded['objects'].items()}
    assert loaded == backup
    return loaded


def restore_synthetic_backup(app, transport, backup, *, interrupt_after=None):
    """Test fixture executor: create-only/idempotent, blocks source/dest drift.

    It accepts only this test's emulator prefixes, rebuilds account reservations
    through repository save, and never restores sessions/security challenges.
    """
    db = app.state.service.db
    assert os.getenv('FIRESTORE_EMULATOR_HOST') == '127.0.0.1:8791'
    assert db.prefix.startswith('firebase_test_restore_')
    with db.transaction() as tx:
        for table in ('organizations', 'users', 'public_signup', 'firebase_identities'):
            for row in backup['auth'].get(table, {}).values():
                key = tx.key(table, row)
                old = tx.raw(table, key)
                if old is not None and old != row:raise ValueError('Synthetic restore auth conflict')
                if old is None:tx.save(table, copy.deepcopy(row), insert=True)
        for table, rows in backup['auth'].items():
            if table in ('organizations', 'users', 'public_signup', 'firebase_identities', 'reservations'):continue
            for row in rows.values():
                key = tx.key(table, row)
                old = tx.raw(table, key)
                if old is not None and old != row:raise ValueError('Synthetic restore auth conflict')
                tx.put(table, key, copy.deepcopy(row))
        tx.put('meta', 'session-count', {'value': 0})
        tx.put('meta', 'audit-sequence', {'value': max([row['sequence'] for row in
            backup['auth'].get('audit', {}).values()] or [0])})
        assert tx.raw('meta', 'credential-authority') == {'value': 'firebase-v1'}
    copied = 0
    for org, tables in backup['science'].items():
        tenant = app.state.scientific.for_organization(org)
        for table, rows in tables.items():
            for key, row in rows.items():
                ref = tenant.db.engine.ref(table, key)
                existing = ref.get()
                if existing.exists and existing.to_dict() != row:raise ValueError('Synthetic restore record conflict')
                if not existing.exists:ref.create(copy.deepcopy(row))
                copied += 1
                if interrupt_after == copied:raise InterruptedError('Synthetic interruption')
        for key, value in backup['objects'][org].items():
            target = tenant.media.prefix+key
            if target in transport.objects:
                if transport.objects[target] != value:raise ValueError('Synthetic restore object conflict')
            else:
                # Invoke the same create-only GCS SDK transport boundary.
                tenant.media.put(key, value)


def _firebase_client(app, uid, gateway):
    from tests.test_firebase_identity import provider, post
    from firebase_admin import auth
    account = auth.get_user(uid, app=gateway.app)
    proof = provider('signInWithPassword', dict(email=account.email,
        password='Synthetic restore passphrase 47!', returnSecureToken=True))
    client = TestClient(app, base_url='http://testserver')
    response = post(client, '/api/firebase/session', {'id_token': proof['idToken']})
    assert response.status_code == 200
    client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf_token']})
    return client


def replay_synthetic_rollback_delta(app, transport, baseline, current):
    """Local writer-fenced fixture replay with explicit old/new document hashes.

    This proves preservation under a stopped synthetic writer only. It does not
    implement a production writer fence or change-data-capture system.
    """
    assert app.state.service.db.prefix.startswith('firebase_test_restore_')
    journal = []
    for org, tables in current['science'].items():
        tenant = app.state.scientific.for_organization(org)
        for table, rows in tables.items():
            for key, row in rows.items():
                before = baseline['science'][org][table].get(key)
                if before == row:continue
                ref = tenant.db.engine.ref(table, key)
                actual = ref.get()
                if (actual.to_dict() if actual.exists else None) != before:
                    raise ValueError('Synthetic rollback baseline drift')
                journal.append(dict(org=org, table=table, id=key,
                    before_sha256=digest(before), after_sha256=digest(row)))
                if actual.exists:ref.set(copy.deepcopy(row))
                else:ref.create(copy.deepcopy(row))
        for key, value in current['objects'][org].items():
            target = tenant.media.prefix+key
            if target in transport.objects:
                assert transport.objects[target] == value
            else:tenant.media.put(key, value)
    return journal


def test_actual_emulator_backup_restore_and_rollback_preserve_synthetic_science(monkeypatch):
    assert os.getenv('FIRESTORE_EMULATOR_HOST') == '127.0.0.1:8791', 'Explicit local Firestore emulator required'
    assert os.getenv('FIREBASE_AUTH_EMULATOR_HOST') == '127.0.0.1:9099', 'Explicit local Auth emulator required'
    from src.firebase_preview.modes import DurableEmulatorConfig
    from src.firebase_preview.app import create_app
    from src.firebase_preview.identity import Gateway
    from src.webapp.settings import Settings
    from tests.test_preproduction_isolation import MemoryGCS
    from tests.test_firebase_identity import provider
    from tests.test_integrated_app import plant
    from tests.test_integrated_science_workflow import (_ok, _calibrate, _mock_segformer,
        _analyze, _editor_payload, _approve, _asset_hashes, _calibration_assets)
    from firebase_admin import auth

    base = ROOT/'.runtime/firebase'/('r'+uuid4().hex[:8]);base.mkdir(parents=True)
    transport = MemoryGCS()
    monkeypatch.setattr('google.cloud.storage.Client', lambda **kwargs: transport)
    gateway = Gateway()
    apps = []
    def make(label):
        prefix = 'firebase_test_restore_'+uuid4().hex
        config = DurableEmulatorConfig(base/label/'a', 'http://testserver', development=True,
            integrated=True, require_verified_email=True, firestore_project='hydroponic-leaf-research-aryae',
            firestore_prefix=prefix)
        settings = Settings(data_dir=base/label/'s', closed_academic_research=False,
            firestore_project=config.firestore_project, firestore_prefix=prefix+'_science',
            gcs_bucket='synthetic-preproduction-private-bucket', max_image_pixels=20000)
        app = create_app(config, gateway, settings.data_dir, scientific_settings=settings)
        apps.append(app)
        return app
    source, restored = make('src'), make('dst')
    identities = {}
    try:
        with source.state.service.db.transaction() as tx:
            for org in ('org-bau', 'org-b'):
                tx.execute('INSERT INTO organizations(id,name) VALUES(?,?)', (org, org))
        for org, role in [('org-bau', 'PLATFORM_OWNER'), ('org-b', 'MEMBER')]:
            identity = provider('signUp', dict(email='restore-'+uuid4().hex+'@example.test',
                password='Synthetic restore passphrase 47!', returnSecureToken=True))
            uid = identity['localId'];identities[org] = uid
            auth.update_user(uid, email_verified=True, app=gateway.app)
            user = 'synthetic-'+org
            with source.state.service.db.transaction() as tx:
                tx.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (user, user, 'FIREBASE_MANAGED', 'ACTIVE', role, org, org, '2026-10-28'))
                tx.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?', (identity['email'], user))
                tx.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?', ('2026-10-28', user))
                tx.execute('INSERT INTO firebase_identities VALUES(?,?)', (uid, user))
        client = _firebase_client(source, identities['org-bau'], gateway)
        old_cookie = client.cookies.get(source.state.config.cookie)
        profile = plant(client, 'SYNTHETIC RESTORE ONLY')
        cal1 = _ok(_calibrate(client, profile), 201)['calibration']
        _mock_segformer(source, monkeypatch)
        analysis = _analyze(client, profile, '2026-10-25')
        body, _ = _editor_payload(analysis)
        _ok(client.post('/api/analyses/'+analysis['id']+'/draft', json=body))
        measurement = _ok(_approve(client, analysis, body), 201)
        source_tenant = source.state.scientific.for_organization('org-bau')
        assert source_tenant.db.one(source_tenant.db.approved_masks, measurement['id'])['payload']['new_real_eligible'] is False
        assert source_tenant.db.rows(source_tenant.db.owner_notifications) == []
        cal2 = _ok(_calibrate(client, profile, 12), 201)['calibration']
        urls = dict(measurement['assets'], **{'c1_'+k:v for k,v in _calibration_assets(cal1).items()},
            **{'c2_'+k:v for k,v in _calibration_assets(cal2).items()})
        hashes = _asset_hashes(client, urls)
        history = _ok(client.get('/api/profiles/'+profile['id']+'/history'))
        csv_bytes = client.get('/api/profiles/'+profile['id']+'/export.csv').content
        backup = logical_backup(source, transport)
        output = os.getenv('HYDRO_RESTORE_EVIDENCE')
        destination = Path(output).resolve() if output else base/'evidence'
        project = ROOT.parents[1]
        assert destination.is_relative_to(project) and not destination.exists()
        destination.mkdir(parents=True)
        backup = private_backup_roundtrip(destination/'private-synthetic-backup', backup)
        # Provider state persists independently of the application backup.
        time.sleep(1.1)
        auth.revoke_refresh_tokens(identities['org-bau'], app=gateway.app)
        assert client.get('/api/profiles').status_code == 401
        # A deliberately interrupted copy resumes additively; no replacement writes.
        with pytest.raises(InterruptedError):restore_synthetic_backup(restored, transport, backup, interrupt_after=1)
        restore_synthetic_backup(restored, transport, backup)
        restore_synthetic_backup(restored, transport, backup)
        assert logical_backup(restored, transport) == backup
        unauth = TestClient(restored, base_url='http://testserver')
        unauth.cookies.set(restored.state.config.cookie, old_cookie)
        assert unauth.get('/api/profiles').status_code == 401
        recovered = _firebase_client(restored, identities['org-bau'], gateway)
        assert _ok(recovered.get('/api/profiles/'+profile['id']+'/history')) == history
        assert recovered.get('/api/profiles/'+profile['id']+'/export.csv').content == csv_bytes
        assert _asset_hashes(recovered, urls) == hashes
        assert _ok(recovered.get('/api/measurements/'+measurement['id'])) == measurement
        other = _firebase_client(restored, identities['org-b'], gateway)
        assert other.get('/api/profiles/'+profile['id']+'/history').status_code == 404
        assert all(other.get(url).status_code == 404 for url in urls.values())
        # Restore does not recover old sessions, including expired provider/backend sessions.
        with restored.state.service.db.transaction() as tx:
            from src.auth_preview.passwords import token_hash
            key = token_hash(recovered.cookies.get(restored.state.config.cookie))
            row = tx.raw('sessions', key);row['expires'] = 1;tx.put('sessions', key, row)
        assert recovered.get('/api/profiles').status_code == 401
        recovered = _firebase_client(restored, identities['org-bau'], gateway)
        _mock_segformer(restored, monkeypatch)
        newer = _analyze(recovered, profile, '2026-10-26')
        newer_body, _ = _editor_payload(newer)
        new_measurement = _ok(_approve(recovered, newer, newer_body), 201)
        rollback_delta = logical_backup(restored, transport)
        rollback = make('rollback')
        restore_synthetic_backup(rollback, transport, backup)
        # Explicitly journalled new science is retained and create-only replayed.
        journal = replay_synthetic_rollback_delta(rollback, transport, backup, rollback_delta)
        assert journal
        rollback_client = _firebase_client(rollback, identities['org-bau'], gateway)
        assert rollback_client.get('/api/measurements/'+new_measurement['id']).json() == new_measurement
        assert rollback_client.get('/api/measurements/'+measurement['id']).json() == measurement
        assert _asset_hashes(rollback_client, urls) == hashes
        assert rollback_client.get('/api/profiles/'+profile['id']+'/export.csv').content == recovered.get('/api/profiles/'+profile['id']+'/export.csv').content
        assert _ok(rollback_client.get('/api/profiles/'+profile['id']+'/history')) == _ok(recovered.get('/api/profiles/'+profile['id']+'/history'))
        assert logical_backup(rollback, transport)['science'] == rollback_delta['science']
        rollback_tenant = rollback.state.scientific.for_organization('org-bau')
        assert rollback_tenant.db.one(rollback_tenant.db.approved_masks, new_measurement['id'])['payload']['new_real_eligible'] is False
        assert rollback_tenant.db.rows(rollback_tenant.db.owner_notifications) == []
        # Drift is never resolved by overwrite/deduplication.
        bad = copy.deepcopy(backup)
        bad['science']['org-bau']['measurements'][measurement['id']]['payload_json'] = '{}'
        with pytest.raises(ValueError, match='record conflict'):restore_synthetic_backup(rollback, transport, bad)
        evidence = dict(status='PASS_LOCAL_SYNTHETIC_ONLY', storage='actual_firestore_and_auth_emulators',
            gcs='SDK_TRANSPORT_DOUBLE_NO_BUCKET_IAM_PROOF', inference='EXPLICIT_SYNTHETIC_DOUBLE',
            source_prefix=source.state.config.firestore_prefix, restored_prefix=restored.state.config.firestore_prefix,
            rollback_prefix=rollback.state.config.firestore_prefix, approved_before=1, approved_after_rollback=2,
            history_csv_assets_calibration_membership_uid_exact=True, interrupted_retry_idempotent=True,
            old_sessions_restored=False, expired_session_denied=True, provider_revoked_session_denied=True,
            fresh_official_provider_signin_required=True, cross_tenant_denied=True,
            rollback_new_write_preserved=True, production_calls=False, production_writes=False,
            rollback_journal=journal, writer_fence='LOCAL_SYNTHETIC_WRITER_STOPPED_ONLY',
            backup_auth_sha256=digest(backup['auth']), backup_science_sha256=digest(backup['science']),
            asset_count=sum(len(x) for x in backup['objects'].values()),
            asset_hashes={org:{key:hashlib.sha256(value).hexdigest() for key,value in values.items()}
                for org,values in backup['objects'].items()}, local_fixture_directory=str(base))
        (destination/'summary.json').write_text(json.dumps(evidence, indent=2)+'\n')
    finally:
        for app in apps:
            app.state.scientific.close();app.state.service.db.close()
        gateway.close()
