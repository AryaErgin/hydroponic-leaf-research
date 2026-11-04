"""Synthetic regression for historical identities dropped by old Undo snapshots."""
import base64, copy, os, unittest
from tests import test_webapp as fixtures


class TestIdentityRegistry(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    upload=fixtures.TestWebMVP.upload
    analyze=fixtures.TestWebMVP.analyze

    def pending(self, operation='add'):
        a=self.analyze(self.profile())
        metadata=copy.deepcopy(a['instance_metadata']);metadata['next_label_id']=3
        event=dict(operation=operation,leaf_ids=[2],internal_instance_ids=['intermediate-uuid'])
        if operation=='add':event.update(painted_runs=[[2430,3]],selected_pixel_count=3)
        if operation=='split':event.update(leaf_ids=[1,2],internal_instance_ids=[a['leaves'][0]['internal_instance_id'],'intermediate-uuid'])
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'],instance_metadata=metadata,
                  instance_edits=[event,dict(operation='undo',leaf_ids=[],internal_instance_ids=[])])
        return a,body

    def test_add_undo_missing_intermediate_registry_approves_final_valid_mask(self):
        a,body=self.pending()
        body.update(approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body)
        self.assertEqual(r.status_code,201,r.text)
        m=r.json();self.assertEqual(m['leaf_count'],1);self.assertEqual(m['per_leaf_pixel_area'],[750])
        entries={r['label_id']:r for r in m['instance_metadata']['instances']}
        self.assertEqual(entries[2]['internal_instance_id'],'intermediate-uuid');self.assertFalse(entries[2]['active']);self.assertTrue(entries[1]['active'])
        self.assertEqual(m['per_leaf_display_numbers'],[1])
        self.assertEqual(self.client.get('/api/profiles/'+a['profile_id']+'/history').json()['measurements'][0]['instance_metadata'],m['instance_metadata'])

    def test_split_undo_retains_child_and_draft_reopen_registry(self):
        a,body=self.pending('split')
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text)
        d=r.json();self.assertIn('intermediate-uuid',[r['internal_instance_id'] for r in d['instance_metadata']['instances']])
        body.update(instance_edits=[])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['instance_metadata'],d['instance_metadata'])

    def test_active_identity_missing_is_still_rejected(self):
        a,body=self.pending();body['instance_metadata']['instances']=[]
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400);self.assertIn('every current leaf',r.text)

    def test_legacy_empty_source_identity_array_remains_readable(self):
        a,body=self.pending();body['instance_edits'][0].update(source_leaf_ids=[1],source_internal_instance_ids=[])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text)

    def test_historical_identity_conflicts_are_rejected(self):
        a,body=self.pending();body['instance_edits'][0]['internal_instance_ids']=[a['leaves'][0]['internal_instance_id']]
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,400)

    def test_unassigned_pixels_still_block_approval_with_recovered_history(self):
        a,body=self.pending();raw=bytearray(base64.b64decode(a['instance_base64']));raw[4860:4862]=b'\0\0'
        body.update(instance_base64=base64.b64encode(raw).decode(),approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(r.status_code,400);self.assertIn('unassigned',r.text.lower())

    def test_add_delete_undo_redo_restore_then_approve_and_restart(self):
        import numpy as np
        from src.webapp.service import Service
        a,body=self.pending();metadata=body['instance_metadata']
        metadata['instances'].append(dict(label_id=2,internal_instance_id='intermediate-uuid'))
        metadata['display_order']=[1,2]
        labels=np.frombuffer(base64.b64decode(a['instance_base64']),'<u2').copy();labels[2430:2433]=2
        body.update(instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=body['instance_edits'][:1])
        r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text)
        created=r.json();metadata=copy.deepcopy(created['instance_metadata'])
        for operation,deleted in [('delete',True),('undo',False),('redo',True),('undo',False)]:
            labels[2430:2433]=0 if deleted else 2;metadata['display_order']=[1] if deleted else [1,2]
            body.update(instance_metadata=metadata,instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_edits=[dict(operation=operation,leaf_ids=[2] if operation=='delete' else [],internal_instance_ids=['intermediate-uuid'] if operation=='delete' else [])])
            r=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(r.status_code,200,r.text)
            if deleted:
                blocked={**body,'instance_edits':[],'approval':'APPROVE','instances_reviewed':True,'review':dict.fromkeys(fixtures.KEYS,True)}
                self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/approve',json=blocked).status_code,400)
        body.update(instance_edits=[],approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(r.json()['per_leaf_internal_instance_ids'],created['per_leaf_internal_instance_ids'])
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        saved=reopened.history(a['profile_id'])['measurements'][0]
        self.assertEqual(saved['instance_metadata'],r.json()['instance_metadata']);self.assertEqual(saved['per_leaf_pixel_area'],[747,3])


from tests import test_firestore_web as cloud
@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Requires local Firestore emulator')
class TestFirestoreIdentityRegistry(TestIdentityRegistry):
    setUp=cloud.setup
