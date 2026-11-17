"""Synthetic capture-date corrections preserve scientific artifacts and identity."""
import copy
import hashlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from sqlalchemy import update

from tests import test_webapp as web
from tests.test_integrated_app import application, image, login, plant


class TestCaptureDate(unittest.TestCase):
    setUp = web.TestWebMVP.setUp
    profile = web.TestWebMVP.profile
    upload = web.TestWebMVP.upload
    analyze = web.TestWebMVP.analyze
    approve = web.TestWebMVP.approve

    def change(self, analysis, date, expected_date=None):
        return self.client.post('/api/analyses/' + analysis['id'] + '/capture-date',
            json={'date': date, 'expected_date': expected_date or analysis['date']})

    def stored(self, analysis):
        service = self.app.state.service
        return copy.deepcopy(service.db.one(service.db.analyses, analysis['id']))

    def asset_hashes(self, row):
        service = self.app.state.service
        return {key: hashlib.sha256(service.media.read(ref)).hexdigest()
            for key, ref in row['payload']['assets'].items()}

    def test_draft_redate_preserves_exact_native_metadata_assets_and_approved_history(self):
        profile = self.profile()
        historical = self.analyze(profile, '2026-10-07')
        approved = self.approve(historical)
        self.assertEqual(approved.status_code, 201, approved.text)
        old = self.stored(historical)
        history = self.client.get('/api/profiles/' + profile['id'] + '/history').json()
        analysis = self.analyze(profile, '2026-10-08')
        draft = self.client.post('/api/analyses/' + analysis['id'] + '/draft', json={
            'width': 120, 'height': 100, 'mask_base64': analysis['mask_base64'],
            'instance_base64': analysis['instance_base64'],
            'instance_analysis_id': analysis['id'],
            'instance_metadata': analysis['instance_metadata']})
        self.assertEqual(draft.status_code, 200, draft.text)
        before = self.stored(analysis)
        blobs = self.asset_hashes(before)
        corrected = self.change(analysis, '2026-10-09')
        self.assertEqual(corrected.status_code, 200, corrected.text)
        result = corrected.json()
        self.assertEqual(result['id'], analysis['id'])
        self.assertEqual(result['date'], '2026-10-09')
        self.assertEqual(result['status'], 'DRAFT')
        after = self.stored(analysis)
        audit = after['payload'].pop('capture_date_edits')
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['from'], '2026-10-08')
        self.assertEqual(audit[0]['to'], '2026-10-09')
        self.assertTrue(audit[0]['timestamp'])
        after['date'] = before['date']
        self.assertEqual(after, before)
        self.assertEqual(self.asset_hashes(after), blobs)
        for key in ('mask_base64', 'instance_base64', 'instance_metadata', 'pipeline',
                    'calibration', 'image_sha256', 'assets'):
            self.assertEqual(result[key], draft.json()[key], key)
        self.assertEqual(self.stored(historical), old)
        self.assertEqual(self.client.get('/api/profiles/' + profile['id'] + '/history').json(), history)

    def test_same_date_and_response_loss_retry_are_idempotent_and_stale_expected_rejects(self):
        analysis = self.analyze(self.profile())
        before = self.stored(analysis)
        self.assertEqual(self.change(analysis, analysis['date']).status_code, 200)
        self.assertEqual(self.stored(analysis), before)
        first = self.change(analysis, '2026-10-09')
        self.assertEqual(first.status_code, 200, first.text)
        saved = self.stored(analysis)
        self.assertEqual(self.change(analysis, '2026-10-09').status_code, 200)
        self.assertEqual(self.stored(analysis), saved)
        stale = self.change(analysis, '2026-10-10')
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.stored(analysis), saved)

    def test_approved_date_collision_rejects_without_mutation(self):
        profile = self.profile()
        old = self.analyze(profile)
        approved = self.approve(old)
        self.assertEqual(approved.status_code, 201, approved.text)
        active = self.analyze(profile, '2026-10-09')
        before = self.stored(active)
        conflict = self.change(active, '2026-10-08')
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(self.stored(active), before)

    def test_concurrent_stale_corrections_commit_only_one_audit(self):
        from src.webapp.service import Conflict
        analysis = self.analyze(self.profile())
        service = self.app.state.service
        def correct(day):
            try:
                return service.capture_date(analysis['id'], date(2026, 10, day), date(2026, 10, 8))
            except Conflict as exc:
                return exc
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(correct, (9, 10)))
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(item, Conflict) for item in outcomes), 1, outcomes)
        saved = self.stored(analysis)
        self.assertEqual(len(saved['payload']['capture_date_edits']), 1)
        self.assertIn(saved['date'], (date(2026, 10, 9), date(2026, 10, 10)))

    def test_only_analyzed_or_draft_can_change_date(self):
        profile = self.profile()
        analysis = self.analyze(profile)
        for status in ('UPLOADED', 'ANALYZING', 'ERROR', 'APPROVED'):
            with self.subTest(status=status):
                service = self.app.state.service
                with service.db.engine.begin() as conn:
                    conn.execute(update(service.db.analyses).where(
                        service.db.analyses.c.id == analysis['id']).values(status=status))
                before = self.stored(analysis)
                response = self.change(analysis, '2026-10-09')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.stored(analysis), before)

    def test_invalid_dates_and_unexpected_fields_are_rejected(self):
        analysis = self.analyze(self.profile())
        before = self.stored(analysis)
        for payload in ({'date': '2026-02-30', 'expected_date': analysis['date']},
                        {'date': 'tomorrow', 'expected_date': analysis['date']},
                        {'date': '2026-10-09'},
                        {'date': '2026-10-09', 'expected_date': analysis['date'], 'profile_id': 'other'}):
            response = self.client.post('/api/analyses/' + analysis['id'] + '/capture-date', json=payload)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.stored(analysis), before)


@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreCaptureDate(TestCaptureDate):
    from tests.test_firestore_web import setup as setUp


def test_capture_date_route_remains_tenant_scoped_and_csrf_protected(application):
    owner = login(application)
    other = login(application, 'org-b')
    profile = plant(owner)
    uploaded = owner.post('/api/uploads', data={'profile_id': profile['id'], 'date': '2026-10-08'},
        files={'image': ('synthetic.png', image(), 'image/png')})
    assert uploaded.status_code == 201, uploaded.text
    path = '/api/analyses/' + uploaded.json()['id'] + '/capture-date'
    payload = {'date': '2026-10-09', 'expected_date': '2026-10-08'}
    assert other.post(path, json=payload).status_code == 404
    assert owner.post(path, json=payload, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert owner.get('/api/analyses/' + uploaded.json()['id']).json()['date'] == '2026-10-08'
