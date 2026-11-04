import base64,json,os,unittest
import numpy as np
from tests import test_webapp as fixtures

class TestAddTransfer(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    upload=fixtures.TestWebMVP.upload
    analyze=fixtures.TestWebMVP.analyze
    def setup_labels(self):
        a=self.analyze(self.profile());labels=np.frombuffer(base64.b64decode(a['instance_base64']),'<u2').copy()
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'])
        return a,labels,body
    def test_explicit_owned_region_transfer_persists_without_mask_change(self):
        a,labels,body=self.setup_labels();index=20*120+30;labels[index:index+3]=2
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=[[index,3]],selected_pixel_count=3)])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text);d=r.json()
        self.assertEqual(d['per_leaf_pixel_area'],[747,3]);self.assertEqual(d['per_leaf_display_numbers'],[1,2]);self.assertEqual(d['mask_base64'],a['mask_base64']);self.assertEqual(d['unassigned_pixel_area'],0)
        self.assertEqual(d['leaves'][0]['internal_instance_id'],a['leaves'][0]['internal_instance_id'])
        self.assertEqual(d['instance_provenance']['edit_events'][-1]['painted_runs'],[[index,3]])
        again=self.client.get('/api/analyses/'+a['id']).json();self.assertEqual(again['instance_metadata'],d['instance_metadata']);self.assertTrue(again['human_approval_required'])
    def test_add_cannot_change_an_unpainted_neighbor(self):
        a,labels,body=self.setup_labels();index=20*120+30;labels[index:index+4]=2
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=[[index,3]],selected_pixel_count=3)])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400);self.assertIn('painted',r.json()['detail'])
    def test_add_selection_outside_lamina_is_rejected(self):
        a,labels,body=self.setup_labels();body.update(instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=[[0,1]],selected_pixel_count=1)])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400);self.assertIn('lamina',r.json()['detail'])
    def expansion(self,indices=(0,1,2)):
        a,labels,body=self.setup_labels();mask=np.frombuffer(base64.b64decode(body['mask_base64']),np.uint8).copy()
        selected=list(indices);mask[selected]=1;labels.ravel()[selected]=2
        runs=[]
        for i in selected:
            if runs and runs[-1][0]+runs[-1][1]==i:runs[-1][1]+=1
            else:runs.append([i,1])
        body.update(mask_base64=base64.b64encode(mask.tobytes()).decode(),instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=runs,selected_pixel_count=len(selected))])
        return a,labels,mask,body
    def test_add_missing_whole_leaf_expands_lamina_and_persists_review_metrics(self):
        a,labels,mask,body=self.expansion();r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body)
        self.assertEqual(r.status_code,200,r.text);d=r.json()
        self.assertEqual(d['per_leaf_pixel_area'],[750,3]);self.assertEqual(d['total_pixel_area'],753);self.assertEqual(d['unassigned_pixel_area'],0)
        self.assertEqual(d['instance_provenance']['edit_events'][-1]['added_lamina_pixel_count'],3)
        self.assertEqual(self.client.get('/api/analyses/'+a['id']).json()['mask_base64'],d['mask_base64'])
        body.update(instance_metadata=d['instance_metadata'],instance_edits=[],approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        approved=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(approved.status_code,201,approved.text)
        m=approved.json();self.assertEqual(m['per_leaf_pixel_area'],[750,3]);self.assertEqual(m['pixel_area'],753);self.assertAlmostEqual(m['projected_area_cm2'],7.53)
        self.assertAlmostEqual(m['mean_leaf_area_cm2'],3.765);self.assertAlmostEqual(m['median_leaf_area_cm2'],3.765)
        self.assertEqual(self.client.get('/api/profiles/'+a['profile_id']+'/history').json()['measurements'][0]['id'],m['id'])
        self.assertIn('7.53',self.client.get('/api/profiles/'+a['profile_id']+'/export.csv').text)
    def test_add_mixed_old_and_new_lamina_only_changes_explicit_region(self):
        a,labels,mask,body=self.expansion((0,20*120+30));r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body)
        self.assertEqual(r.status_code,200,r.text);d=r.json();self.assertEqual(d['per_leaf_pixel_area'],[749,2]);self.assertEqual(d['total_pixel_area'],751)
        event=d['instance_provenance']['edit_events'][-1];self.assertEqual(event['added_lamina_pixel_count'],1);self.assertEqual(event['source_leaf_ids'],[1])
    def test_add_cannot_expand_unpainted_mask_or_erase_previous_lamina(self):
        a,labels,mask,body=self.expansion();mask[3]=1;body['mask_base64']=base64.b64encode(mask.tobytes()).decode()
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400,r.text);self.assertIn('painted',r.json()['detail'])
        mask[3]=0;mask[20*120+35]=0;labels.ravel()[20*120+35]=0;body.update(mask_base64=base64.b64encode(mask.tobytes()).decode(),instance_base64=base64.b64encode(labels.tobytes()).decode())
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400,r.text)
    def test_two_add_expansions_replay_mask_and_ownership_together(self):
        a,labels,mask,body=self.expansion();mask[2:5]=1;labels.ravel()[2:5]=3
        body.update(mask_base64=base64.b64encode(mask.tobytes()).decode(),instance_base64=base64.b64encode(labels.tobytes()).decode())
        body['instance_edits'].append(dict(operation='add',leaf_ids=[3],painted_runs=[[2,3]],selected_pixel_count=3))
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text);d=r.json()
        self.assertEqual(d['per_leaf_pixel_area'],[750,2,3]);self.assertEqual(d['total_pixel_area'],755)
        self.assertEqual([e['added_lamina_pixel_count'] for e in d['instance_provenance']['edit_events']],[3,2])
    def test_mixed_add_merge_keeps_new_pixels_in_the_explicit_leaf_lineage(self):
        a,labels,body=self.setup_labels();positions=np.flatnonzero(labels.ravel());labels.ravel()[positions[375:]]=2
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[])
        saved=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(saved.status_code,200,saved.text)
        mask=np.frombuffer(base64.b64decode(body['mask_base64']),np.uint8).copy();mask[0]=1;labels[labels>0]=1
        body.update(mask_base64=base64.b64encode(mask.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[3],painted_runs=[[0,1]],selected_pixel_count=1),dict(operation='merge',leaf_ids=[1,2])])
        for wrong_owner in [1,4]:
            labels.ravel()[0]=wrong_owner;body['instance_base64']=base64.b64encode(labels.tobytes()).decode()
            r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400,r.text)
        labels.ravel()[0]=3;body['instance_base64']=base64.b64encode(labels.tobytes()).decode()
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['per_leaf_pixel_area'],[750,1])
    def test_add_then_merge_new_leaf_is_a_valid_lamina_expansion(self):
        a,labels,mask,body=self.expansion();labels[labels>0]=1
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode());body['instance_edits'].append(dict(operation='merge',leaf_ids=[1,2]))
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['per_leaf_pixel_area'],[753]);self.assertEqual(r.json()['instance_provenance']['edit_events'][0]['added_lamina_pixel_count'],3)
    def test_add_without_explicit_selection_is_rejected(self):
        a,labels,body=self.setup_labels();labels[20*120+30]=2;body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2])])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400);self.assertIn('painted',r.json()['detail'])
    def test_two_add_transfers_replay_exactly_and_keep_source_identities(self):
        a,labels,body=self.setup_labels();index=20*120+30;labels[index]=2;labels[index+1:index+3]=3
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=[[index,3]],selected_pixel_count=3),dict(operation='add',leaf_ids=[3],painted_runs=[[index+1,2]],selected_pixel_count=2)])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text);d=r.json()
        self.assertEqual(d['per_leaf_pixel_area'],[747,1,2]);self.assertEqual(d['instance_provenance']['edit_events'][-1]['source_leaf_ids'],[2])
    def test_metadata_omitted_batch_keeps_fully_consumed_intermediate_identity(self):
        a,labels,body=self.setup_labels();index=20*120+30;labels[index:index+3]=3
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=[[index,3]],selected_pixel_count=3),dict(operation='add',leaf_ids=[3],painted_runs=[[index,3]],selected_pixel_count=3)])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text);d=r.json()
        self.assertEqual(d['per_leaf_pixel_area'],[747,3]);registry={r['label_id']:r['internal_instance_id'] for r in d['instance_metadata']['instances']}
        self.assertIn(2,registry);self.assertEqual(d['instance_provenance']['edit_events'][-1]['source_internal_instance_ids'],[registry[2]])
    def test_fully_consumed_source_is_removed_and_numbering_compacts(self):
        a,labels,body=self.setup_labels();selected=np.flatnonzero(labels);runs=[]
        for i in map(int,selected):
            if runs and runs[-1][0]+runs[-1][1]==i:runs[-1][1]+=1
            else:runs.append([i,1])
        labels[labels>0]=2;body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=runs,selected_pixel_count=750)])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text);d=r.json()
        self.assertEqual(d['leaf_count'],1);self.assertEqual(d['per_leaf_display_numbers'],[1]);self.assertEqual(d['per_leaf_pixel_area'],[750]);self.assertEqual(d['mask_base64'],a['mask_base64'])
    def test_added_leaf_requires_human_review_then_approval_persists_identity(self):
        a,labels,body=self.setup_labels();index=20*120+30;labels[index:index+3]=2
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation='add',leaf_ids=[2],painted_runs=[[index,3]],selected_pixel_count=3)])
        d=self.client.post('/api/analyses/'+a['id']+'/draft',json=body).json();self.assertTrue(d['human_approval_required'])
        self.assertEqual(self.client.get('/api/profiles/'+a['profile_id']+'/history').json()['measurements'],[])
        body.update(instance_metadata=d['instance_metadata'],instance_edits=[],approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        m=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(m.status_code,201,m.text)
        self.assertEqual(m.json()['per_leaf_internal_instance_ids'],d['per_leaf_internal_instance_ids'])
        self.assertEqual(m.json()['per_leaf_pixel_area'],[747,3]);self.assertEqual(m.json()['instance_provenance']['edit_events'][-1]['painted_runs'],[[index,3]])

from tests import test_firestore_web as cloud
@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Requires local Firestore emulator')
class TestFirestoreAddTransfer(TestAddTransfer):
    setUp=cloud.setup
