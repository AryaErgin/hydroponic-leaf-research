import base64,csv,io,json,os,unittest
import numpy as np
from src.webapp import leaf_instances as leaf
from tests import test_webapp as fixtures

class TestInstanceIdentity(unittest.TestCase):
    def test_aliases_and_incomplete_display_order_are_rejected(self):
        from src.webapp.instance_identity import resolve
        labels=np.array([[1,2]],np.uint16)
        metadata={'version':1,'next_label_id':3,'instances':[{'label_id':i,'internal_instance_id':'same'} for i in [1,2]],'display_order':[1,2]}
        with self.assertRaisesRegex(ValueError,'unique'):resolve(labels,'photo',metadata,strict=True)
        metadata['instances'][1]['internal_instance_id']='other';metadata['display_order']=[1]
        with self.assertRaisesRegex(ValueError,'every current leaf'):resolve(labels,'photo',metadata,strict=True)
    def test_gaps_compact_without_changing_technical_identity(self):
        labels=np.array([[1,3,7]],np.uint16);mask=labels>0
        result=leaf.summary(labels,mask,.1,analysis_id='photo-a')
        self.assertEqual([r['display_leaf_number'] for r in result['leaves']],[1,2,3])
        self.assertEqual([r['leaf_id'] for r in result['leaves']],[1,3,7])
        uid=result['leaves'][1]['internal_instance_id']
        labels[0,0]=0;mask=labels>0
        after=leaf.summary(labels,mask,.1,analysis_id='photo-a',instance_metadata=result['instance_metadata'])
        self.assertEqual(after['leaves'][0]['internal_instance_id'],uid)
        self.assertEqual(after['leaves'][0]['display_leaf_number'],1)

class TestNumberingWeb(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    upload=fixtures.TestWebMVP.upload
    analyze=fixtures.TestWebMVP.analyze
    def test_numbering_identity_draft_approval_reopen_export_and_legacy(self):
        from src.webapp.service import Service
        p=self.profile();a=self.analyze(p);labels=np.zeros((100,120),'<u2');labels[20:45,30:40]=2;labels[20:45,40:50]=8;labels[20:45,50:60]=11
        metadata={'version':1,'next_label_id':12,'instances':[{'label_id':i,'internal_instance_id':'stable-'+str(i)} for i in [2,8,11]],'display_order':[2,11,8]}
        body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=base64.b64encode(labels.tobytes()).decode(),instance_analysis_id=a['id'],instance_metadata=metadata)
        d=self.client.post('/api/analyses/'+a['id']+'/draft',json=body);self.assertEqual(d.status_code,200,d.text)
        self.assertEqual([r['leaf_id'] for r in d.json()['leaves']],[2,11,8]);self.assertEqual([r['display_leaf_number'] for r in d.json()['leaves']],[1,2,3])
        a=d.json();body.update(approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        merged=labels.copy();merged[merged==8]=11
        merge_meta=json.loads(json.dumps(a['instance_metadata']));merge_meta['display_order']=[2,11]
        edit={**body,'instance_metadata':merge_meta,'instance_base64':base64.b64encode(merged.tobytes()).decode(),'instance_edits':[dict(operation='merge',leaf_ids=[8,11])]}
        changed=self.client.post('/api/analyses/'+a['id']+'/draft',json=edit);self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['per_leaf_display_numbers'],[1,2]);self.assertEqual(changed.json()['leaves'][1]['internal_instance_id'],'stable-11')
        body.update(instance_metadata=a['instance_metadata'],instance_edits=[dict(operation='undo',leaf_ids=[])])
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(r.status_code,201,r.text);m=r.json()
        self.assertEqual(m['leaves'][1]['internal_instance_id'],'stable-11')
        reopened=Service(self.settings);self.addCleanup(reopened.db.engine.dispose)
        saved=reopened.history(p['id'])['measurements'][0];self.assertEqual(saved['instance_metadata'],m['instance_metadata'])
        exported=list(csv.DictReader(io.StringIO(self.client.get('/api/profiles/'+p['id']+'/export.csv').text)))[0]
        self.assertEqual(json.loads(exported['per_leaf_internal_instance_ids']),['stable-2','stable-11','stable-8'])
        self.assertEqual(json.loads(exported['per_leaf_display_numbers']),[1,2,3])
        self.assertEqual(json.loads(exported['instance_metadata']),m['instance_metadata'])
        self.assertTrue(m['instances_reviewed']);self.assertEqual(sum(m['per_leaf_pixel_area']),m['pixel_area'])
    def test_existing_identity_cannot_be_replaced_or_aliased(self):
        a=self.analyze(self.profile());body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'])
        metadata=a['instance_metadata'];metadata['instances'][0]['internal_instance_id']='substituted'
        body['instance_metadata']=metadata
        self.assertEqual(self.client.post('/api/analyses/'+a['id']+'/draft',json=body).status_code,400)
    def test_legacy_instance_record_get_has_stable_identity_without_database_rewrite(self):
        from sqlalchemy import update
        a=self.analyze(self.profile());body=dict(width=120,height=100,mask_base64=a['mask_base64'],instance_base64=a['instance_base64'],instance_analysis_id=a['id'],approval='APPROVE',instances_reviewed=True,review=dict.fromkeys(fixtures.KEYS,True))
        r=self.client.post('/api/analyses/'+a['id']+'/approve',json=body);self.assertEqual(r.status_code,201,r.text)
        s=self.app.state.service;table=s.db.measurements;m=s.db.one(table,r.json()['id'])['payload']
        for key in ('instance_metadata','per_leaf_display_numbers','per_leaf_internal_instance_ids'):m.pop(key,None)
        for row in m['leaves']:
            row.pop('display_leaf_number',None);row.pop('internal_instance_id',None)
        with s.db.engine.begin() as conn:conn.execute(update(table).where(table.c.id==m['id']).values(payload=m))
        first=self.client.get('/api/measurements/'+m['id']).json();second=self.client.get('/api/measurements/'+m['id']).json()
        self.assertEqual(first['leaves'],second['leaves']);self.assertEqual(first['leaves'][0]['display_leaf_number'],1)
        self.assertEqual(s.db.one(table,m['id'])['payload'],m)

from tests import test_firestore_web as cloud
@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Requires local Firestore emulator')
class TestFirestoreNumbering(TestNumberingWeb):
    setUp=cloud.setup
