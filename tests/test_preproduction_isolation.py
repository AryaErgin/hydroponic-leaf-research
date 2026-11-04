"""Preproduction acceptance, using synthetic records and local emulators only.

The Firestore cases execute the real SDK against an explicit loopback emulator.
GCS uses an in-memory transport double: these cases prove application namespaces,
create-only/hash contracts and HTTP authorization, NOT bucket IAM authorization.
Only the complete workflow loads the actual unchanged promoted model; the route
matrix uses an explicitly labelled inference fixture to keep security tests fast.
"""
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.auth_preview.config import Config
from src.auth_preview.passwords import hash_password
from src.hydroponic.config import ROOT
from src.integrated.app import create_app
from src.webapp.settings import Settings
from tests.test_integrated_app import PASSWORD, image, login, plant
from tests.test_integrated_science_workflow import (
    _approve, _asset_hashes, _calibrate, _calibration_assets, _mock_segformer, _ok,
)
from tests.test_webapp import KEYS


class MemoryGCS:
    """Deliberate SDK transport double; never creates a real storage client."""
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.writes = []

    def bucket(self, name):
        assert name == 'synthetic-preproduction-private-bucket'
        return self

    def blob(self, key):
        owner = self

        class Blob:
            metadata = None

            def upload_from_string(self, data, *, content_type, if_generation_match, timeout):
                assert if_generation_match == 0 and timeout == 60
                if key in owner.objects:
                    raise FileExistsError(key)
                owner.objects[key] = bytes(data)
                owner.writes.append((key, dict(self.metadata), content_type))

            def download_as_bytes(self, *, timeout):
                assert timeout == 60
                owner.reads.append(key)
                return owner.objects[key]

        return Blob()


@pytest.fixture(params=['sqlite-local', 'firestore-gcs-double'])
def acceptance_app(request, monkeypatch):
    cloud = request.param.startswith('firestore')
    if cloud:
        endpoint = os.getenv('FIRESTORE_EMULATOR_HOST')
        if not endpoint:
            pytest.skip('Explicit local FIRESTORE_EMULATOR_HOST required; no production fallback')
        assert urlsplit('http://' + endpoint).hostname in ('localhost', '127.0.0.1', '::1')
    parent = ROOT / '.runtime' / 'preproduction-isolation'
    parent.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(dir=parent))
    prefix = 'preprod_' + uuid4().hex
    options = dict(firestore_project='hydroponic-leaf-research-aryae',
                   firestore_prefix=prefix) if cloud else {}
    transport = MemoryGCS()
    if cloud:
        monkeypatch.setattr('google.cloud.storage.Client', lambda **kwargs: transport)
    config = Config(base / 'auth', 'http://testserver', development=True,
                    integrated=True, require_verified_email=True,
                    email_hmac_key=b'synthetic-preproduction-only-32-bytes', **options)
    settings = Settings(data_dir=base / 'science', closed_academic_research=False,
                        max_image_pixels=20000,
                        gcs_bucket='synthetic-preproduction-private-bucket' if cloud else None,
                        **options)
    app = create_app(config, settings)
    app.state.preproduction_backend = request.param
    app.state.preproduction_transport = transport
    encoded = hash_password(PASSWORD)
    with app.state.service.db.transaction() as db:
        for org in ('org-bau', 'org-b'):
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)', (org, org))
            for role in ('MEMBER', 'ORG_ADMIN', 'PLATFORM_OWNER'):
                uid = org + '-' + role
                db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (uid, uid, encoded, 'ACTIVE', role, org, org, '2026-10-28'))
                db.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?',
                           (uid.lower() + '@example.test', uid))
                db.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?',
                           ('2026-10-28', uid))
        db.execute('INSERT INTO public_signup VALUES(1,?)', ('org-bau',))
    try:
        yield app
    finally:
        if cloud:
            # Delete only this fixture's unique, known emulator collections.
            for service in app.state.scientific.services.values():
                engine = service.db.engine
                for table in engine.tables:
                    for row in engine.client.collection(engine.prefix + '_' + table).stream():
                        row.reference.delete()
            for collection in app.state.service.db.collections():
                for row in collection.stream():
                    row.reference.delete()
        app.state.scientific.close()
        if cloud:
            app.state.service.db.close()
        resolved = base.resolve()
        assert resolved.is_relative_to(parent.resolve()) and resolved != parent.resolve()
        shutil.rmtree('\\\\?\\' + str(resolved) if os.name == 'nt' else resolved)


