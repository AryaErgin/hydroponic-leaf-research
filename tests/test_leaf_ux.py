import base64,csv,io,os,unittest
import numpy as np
from src.webapp import leaf_instances as e
from tests import test_webapp as f


class TestSplitUX(unittest.TestCase):
    def labels(self):
        l=np.zeros((40,60),np.uint16);l[5:35,5:40]=1;l[5:20,45:55]=8
        return l
    def test_split_deterministic_exact_count_and_native_coverage(self):
        l=self.labels();cut=[[22,2],[22,37]]
        a=e.split_instance(l,1,cut);b=e.split_instance(l,1,cut)
        np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(a>0,l>0)
        np.testing.assert_array_equal(a[l==8],l[l==8])
        self.assertEqual(len(np.unique(a[a>0])),3)
    def test_short_cut_explains_no_crossing(self):
        with self.assertRaisesRegex(ValueError,'Cut did not fully cross'):
            e.split_instance(self.labels(),1,[[22,10],[22,15]])
    def test_preexisting_disconnected_piece_is_not_a_successful_cut(self):
        l=self.labels();l[38,12]=1
        with self.assertRaisesRegex(ValueError,'disconnected'):
            e.split_instance(l,1,[[20,10],[20,15]])
    def test_more_than_two_cut_regions_rejected_without_mutation(self):
        l=self.labels();before=l.copy()
        with self.assertRaisesRegex(ValueError,'fragment|exactly two'):
            e.split_instance(l,1,[[15,2],[15,37],[30,37],[30,2]])
        np.testing.assert_array_equal(l,before)
    def test_tiny_fragment_explained(self):
        with self.assertRaisesRegex(ValueError,'invalid fragment'):
            e.split_instance(self.labels(),1,[[4,8],[8,4]])
    def test_median_scaled_pixel_and_incomplete(self):
        mask=np.ones((1,12),bool);labels=np.array([[1]*2+[2]*4+[3]*6],np.uint16)
        a=e.summary(labels,mask,.1,True)
        self.assertEqual(a['median_leaf_pixel_area'],4);self.assertAlmostEqual(a['median_leaf_area_cm2'],.04)
        self.assertEqual(e.summary(labels,mask,None)['median_leaf_area'],4)
        labels[0,0]=0;self.assertIsNone(e.summary(labels,mask,.1)['median_leaf_area_cm2'])


class TestUXWeb(unittest.TestCase):
    setUp=f.TestWebMVP.setUp
    profile=f.TestWebMVP.profile
    upload=f.TestWebMVP.upload
    analyze=f.TestWebMVP.analyze
    def body(self,a):
        return dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'],instances_reviewed=True,approval='APPROVE',review=dict.fromkeys(f.KEYS,True))
    def test_approval_median_history_pending_csv_reopen_and_plant_isolation(self):
        from src.webapp.service import Service
        p=self.profile();other=self.profile(plant='other');a=self.analyze(p);body=self.body(a)
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text);m=r.json()
        self.assertAlmostEqual(m['median_leaf_area_cm2'],7.5)
        h=self.client.get('/api/profiles/'+p['id']+'/history').json()['measurements']
        self.assertEqual(h[0]['id'],m['id']);self.assertEqual(h[0]['captured_at'],a['date'])
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/analyses').json(),[])
        self.assertEqual(self.client.get('/api/profiles/'+other['id']+'/history').json()['measurements'],[])
        rows=list(csv.DictReader(io.StringIO(self.client.get('/api/profiles/'+p['id']+'/export.csv').text)))
        for key in ['leaf_count','total_leaf_area','mean_leaf_area','median_leaf_area','median_leaf_area_cm2','per_leaf_pixel_area','leaf_instances','captured_at']:self.assertTrue(rows[0][key],key)
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        self.assertAlmostEqual(reopened.history(p['id'])['measurements'][0]['median_leaf_area_cm2'],7.5)
        self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/approve',json=body).status_code,409)
    def test_instance_payload_cannot_bypass_complete_review(self):
        a=self.analyze(self.profile());body=self.body(a);body['instances_reviewed']=False
        self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/approve',json=body).status_code,400)
    def test_exact_unassigned_reason_and_no_measurement_on_failure(self):
        a=self.analyze(self.profile());body=self.body(a);raw=bytearray(base64.b64decode(body['instance_base64']))
        raw[(20*120+30)*2:(20*120+30)*2+2]=b'\0\0';body['instance_base64']=base64.b64encode(raw).decode()
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,400);self.assertIn('1 lamina pixels are unassigned',r.json()['detail'])
        self.assertEqual(self.app.state.service.db.rows(self.app.state.service.db.measurements),[])
    def test_backend_add_requires_explicit_painted_region_for_transfer(self):
        a=self.analyze(self.profile());body=self.body(a)
        labels=np.frombuffer(base64.b64decode(body['instance_base64']),dtype='<u2').copy()
        labels[20*120+30]=2;body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2])])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body)
        self.assertEqual(r.status_code,400);self.assertIn('painted',r.json()['detail'])
    def test_backend_assignment_operation_cannot_change_total_mask(self):
        a=self.analyze(self.profile());body=self.body(a)
        mask=bytearray(base64.b64decode(body['mask_base64']));labels=bytearray(base64.b64decode(body['instance_base64']));i=20*120+30;mask[i]=0;labels[i*2:i*2+2]=b'\0\0'
        body.update(mask_base64=base64.b64encode(mask).decode(),instance_base64=base64.b64encode(labels).decode(),instance_edits=[dict(operation='reassign',leaf_ids=[1])])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body)
        self.assertEqual(r.status_code,400);self.assertIn('paint/erase',r.json()['detail'])
    def test_mismatched_calibration_withholds_cm2_and_blocks_backend_approval(self):
        from PIL import Image
        p=self.profile();image=io.BytesIO();Image.fromarray(self.rgb).resize((100,90)).save(image,format='PNG')
        r=self.upload(p,image=image.getvalue());self.assertEqual(r.status_code,201,r.text)
        r=self.client.post('/api/analyses/'+r.json()['id']+'/analyze',params={'engine':'classical'});self.assertEqual(r.status_code,200,r.text);a=r.json()
        self.assertFalse(a['calibration_valid']);self.assertIsNone(a['total_projected_area_cm2'])
        self.assertEqual(a['leaf_area_unit'],'pixels');self.assertIn('dimensions',a['calibration_issue'])
        body=self.body(a);body.update(width=100,height=90)
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,400);self.assertIn('changed aspect/framing',r.json()['detail'])


from tests import test_firestore_web as cloud
@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Requires local Firestore emulator')
class TestFirestoreUX(TestUXWeb):
    setUp=cloud.setup
