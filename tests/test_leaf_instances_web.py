"""Isolated synthetic fixtures; no real biological approvals."""
import base64
import io
import json
import unittest
import numpy as np
from PIL import Image
from tests import test_webapp as fixtures
KEYS=fixtures.KEYS

class TestLeafWorkflow(unittest.TestCase):
    setUp = fixtures.TestWebMVP.setUp
    profile = fixtures.TestWebMVP.profile
    upload = fixtures.TestWebMVP.upload
    analyze = fixtures.TestWebMVP.analyze

    def test_candidate_inside_mask_and_never_final(self):
        p=self.profile();a=self.analyze(p)
        self.assertIn('leaf_count',a)
        self.assertEqual(a['leaf_count'],1)
        self.assertEqual(a['per_leaf_pixel_area'],[750])
        self.assertAlmostEqual(a['total_projected_area_cm2'],7.5)
        self.assertTrue(a['human_approval_required'])
        self.assertEqual(self.app.state.service.history(p['id'])['measurements'],[])
        labels=np.frombuffer(base64.b64decode(a['instance_base64']),dtype='<u2').reshape(100,120)
        mask=np.frombuffer(base64.b64decode(a['mask_base64']),dtype=np.uint8).reshape(100,120)
        np.testing.assert_array_equal(labels>0,mask>0)

    def test_reviewed_instances_persist_and_plants_are_isolated(self):
        p=self.profile();other=self.profile(plant='other');a=self.analyze(p)
        labels=np.zeros((100,120),dtype='<u2');labels[20:45,30:45]=1;labels[20:45,45:60]=2
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],
            instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_analysis_id=a['id'])
        d=self.client.post('/api/analyses/'+a['id']+'/draft',json=body)
        self.assertEqual(d.status_code,200,d.text)
        self.assertEqual(d.json()['per_leaf_pixel_area'],[375,375])
        self.assertEqual(self.client.get('/api/analyses/'+a['id']).json()['instance_base64'],body['instance_base64'])
        body.update(approval='APPROVE',review=dict.fromkeys(KEYS,True),instances_reviewed=True)
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text);m=r.json()
        self.assertEqual(m['leaf_count'],2);self.assertAlmostEqual(m['mean_leaf_area_cm2'],3.75)
        self.assertEqual(sum(m['per_leaf_pixel_area']),m['pixel_area'])
        self.assertAlmostEqual(sum(m['per_leaf_projected_area_cm2']),m['projected_area_cm2'])
        self.assertTrue(m['instance_provenance']['edited'])
        self.assertEqual(m['instance_provenance']['corrected_leaf_ids'],[1,2])
        raw=self.client.get(m['assets']['leaf_instances']).content
        np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(raw))),labels)
        provenance=self.client.get(m['assets']['provenance']).json()
        self.assertEqual(provenance['leaf_count'],2)
        saved=self.client.get('/api/analyses/'+a['id']).json()
        self.assertEqual(saved['instance_base64'],body['instance_base64'])
        self.assertEqual(self.app.state.service.history(other['id'])['measurements'],[])
        self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/draft',json=body).status_code,409)
        from src.webapp.service import Service
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        self.assertEqual(reopened.history(p['id'])['measurements'][0]['leaf_count'],2)

    def test_pixels_only_and_total_only_compatibility(self):
        p=self.profile(calibrated=False);a=self.analyze(p)
        self.assertIsNone(a['total_projected_area_cm2']);self.assertIsNone(a['mean_leaf_area_cm2'])
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],approval='APPROVE',review=dict.fromkeys(KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text);self.assertEqual(r.json()['pixel_area'],750)
        self.assertIsNone(r.json()['leaf_count'])
        self.assertEqual(r.json()['leaf_measurement_status'],'NOT_REVIEWED')

    def test_invalid_outside_unassigned_and_cross_analysis_labels_rejected(self):
        p=self.profile();a=self.analyze(p)
        labels=np.zeros((100,120),dtype='<u2');labels[20:45,30:60]=1
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_analysis_id=a['id'],approval='APPROVE',review=dict.fromkeys(KEYS,True),instances_reviewed=True)
        for kind in ['outside','unassigned','wrong_analysis','bad_encoding','too_large_id']:
            b=dict(body);l=labels.copy()
            if kind=='outside':l[0,0]=2
            if kind=='unassigned':l[20,30]=0
            if kind=='wrong_analysis':b['instance_analysis_id']='other-analysis'
            if kind=='too_large_id':l[20,30]=65535
            b['instance_base64']='?' if kind=='bad_encoding' else base64.b64encode(l.tobytes()).decode()
            r=self.client.post('/api/analyses/'+a['id']+'/approve',json=b)
            self.assertEqual(r.status_code,400,(kind,r.text))
        self.assertEqual(self.app.state.service.history(p['id'])['measurements'],[])

    def test_split_route_keeps_total_and_other_leaves_and_stays_unapproved(self):
        a=self.analyze(self.profile());body=dict(width=120,height=100,mask_base64=a['mask_base64'],
            instance_base64=a['instance_base64'],instance_analysis_id=a['id'],leaf_id=1,points=[[45,10],[45,60]])
        r=self.client.post('/api/analyses/'+a['id']+'/split-leaf',json=body)
        self.assertEqual(r.status_code,200,r.text);s=r.json()
        self.assertEqual(s['leaf_count'],2);self.assertEqual(sum(s['per_leaf_pixel_area']),750)
        self.assertEqual(self.client.get('/api/analyses/'+a['id']).json()['instance_base64'],a['instance_base64'])
        self.assertEqual(self.app.state.service.history(a['profile_id'])['measurements'],[])

    def test_reviewed_pixel_areas_without_scale_are_final_only_after_approve(self):
        p=self.profile(calibrated=False);a=self.analyze(p)
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],
            instance_analysis_id=a['id'],instances_reviewed=True,approval='APPROVE',review=dict.fromkeys(KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text);m=r.json()
        self.assertEqual(m['leaf_count'],1);self.assertEqual(m['per_leaf_pixel_area'],[750])
        self.assertEqual(m['per_leaf_projected_area_cm2'],[None]);self.assertIsNone(m['total_projected_area_cm2'])
        self.assertIsNone(m['mean_leaf_area_cm2']);self.assertEqual(m['mean_leaf_pixel_area'],750)

    def test_total_edit_draft_preserves_surviving_ids_and_allows_unassigned_until_review(self):
        a=self.analyze(self.profile());mask=np.frombuffer(base64.b64decode(a['mask_base64']),dtype=np.uint8).reshape(100,120).copy()
        mask[20,30]=0;mask[50,50]=1
        body=dict(width=120,height=100,mask_base64=base64.b64encode(mask.tobytes()).decode())
        d=self.client.post('/api/analyses/'+a['id']+'/draft',json=body)
        self.assertEqual(d.status_code,200,d.text);self.assertEqual(d.json()['unassigned_pixel_area'],1)
        labels=np.frombuffer(base64.b64decode(d.json()['instance_base64']),dtype='<u2').reshape(100,120)
        self.assertEqual(labels[20,30],0);self.assertEqual(labels[20,31],1);self.assertEqual(labels[50,50],0)

    def test_historical_total_only_approval_does_not_invent_final_leaf_counts(self):
        from sqlalchemy import update
        p=self.profile();a=self.analyze(p)
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],approval='APPROVE',review=dict.fromkeys(KEYS,True))
        m=self.client.post('/api/analyses/'+a['id']+'/approve',json=body).json()
        db=self.app.state.service.db;raw=db.one(db.measurements,m['id'])['payload']
        for key in list(raw):
            if key.startswith(('leaf_','per_leaf_','mean_leaf_','instance_')) or key in ('leaves','instances_reviewed'):raw.pop(key)
        for key in ['leaf_instances','leaf_instance_overlay','leaf_table']:raw['assets'].pop(key,None)
        with db.engine.begin() as conn:conn.execute(update(db.measurements).where(db.measurements.c.id==m['id']).values(payload=raw))
        saved=self.client.get('/api/analyses/'+a['id']).json()
        self.assertIsNone(saved['leaf_count']);self.assertEqual(saved['leaf_measurement_status'],'NOT_REVIEWED')
        self.assertEqual(saved['leaves'],[]);self.assertFalse(saved['human_approval_required'])

    def test_csv_retains_reviewed_leaf_metrics_and_label_asset(self):
        import csv
        p=self.profile();a=self.analyze(p)
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],
            instance_analysis_id=a['id'],instances_reviewed=True,approval='APPROVE',review=dict.fromkeys(KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(r.status_code,201,r.text)
        rows=list(csv.DictReader(io.StringIO(self.client.get('/api/profiles/'+p['id']+'/export.csv').text)))
        self.assertEqual(rows[0]['leaf_count'],'1');self.assertEqual(json.loads(rows[0]['per_leaf_pixel_area']),[750])
        self.assertAlmostEqual(float(rows[0]['mean_leaf_area_cm2']),7.5)
        self.assertTrue(rows[0]['leaf_instances'].endswith('/leaf_instances.png'))

import os
from tests import test_firestore_web as firestore_fixtures
@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Requires local Firestore emulator')
class TestFirestoreLeafWorkflow(TestLeafWorkflow):
    setUp=firestore_fixtures.setup

if __name__=='__main__':unittest.main()