def upload(client, profile, date='2026-10-26'):
    return _ok(client.post('/api/uploads', data={
        'profile_id': profile['id'], 'date': date, 'geometry_unchanged': 'true',
        'request_id': str(uuid4()),
    }, files={'image': ('synthetic-preproduction.png', image(), 'image/png')}), 201)


def corrected_payload(analysis):
    """Explicit synthetic editor correction; no model accuracy claim is made."""
    labels = np.zeros((100, 120), dtype='<u2')
    metadata = copy.deepcopy(analysis['instance_metadata'])
    first = max([r['label_id'] for r in metadata['instances']] + [0]) + 1
    labels[20:70, 20:55] = first
    labels[20:70, 55:90] = first + 1
    metadata['instances'].extend(dict(label_id=n,
        internal_instance_id='synthetic-preprod-' + str(n)) for n in (first, first + 1))
    metadata.update(display_order=[first + 1, first], next_label_id=first + 2)
    return dict(width=120, height=100,
        mask_base64=base64.b64encode((labels > 0).astype(np.uint8).tobytes()).decode(),
        instance_base64=base64.b64encode(labels.tobytes()).decode(),
        instance_analysis_id=analysis['id'], instance_metadata=metadata,
        calibration_revision=analysis.get('calibration_revision', 0)), labels


def real_workflow(app, client):
    profile = plant(client, 'Synthetic preproduction actual model')
    first_cal = _ok(_calibrate(client, profile), 201)['calibration']
    service = app.state.scientific.for_organization('org-bau')
    champion = service.models.champion()
    assert champion['version'] == 'exp006-segformer-001'
    before = {name: hashlib.sha256((service.models.root / ref['path']).read_bytes()).hexdigest()
              for name, ref in champion['artifacts'].items()}
    pending = upload(client, profile)
    analysis = _ok(client.post('/api/analyses/' + pending['id'] + '/analyze'))
    assert analysis['candidate_model']['version'] == champion['version']
    assert analysis['candidate_model']['fallback_reason'] is None
    assert analysis['pipeline']['checkpoint_loaded'] is True
    assert analysis['pipeline']['checkpoint_sha256'] == champion['artifacts']['checkpoint']['sha256']
    assert analysis['human_approval_required'] is True
    assert analysis['measurement_status'] == 'UNREVIEWED_CANDIDATE'
    raw_mask = np.frombuffer(base64.b64decode(analysis['mask_base64']), dtype=np.uint8)
    assert len(raw_mask) == 12000 and np.isin(raw_mask, [0, 1]).all()
    assert _ok(client.get('/api/profiles/' + profile['id'] + '/history'))['measurements'] == []
    body, labels = corrected_payload(analysis)
    draft = _ok(client.post('/api/analyses/' + analysis['id'] + '/draft', json=body))
    assert draft['human_approval_required'] is True
    assert draft['per_leaf_display_numbers'] == [1, 2]
    # This approval concerns only a conspicuous generated rectangle fixture.
    measurement = _ok(_approve(client, analysis, body), 201)
    assert measurement['pixel_area'] == 3500
    assert measurement['leaf_count'] == 2
    assert measurement['total_projected_area_cm2'] == pytest.approx(35)
    assert measurement['per_leaf_pixel_area'] == [1750, 1750]
    assert sum(measurement['per_leaf_pixel_area']) == int((labels > 0).sum())
    canonical = service.db.one(service.db.approved_masks, measurement['id'])['payload']
    assert canonical['new_real_eligible'] is False
    assert service.db.rows(service.db.owner_notifications) == []
    hashes = _asset_hashes(client, measurement['assets'])
    hashes.update(_asset_hashes(client, _calibration_assets(first_cal)))
    second_cal = _ok(_calibrate(client, profile, 12), 201)['calibration']
    assert second_cal['id'] != first_cal['id']
    assert second_cal['cm_per_pixel'] == pytest.approx(.15)
    assert _ok(client.get('/api/measurements/' + measurement['id'])) == measurement
    assert _asset_hashes(client, {str(n): url for n, url in enumerate(hashes)}) == hashes
    history = _ok(client.get('/api/profiles/' + profile['id'] + '/history'))
    assert history['measurements'] == [measurement]
    assert history['growth']['current_area_cm2'] == pytest.approx(35)
    rows = list(csv.DictReader(io.StringIO(client.get('/api/profiles/' + profile['id'] + '/export.csv').text)))
    assert len(rows) == 1 and rows[0]['captured_at'] == '2026-10-26'
    assert float(rows[0]['total_projected_area_cm2']) == pytest.approx(35)
    assert before == {name: hashlib.sha256((service.models.root / ref['path']).read_bytes()).hexdigest()
                      for name, ref in champion['artifacts'].items()}
    return profile, measurement, history, hashes


