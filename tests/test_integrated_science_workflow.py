"""Synthetic HTTP workflow only: no real photographs, model weights, or approvals.

Only SegFormer inference and its model-registry record are fixture doubles. The
scoped application, calibration, lamina proposal, identity validation, approval,
assets, history and CSV implementations execute normally.
"""
import base64
import copy
import csv
import hashlib
import io
import json
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from tests.test_integrated_app import application, image, login, plant
from tests.test_webapp import KEYS


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _calibrate(client, profile, diameter=8, **extra):
    return client.post(
        '/api/plants/' + profile['id'] + '/calibration/two-point',
        data={'diameter_cm': str(diameter), 'points': '[[20,80],[100,80]]',
              'reviewed': 'true', **extra},
        files={'image': ('synthetic-fixture.png', image(), 'image/png')},
    )


def _mock_segformer(application, monkeypatch):
    """Mock only the inference boundary, with conspicuous fixture provenance."""
    from src.training import inference
    service = application.state.scientific.for_organization('org-bau')
    record = {
        'version': 'synthetic-unit-fixture-segformer', 'architecture': 'segformer',
        'artifacts': {'checkpoint': {'sha256': '0' * 64}},
    }
    monkeypatch.setattr(service.models, 'champion', lambda: record)
    calls = []

    def predict(rgb, version=None, registry=None, device=None):
        assert version == record['version'] and registry is service.models
        assert device == 'cpu' and rgb.shape == (100, 120, 3)
        calls.append(version)
        mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
        mask[20:70, 20:90] = 1
        return {'mask': mask, 'metadata': {
            'fixture_kind': 'SYNTHETIC_MOCK_NO_MODEL_WEIGHTS_LOADED',
            'threshold': .5, 'semantic_accuracy_established': False,
        }}

    monkeypatch.setattr(inference, 'predict_version', predict)
    return calls


def _analyze(client, profile, date, request_id=None):
    upload = _ok(client.post(
        '/api/uploads',
        data={'profile_id': profile['id'], 'date': date,
              'geometry_unchanged': 'true', 'request_id': request_id or str(uuid4())},
        files={'image': ('synthetic-fixture.png', image(), 'image/png')},
    ), 201)
    result = _ok(client.post('/api/analyses/' + upload['id'] + '/analyze'))
    assert result['pipeline']['fixture_kind'] == 'SYNTHETIC_MOCK_NO_MODEL_WEIGHTS_LOADED'
    assert result['candidate_model']['architecture'] == 'segformer'
    assert result['human_approval_required'] is True
    assert result['measurement_status'] == 'UNREVIEWED_CANDIDATE'
    return result


def _editor_payload(analysis):
    """Three reviewed leaves with gapped native labels and compact display order."""
    mask = np.frombuffer(base64.b64decode(analysis['mask_base64']), dtype=np.uint8).reshape(100, 120)
    labels = np.zeros(mask.shape, dtype='<u2')
    columns = np.indices(mask.shape)[1]
    labels[(mask > 0) & (columns < 40)] = 2
    labels[(mask > 0) & (columns >= 40) & (columns < 60)] = 8
    labels[(mask > 0) & (columns >= 60)] = 11
    metadata = copy.deepcopy(analysis['instance_metadata'])
    metadata['instances'].extend({'label_id': label, 'internal_instance_id':
        f"synthetic-{analysis['id']}-{label}"} for label in (2, 8, 11))
    metadata.update(display_order=[2, 11, 8], next_label_id=12)
    body = dict(width=120, height=100, mask_base64=analysis['mask_base64'],
        instance_base64=base64.b64encode(labels.tobytes()).decode(),
        instance_analysis_id=analysis['id'], instance_metadata=metadata,
        calibration_revision=analysis.get('calibration_revision', 0))
    return body, labels


def _approve(client, analysis, body):
    return client.post('/api/analyses/' + analysis['id'] + '/approve', json={
        **body, 'approval': 'APPROVE', 'instances_reviewed': True,
        'review': dict.fromkeys(KEYS, True),
    })


def _asset_hashes(client, assets):
    result = {}
    for name, url in assets.items():
        response = client.get(url)
        assert response.status_code == 200, (name, url, response.text)
        result[url] = hashlib.sha256(response.content).hexdigest()
    return result


def _calibration_assets(calibration):
    return {name: '/api/assets/calibration/' + calibration['id'] + '/' + name
            for name in calibration['assets']}


