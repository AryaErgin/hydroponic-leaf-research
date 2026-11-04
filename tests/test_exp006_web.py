"""Synthetic web integration; no real project image is uploaded or approved."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import io
import numpy as np
from PIL import Image

from tests import test_webapp as web_fixtures
from tests import test_training_registry as registry_fixtures


class TestExp006Web(unittest.TestCase):
    setUp=web_fixtures.TestWebMVP.setUp
    profile=web_fixtures.TestWebMVP.profile
    upload=web_fixtures.TestWebMVP.upload
    analyze=web_fixtures.TestWebMVP.analyze
    approve=web_fixtures.TestWebMVP.approve
    file=registry_fixtures.RegistryFixtures.file
    model=registry_fixtures.RegistryFixtures.model

    def test_closed_research_lamina_approval_preserves_permission_but_not_foreground_eligibility(self):
        self.settings.closed_academic_research=True
        analysis=self.analyze(self.profile())
        self.assertEqual(analysis['source_ownership'],'project_owner')
        self.assertEqual(analysis['research_permission_provenance']['declaration'],'closed_academic_research_policy')
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.approved_masks),[])
        response=self.approve(analysis);self.assertEqual(response.status_code,201,response.text)
        canonical=self.app.state.service.db.rows(self.app.state.service.db.approved_masks)[0]['payload']
        self.assertTrue(canonical['license_permits_research'])
        self.assertFalse(canonical['new_real_eligible'])
        self.assertEqual(canonical['measurement_target'],'visible_leaf_lamina')

    def test_demo_without_consent_is_not_eligible_and_does_not_rewrite_history(self):
        analysis=self.analyze(self.profile());self.approve(analysis)
        original=self.app.state.service.db.rows(self.app.state.service.db.approved_masks)[0]['payload']
        self.settings.closed_academic_research=True
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.approved_masks)[0]['payload'],original)
        self.assertFalse(original['new_real_eligible'])

    def test_capture_date_and_approval_timestamp_are_stored_separately(self):
        approved=self.approve(self.analyze(self.profile())).json()
        raw=self.app.state.service.db.one(self.app.state.service.db.measurements,approved['id'])['payload']
        self.assertEqual(raw['captured_at'],'2026-10-08')
        self.assertEqual(raw['approved_at'],raw['timestamp'])
        self.assertNotEqual(raw['captured_at'],raw['approved_at'])

    def test_upload_without_source_permission_can_be_measured_but_not_used_for_training(self):
        analysis=self.analyze(self.profile());self.assertEqual(analysis['source_ownership'],'unknown')
        response=self.approve(analysis);self.assertEqual(response.status_code,201,response.text)
        canonical=self.app.state.service.db.rows(self.app.state.service.db.approved_masks)[0]['payload']
        self.assertFalse(canonical['license_permits_research']);self.assertFalse(canonical['new_real_eligible'])

    def test_explicit_own_capture_permission_is_separate_from_human_mask_approval(self):
        profile=self.profile()
        response=self.client.post('/api/uploads',files={'image':('lettuce.png',self.image,'image/png')},
            data=dict(profile_id=profile['id'],date='2026-10-08',geometry_unchanged='true',source_research_permission='true'))
        self.assertEqual(response.status_code,201,response.text);uploaded=response.json()
        self.assertEqual(uploaded['source_ownership'],'project_owner');self.assertTrue(uploaded['human_approval_required'])
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.approved_masks),[])
        analysis=self.client.post('/api/analyses/'+uploaded['id']+'/analyze',params={'engine':'classical'}).json()
        response=self.approve(analysis);self.assertEqual(response.status_code,201,response.text)
        canonical=self.app.state.service.db.rows(self.app.state.service.db.approved_masks)[0]['payload']
        self.assertTrue(canonical['license_permits_research']);self.assertFalse(canonical['new_real_eligible'])
        self.assertEqual(canonical['training_ineligibility_reason'],'LAMINA_TARGET_DIFFERS_FROM_WHOLE_FOREGROUND_COHORT')
        self.assertEqual(canonical['research_permission_provenance']['declaration'],'source_research_permission')

    def test_auto_without_promoted_model_fails_closed(self):
        upload=self.upload(self.profile()).json()
        response=self.client.post('/api/analyses/'+upload['id']+'/analyze')
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(self.app.state.service.analysis(upload['id'])['status'],'ERROR')

    def test_registered_challenger_is_listed_but_never_auto_activated(self):
        from src.training.model_registry import ModelRegistry
        models=ModelRegistry(self.base/'models');models.register(self.model());self.app.state.service.models=models
        response=self.client.get('/api/models');self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertIsNone(data['champion_version']);self.assertEqual(data['models'][0]['version'],'v1')
        self.assertEqual(data['models'][0]['preview_engine'],'learned:v1')
        analysis=self.analyze(self.profile());self.assertEqual(analysis['candidate_model']['architecture'],'classical_cv')

    def test_promoted_fixture_generates_native_learned_candidate_and_still_requires_review(self):
        from src.training.model_registry import ModelRegistry
        from src.training import inference
        models=ModelRegistry(self.base/'models');saved=models.register(self.model(eligible=True))
        # This synthetic registry fixture contains no real weights or annotation.
        models.promote('v1',approval='APPROVE_MODEL',report_sha256=saved['artifacts']['evaluation']['sha256'],expected_current=None)
        self.app.state.service.models=models
        predicted=np.zeros((100,120),bool);predicted[10:20,20:30]=True
        with patch.object(inference,'predict_version',return_value=dict(mask=predicted,metadata={'fixture_only':True})) as predict:
            analysis=self.analyze(self.profile())
        predict.assert_called_once();self.assertEqual(predict.call_args.kwargs['version'],'v1')
        self.assertEqual(analysis['candidate_model']['version'],'v1');self.assertEqual(analysis['candidate_model']['architecture'],'unet_resnet18')
        self.assertEqual(analysis['candidate_model']['checkpoint_sha256'],saved['artifacts']['checkpoint']['sha256'])
        self.assertEqual(analysis['pipeline']['raw_pixel_area'],100)
        self.assertIsNone(analysis['pipeline']['reported_pixel_area']);self.assertIsNone(analysis['pipeline']['projected_area_cm2'])
        self.assertIn('unvalidated_target_domain',analysis['pipeline']['qc_reasons'])
        self.assertIn('human_review_required',analysis['pipeline']['qc_reasons'])
        self.assertFalse(analysis['pipeline']['diagnostics']['empty_mask']);self.assertFalse(analysis['pipeline']['diagnostics']['full_image_mask'])
        self.assertEqual(analysis['pipeline']['device'],'cpu');self.assertEqual(analysis['candidate_model']['device'],'cpu')
        self.assertGreaterEqual(analysis['candidate_model']['inference_seconds'],0)
        self.assertEqual(analysis['pipeline']['inference_seconds'],analysis['candidate_model']['inference_seconds'])
        self.assertTrue(analysis['human_approval_required']);self.assertEqual(analysis['measurement_status'],'UNREVIEWED_CANDIDATE')
        with Image.open(io.BytesIO(self.client.get(analysis['assets']['candidate_mask']).content)) as mask:
            self.assertTrue(np.array_equal(np.asarray(mask)!=0,predicted))
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.approved_masks),[])

    def test_empty_or_full_learned_candidates_are_sanity_flagged_without_final_area(self):
        from src.training.model_registry import ModelRegistry
        from src.training import inference
        models=ModelRegistry(self.base/'models');models.register(self.model());self.app.state.service.models=models
        payload=dict(species='lettuce',image_sha256=hashlib.sha256(self.image).hexdigest())
        for foreground in (False,True):
            predicted=np.full(self.rgb.shape[:2],foreground,bool)
            with self.subTest(foreground=foreground), patch.object(inference,'predict_version',return_value=dict(mask=predicted)):
                candidate,model=self.app.state.service._candidate(self.rgb,payload,'learned:v1')
            metadata=candidate['metadata'];self.assertEqual(metadata['raw_pixel_area'],int(predicted.sum()))
            self.assertEqual(metadata['diagnostics']['empty_mask'],not foreground)
            self.assertEqual(metadata['diagnostics']['full_image_mask'],foreground)
            self.assertIn('full_image_candidate' if foreground else 'empty_candidate',metadata['qc_reasons'])
            self.assertIsNone(metadata['reported_pixel_area']);self.assertIsNone(metadata['projected_area_cm2'])
            self.assertEqual(model['candidate_kind'],'AUTOMATIC_PREDICTION')

    def test_explicit_missing_learned_engine_fails_without_substitute(self):
        upload=self.upload(self.profile()).json()
        response=self.client.post('/api/analyses/'+upload['id']+'/analyze',params={'engine':'learned:missing'})
        self.assertEqual(response.status_code,503,response.text)
        self.assertNotIn('candidate_mask',self.app.state.service.analysis(upload['id'])['assets'])

    def test_blocked_native_runtime_fails_closed_and_keeps_error_provenance(self):
        from src.training.model_registry import ModelRegistry
        from src.training import inference
        models=ModelRegistry(self.base/'models');models.register(self.model());self.app.state.service.models=models
        upload=self.upload(self.profile()).json()
        with patch.object(inference,'predict_version',side_effect=OSError(4551,'Application Control policy blocked native runtime')):
            response=self.client.post('/api/analyses/'+upload['id']+'/analyze',params={'engine':'learned:v1'})
        self.assertEqual(response.status_code,503,response.text)
        analysis=self.app.state.service.analysis(upload['id']);self.assertEqual(analysis['status'],'ERROR')
        self.assertIn('4551',analysis['analysis_error']);self.assertNotIn('candidate_model',analysis)

    def test_explicit_classical_engine_does_not_attempt_optional_native_runtime(self):
        from src.training import inference
        upload=self.upload(self.profile()).json()
        with patch.object(inference,'predict_version',side_effect=AssertionError('Native runtime should stay lazy')):
            response=self.client.post('/api/analyses/'+upload['id']+'/analyze',params={'engine':'classical'})
        self.assertEqual(response.status_code,200,response.text);metadata=response.json()['candidate_model']
        self.assertEqual(metadata['requested_engine'],'classical');self.assertIsNone(metadata['fallback_reason'])

    def test_invalid_engine_rejected_before_starting_analysis(self):
        upload=self.upload(self.profile()).json()
        response=self.client.post('/api/analyses/'+upload['id']+'/analyze',params={'engine':'learned:../escape'})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.client.get('/api/analyses/'+upload['id']).json()['status'],'UPLOADED')

    def test_approval_atomically_records_approved_lineage_and_exact_correction(self):
        service=self.app.state.service
        self.assertTrue(hasattr(service.db,'approved_masks'),'approved-mask table missing')
        analysis=self.analyze(self.profile());self.assertEqual(service.db.rows(service.db.approved_masks),[])
        response=self.approve(analysis,area=100);self.assertEqual(response.status_code,201,response.text)
        measurement=response.json();rows=service.db.rows(service.db.approved_masks);self.assertEqual(len(rows),1)
        canonical=rows[0]['payload'];self.assertEqual(rows[0]['measurement_id'],measurement['id'])
        self.assertEqual(canonical['group_id'],'experiment-1/plant-1');self.assertEqual(canonical['pixel_area'],100)
        self.assertEqual(canonical['candidate_model'],analysis['candidate_model'])
        approved=service._mask(canonical['assets']['approved_mask']);candidate=service._mask(canonical['assets']['candidate_mask'])
        magnitude=canonical['correction_magnitude']
        self.assertEqual(magnitude['changed_pixels'],int((approved!=candidate).sum()))
        self.assertEqual(magnitude['added_target_pixels'],int((approved & ~candidate).sum()))
        self.assertEqual(magnitude['removed_candidate_pixels'],int((candidate & ~approved).sum()))
        self.assertEqual(canonical['assets']['original']['sha256'],hashlib.sha256(self.image).hexdigest())
        self.assertEqual(self.approve(analysis).status_code,409);self.assertEqual(len(service.db.rows(service.db.approved_masks)),1)

    def test_registry_failure_rolls_back_measurement_and_analysis_approval(self):
        from src.training import approved_registry
        service=self.app.state.service
        self.assertTrue(hasattr(service.db,'approved_masks'),'approved-mask table missing')
        analysis=self.analyze(self.profile())
        with patch.object(approved_registry,'from_measurement',side_effect=ValueError('registry validation failed')):
            response=self.approve(analysis)
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(service.db.rows(service.db.measurements),[]);self.assertEqual(service.db.rows(service.db.approved_masks),[])
        self.assertEqual(self.client.get('/api/analyses/'+analysis['id']).json()['status'],'ANALYZED')

    def test_export_retains_candidate_identity_and_correction_lineage(self):
        import csv,io,json
        profile=self.profile();analysis=self.analyze(profile)
        response=self.approve(analysis,area=100);self.assertEqual(response.status_code,201,response.text)
        measurement=response.json()
        export=self.client.get('/api/profiles/'+profile['id']+'/export.csv')
        self.assertEqual(export.status_code,200)
        row=list(csv.DictReader(io.StringIO(export.text)))[0]
        self.assertEqual(row['candidate_model_version'],measurement['candidate_model']['version'])
        self.assertEqual(row['candidate_model_architecture'],measurement['candidate_model']['architecture'])
        self.assertEqual(row['candidate_checkpoint_sha256'],measurement['candidate_model'].get('checkpoint_sha256') or '')
        self.assertEqual(json.loads(row['correction_magnitude']),measurement['correction_magnitude'])

    def test_manual_rumex_approval_preserved_without_lettuce_registry(self):
        service=self.app.state.service
        self.assertTrue(hasattr(service.db,'approved_masks'),'approved-mask table missing')
        analysis=self.analyze(self.profile('rumex_acetosa'));response=self.approve(analysis)
        self.assertEqual(response.status_code,201,response.text);self.assertEqual(service.db.rows(service.db.approved_masks),[])
        self.assertEqual(analysis['candidate_model']['candidate_kind'],'UNAVAILABLE_MANUAL_INITIALIZATION')


if __name__=='__main__':
    unittest.main()