def test_actual_promoted_model_complete_workflow_new_app_and_durable_reopen(acceptance_app):
    app = acceptance_app
    client = login(app)
    profile, measurement, history, hashes = real_workflow(app, client)
    # New FastAPI + auth + tenant service objects, sharing only persisted fixtures.
    recreated = create_app(app.state.config, app.state.scientific.settings)
    try:
        fresh = login(recreated)
        assert _ok(fresh.get('/api/profiles/' + profile['id'] + '/history')) == history
        assert _ok(fresh.get('/api/measurements/' + measurement['id'])) == measurement
        assert _asset_hashes(fresh, {str(n): url for n, url in enumerate(hashes)}) == hashes
        assert fresh.get('/api/profiles/' + profile['id'] + '/export.csv').status_code == 200
    finally:
        recreated.state.scientific.close()
        if recreated.state.config.firestore_project:
            recreated.state.service.db.close()


def protected_calls(profile, calibration, analysis, measurement, body, assets):
    """Every resource-bearing scientific route, with valid request shapes."""
    pid, aid = profile['id'], analysis['id']
    return [
        ('GET', '/api/profiles/' + pid + '/analyses', {}),
        ('GET', '/api/profiles/' + pid + '/history', {}),  # graph data
        ('GET', '/api/profiles/' + pid + '/export.csv', {}),
        ('GET', '/api/analyses/' + aid, {}),
        ('GET', '/api/measurements/' + measurement['id'], {}),
        ('PUT', '/api/plants/' + pid, {'json': {'name': 'Forged plant', 'species': 'lettuce'}}),
        ('PUT', '/api/plants/' + pid + '/calibration', {'json': {'calibration_id': calibration['id']}}),
        ('PUT', '/api/profiles/' + pid + '/calibration', {'json': {'calibration_id': calibration['id']}}),
        ('POST', '/api/plants/' + pid + '/calibration/two-point', {'data': {
            'diameter_cm': '8', 'points': '[[20,80],[100,80]]', 'reviewed': 'true'},
            'files': {'image': ('synthetic.png', image(), 'image/png')}}),
        ('POST', '/api/uploads', {'data': {'profile_id': pid, 'date': '2026-10-27'},
            'files': {'image': ('synthetic.png', image(), 'image/png')}}),
        ('POST', '/api/analyses/' + aid + '/analyze', {}),
        ('POST', '/api/analyses/' + aid + '/draft', {'json': body}),
        ('POST', '/api/analyses/' + aid + '/split-leaf', {'json': body}),
        ('POST', '/api/analyses/' + aid + '/confirm-calibration', {'json': {'confirmed': True}}),
        ('POST', '/api/analyses/' + aid + '/calibration', {'json': {'calibration_id': calibration['id']}}),
        ('POST', '/api/analyses/' + aid + '/geometry-changed', {}),
        ('POST', '/api/analyses/' + aid + '/approve', {'json': {**body,
            'approval': 'APPROVE', 'instances_reviewed': True, 'review': dict.fromkeys(KEYS, True)}}),
    ] + [('GET', url, {}) for url in sorted(assets)]


