import base64,csv,io
import unittest
from tests import test_leaf_instances_web as fixtures
KEYS=fixtures.KEYS
from src.webapp.lamina import TARGET


class TestLaminaWorkflow(unittest.TestCase):
    setUp=fixtures.TestLeafWorkflow.setUp
    profile=fixtures.TestLeafWorkflow.profile
    upload=fixtures.TestLeafWorkflow.upload
    analyze=fixtures.TestLeafWorkflow.analyze

    def test_original_foreground_preserved_and_target_explicit(self):
        a=self.analyze(self.profile());s=self.app.state.service
        self.assertEqual(a['measurement_target'],TARGET)
        self.assertEqual(a['area_definition'],'visible_projected_leaf_lamina')
        self.assertAlmostEqual(a['total_leaf_area'],7.5)
        self.assertAlmostEqual(a['mean_leaf_area'],7.5)
        raw=s.db.one(s.db.analyses,a['id'])['payload']
        self.assertEqual(int(s._mask(raw['assets']['plant_foreground_mask']).sum()),750)
        self.assertEqual(a['lamina_refinement']['excluded_pixel_area'],0)
        self.assertTrue(a['human_approval_required'])

    def test_approved_lamina_target_assets_and_csv_survive_reopen(self):
        from src.webapp.service import Service
        p=self.profile();a=self.analyze(p)
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'],instances_reviewed=True,approval='APPROVE',review=dict.fromkeys(KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text);m=r.json()
        self.assertEqual(m['measurement_target'],TARGET)
        self.assertEqual(m['total_leaf_area'],m['projected_area_cm2'])
        self.assertIn('plant_foreground_mask',m['assets'])
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        saved=reopened.history(p['id'])['measurements'][0]
        self.assertAlmostEqual(saved['total_leaf_area'],7.5)
        self.assertEqual(saved['instance_provenance']['lamina_refinement']['method_version'],'geometry-lamina-conservative-v1')
        rows=list(csv.DictReader(io.StringIO(reopened.export(p['id']))))
        self.assertEqual(rows[0]['measurement_target'],TARGET)
        self.assertEqual(rows[0]['leaf_area_unit'],'cm2')
        self.assertTrue(rows[0]['plant_foreground_mask'])
        canonical=reopened.db.rows(reopened.db.approved_masks)[0]['payload']
        self.assertFalse(canonical['new_real_eligible'])
        self.assertEqual(canonical['measurement_target'],TARGET)

    def test_pixels_only_lamina_units(self):
        a=self.analyze(self.profile(calibrated=False))
        self.assertEqual(a['total_leaf_area'],750)
        self.assertEqual(a['mean_leaf_area'],750)
        self.assertEqual(a['leaf_area_unit'],'pixels')
        self.assertIsNone(a['mean_leaf_area_cm2'])

    def test_connector_removed_in_api_and_original_preserved_through_approval(self):
        import numpy as np
        from unittest.mock import patch
        y,x=np.indices((100,120));blade=((x-60)/20)**2+((y-35)/22)**2<=1
        parent=blade.copy();parent[55:95,59:62]=True
        s=self.app.state.service;p=self.profile()
        with patch.object(s,'_candidate',return_value=(dict(mask=parent,metadata=dict(qc_status='SYNTHETIC_GEOMETRY')),{})):
            a=self.analyze(p)
        m=np.frombuffer(base64.b64decode(a['mask_base64']),np.uint8).reshape(parent.shape)>0
        self.assertTrue(m[blade].all());self.assertFalse(m[75:90,59:62].any())
        self.assertLess(a['total_pixel_area'],int(parent.sum()))
        self.assertEqual(sum(a['per_leaf_pixel_area']),int(m.sum()))
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'],instances_reviewed=True,approval='APPROVE',review=dict.fromkeys(KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text);record=r.json()
        raw=s.db.one(s.db.measurements,record['id'])['payload']
        np.testing.assert_array_equal(s._mask(raw['assets']['plant_foreground_mask']),parent)
        np.testing.assert_array_equal(s._mask(raw['assets']['approved_foreground_exclusion_mask']),parent&~m)
        self.assertEqual(record['pixel_area'],int(m.sum()))
        self.assertAlmostEqual(record['projected_area_cm2'],float(m.sum())*.01)
        self.assertEqual(record['annotation_provenance']['approved_lamina_partition']['excluded_foreground_pixels'],int((parent&~m).sum()))
