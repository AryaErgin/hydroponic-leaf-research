"""Synthetic mobile calibration contracts; no real biological approvals."""
import base64
import copy
import hashlib
import io
import json
import os
import unittest
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import update
from tests import test_webapp as web


class TestMobileCalibrationBackend(unittest.TestCase):
    setUp=web.TestWebMVP.setUp
    profile=web.TestWebMVP.profile
    upload=web.TestWebMVP.upload
    analyze=web.TestWebMVP.analyze
    approve=web.TestWebMVP.approve

    def plant(self,name='Mobile',**changes):
        body=dict(name=name,species='lettuce',calibration_choice='none',request_id=str(uuid.uuid4()))
        body.update(changes)
        r=self.client.post('/api/plants',json=body)
        self.assertEqual(r.status_code,201,r.text)
        return r.json(),body

    def calibrate(self,p,a=None,**changes):
        data=dict(diameter_cm='12',points='[[10,50],[110,50]]',reviewed='true')
        if a:data.update(analysis_id=a['id'],image_sha256=a['image_sha256'])
        data.update(changes)
        return self.client.post('/api/plants/'+p['id']+'/calibration/two-point',
            data=data,files={'image':('synthetic.png',self.image,'image/png')})

    def test_none_creation_and_response_loss_retry_are_stable(self):
        p,body=self.plant()
        self.assertIsNone(p['calibration']);self.assertIsNone(p['pot_diameter_cm'])
        again=self.client.post('/api/plants',json=body)
        self.assertEqual(again.status_code,201,again.text);self.assertEqual(again.json()['id'],p['id'])
        self.assertEqual(len(self.app.state.service.profiles()),1)
        self.assertEqual(self.client.post('/api/plants',json={**body,'name':'Different'}).status_code,409)
        self.assertEqual(self.client.post('/api/plants',json={**body,'request_id':'bad'}).status_code,422)

    def test_turkish_display_name_has_safe_internal_identity(self):
        p,_=self.plant('Kuzukulağı-01',species='rumex_acetosa')
        self.assertEqual(p['display_name'],'Kuzukulağı-01');self.assertTrue(p['plant_id'].isascii())

    def test_preparation_resizes_phone_frames_before_picker_and_heic_is_decodable(self):
        large=io.BytesIO();Image.new('RGB',(400,300),'green').save(large,format='JPEG')
        r=self.client.post('/api/images/prepare',files={'image':('phone.jpg',large.getvalue(),'image/jpeg')})
        self.assertEqual(r.status_code,200,r.text)
        meta=json.loads(r.headers['x-image-provenance'])
        self.assertEqual(meta['original_size_px'],[400,300]);self.assertTrue(meta['resized'])
        with Image.open(io.BytesIO(r.content)) as image:
            self.assertLessEqual(image.width*image.height,self.settings.max_image_pixels)
            self.assertLess(abs(image.width/image.height-4/3),.02)
        import pillow_heif
        pillow_heif.register_heif_opener()
        heic=io.BytesIO();Image.fromarray(self.rgb).save(heic,format='HEIF')
        r=self.client.post('/api/images/prepare',files={'image':('phone.heic',heic.getvalue(),'image/heic')})
        self.assertEqual(r.status_code,200,r.text)
        with Image.open(io.BytesIO(r.content)) as image:self.assertEqual(image.mode,'RGB');self.assertEqual(image.size,(120,100))

    def test_realistic_twelve_megapixel_frame_and_jpeg_size_fallback(self):
        self.settings.max_image_pixels=8_000_000;self.settings.max_upload_bytes=10*1024*1024
        raw=io.BytesIO();Image.new('RGB',(4000,3000),(100,160,50)).save(raw,format='JPEG')
        r=self.client.post('/api/images/prepare',files={'image':('phone.jpg',raw.getvalue(),'image/jpeg')})
        self.assertEqual(r.status_code,200,r.text[:200])
        metadata=json.loads(r.headers['x-image-provenance']);self.assertEqual(metadata['original_size_px'],[4000,3000])
        self.assertTrue(metadata['resized']);self.assertFalse(metadata['raw_original_retained'])
        self.assertLessEqual(np.prod(metadata['normalized_size_px']),8_000_000)
        self.settings.max_image_pixels=100_000;self.settings.max_upload_bytes=100_000
        noise=np.random.default_rng(5).integers(0,256,(256,256,3),dtype=np.uint8)
        raw=io.BytesIO();exif=Image.Exif();exif[274]=6
        Image.fromarray(noise).save(raw,format='JPEG',quality=50,exif=exif)
        r=self.client.post('/api/images/prepare',files={'image':('noise.jpg',raw.getvalue(),'image/jpeg')})
        self.assertEqual(r.status_code,200,r.text[:200]);self.assertEqual(r.headers['content-type'],'image/jpeg')
        self.assertLessEqual(len(r.content),100_000)
        self.assertEqual(json.loads(r.headers['x-image-provenance'])['jpeg_quality'],95)

    def test_upload_response_loss_deduplicates_and_cross_plant_reuse_rejects(self):
        p,_=self.plant();q,_=self.plant('Other');request=str(uuid.uuid4())
        def send(profile,date='2026-10-08'):
            return self.client.post('/api/uploads',files={'image':('synthetic.png',self.image,'image/png')},
                data=dict(profile_id=profile['id'],date=date,request_id=request))
        first=send(p);self.assertEqual(first.status_code,201,first.text)
        a=first.json();again=send(p);self.assertEqual(again.status_code,201,again.text)
        self.assertEqual(again.json()['id'],a['id'])
        self.assertEqual(len(self.app.state.service.db.rows(self.app.state.service.db.analyses)),1)
        self.assertEqual(send(q).status_code,409);self.assertEqual(send(p,'2026-10-09').status_code,409)

    def test_prepare_preserves_normal_bytes_and_normalizes_exif_and_modes(self):
        r=self.client.post('/api/images/prepare',files={'image':('native.png',self.image,'image/png')})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.content,self.image)
        for mode in ['L','RGBA']:
            s=io.BytesIO();Image.new(mode,(120,100)).save(s,format='PNG')
            prepared=self.client.post('/api/images/prepare',files={'image':('mobile.png',s.getvalue(),'image/png')})
            self.assertEqual(prepared.status_code,200,prepared.text)
            with Image.open(io.BytesIO(prepared.content)) as image:self.assertEqual(image.mode,'RGB')
        raw=io.BytesIO();exif=Image.Exif();exif[274]=6
        Image.fromarray(self.rgb).save(raw,format='JPEG',exif=exif)
        prepared=self.client.post('/api/images/prepare',files={'image':('mobile.jpg',raw.getvalue(),'image/jpeg')})
        self.assertEqual(prepared.status_code,200,prepared.text)
        provenance=json.loads(prepared.headers['x-image-provenance'])
        self.assertEqual(provenance['original_exif_orientation'],6)
        self.assertEqual(provenance['original_sha256'],hashlib.sha256(raw.getvalue()).hexdigest())
        with Image.open(io.BytesIO(raw.getvalue())) as image:expected=np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
        with Image.open(io.BytesIO(prepared.content)) as image:self.assertTrue(np.array_equal(expected,np.asarray(image)))
        p,_=self.plant()
        a=self.client.post('/api/uploads',files={'image':('prepared.png',prepared.content,'image/png')},
            data=dict(profile_id=p['id'],date='2026-10-08',image_provenance=prepared.headers['x-image-provenance']))
        self.assertEqual(a.status_code,201,a.text)
        self.assertEqual(a.json()['image_provenance']['original_exif_orientation'],6)
        self.assertEqual(a.json()['image_size_px'],[100,120])

    def test_atomic_current_review_preserves_draft_and_retry_does_not_add_version(self):
        p,_=self.plant();a=self.analyze(p)
        mask=np.zeros((100,120),np.uint8);mask[15:45,20:55]=1
        body=dict(width=120,height=100,mask_base64=base64.b64encode(mask.tobytes()).decode(),
            instance_base64=base64.b64encode(mask.astype('<u2').tobytes()).decode(),instance_analysis_id=a['id'])
        draft=self.client.post('/api/analyses/'+a['id']+'/draft',json=body).json()
        r=self.calibrate(p,draft);self.assertEqual(r.status_code,201,r.text);saved=r.json()
        self.assertEqual(saved['plant']['pot_diameter_cm'],12)
        for key in ('mask_base64','instance_base64','instance_metadata','assets','status'):
            self.assertEqual(saved['analysis'][key],draft[key])
        self.assertAlmostEqual(saved['analysis']['effective_cm_per_pixel'],.12)
        before=copy.deepcopy(self.app.state.service.db.rows(self.app.state.service.db.calibrations))
        again=self.calibrate(p,draft);self.assertEqual(again.status_code,201,again.text)
        self.assertEqual(again.json()['analysis']['calibration_revision'],saved['analysis']['calibration_revision'])
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.calibrations),before)

    def test_wrong_plant_photo_stale_session_and_approved_review_never_mutate(self):
        p,_=self.plant();q,_=self.plant('Other');a=self.analyze(p)
        service=self.app.state.service
        before=copy.deepcopy(service.db.rows(service.db.profiles))
        for target,changes in [(q,{}),(p,{'image_sha256':'wrong'})]:
            r=self.calibrate(target,a,**changes);self.assertIn(r.status_code,[400,409],r.text)
        self.assertEqual(service.db.rows(service.db.profiles),before)
        newer=self.upload(p,date='2026-10-09').json()
        self.assertEqual(self.calibrate(p,a).status_code,409)
        self.assertEqual(service.db.rows(service.db.calibrations),[])
        r=self.calibrate(p,newer);self.assertEqual(r.status_code,201,r.text)
        old=self.profile(plant='Historical');approved=self.approve(self.analyze(old)).json()
        rows=copy.deepcopy(service.db.rows(service.db.measurements));original=copy.deepcopy(service.db.one(service.db.analyses,approved['analysis_id']))
        r=self.calibrate(old,dict(id=approved['analysis_id'],image_sha256=hashlib.sha256(self.image).hexdigest()))
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(service.db.rows(service.db.measurements),rows)
        self.assertEqual(service.db.one(service.db.analyses,approved['analysis_id']),original)

    def test_storage_failure_rolls_back_association_and_retry_succeeds(self):
        p,_=self.plant();a=self.analyze(p);s=self.app.state.service
        profiles=copy.deepcopy(s.db.rows(s.db.profiles));review=copy.deepcopy(s.db.one(s.db.analyses,a['id']))
        original=s.media.put;calls=[]
        def fails_second(*args,**kwargs):
            calls.append(args[0])
            if len(calls)==2:raise OSError('Synthetic unavailable storage')
            return original(*args,**kwargs)
        with patch.object(s.media,'put',side_effect=fails_second):
            r=self.calibrate(p,a);self.assertEqual(r.status_code,503,r.text)
        self.assertEqual(s.db.rows(s.db.profiles),profiles);self.assertEqual(s.db.one(s.db.analyses,a['id']),review)
        self.assertEqual(s.db.rows(s.db.calibrations),[])
        r=self.calibrate(p,a);self.assertEqual(r.status_code,201,r.text)

    def test_failure_before_transaction_commit_keeps_all_metadata_unchanged(self):
        p,_=self.plant();a=self.analyze(p);s=self.app.state.service
        before={name:copy.deepcopy(s.db.rows(getattr(s.db,name))) for name in ('profiles','analyses','calibrations','review_sessions')}
        begin=s.db.engine.begin
        @contextmanager
        def fail_commit():
            with begin() as conn:
                yield conn
                raise OSError('Synthetic commit failed before metadata became durable')
        with patch.object(s.db.engine,'begin',side_effect=fail_commit):
            r=self.calibrate(p,a);self.assertEqual(r.status_code,503,r.text)
        for name,rows in before.items():self.assertEqual(s.db.rows(getattr(s.db,name)),rows)
        self.assertEqual(self.calibrate(p,a).status_code,201)

    def test_recalibration_preserves_approved_history_assets_csv_and_old_versions(self):
        p=self.profile();a=self.analyze(p);approved=self.approve(a)
        self.assertEqual(approved.status_code,201,approved.text)
        s=self.app.state.service
        before={name:copy.deepcopy(s.db.rows(getattr(s.db,name))) for name in ('measurements','approved_masks','calibrations')}
        old_analysis=copy.deepcopy(s.db.one(s.db.analyses,a['id']))
        csv=self.client.get('/api/profiles/'+p['id']+'/export.csv').text
        assets={url:hashlib.sha256(self.client.get(url).content).hexdigest() for url in approved.json()['assets'].values()}
        current=self.analyze(p,date='2026-10-09')
        r=self.calibrate(p,current);self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(s.db.rows(s.db.measurements),before['measurements'])
        self.assertEqual(s.db.rows(s.db.approved_masks),before['approved_masks'])
        for cal in before['calibrations']:self.assertEqual(s.db.one(s.db.calibrations,cal['id']),cal)
        self.assertEqual(s.db.one(s.db.analyses,a['id']),old_analysis)
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/export.csv').text,csv)
        for url,digest in assets.items():self.assertEqual(hashlib.sha256(self.client.get(url).content).hexdigest(),digest)
        from src.webapp.service import Service
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        self.assertEqual(reopened.profile(p['id'])['pot_diameter_cm'],12)
        self.assertEqual(reopened.analysis(current['id'])['effective_cm_per_pixel'],.12)

    def test_old_retry_cannot_replace_newer_version_and_invalid_requests_leave_no_scale(self):
        p,_=self.plant();a=self.analyze(p);s=self.app.state.service
        for changes in ({'reviewed':'false'},{'diameter_cm':'0'},{'points':'null'},{'points':'[[0,0],[120,100]]'},{'points':'[[1,1],[1,1]]'}):
            response=self.calibrate(p,a,**changes);self.assertIn(response.status_code,[400,422],response.text)
        self.assertEqual(s.db.rows(s.db.calibrations),[])
        first=self.calibrate(p,a);self.assertEqual(first.status_code,201,first.text)
        second=self.calibrate(p,a,diameter_cm='15');self.assertEqual(second.status_code,201,second.text)
        before=copy.deepcopy(s.db.one(s.db.analyses,a['id']))
        self.assertEqual(self.calibrate(p,a).status_code,409)
        self.assertEqual(s.db.one(s.db.analyses,a['id']),before)
        self.assertEqual(s.profile(p['id'])['pot_diameter_cm'],15)

    def test_concurrent_same_request_creates_one_plant_and_one_upload(self):
        s=self.app.state.service;request=str(uuid.uuid4())
        body=dict(name='Parallel',species='lettuce',calibration_choice='none',request_id=request)
        def create(_):
            try:return s.create_plant(body)['id']
            except Exception as exc:return exc
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(create,range(2)))
        # Firestore contention is retryable, never a second plant/unique row.
        from src.webapp.service import Conflict
        self.assertTrue(all(isinstance(x,(str,Conflict)) for x in outcomes),repr(outcomes))
        p=s.create_plant(body);self.assertEqual(len(s.profiles()),1)
        from datetime import date
        def upload(_):
            try:return s.upload(p['id'],date(2026,10,8),False,self.image,'synthetic.png','image/png',request_id=request)['id']
            except Exception as exc:return exc
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(upload,range(2)))
        self.assertTrue(all(isinstance(x,(str,Conflict)) for x in outcomes),repr(outcomes))
        repeated=upload(0);self.assertIsInstance(repeated,str)
        self.assertEqual(len(s.db.rows(s.db.analyses)),1)
        a=s.analysis(repeated)
        def calibrate(_):
            try:return s.calibrate_plant_photo(p['id'],self.image,'synthetic.png','image/png',12,[[10,50],[110,50]],True,a['id'],a['image_sha256'])['calibration']['id']
            except Exception as exc:return exc
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(calibrate,range(2)))
        self.assertTrue(all(isinstance(x,(str,Conflict)) for x in outcomes),repr(outcomes))
        self.assertIsInstance(calibrate(0),str);self.assertEqual(len(s.db.rows(s.db.calibrations)),1)
        self.assertEqual(s.analysis(a['id'])['calibration_revision'],1)

    def test_analysis_retry_does_not_require_verified_scale(self):
        from src.webapp.service import CandidateUnavailable
        p=self.profile();a=self.upload(p,geometry=False).json();s=self.app.state.service
        with patch.object(s,'_candidate',side_effect=CandidateUnavailable('Synthetic model loading interrupted')):
            self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/analyze?engine=classical').status_code,503)
        r=self.client.post('/api/analyses/'+a['id']+'/analyze?engine=classical')
        self.assertEqual(r.status_code,200,r.text);self.assertIsNone(r.json()['total_projected_area_cm2'])
        self.assertFalse(r.json()['calibration_valid'])
        self.assertEqual(s.pending_review(p['id'])[0]['id'],a['id'])


@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Requires local Firestore emulator')
class TestFirestoreMobileCalibration(TestMobileCalibrationBackend):
    from tests.test_firestore_web import setup as setUp