def test_cross_tenant_all_scientific_routes_inactive_gates_and_no_storage_reads(acceptance_app, monkeypatch):
    app = acceptance_app
    member = login(app)
    profile = plant(member)
    calibration = _ok(_calibrate(member, profile), 201)['calibration']
    _mock_segformer(app, monkeypatch)
    analysis = _ok(member.post('/api/analyses/' + upload(member, profile)['id'] + '/analyze'))
    body, _ = corrected_payload(analysis)
    draft = _ok(member.post('/api/analyses/' + analysis['id'] + '/draft', json=body))
    measurement = _ok(_approve(member, analysis, body), 201)
    assets = set(analysis['assets'].values()) | set(draft['assets'].values()) | set(measurement['assets'].values()) | set(_calibration_assets(calibration).values())
    calls = protected_calls(profile, calibration, analysis, measurement, body, assets)
    current = _ok(member.post('/api/analyses/' + upload(member, profile, '2026-10-27')['id'] + '/analyze'))
    current_body, _ = corrected_payload(current)
    current_draft = _ok(member.post('/api/analyses/' + current['id'] + '/draft', json=current_body))
    calls += protected_calls(profile, calibration, current, measurement, current_body,
                             set(current_draft['assets'].values()))
    service = app.state.scientific.for_organization('org-bau')
    tables = ('calibrations', 'profiles', 'analyses', 'review_sessions', 'measurements',
              'approved_masks', 'approved_sorrel_masks', 'owner_notifications', 'owner_training_snapshots')
    records = {name: copy.deepcopy(service.db.rows(getattr(service.db, name))) for name in tables}
    snapshot = copy.deepcopy(app.state.preproduction_transport.objects)
    for role in ('MEMBER', 'ORG_ADMIN', 'PLATFORM_OWNER'):
        other = login(app, 'org-b', role)
        assert _ok(other.get('/api/profiles')) == []
        assert _ok(other.get('/api/calibrations')) == []
        reads = len(app.state.preproduction_transport.reads)
        for method, url, kwargs in calls:
            response = other.request(method, url, **kwargs)
            assert response.status_code in (404, 409), (role, method, url, response.text)
        for claims in ({'params': {'organization_id': 'org-bau'}},
                       {'params': {'org_id': 'org-bau'}},
                       {'headers': {'X-Organization-ID': 'org-bau'}},
                       {'headers': {'X-Org-ID': 'org-bau'}}):
            assert other.get('/api/profiles', **claims).status_code == 403
        assert len(app.state.preproduction_transport.reads) == reads
    other = login(app, 'org-b')
    other_profile = plant(other, 'UniversityB separate plant')
    assert other.put('/api/plants/' + other_profile['id'] + '/calibration',
                     json={'calibration_id': calibration['id']}).status_code == 404
    # Existing valid sessions immediately lose every scientific operation.
    for status in ('PENDING', 'SUSPENDED'):
        with app.state.service.db.transaction() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', ('org-bau-MEMBER',)).fetchone()
            db.execute('UPDATE users SET status=?,role=?,org_id=?,version=version+1 WHERE id=? AND version=?',
                       (status, row['role'], row['org_id'], row['id'], row['version']))
        for method, url, kwargs in calls:
            assert member.request(method, url, **kwargs).status_code == 403, (status, method, url)
        for url in ('/api/config', '/api/models', '/api/calibrations', '/api/profiles', '/api/setup'):
            assert member.get(url).status_code == 403, (status, url)
        assert member.post('/api/images/prepare', files={'image': ('synthetic.png', image(), 'image/png')}).status_code == 403
        for url in ('/api/plants', '/api/profiles', '/api/calibrations/existing',
                    '/api/calibrations/import', '/api/calibrations/two-point'):
            assert member.post(url, json={}).status_code == 403, (status, url)
    assert app.state.preproduction_transport.objects == snapshot
    assert {name: service.db.rows(getattr(service.db, name)) for name in tables} == records


