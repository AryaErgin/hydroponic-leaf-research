import base64
import csv
import io
import json
import unittest

import numpy as np
from PIL import Image
from sqlalchemy import update
from tests import test_webapp as web
from tests.test_calibration_reuse import encoded, fixture


class TestCalibrationReuseWeb(unittest.TestCase):
    setUp=web.TestWebMVP.setUp
    profile=web.TestWebMVP.profile
    upload=web.TestWebMVP.upload
    analyze=web.TestWebMVP.analyze
    approve=web.TestWebMVP.approve

    def resized(self):return encoded(Image.fromarray(self.rgb).resize((60,50)))

    def confirmation(self,a,**changes):
        return dict(image_sha256=a['image_sha256'],calibration_id=a['calibration']['id'],same_physical_pot=True,
            same_camera_geometry=True,full_frame_resize_only=True,**changes)

    def test_unverified_resize_withholds_cm_and_explicit_confirmation_preserves_draft(self):
        p=self.profile();a=self.upload(p,image=self.resized()).json()
        self.assertEqual(a['calibration_reuse']['status'],'CONFIRMATION_REQUIRED')
        self.assertIsNone(a['effective_cm_per_pixel'])
        a=self.client.post('/api/analyses/'+a['id']+'/analyze?engine=classical').json()
        self.assertIsNone(a['total_projected_area_cm2'])
        mask=np.ones((50,60),np.uint8);labels=np.ones((50,60),dtype='<u2')
        body=dict(width=60,height=50,mask_base64=base64.b64encode(mask.tobytes()).decode(),
            instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_analysis_id=a['id'])
        draft=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(draft.status_code,200,draft.text)
        before=draft.json()
        confirm=self.client.post('/api/analyses/'+a['id']+'/confirm-calibration',json=self.confirmation(a))
        self.assertEqual(confirm.status_code,200,confirm.text);saved=confirm.json()
        self.assertEqual(saved['mask_base64'],before['mask_base64']);self.assertEqual(saved['instance_base64'],before['instance_base64'])
        self.assertEqual(saved['instance_metadata'],before['instance_metadata'])
        self.assertAlmostEqual(saved['effective_cm_per_pixel'],.2);self.assertAlmostEqual(saved['total_projected_area_cm2'],120)
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/history').json()['measurements'],[])
        request={**body,'approval':'APPROVE','review':dict.fromkeys(web.KEYS,True),'instances_reviewed':True,'instance_metadata':saved['instance_metadata'],'calibration_revision':saved['calibration_revision']}
        response=self.client.post('/api/analyses/'+a['id']+'/approve',json=request);self.assertEqual(response.status_code,201,response.text)
        m=response.json();self.assertAlmostEqual(m['projected_area_cm2'],120);self.assertAlmostEqual(m['cm_per_pixel'],.2)
        self.assertEqual(m['calibration']['cm_per_pixel'],.1);self.assertEqual(m['calibration_reuse']['status'],'CONFIRMED')
        self.assertEqual(m['calibration_id'],p['calibration']['id'])
        reread=self.client.get('/api/analyses/'+a['id']).json();self.assertEqual(reread['effective_cm_per_pixel'],.2)
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/history').json()['measurements'][0]['calibration_reuse'],m['calibration_reuse'])
        exported=list(csv.DictReader(io.StringIO(self.client.get('/api/profiles/'+p['id']+'/export.csv').text)))[0]
        self.assertAlmostEqual(float(exported['projected_area_cm2']),120);self.assertEqual(json.loads(exported['calibration_reuse'])['status'],'CONFIRMED')
        self.assertEqual(self.app.state.service.profile(p['id'])['calibration'],p['calibration'])
        self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/confirm-calibration',json=self.confirmation(a)).status_code,409)

    def test_stale_incomplete_confirmation_and_changed_geometry_cannot_bypass(self):
        p=self.profile();a=self.upload(p,image=self.resized()).json();url='/api/analyses/'+a['id']+'/confirm-calibration'
        valid=self.confirmation(a)
        for changes in ({'image_sha256':'wrong'},{'calibration_id':'wrong'},{'same_physical_pot':False},{'same_camera_geometry':'true'},{'full_frame_resize_only':False}):
            response=self.client.post(url,json={**valid,**changes});self.assertEqual(response.status_code,400,response.text)
        other=self.upload(p,image=self.resized(),geometry=False).json()
        self.assertEqual(self.client.post('/api/analyses/'+other['id']+'/confirm-calibration',json=self.confirmation(other)).status_code,400)
        crop=encoded(Image.new('RGB',(60,45),'green'));other=self.upload(p,image=crop).json()
        self.assertEqual(self.client.post('/api/analyses/'+other['id']+'/confirm-calibration',json=self.confirmation(other)).status_code,400)
        self.assertEqual(self.app.state.service.profile(p['id'])['calibration'],p['calibration'])

    def test_imported_reference_auto_resize_and_native_frame_persistence(self):
        image,raw,_=fixture();self.settings.max_image_pixels=300000;self.settings.max_upload_bytes=1000000
        points=[[60,200],[240,200]]
        cal=self.client.post('/api/calibrations/two-point',files={'image':('reference.png',raw,'image/png')},
            data={'diameter_cm':'5.82','points':json.dumps(points),'reviewed':'true'}).json()
        self.assertEqual(cal['reference_frame']['size_px'],[300,400])
        p=self.profile(calibrated=False);self.client.put('/api/profiles/'+p['id']+'/calibration',json={'calibration_id':cal['id']})
        p=self.app.state.service.profile(p['id'])
        a=self.upload(p,image=encoded(image.resize((384,512),Image.Resampling.LANCZOS))).json()
        self.assertEqual(a['calibration_reuse']['status'],'VERIFIED',a)
        self.assertAlmostEqual(a['effective_cm_per_pixel'],cal['cm_per_pixel']/1.28)
        self.assertEqual(self.client.get(a['calibration_reference_url']).content,raw)

    def test_legacy_pending_review_reassessed_without_rewriting_original(self):
        p=self.profile();a=self.upload(p,image=self.resized()).json();s=self.app.state.service
        row=s.db.one(s.db.analyses,a['id']);old=dict(row['payload']);old.pop('calibration_reuse')
        with s.db.engine.begin() as conn:conn.execute(update(s.db.analyses).where(s.db.analyses.c.id==a['id']).values(payload=old))
        self.assertEqual(self.client.get('/api/analyses/'+a['id']).json()['calibration_reuse']['status'],'CONFIRMATION_REQUIRED')
        self.assertNotIn('calibration_reuse',s.db.one(s.db.analyses,a['id'])['payload'])

    def test_stale_calibration_approval_is_rejected_and_geometry_change_revokes_scale(self):
        p=self.profile();a=self.analyze(p)
        cal=self.client.post('/api/calibrations/two-point',files={'image':('same.png',self.image,'image/png')},
            data={'diameter_cm':'20','points':'[[10,50],[110,50]]','reviewed':'true'}).json()
        url='/api/analyses/'+a['id']
        rebound=self.client.post(url+'/calibration',json=dict(calibration_id=cal['id'],image_sha256=a['image_sha256']))
        self.assertEqual(rebound.status_code,200,rebound.text)
        stale=self.approve(a)
        self.assertEqual(stale.status_code,409,stale.text)
        self.assertIn('calibration changed',stale.json()['detail'].lower())
        changed=self.client.post(url+'/geometry-changed',json={})
        self.assertEqual(changed.status_code,200,changed.text);state=changed.json()
        self.assertIsNone(state['effective_cm_per_pixel']);self.assertFalse(state['calibration_valid'])
        self.assertEqual(state['mask_base64'],a['mask_base64'])
        rejected=self.approve(a,calibration_revision=state['calibration_revision'])
        self.assertEqual(rejected.status_code,400,rejected.text)

    def test_missing_cloud_reference_withholds_scale(self):
        from google.api_core.exceptions import NotFound
        from unittest.mock import patch
        image,raw,_=fixture();self.settings.max_image_pixels=300000;self.settings.max_upload_bytes=1000000
        cal=self.client.post('/api/calibrations/two-point',files={'image':('reference.png',raw,'image/png')},
            data={'diameter_cm':'5.82','points':'[[60,200],[240,200]]','reviewed':'true'}).json()
        p=self.profile(calibrated=False);self.client.put('/api/profiles/'+p['id']+'/calibration',json={'calibration_id':cal['id']})
        media=self.app.state.service.media;read=media.read
        def missing(ref):
            if ref==cal['assets']['reference_image']:raise NotFound('Reference missing')
            return read(ref)
        with patch.object(media,'read',side_effect=missing):
            a=self.upload(p,image=encoded(image.resize((384,512)))).json()
        self.assertEqual(a['calibration_reuse']['status'],'CONFIRMATION_REQUIRED');self.assertIsNone(a['effective_cm_per_pixel'])

    def test_cross_resolution_growth_and_existing_record_immutability(self):
        p=self.profile();first=self.analyze(p);saved=self.approve(first).json();service=self.app.state.service
        original=service.db.one(service.db.measurements,saved['id'])['payload']
        a=self.upload(p,date='2026-10-09',image=self.resized()).json()
        a=self.client.post('/api/analyses/'+a['id']+'/analyze?engine=classical').json()
        a=self.client.post('/api/analyses/'+a['id']+'/confirm-calibration',json=self.confirmation(a)).json()
        mask=np.zeros((50,60),np.uint8);mask.reshape(-1)[:25]=1;labels=mask.astype('<u2')
        body=dict(width=60,height=50,mask_base64=base64.b64encode(mask.tobytes()).decode(),
            instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_analysis_id=a['id'],
            approval='APPROVE',review=dict.fromkeys(web.KEYS,True),instances_reviewed=True,calibration_revision=a['calibration_revision'])
        result=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(result.status_code,201,result.text)
        self.assertAlmostEqual(result.json()['projected_area_cm2'],saved['projected_area_cm2'])
        growth=self.client.get('/api/profiles/'+p['id']+'/history').json()['growth']
        self.assertIsNone(growth['reason']);self.assertAlmostEqual(growth['absolute_growth_cm2'],0)
        self.assertEqual(service.db.one(service.db.measurements,saved['id'])['payload'],original)
        from src.webapp.service import Service
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        self.assertEqual(reopened.analysis(a['id'])['effective_cm_per_pixel'],.2)
        self.assertEqual(reopened.measurement(result.json()['id'])['calibration_reuse']['status'],'CONFIRMED')

    def test_recalibration_is_versioned_and_never_retroactively_changes_approved_history(self):
        from src.webapp.app import create_app
        from fastapi.testclient import TestClient
        from hashlib import sha256
        p=self.profile();legacy=self.analyze(p)
        first=self.approve(legacy);self.assertEqual(first.status_code,201,first.text)
        newer=self.analyze(p,date='2026-10-09')
        mask=np.zeros((100,120),np.uint8);mask.reshape(-1)[:100]=1
        second=self.approve(newer,instance_base64=base64.b64encode(mask.astype('<u2').tobytes()).decode(),
            instance_analysis_id=newer['id'],instances_reviewed=True)
        self.assertEqual(second.status_code,201,second.text)
        service=self.app.state.service
        before_rows=service.db.rows(service.db.measurements)
        old_cal=service.db.one(service.db.calibrations,p['calibration']['id'])
        def views(client):
            history=client.get('/api/profiles/'+p['id']+'/history').json()
            measurements={m['id']:client.get('/api/measurements/'+m['id']).json() for m in history['measurements']}
            analyses={m['analysis_id']:client.get('/api/analyses/'+m['analysis_id']).json() for m in history['measurements']}
            artifacts={m['id']:{k:sha256(client.get(url).content).hexdigest() for k,url in m['assets'].items()} for m in measurements.values()}
            return dict(history=history,measurements=measurements,analyses=analyses,artifacts=artifacts,
                csv=client.get('/api/profiles/'+p['id']+'/export.csv').text)
        before=views(self.client);seen={p['calibration']['id']};version_analyses=[]
        for scale,date in [(.2,'2026-10-10'),(.3,'2026-10-11')]:
            calibration=self.client.post('/api/calibrations/two-point',
                files={'image':('SYNTHETIC-reference.png',self.image,'image/png')},
                data={'diameter_cm':str(scale*100),'points':'[[10,50],[110,50]]','reviewed':'true'})
            self.assertEqual(calibration.status_code,201,calibration.text)
            response=self.client.put('/api/plants/'+p['id']+'/calibration',json={'calibration_id':calibration.json()['id']})
            self.assertEqual(response.status_code,200,response.text);updated=response.json()
            self.assertNotIn(updated['calibration']['id'],seen);seen.add(updated['calibration']['id'])
            current=self.analyze(p,date=date)
            self.assertEqual(current['calibration']['id'],updated['calibration']['id'])
            self.assertAlmostEqual(current['effective_cm_per_pixel'],scale)
            self.assertAlmostEqual(current['total_projected_area_cm2'],current['total_pixel_area']*scale**2)
            version_analyses.append((current['id'],updated['calibration']['id'],scale))
            self.assertEqual(views(self.client),before)
            self.assertEqual(service.db.rows(service.db.measurements),before_rows)
            self.assertEqual(service.db.one(service.db.calibrations,old_cal['id']),old_cal)
        for aid,cid,scale in version_analyses:
            a=self.client.get('/api/analyses/'+aid).json()
            self.assertEqual(a['calibration']['id'],cid);self.assertEqual(a['effective_cm_per_pixel'],scale)
        with TestClient(create_app(self.settings),client=('127.0.0.1',50000)) as reopened:
            self.assertEqual(views(reopened),before)
            for aid,cid,scale in version_analyses:
                a=reopened.get('/api/analyses/'+aid).json()
                self.assertEqual(a['calibration']['id'],cid);self.assertEqual(a['effective_cm_per_pixel'],scale)
        self.assertEqual(service.db.one(service.db.calibrations,old_cal['id']),old_cal)
