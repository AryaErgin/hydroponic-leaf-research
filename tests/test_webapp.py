"""End-to-end synthetic approval tests; never approve a real project image."""
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import threading
from sqlalchemy import update

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from src.hydroponic.config import ROOT, load_profiles
from src.hydroponic.color import segment
from src.manual_pot import save_reviewed_diameter

KEYS=('visible_target_only','visible_tissue_complete','moss_excluded','growing_medium_excluded',
      'dead_tissue_excluded','pot_and_background_excluded','scale_approximation_accepted')


class TestWebMVP(unittest.TestCase):
    def setUp(self):
        from src.webapp.app import create_app
        from src.webapp.settings import Settings
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.settings=Settings(data_dir=self.base/'runtime',max_upload_bytes=100000,max_image_pixels=20000,closed_academic_research=False)
        self.app=create_app(self.settings)
        # Unit fixtures must not inherit an operator's promoted real model.
        from src.training.model_registry import ModelRegistry
        self.app.state.service.models=ModelRegistry(self.base/'fixture-models')
        self.client=TestClient(self.app,client=('127.0.0.1',50000));self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        image=Image.new('RGB',(120,100),(35,35,35));array=np.array(image);array[20:45,30:60]=[70,160,35]
        stream=io.BytesIO();Image.fromarray(array).save(stream,format='PNG');self.image=stream.getvalue();self.rgb=array

    def profile(self,species='lettuce',calibrated=True,plant='plant-1'):
        payload=dict(experiment_id='experiment-1',plant_id=plant,species=species,
            calibration_type='reviewed_scalar' if calibrated else 'pixels_only',
            cm_per_pixel=.1 if calibrated else None,reference_size_px=[120,100] if calibrated else None,
            calibration_provenance='Synthetic reviewed camera scale' if calibrated else '',
            calibration_reviewed=calibrated)
        response=self.client.post('/api/profiles',json=payload);self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def upload(self,profile,date='2026-10-08',image=None,geometry=True):
        return self.client.post('/api/uploads',files={'image':('lettuce.png',self.image if image is None else image,'image/png')},
            data={'profile_id':profile['id'],'date':date,'geometry_unchanged':str(geometry).lower()})

    def analyze(self,profile,date='2026-10-08',geometry=True):
        response=self.upload(profile,date,geometry=geometry);self.assertEqual(response.status_code,201,response.text)
        response=self.client.post('/api/analyses/'+response.json()['id']+'/analyze',params={'engine':'auto' if self.app.state.service.models.champion() else 'classical'})
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def approve(self,analysis,area=100,**updates):
        mask=np.zeros((100,120),np.uint8);mask.reshape(-1)[:area]=1
        payload=dict(width=120,height=100,mask_base64=base64.b64encode(mask.tobytes()).decode(),
            approval='APPROVE',review=dict.fromkeys(KEYS,True));payload.update(updates)
        return self.client.post('/api/analyses/'+analysis['id']+'/approve',json=payload)

    def test_upload_keeps_original_bytes_and_no_measurement(self):
        profile=self.profile();response=self.upload(profile)
        self.assertEqual(response.status_code,201,response.text)
        upload=response.json();self.assertEqual(upload['status'],'UPLOADED')
        self.assertEqual(self.client.get(upload['assets']['original']).content,self.image)
        self.assertEqual(upload['image_sha256'],hashlib.sha256(self.image).hexdigest())
        self.assertEqual(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements'],[])

    def test_analysis_matches_existing_single_plant_candidate_and_never_finalizes(self):
        profile=self.profile();analysis=self.analyze(profile)
        with Image.open(io.BytesIO(self.client.get(analysis['assets']['candidate_mask']).content)) as mask:
            settings,_=load_profiles('lettuce');self.assertTrue(np.array_equal(np.array(mask)>0,segment(self.rgb,settings['color'])))
            self.assertEqual(mask.info['measurement_status'],'UNREVIEWED_CANDIDATE')
        self.assertEqual(analysis['measurement_status'],'UNREVIEWED_CANDIDATE')
        self.assertIn('source_sha256',analysis['pipeline'])
        self.assertTrue(analysis['human_approval_required'])
        self.assertEqual(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements'],[])

    def test_approval_requires_explicit_action_all_reviews_and_native_binary(self):
        analysis=self.analyze(self.profile())
        for update in [dict(approval=''),dict(review={}),dict(width=60),dict(mask_base64='?'),
                       dict(mask_base64=base64.b64encode(bytes([128])*12000).decode()),dict(review=dict.fromkeys(KEYS,'true'))]:
            with self.subTest(update=list(update)):
                self.assertEqual(self.approve(analysis,**update).status_code,400)
        self.assertEqual(self.client.get('/api/profiles/'+analysis['profile_id']+'/history').json()['measurements'],[])
        self.assertEqual(self.approve(analysis,area=0).status_code,400)

    def test_approved_measurement_preserves_qc_scale_media_and_correction_provenance(self):
        profile=self.profile();analysis=self.analyze(profile);response=self.approve(analysis)
        self.assertEqual(response.status_code,201,response.text);measurement=response.json()
        self.assertEqual(measurement['pixel_area'],100);self.assertAlmostEqual(measurement['projected_area_cm2'],1.)
        self.assertEqual(measurement['cm_per_pixel'],.1)
        self.assertEqual(measurement['measurement_status'],'MANUAL_GROUND_TRUTH')
        self.assertEqual(measurement['plant_id'],'plant-1');self.assertEqual(measurement['date'],'2026-10-08')
        self.assertTrue(measurement['correction_required']);self.assertIn('segmentation_qc',measurement['qc'])
        self.assertEqual(self.client.get(measurement['assets']['original']).content,self.image)
        with Image.open(io.BytesIO(self.client.get(measurement['assets']['approved_mask']).content)) as mask:
            self.assertEqual(mask.size,(120,100));self.assertEqual(np.count_nonzero(mask),100)
            self.assertEqual(mask.info['measurement_status'],'MANUAL_GROUND_TRUTH')
        self.assertEqual(measurement['annotation_provenance']['review_method'],'explicit_web_APPROVE')
        self.assertEqual(len(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements']),1)

    def test_unchanged_candidate_can_be_explicitly_approved_without_correction(self):
        analysis=self.analyze(self.profile());result=self.approve(analysis,mask_base64=analysis['mask_base64'])
        self.assertEqual(result.status_code,201,result.text);self.assertFalse(result.json()['correction_required'])

    def test_approved_analysis_returns_exact_approved_pixels_and_separate_candidate(self):
        analysis=self.analyze(self.profile());self.assertEqual(self.approve(analysis).status_code,201)
        saved=self.client.get('/api/analyses/'+analysis['id']).json()
        self.assertEqual(sum(base64.b64decode(saved['mask_base64'])),100)
        self.assertEqual(saved['measurement_status'],'MANUAL_GROUND_TRUTH')
        with Image.open(io.BytesIO(self.client.get(saved['assets']['approved_mask']).content)) as mask:
            self.assertEqual(np.count_nonzero(mask),100)
        self.assertEqual(saved['candidate_status'],'UNREVIEWED_CANDIDATE')

    def test_analysis_lease_recovers_interrupted_work_and_preserves_live_work(self):
        analysis=self.upload(self.profile()).json();service=self.app.state.service;table=service.db.analyses
        payload=service.db.one(table,analysis['id'])['payload']
        payload.update(analysis_token='old-attempt',analysis_started_at=datetime.now(timezone.utc).isoformat())
        with service.db.engine.begin() as conn:
            conn.execute(update(table).where(table.c.id==analysis['id']).values(status='ANALYZING',payload=payload))
        self.assertEqual(self.client.post('/api/analyses/'+analysis['id']+'/analyze',params={'engine':'classical'}).status_code,409)
        payload['analysis_started_at']=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
        with service.db.engine.begin() as conn:
            conn.execute(update(table).where(table.c.id==analysis['id']).values(payload=payload))
        result=self.client.post('/api/analyses/'+analysis['id']+'/analyze',params={'engine':'classical'})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['status'],'ANALYZED')
        self.assertNotEqual(result.json()['analysis_token'],'old-attempt')

    def test_nonloopback_peer_cannot_bypass_private_access_with_host_header(self):
        self.profile()
        with TestClient(self.app,client=('203.0.113.10',4444)) as client:
            self.assertEqual(client.get('/api/profiles',headers={'Host':'localhost'}).status_code,403)

    def test_chunked_oversized_request_is_bounded_before_parsing(self):
        limit=self.settings.max_upload_bytes+1024*1024
        result=self.client.post('/api/uploads',content=iter([b'x'*65536]*(limit//65536+2)),
            headers={'Content-Type':'multipart/form-data; boundary=example'})
        self.assertEqual(result.status_code,413,result.text)

    def test_expired_attempt_cannot_finish_or_reset_a_newer_analysis(self):
        import src.webapp.service as module
        analysis=self.upload(self.profile()).json();service=self.app.state.service;table=service.db.analyses
        original=module.process_single_plant_rgb
        def replaced(*args,**kwargs):
            row=service.db.one(table,analysis['id']);payload={**row['payload'],'analysis_token':'new-owner'}
            with service.db.engine.begin() as conn:
                conn.execute(update(table).where(table.c.id==analysis['id']).values(payload=payload))
            return original(*args,**kwargs)
        with patch.object(module,'process_single_plant_rgb',side_effect=replaced):
            self.assertEqual(self.client.post('/api/analyses/'+analysis['id']+'/analyze',params={'engine':'classical'}).status_code,409)
        row=service.db.one(table,analysis['id']);self.assertEqual(row['status'],'ANALYZING')
        self.assertEqual(row['payload']['analysis_token'],'new-owner')

    def test_concurrent_approvals_commit_only_one_measurement_for_plant_date(self):
        profile=self.profile();first=self.analyze(profile);second=self.analyze(profile);barrier=threading.Barrier(2)
        def approve(analysis):barrier.wait();return self.approve(analysis)
        with ThreadPoolExecutor(max_workers=2) as executor:responses=list(executor.map(approve,[first,second]))
        statuses=sorted(r.status_code for r in responses)
        # Firestore may abort both contending transactions. Approval deliberately
        # does not replay side-effecting IO automatically: explicit retry is the
        # contract, while zero/one commits and date uniqueness remain mandatory.
        if statuses==[409,409]:
            self.assertIsNotNone(self.app.state.service.settings.firestore_project)
            for response in responses:self.assertIn('Concurrent record update',response.json()['detail'])
            self.assertEqual(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements'],[])
            self.assertEqual(self.approve(first).status_code,201)
            self.assertEqual(self.approve(second).status_code,409)
        else:self.assertEqual(statuses,[201,409])
        self.assertEqual(len(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements']),1)

    def test_draft_is_binary_persistent_resumable_and_never_ground_truth(self):
        analysis=self.analyze(self.profile());mask=np.zeros((100,120),np.uint8);mask[5:10,20:25]=1
        payload=dict(width=120,height=100,mask_base64=base64.b64encode(mask.tobytes()).decode())
        result=self.client.post('/api/analyses/'+analysis['id']+'/draft',json=payload)
        self.assertEqual(result.status_code,200,result.text)
        resumed=self.client.get('/api/analyses/'+analysis['id']).json()
        self.assertEqual(resumed['mask_base64'],payload['mask_base64'])
        self.assertEqual(resumed['status'],'DRAFT')
        self.assertEqual(self.client.get('/api/profiles/'+analysis['profile_id']+'/history').json()['measurements'],[])

    def test_repeated_approval_and_duplicate_day_cannot_make_second_final_measurement(self):
        profile=self.profile();analysis=self.analyze(profile)
        self.assertEqual(self.approve(analysis).status_code,201)
        self.assertEqual(self.approve(analysis).status_code,409)
        second=self.analyze(profile);self.assertEqual(self.approve(second).status_code,409)
        self.assertEqual(len(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements']),1)

    def test_calibration_reuse_requires_attestation_and_native_dimensions(self):
        profile=self.profile();analysis=self.analyze(profile,geometry=False)
        self.assertEqual(self.approve(analysis).status_code,400)
        wrong=io.BytesIO();Image.new('RGB',(60,50),(70,160,30)).save(wrong,format='PNG')
        uploaded=self.upload(profile,image=wrong.getvalue()).json()
        self.assertEqual(self.client.post('/api/analyses/'+uploaded['id']+'/analyze',params={'engine':'classical'}).status_code,200)
        self.assertEqual(self.approve(uploaded).status_code,400)
        valid=self.analyze(profile,date='2026-10-09');self.assertEqual(self.approve(valid).status_code,201)
        self.assertEqual(valid['calibration']['id'],profile['calibration']['id'])

    def test_import_existing_reviewed_pot_calibration_reuses_verified_scale(self):
        source=self.base/'lettuce_ref.png';source.write_bytes(self.image)
        calibration=save_reviewed_diameter(source,[[10,50],[110,50]],5.82,reviewed=True,cache_root=self.base/'calibration')
        response=self.client.post('/api/calibrations/import',files={
            'reference_image':('lettuce_ref.png',self.image,'image/png'),
            'calibration_file':('reference.pot.json',calibration.read_bytes(),'application/json')})
        self.assertEqual(response.status_code,201,response.text);cal=response.json()
        self.assertAlmostEqual(cal['cm_per_pixel'],.0582);self.assertEqual(cal['pot_diameter_cm'],5.82)
        response=self.client.post('/api/profiles',json=dict(experiment_id='pot',plant_id='one',species='lettuce',calibration_id=cal['id']))
        self.assertEqual(response.status_code,201,response.text)
        analysis=self.analyze(response.json());measurement=self.approve(analysis).json()
        self.assertAlmostEqual(measurement['projected_area_cm2'],.338724)
        self.assertEqual(measurement['calibration']['provenance']['reference_image_sha256'],hashlib.sha256(self.image).hexdigest())

    def test_rumex_has_no_invented_automatic_mask_but_manual_measurement_works(self):
        profile=self.profile('rumex_acetosa');analysis=self.analyze(profile)
        self.assertEqual(analysis['species_validation'],'UNVALIDATED / REVIEW REQUIRED')
        self.assertFalse(analysis['automatic_mask_available'])
        self.assertEqual(analysis['pipeline']['qc_status'],'UNCONFIGURED')
        self.assertEqual(set(base64.b64decode(analysis['mask_base64'])),{0})
        self.assertEqual(self.approve(analysis,area=0).status_code,400)
        result=self.approve(analysis);self.assertEqual(result.status_code,201,result.text)
        self.assertEqual(result.json()['species'],'rumex_acetosa')
        self.assertEqual(result.json()['species_validation'],'UNVALIDATED / REVIEW REQUIRED')

    def test_pixels_only_profile_withholds_physical_area_and_growth(self):
        profile=self.profile(calibrated=False);analysis=self.analyze(profile);result=self.approve(analysis)
        self.assertEqual(result.status_code,201,result.text);self.assertIsNone(result.json()['projected_area_cm2'])
        self.assertEqual(result.json()['pixel_area'],100)
        self.assertIsNone(self.client.get('/api/profiles/'+profile['id']+'/history').json()['growth']['relative_growth_rate_per_day'])

    def test_growth_uses_chronological_dates_true_gap_and_log_rate(self):
        profile=self.profile()
        self.assertEqual(self.approve(self.analyze(profile,'2026-10-10'),400).status_code,201)
        self.assertEqual(self.approve(self.analyze(profile,'2026-10-08'),100).status_code,201)
        history=self.client.get('/api/profiles/'+profile['id']+'/history').json();growth=history['growth']
        self.assertEqual([m['date'] for m in history['measurements']],['2026-10-08','2026-10-10'])
        self.assertAlmostEqual(growth['current_area_cm2'],4.);self.assertAlmostEqual(growth['previous_area_cm2'],1.)
        self.assertEqual(growth['delta_days'],2);self.assertAlmostEqual(growth['absolute_growth_cm2'],3.)
        self.assertAlmostEqual(growth['daily_growth_cm2'],1.5);self.assertAlmostEqual(growth['percentage_growth'],300.)
        self.assertAlmostEqual(growth['relative_growth_rate_per_day'],.6931471805599453)

    def test_changed_calibration_withholds_comparative_growth(self):
        profile=self.profile();self.assertEqual(self.approve(self.analyze(profile,'2026-10-08')).status_code,201)
        response=self.client.put('/api/profiles/'+profile['id']+'/calibration',json=dict(calibration_type='reviewed_scalar',
            cm_per_pixel=.2,reference_size_px=[120,100],calibration_provenance='New camera geometry',calibration_reviewed=True))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.approve(self.analyze(response.json(),'2026-10-09')).status_code,201)
        growth=self.client.get('/api/profiles/'+profile['id']+'/history').json()['growth']
        self.assertIsNone(growth['absolute_growth_cm2']);self.assertEqual(growth['reason'],'calibration_changed')

    def test_csv_exports_only_approved_chronological_measurements(self):
        profile=self.profile();self.analyze(profile,'2026-10-09')
        self.assertEqual(self.approve(self.analyze(profile,'2026-10-08')).status_code,201)
        result=self.client.get('/api/profiles/'+profile['id']+'/export.csv')
        self.assertEqual(result.status_code,200);self.assertIn('text/csv',result.headers['content-type'])
        self.assertIn('projected_area_cm2',result.text);self.assertIn('MANUAL_GROUND_TRUTH',result.text)
        # Capture date and approval timestamp can fall on different days.
        import csv
        rows=list(csv.DictReader(io.StringIO(result.text)))
        self.assertEqual([row['date'] for row in rows],['2026-10-08'])

    def test_invalid_corrupt_non_rgb_rotated_oversized_images_rejected(self):
        profile=self.profile()
        self.assertEqual(self.upload(profile,image=b'not an image').status_code,400)
        for mode in ['L','RGBA']:
            stream=io.BytesIO();Image.new(mode,(120,100)).save(stream,format='PNG')
            self.assertEqual(self.upload(profile,image=stream.getvalue()).status_code,400)
        self.assertEqual(self.upload(profile,image=b'x'*100001).status_code,413)
        stream=io.BytesIO();Image.new('RGB',(200,200)).save(stream,format='PNG')
        self.assertEqual(self.upload(profile,image=stream.getvalue()).status_code,400)
        stream=io.BytesIO();image=Image.new('RGB',(120,100));exif=Image.Exif();exif[274]=6;image.save(stream,format='JPEG',exif=exif)
        result=self.client.post('/api/uploads',files={'image':('photo.jpg',stream.getvalue(),'image/jpeg')},data={'profile_id':profile['id'],'date':'2026-10-08'})
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.client.post('/api/uploads',files={'image':('bad.svg',self.image,'image/svg+xml')},data={'profile_id':profile['id'],'date':'2026-10-08'}).status_code,400)

    def test_invalid_profile_scale_species_duplicate_and_dates_rejected(self):
        for update in [dict(species='invented'),dict(cm_per_pixel=-1),dict(calibration_reviewed=False),dict(reference_size_px=[0,100])]:
            payload=dict(experiment_id='e',plant_id='p',species='lettuce',calibration_type='reviewed_scalar',cm_per_pixel=.1,
                reference_size_px=[120,100],calibration_provenance='reviewed',calibration_reviewed=True);payload.update(update)
            self.assertIn(self.client.post('/api/profiles',json=payload).status_code,[400,422])
        profile=self.profile();self.assertEqual(self.client.post('/api/profiles',json=dict(experiment_id='experiment-1',plant_id='plant-1',species='lettuce')).status_code,409)
        self.assertEqual(self.upload(profile,date='not-a-date').status_code,422)

    def test_persistence_survives_application_restart(self):
        from src.webapp.app import create_app
        profile=self.profile();analysis=self.analyze(profile);self.assertEqual(self.approve(analysis).status_code,201)
        with TestClient(create_app(self.settings),client=('127.0.0.1',50001)) as client:
            self.assertEqual(len(client.get('/api/profiles').json()),1)
            history=client.get('/api/profiles/'+profile['id']+'/history').json()
            self.assertEqual(history['measurements'][0]['pixel_area'],100)
            self.assertEqual(client.get(history['measurements'][0]['assets']['original']).content,self.image)

    def test_media_failure_cannot_commit_final_measurement(self):
        profile=self.profile();analysis=self.analyze(profile)
        with patch.object(self.app.state.service.media,'put',side_effect=OSError('simulated disk failure')):
            self.assertEqual(self.approve(analysis).status_code,503)
        self.assertEqual(self.client.get('/api/profiles/'+profile['id']+'/history').json()['measurements'],[])
        self.assertEqual(self.client.get('/api/analyses/'+analysis['id']).json()['status'],'ANALYZED')

    def test_auth_origin_and_health_boundaries(self):
        from src.webapp.app import create_app
        from src.webapp.settings import Settings
        config=Settings(data_dir=self.base/'secured',auth_token='test-secret-'+'x'*32)
        with TestClient(create_app(config)) as client:
            self.assertEqual(client.get('/health').status_code,200)
            self.assertEqual(client.get('/api/profiles').status_code,401)
            self.assertEqual(client.post('/api/login',json={'token':'wrong'}).status_code,401)
            self.assertEqual(client.post('/api/login',json={'token':config.auth_token}).status_code,200)
            self.assertEqual(client.get('/api/profiles').status_code,200)
            self.assertEqual(client.post('/api/profiles',json={},headers={'Origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/profiles',headers={'Host':'foreign.example'}).status_code,403)

    def test_cloud_run_refuses_ephemeral_database_and_missing_private_access(self):
        from src.webapp.settings import Settings
        with self.assertRaises(ValueError):Settings(data_dir=self.base/'cloud',cloud_run=True).validate()


if __name__=='__main__':unittest.main()