@pytest.mark.parametrize('role', ['MEMBER', 'ORG_ADMIN'])
def test_role_tampering_self_promotion_and_unmounted_training_routes(acceptance_app, role):
    app = acceptance_app
    client = login(app, role=role)
    uid = 'org-bau-' + role
    for requested in ('ORG_ADMIN', 'PLATFORM_ADMIN', 'PLATFORM_OWNER'):
        response = client.post('/api/admin/users/' + uid + '/role',
                               json={'role': requested, 'version': 1})
        assert response.status_code in (403, 422), response.text
    assert client.post('/api/admin/users/org-b-ORG_ADMIN/decision',
        json={'action': 'approve', 'version': 1, 'verified_org_id': 'org-bau'}).status_code == 403
    with app.state.service.db.transaction() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        assert row['role'] == role and row['org_id'] == 'org-bau'
    # Integrated application intentionally has no training or legacy artifact API.
    # These are absence checks, not a claim that an unimplemented API was audited.
    absent = ['/api/workspace/' + kind for kind in (
        'plants', 'photos', 'calibrations', 'analyses', 'reviews', 'measurements',
        'masks', 'instances', 'growth', 'scientific_artifacts', 'training_annotations',
        'background_annotations')] + [
        '/api/workspace/export.csv', '/api/artifacts/bau-masks',
        '/api/training/annotations', '/api/training/background', '/api/training/export',
        '/api/training/artifacts/bau', '/api/annotations', '/api/background-annotations',
    ]
    for url in absent:
        assert client.get(url).status_code == 404, url
        assert client.post(url, json={'role': 'PLATFORM_OWNER', 'organization_id': 'org-bau'}).status_code == 404, url


def test_media_identical_keys_are_scoped_create_only_and_hash_checked(acceptance_app):
    app = acceptance_app
    bau = app.state.scientific.for_organization('org-bau')
    other = app.state.scientific.for_organization('org-b')
    a = bau.media.put('same/image.png', b'BAU synthetic', 'image/png')
    b = other.media.put('same/image.png', b'UniversityB synthetic', 'image/png')
    assert bau.media.prefix != other.media.prefix
    assert bau.settings.firestore_prefix != other.settings.firestore_prefix
    assert bau.media.read(a) == b'BAU synthetic'
    assert other.media.read(b) == b'UniversityB synthetic'
    with pytest.raises(FileExistsError):
        bau.media.put('same/image.png', b'overwrite', 'image/png')
    if bau.media.bucket:
        app.state.preproduction_transport.objects[bau.media.prefix + a['key']] = b'tampered'
    else:
        bau.media._path(a['key']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        bau.media.read(a)


def test_firebase_emulator_actual_model_active_workflow_and_pending_suspended_gates(site):
    """Real Firebase SDK emulator verification + real scientific inference, local SQL."""
    from tests.test_firebase_identity import activate, post, signup
    app, client, gateway = site
    identity, user = signup(client)
    assert client.get('/api/profiles').status_code == 403
    activate(app, client, identity, user)
    client.headers.update({'Origin': 'http://testserver',
                          'X-CSRF-Token': client.get('/api/session').json()['csrf_token']})
    profile, measurement, history, hashes = real_workflow(app, client)
    assert post(client, '/api/admin/users/' + user['id'] + '/role',
                {'role': 'PLATFORM_ADMIN', 'version': 1}).status_code == 403
    for state in ('PENDING', 'SUSPENDED'):
        with app.state.service.db.transaction() as db:
            db.execute('UPDATE users SET status=? WHERE id=?', (state, user['id']))
        for url in ('/api/profiles', '/api/profiles/' + profile['id'] + '/history',
                    '/api/profiles/' + profile['id'] + '/export.csv',
                    '/api/measurements/' + measurement['id'], *hashes):
            assert client.get(url).status_code == 403, (state, url)


# Importing this existing fixture does not start or configure any cloud service.
from tests.test_firebase_identity import site