def test_synthetic_science_workflow_capture_history_reopen_and_tenant_isolation(application, monkeypatch):
    member = login(application)
    other_org = login(application, 'org-b')
    profile = plant(member, 'Synthetic main plant')
    other_plant = plant(member, 'Synthetic separate plant')
    first_cal = _ok(_calibrate(member, profile), 201)['calibration']
    assert first_cal['cm_per_pixel'] == pytest.approx(.1)
    inference_calls = _mock_segformer(application, monkeypatch)

    request_id = str(uuid4())
    first = _analyze(member, profile, '2026-10-25', request_id)
    assert _ok(member.get('/api/profiles/' + profile['id'] + '/history'))['measurements'] == []
    assert _ok(member.get('/api/profiles/' + profile['id'] + '/analyses'))[0]['id'] == first['id']
    # The editor cannot relabel an analysis as a different plant's calibration.
    assert _calibrate(member, other_plant, analysis_id=first['id'],
                      image_sha256=first['image_sha256']).status_code in (400, 409)
    assert member.post('/api/uploads', data={'profile_id': other_plant['id'],
        'date': '2026-10-25', 'request_id': request_id},
        files={'image': ('synthetic-fixture.png', image(), 'image/png')}).status_code == 409

    body, labels = _editor_payload(first)
    drafted = _ok(member.post('/api/analyses/' + first['id'] + '/draft', json=body))
    assert drafted['status'] == 'DRAFT' and drafted['human_approval_required'] is True
    assert drafted['per_leaf_display_numbers'] == [1, 2, 3]
    assert [leaf['leaf_id'] for leaf in drafted['leaves']] == [2, 11, 8]
    assert drafted['unassigned_pixel_area'] == 0
    drafted_mask = np.frombuffer(base64.b64decode(drafted['mask_base64']), dtype=np.uint8).reshape(100, 120)
    np.testing.assert_array_equal(labels > 0, drafted_mask > 0)
    assert _ok(member.get('/api/profiles/' + profile['id'] + '/history'))['measurements'] == []

    first_measurement = _ok(_approve(member, first, body), 201)
    tenant = application.state.scientific.for_organization('org-bau')
    canonical = tenant.db.one(tenant.db.approved_masks, first_measurement['id'])['payload']
    assert canonical['new_real_eligible'] is False, 'Synthetic test captures must not become training-eligible'
    assert tenant.db.rows(tenant.db.owner_notifications) == []
    areas = [int(np.count_nonzero(labels == value)) for value in (2, 11, 8)]
    assert first_measurement['per_leaf_pixel_area'] == areas
    assert first_measurement['leaf_count'] == 3
    assert first_measurement['median_leaf_pixel_area'] == float(np.median(areas))
    assert first_measurement['median_leaf_area_cm2'] == pytest.approx(np.median(areas) * .01)
    assert first_measurement['total_projected_area_cm2'] == pytest.approx(sum(areas) * .01)
    assert sum(first_measurement['per_leaf_pixel_area']) == first_measurement['pixel_area']
    assert first_measurement['per_leaf_display_numbers'] == [1, 2, 3]
    assert first_measurement['per_leaf_internal_instance_ids'] == drafted['per_leaf_internal_instance_ids']
    assert first_measurement['captured_at'] == '2026-10-25'
    assert first_measurement['approved_at'] != first_measurement['captured_at']
    assert _ok(member.get('/api/profiles/' + profile['id'] + '/analyses')) == []
    assert _ok(member.get('/api/profiles/' + other_plant['id'] + '/analyses')) == []
    assert _ok(member.get('/api/profiles/' + other_plant['id'] + '/history'))['measurements'] == []
    with Image.open(io.BytesIO(member.get(first_measurement['assets']['leaf_instances']).content)) as saved:
        np.testing.assert_array_equal(np.asarray(saved), labels)
    immutable_assets = _asset_hashes(member, first['assets'])
    immutable_assets.update(_asset_hashes(member, drafted['assets']))
    immutable_assets.update(_asset_hashes(member, first_measurement['assets']))
    immutable_assets.update(_asset_hashes(member, _calibration_assets(first_cal)))

    # A new physical diameter applies prospectively, preserving approved history.
    second_cal = _ok(_calibrate(member, profile, 12), 201)['calibration']
    assert second_cal['id'] != first_cal['id']
    assert second_cal['cm_per_pixel'] == pytest.approx(.15)
    assert _ok(member.get('/api/measurements/' + first_measurement['id'])) == first_measurement
    assert _asset_hashes(member, {str(n): url for n, url in enumerate(immutable_assets)}) == immutable_assets
    second = _analyze(member, profile, '2026-10-20')
    assert second['calibration']['id'] == second_cal['id']
    second_body, _ = _editor_payload(second)
    second_measurement = _ok(_approve(member, second, second_body), 201)
    assert second_measurement['cm_per_pixel'] == pytest.approx(.15)
    assert second_measurement['total_projected_area_cm2'] == pytest.approx(sum(areas) * .0225)
    assert len(inference_calls) == 2

    # Approval order is reversed from capture order; graph input and CSV use capture.
    history = _ok(member.get('/api/profiles/' + profile['id'] + '/history'))
    assert [m['id'] for m in history['measurements']] == [second_measurement['id'], first_measurement['id']]
    assert [m['captured_at'] for m in history['measurements']] == ['2026-10-20', '2026-10-25']
    assert first_measurement['approved_at'] <= second_measurement['approved_at']
    assert history['growth']['reason'] == 'calibration_changed'
    assert history['growth']['daily_growth_cm2'] is None
    for measurement in history['measurements']:
        assert measurement['leaf_count'] == 3
        assert all(measurement[key] is not None for key in
            ('total_leaf_area', 'mean_leaf_area', 'median_leaf_area', 'median_leaf_area_cm2'))
    rows = list(csv.DictReader(io.StringIO(member.get('/api/profiles/' + profile['id'] + '/export.csv').text)))
    assert [row['captured_at'] for row in rows] == ['2026-10-20', '2026-10-25']
    assert json.loads(rows[1]['per_leaf_internal_instance_ids']) == first_measurement['per_leaf_internal_instance_ids']
    assert json.loads(rows[1]['per_leaf_display_numbers']) == [1, 2, 3]
    assert float(rows[1]['median_leaf_area_cm2']) == pytest.approx(first_measurement['median_leaf_area_cm2'])

    all_assets = (set(immutable_assets) | set(second['assets'].values()) |
                 set(second_measurement['assets'].values()) | set(_calibration_assets(second_cal).values()))
    for url in all_assets:
        assert other_org.get(url).status_code == 404, url
    for url in ('/api/analyses/' + first['id'], '/api/analyses/' + second['id'],
                '/api/measurements/' + first_measurement['id'],
                '/api/profiles/' + profile['id'] + '/history',
                '/api/profiles/' + profile['id'] + '/analyses',
                '/api/profiles/' + profile['id'] + '/export.csv'):
        assert other_org.get(url).status_code == 404, url
    # The scientific draft lock returns the same conflict for unknown/final IDs.
    assert other_org.post('/api/analyses/' + first['id'] + '/draft', json=body).status_code == 409
    assert _ok(other_org.get('/api/profiles')) == []

    assert member.post('/api/logout', json={}).status_code == 200
    assert member.get('/api/measurements/' + first_measurement['id']).status_code == 401
    # Recreate the tenant service so reads also prove on-disk persistence.
    old_service = application.state.scientific.services.pop('org-bau')
    old_service.db.engine.dispose()
    reopened = login(application)
    assert _ok(reopened.get('/api/profiles/' + profile['id'] + '/history')) == history
    assert _ok(reopened.get('/api/analyses/' + first['id']))['per_leaf_internal_instance_ids'] == first_measurement['per_leaf_internal_instance_ids']
    assert _asset_hashes(reopened, {str(n): url for n, url in enumerate(immutable_assets)}) == immutable_assets
    assert reopened.post('/api/analyses/' + first['id'] + '/draft', json=body).status_code == 409


