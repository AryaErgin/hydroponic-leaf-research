"""Species counters/registries use disposable artificial approved records only."""
import json,copy
from unittest.mock import patch
from tests import test_webapp as fixtures
from tests.test_training_registry import RegistryFixtures
import unittest

class TestSpeciesRegistry(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    analyze=fixtures.TestWebMVP.analyze
    upload=fixtures.TestWebMVP.upload
    approve=fixtures.TestWebMVP.approve
    def foreground(self,a):
        # Simulate a pending pre-lamina analysis for legacy cohort isolation.
        from sqlalchemy import update
        s=self.app.state.service;stored=s.db.one(s.db.analyses,a['id'])['payload']
        stored['measurement_target']='visible_plant_foreground'
        with s.db.engine.begin() as c:c.execute(update(s.db.analyses).where(s.db.analyses.c.id==a['id']).values(payload=stored))
    def test_sorrel_approval_routes_only_to_sorrel_and_keeps_provenance(self):
        self.settings.closed_academic_research=True;p=self.profile('rumex_acetosa');a=self.analyze(p);s=self.app.state.service
        self.assertEqual(s.db.rows(s.db.approved_sorrel_masks),[])
        self.foreground(a);r=self.approve(a);self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(s.db.rows(s.db.approved_masks),[])
        row=s.db.rows(s.db.approved_sorrel_masks)[0]['payload']
        self.assertTrue(row['new_real_eligible']);self.assertEqual(row['species'],'rumex_acetosa')
        for key in ['plant_id','date','calibration','candidate_model','assets','annotation_provenance']:self.assertIn(key,row)
        state=s.db.rows(s.db.owner_notifications)[0]['payload']
        self.assertEqual(state['species'],'rumex_acetosa');self.assertEqual(state['new_eligible_count'],1)
    def test_lettuce_does_not_touch_sorrel_registry(self):
        self.settings.closed_academic_research=True;a=self.analyze(self.profile());s=self.app.state.service
        self.foreground(a);self.assertEqual(self.approve(a).status_code,201)
        self.assertEqual(len(s.db.rows(s.db.approved_masks)),1);self.assertEqual(s.db.rows(s.db.approved_sorrel_masks),[])
        self.assertEqual(s.db.rows(s.db.owner_notifications)[0]['payload']['species'],'lettuce')

class TestSpeciesCounters(RegistryFixtures):
    def test_species_counters_threshold_events_and_duplicates_are_separate(self):
        from src.training.owner_notifications import advance
        states={s:None for s in ['lettuce','rumex_acetosa']};events=[]
        for species in states:
            for i in range(10):
                record,refs=self.approved(i);record['species']=species
                row=self.module('approved_registry').from_measurement(record,refs)
                states[species],event=advance(states[species],row,species,set(),species=species)
                events.append(event)
            saved=copy.deepcopy(states[species]);states[species],e=advance(saved,row,species,set(),species=species)
            self.assertFalse(e);self.assertEqual(states[species]['new_eligible_count'],10)
            other='lettuce'if species!='lettuce'else'rumex_acetosa'
            invalid={**row,'species':other};unchanged,e=advance(saved,invalid,species,set(),species=species)
            self.assertEqual(unchanged,saved);self.assertFalse(e)
        self.assertEqual(sum(events),2)
    def test_lettuce_training_rejects_sorrel_data(self):
        from src.training.retrain_lettuce import verified_rows
        r,refs=self.approved();r['species']='rumex_acetosa';row=self.module('approved_registry').from_measurement(r,refs)
        p=self.base/'mixed.jsonl';p.write_text(json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'species'):verified_rows(p)
    def test_no_successful_training_means_no_reset(self):
        from src.training.owner_notifications import snapshot
        class Registry:
            root=self.base
            def list(self):return []
        a=snapshot(Registry(),species='rumex_acetosa');b=snapshot(Registry(),species='rumex_acetosa')
        self.assertEqual(a,b)
    def test_successful_species_snapshot_excludes_covered_samples(self):
        from src.training.owner_notifications import snapshot,advance
        record,refs=self.approved();record['species']='rumex_acetosa';row=self.module('approved_registry').from_measurement(record,refs)
        ledger=dict(status='COMPLETED',species='rumex_acetosa',snapshot_id='trained',covered_image_sha256=[refs['original']['sha256']])
        sid,covered=snapshot(None,'rumex_acetosa',ledger)
        state,event=advance(None,row,sid,covered,species='rumex_acetosa')
        self.assertEqual(state['new_eligible_count'],0);self.assertFalse(event)
        with self.assertRaises(ValueError):snapshot(None,'lettuce',ledger)
    def test_email_policies_use_one_owner_channel_opened_only(self):
        from src.training.owner_email_setup import policy
        channel='projects/hydroponic-leaf-research-aryae/notificationChannels/owner'
        for species in ['lettuce','rumex_acetosa']:
            p=policy(species,channel);self.assertEqual(p['notificationChannels'],[channel])
            self.assertEqual(p['alertStrategy']['notificationPrompts'],['OPENED'])
            self.assertIn(species,p['conditions'][0]['conditionMatchedLog']['filter'])
            self.assertNotIn('professor',json.dumps(p))
    def test_sorrel_preparation_rejects_lettuce_and_blocks_without_data(self):
        from src.training.retrain_sorrel import prepare
        p=self.base/'registry.jsonl';p.write_text('')
        r=prepare(p,self.base/'prep');self.assertEqual(r['status'],'BLOCKED_INSUFFICIENT_REAL_DATA')
        self.assertFalse(r['training_executed'])
        row,refs=self.approved();p.write_text(json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'species'):prepare(p,self.base/'bad')
    def test_test_demo_and_duplicate_masks_never_increment(self):
        from src.training.owner_notifications import advance
        for species in ['lettuce','rumex_acetosa']:
            r,refs=self.approved();r['species']=species;row=self.module('approved_registry').from_measurement(r,refs)
            state,event=advance(None,row,'batch',set(),species=species)
            for key in ['test_only','demo','synthetic','diagnostic','historically_exposed']:
                unchanged,e=advance(state,{**row,key:True},'batch',set(),species=species)
                self.assertEqual(unchanged,state);self.assertFalse(e)
            duplicate=copy.deepcopy(row);duplicate['assets']['original']['sha256']='a'*64
            unchanged,e=advance(state,duplicate,'batch',set(),species=species)
            self.assertEqual(unchanged,state);self.assertFalse(e)