def test_integrated_approval_retains_native_instance_and_explicit_review_guards(application, monkeypatch):
    client = login(application)
    profile = plant(client, 'Synthetic approval guard plant')
    _ok(_calibrate(client, profile), 201)
    _mock_segformer(application, monkeypatch)
    analysis = _analyze(client, profile, '2026-10-21')
    body, labels = _editor_payload(analysis)
    assert labels[20, 20] > 0 and labels[0, 0] == 0
    invalid = []
    unassigned = labels.copy(); unassigned[20, 20] = 0
    outside = labels.copy(); outside[0, 0] = 2
    invalid.extend([
        {'instance_base64': base64.b64encode(unassigned.tobytes()).decode()},
        {'instance_base64': base64.b64encode(outside.tobytes()).decode()},
        {'instance_analysis_id': 'different-synthetic-analysis'},
        {'review': dict.fromkeys(KEYS, 'true')},
        {'instances_reviewed': False}, {'approval': ''},
        {'calibration_revision': analysis.get('calibration_revision', 0) + 1},
    ])
    complete = {**body, 'approval': 'APPROVE', 'instances_reviewed': True,
                'review': dict.fromkeys(KEYS, True)}
    for changes in invalid:
        response = client.post('/api/analyses/' + analysis['id'] + '/approve', json={**complete, **changes})
        assert response.status_code in (400, 409), (changes, response.text)
        assert _ok(client.get('/api/profiles/' + profile['id'] + '/history'))['measurements'] == []
    assert _ok(client.get('/api/analyses/' + analysis['id']))['human_approval_required'] is True
    assert _ok(client.get('/api/profiles/' + profile['id'] + '/analyses'))[0]['id'] == analysis['id']
