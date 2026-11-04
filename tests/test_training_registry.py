"""Registry gates use tiny synthetic fixtures, never real project approvals."""
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from src.hydroponic.config import ROOT


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RegistryFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def module(self, name):
        try:
            return importlib.import_module('src.training.' + name)
        except ModuleNotFoundError:
            self.fail('Missing registry implementation: ' + name)

    def file(self, name, data):
        p = self.base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            data = (json.dumps(data) + '\n').encode()
        p.write_bytes(data)
        return {'path': str(p), 'sha256': sha(data)}

    def model(self, version='v1', eligible=False, current=None):
        checkpoint = self.file(version + '/source.safetensors', b'fixture-weights-' + version.encode())
        train = self.file(version + '/train.jsonl', b'{"group_id":"dev/plant-a","image_sha256":"' + b'a'*64 + b'"}\n')
        report = {'status': 'COMPLETED', 'species': 'lettuce', 'checkpoint_sha256': checkpoint['sha256'],
                  'validation': {'kind': 'public_heldout'}, 'comparison': {'status': 'UNAVAILABLE'}}
        artifacts = {'checkpoint': checkpoint, 'config': self.file(version + '/config.json', {}),
                     'train_manifest': train, 'provenance': self.file(version + '/provenance.json', {'source': 'synthetic unit fixture'})}
        if eligible:
            val = [dict(group_id='holdout/p'+str(i % 2), image_sha256=str(i+1)*64,
                        dataset='real_approved', synthetic=False, diagnostic=False, split='real_validation') for i in range(5)]
            artifacts['validation_manifest'] = self.file(version+'/validation.jsonl', ''.join(json.dumps(r)+'\n' for r in val).encode())
            report['validation'] = dict(kind='real_untouched', manifest_sha256=artifacts['validation_manifest']['sha256'],
                mask_count=5, plant_group_count=2, plant_disjoint=True, synthetic_count=0, diagnostic_count=0, training_overlap_count=0)
            report['comparison'] = dict(status='COMPLETED', candidate=dict(iou=.9, dice=.95, area_mape=5., qc_failure_rate=.1),
                baseline=dict(iou=.8, dice=.9, area_mape=10., qc_failure_rate=.2), current_champion_version=current,
                champion=None if current is None else dict(iou=.85, dice=.92, area_mape=7., qc_failure_rate=.15))
        artifacts['evaluation'] = self.file(version+'/evaluation.json', report)
        return dict(version=version, architecture='unet_resnet18', species='lettuce', artifacts=artifacts)

    def approved(self, index=0, plant='p1', new=True, synthetic=False):
        image = np.zeros((3, 4, 3), np.uint8)
        image[:] = [index+1, 60, 20]
        import itertools
        points=list(itertools.combinations(range(12),2))[index%66]
        mask = np.zeros((3, 4), np.uint8); mask.reshape(-1)[list(points)] = 255
        candidate = mask.copy(); candidate.reshape(-1)[points[1]] = 0
        refs = {}
        for key, arr in [('original', image), ('approved_mask', mask), ('candidate_mask', candidate)]:
            s = io.BytesIO(); Image.fromarray(arr).save(s, format='PNG')
            refs[key] = self.file(f'approved/{index}/{key}.png', s.getvalue())
            refs[key]['key'] = f'{index}/{key}.png'
        review = dict.fromkeys(('visible_target_only','visible_tissue_complete','moss_excluded','growing_medium_excluded',
                    'dead_tissue_excluded','pot_and_background_excluded','scale_approximation_accepted'), True)
        meta = dict(review=review, review_method='explicit_web_APPROVE', image_size_px=[4,3],
                    image_sha256=refs['original']['sha256'], mask_sha256=refs['approved_mask']['sha256'])
        record = dict(id=str(index), analysis_id='a'+str(index), profile_id='profile', plant_id=plant, experiment_id='exp',
            species='lettuce', date='2026-10-08', timestamp='2026-10-08T21:00:00Z', measurement_status='MANUAL_GROUND_TRUTH',
            pixel_area=2, projected_area_cm2=.02, cm_per_pixel=.1, calibration={'kind':'test'}, calibration_id='cal',
            annotation_provenance=meta, model_version='fixture', correction_required=True, assets=refs,
            data_origin='synthetic' if synthetic else 'real', new_real_eligible=new,source_ownership='project_owner')
        return record, refs


class TestModelRegistry(RegistryFixtures):
    def registry(self):
        return self.module('model_registry').ModelRegistry(self.base/'models')

    def test_registration_copies_immutable_weights_and_never_promotes(self):
        r = self.registry(); record = self.model(); saved = r.register(record)
        self.assertEqual(saved['version'], 'v1'); self.assertIsNone(r.champion())
        self.assertEqual([x['version'] for x in r.list()], ['v1'])
        Path(record['artifacts']['checkpoint']['path']).write_bytes(b'source later changed')
        self.assertEqual(r.get('v1')['artifacts']['checkpoint']['sha256'], sha(b'fixture-weights-v1'))
        with self.assertRaises(FileExistsError): r.register(self.model())

    def test_registration_rejects_bad_hash_and_path_traversal_without_version(self):
        r = self.registry(); record = self.model(); record['artifacts']['checkpoint']['sha256'] = '0'*64
        with self.assertRaises(ValueError): r.register(record)
        self.assertEqual(r.list(), [])
        record = self.model(); record['version'] = '../escape'
        with self.assertRaises(ValueError): r.register(record)
        with self.assertRaises(ValueError): self.module('model_registry').ModelRegistry(ROOT.parent/'outside-models')

    def test_public_evaluation_cannot_promote_even_with_human_keyword(self):
        r = self.registry(); saved = r.register(self.model())
        self.assertFalse(r.recommend('v1')['eligible'])
        with self.assertRaises(ValueError): r.promote('v1', approval='APPROVE_MODEL', report_sha256=saved['artifacts']['evaluation']['sha256'], expected_current=None)
        self.assertIsNone(r.champion())

    def test_initial_public_research_gate_can_recommend_unvalidated_challenger(self):
        r=self.registry();record=self.model();p=Path(record['artifacts']['evaluation']['path'])
        report=dict(status='COMPLETED',scope='EXP006_PUBLIC_RESEARCH',target_validation='UNVALIDATED / REVIEW REQUIRED',
            checkpoint_sha256=record['artifacts']['checkpoint']['sha256'],
            selection=dict(domain='oak1_dev',sample_count=100,mean_iou=.94,mean_abs_percentage_area_error=4.,
                           baseline=dict(mean_iou=.90,mean_abs_percentage_area_error=5.)),
            confirmation=[dict(domain='public-other',split='EXTERNAL_TEST',sample_count=10,mean_iou=.7,
                               mean_abs_percentage_area_error=20.,group_disjoint=True,synthetic=False,
                               baseline=dict(mean_iou=.70,mean_abs_percentage_area_error=20.))])
        p.write_text(json.dumps(report));record['artifacts']['evaluation']['sha256']=sha(p.read_bytes());r.register(record)
        self.assertTrue(r.recommend('v1')['eligible']);self.assertIsNone(r.champion())
        report['target_validation']='VALIDATED';p.write_text(json.dumps(report));record['version']='v2';record['artifacts']['evaluation']['sha256']=sha(p.read_bytes());r.register(record)
        self.assertFalse(r.recommend('v2')['eligible'])

    def test_catastrophic_external_regression_vetoes_public_champion(self):
        r=self.registry();record=self.model();p=Path(record['artifacts']['evaluation']['path'])
        report=dict(status='COMPLETED',scope='EXP006_PUBLIC_RESEARCH',target_validation='UNVALIDATED / REVIEW REQUIRED',
            checkpoint_sha256=record['artifacts']['checkpoint']['sha256'],
            selection=dict(domain='oak1_dev',sample_count=10,mean_iou=.94,mean_abs_percentage_area_error=4.,
                           baseline=dict(mean_iou=.90,mean_abs_percentage_area_error=5.)),
            confirmation=[dict(domain='heldout',split='PUBLIC_TEST',sample_count=10,mean_iou=0.,
                               mean_abs_percentage_area_error=1000.,group_disjoint=True,
                               baseline=dict(mean_iou=.90,mean_abs_percentage_area_error=5.))])
        p.write_text(json.dumps(report));record['artifacts']['evaluation']['sha256']=sha(p.read_bytes());r.register(record)
        decision=r.recommend('v1');self.assertFalse(decision['eligible'])
        self.assertTrue(any('stability veto' in reason for reason in decision['reasons']))

    def test_each_confirmation_requires_ten_rows_even_when_total_is_ten(self):
        r=self.registry();record=self.model();p=Path(record['artifacts']['evaluation']['path'])
        report=dict(status='COMPLETED',scope='EXP006_PUBLIC_RESEARCH',target_validation='UNVALIDATED / REVIEW REQUIRED',
            checkpoint_sha256=record['artifacts']['checkpoint']['sha256'],
            selection=dict(domain='oak1_dev',sample_count=19,mean_iou=.94,mean_abs_percentage_area_error=4.,
                           baseline=dict(mean_iou=.90,mean_abs_percentage_area_error=5.)),
            confirmation=[dict(domain='heldout-'+str(n),split='PUBLIC_TEST',sample_count=n,mean_iou=.94,
                               mean_abs_percentage_area_error=4.,group_disjoint=True,
                               baseline=dict(mean_iou=.90,mean_abs_percentage_area_error=5.)) for n in (1,9)])
        p.write_text(json.dumps(report));record['artifacts']['evaluation']['sha256']=sha(p.read_bytes());r.register(record)
        self.assertFalse(r.recommend('v1')['eligible'])
        self.assertIsNone(r.champion())

    def test_external_only_or_malformed_selection_returns_ineligible_without_crashing(self):
        for index,selection in enumerate((None,[],{'baseline':None})):
            with self.subTest(selection=selection):
                r=self.registry();record=self.model(version='external'+str(index));p=Path(record['artifacts']['evaluation']['path'])
                report=dict(status='COMPLETED',scope='EXP006_PUBLIC_RESEARCH',target_validation='UNVALIDATED / REVIEW REQUIRED',
                            checkpoint_sha256=record['artifacts']['checkpoint']['sha256'],selection=selection,confirmation=None)
                p.write_text(json.dumps(report));record['artifacts']['evaluation']['sha256']=sha(p.read_bytes());r.register(record)
                self.assertFalse(r.recommend(record['version'])['eligible'])

    def test_synthetic_or_training_anchor_development_cannot_pass_research_gate(self):
        r=self.registry();record=self.model();p=Path(record['artifacts']['evaluation']['path'])
        report=dict(status='COMPLETED',scope='EXP006_PUBLIC_RESEARCH',target_validation='UNVALIDATED / REVIEW REQUIRED',
            checkpoint_sha256=record['artifacts']['checkpoint']['sha256'],
            selection=dict(domain='oak1_dev',sample_count=100,mean_iou=.94,mean_abs_percentage_area_error=4.,synthetic=True,
                           baseline=dict(mean_iou=.90,mean_abs_percentage_area_error=5.)),
            confirmation=[dict(domain='public-other',split='PUBLIC_TEST',sample_count=10,mean_iou=.7,
                               mean_abs_percentage_area_error=20.,group_disjoint=True)])
        p.write_text(json.dumps(report));record['artifacts']['evaluation']['sha256']=sha(p.read_bytes());r.register(record)
        self.assertFalse(r.recommend('v1')['eligible'])

    def test_promotion_requires_keyword_report_hash_and_current_pointer(self):
        r = self.registry(); saved = r.register(self.model(eligible=True)); h = saved['artifacts']['evaluation']['sha256']
        for args in [dict(approval=''), dict(report_sha256='0'*64), dict(expected_current='unknown')]:
            values = dict(approval='APPROVE_MODEL', report_sha256=h, expected_current=None); values.update(args)
            with self.assertRaises(ValueError): r.promote('v1', **values)
        r.promote('v1', approval='APPROVE_MODEL', report_sha256=h, expected_current=None)
        self.assertEqual(r.champion()['version'], 'v1')
        second = r.register(self.model('v2', eligible=True, current='v1'))
        r.promote('v2', approval='APPROVE_MODEL', report_sha256=second['artifacts']['evaluation']['sha256'], expected_current='v1')
        self.assertEqual(r.champion()['version'], 'v2')
        history = [json.loads(x.read_text()) for x in sorted((r.root/'promotion_history').glob('*.json'))]
        self.assertEqual(len(history), 2); self.assertEqual(history[-1]['previous_version'], 'v1')
        self.assertEqual(r.get('v1')['version'], 'v1')

    def test_saved_checkpoint_tampering_blocks_lookup_and_promotion(self):
        r = self.registry(); saved = r.register(self.model(eligible=True))
        (r.root/'versions/v1/model.safetensors').write_bytes(b'tampered')
        with self.assertRaises(ValueError): r.get('v1')
        with self.assertRaises(ValueError): r.promote('v1', approval='APPROVE_MODEL', report_sha256=saved['artifacts']['evaluation']['sha256'], expected_current=None)

    def test_claimed_real_validation_with_training_group_overlap_is_rejected(self):
        r = self.registry(); record = self.model(eligible=True)
        p = Path(record['artifacts']['validation_manifest']['path']); text=p.read_text().replace('holdout/p0', 'dev/plant-a');p.write_text(text)
        h=sha(p.read_bytes());record['artifacts']['validation_manifest']['sha256']=h
        e=Path(record['artifacts']['evaluation']['path']); report=json.loads(e.read_text());report['validation']['manifest_sha256']=h;e.write_text(json.dumps(report));record['artifacts']['evaluation']['sha256']=sha(e.read_bytes())
        r.register(record); self.assertFalse(r.recommend('v1')['eligible'])


class TestApprovedRegistry(RegistryFixtures):
    def test_research_permission_requires_explicit_owned_source_or_preserved_external_grant(self):
        m=self.module('approved_registry');record,refs=self.approved();record['source_ownership']='project_owner'
        canonical=m.from_measurement(record,refs);self.assertTrue(canonical['license_permits_research'])
        self.assertEqual(canonical['license'],'OWN_HUMAN_APPROVED_RESEARCH')
        record['source_ownership']='external';record['license']='CC-BY-4.0';record['license_permits_research']=True
        canonical=m.from_measurement(record,refs);self.assertEqual(canonical['license'],'CC-BY-4.0')
        record.pop('license_permits_research');self.assertFalse(m.from_measurement(record,refs)['license_permits_research'])
    def test_candidate_model_lineage_takes_precedence_over_legacy_default(self):
        m=self.module('approved_registry');record,refs=self.approved();record.pop('model_version')
        record['candidate_model']=dict(version='learned-v2',architecture='segformer_b0',checkpoint_sha256='c'*64)
        result=m.from_measurement(record,refs)
        self.assertEqual(result['model_version'],'learned-v2');self.assertEqual(result['candidate_model']['checkpoint_sha256'],'c'*64)

    def test_canonical_record_requires_review_and_preserves_refs_and_provenance(self):
        m=self.module('approved_registry');record,refs=self.approved();result=m.from_measurement(record,refs)
        self.assertEqual(result['group_id'],'exp/p1');self.assertEqual(result['assets'],refs)
        self.assertEqual(result['model_version'],'fixture');self.assertEqual(result['projected_area_cm2'],.02)
        self.assertTrue(result['new_real_eligible'])
        bad=copy.deepcopy(record);bad['measurement_status']='UNREVIEWED_CANDIDATE'
        with self.assertRaises(ValueError):m.from_measurement(bad,refs)
        bad=copy.deepcopy(record);bad['annotation_provenance']['review']['moss_excluded']=False
        with self.assertRaises(ValueError):m.from_measurement(bad,refs)

    def test_diagnostic_image_and_synthetic_records_do_not_count_new_real(self):
        m=self.module('approved_registry');record,refs=self.approved(synthetic=True)
        self.assertFalse(m.from_measurement(record,refs)['new_real_eligible'])
        record,refs=self.approved();refs['original']['sha256']='9326ecb572827af62272a140e35fa5b052f410ef2c5a83bd0dc1193d686a323f'
        record['annotation_provenance']['image_sha256']=refs['original']['sha256']
        result=m.from_measurement(record,refs);self.assertFalse(result['new_real_eligible']);self.assertTrue(result['diagnostic'])

    def test_collector_exports_approved_lettuce_only_native_hashes_and_exact_correction(self):
        m=self.module('approved_registry');record,refs=self.approved()
        # Small local service fixture implements the read-only DB/media contracts.
        class DB:
            measurements='measurements'
            def rows(self,table): return [{'payload':record}, {'payload':{**record,'species':'rumex_acetosa'}}, {'payload':{**record,'measurement_status':'UNREVIEWED_DRAFT'}}]
        class Media:
            def read(self,ref): return Path(ref['path']).read_bytes()
        class Service: db=DB();media=Media()
        out=m.export_web(Service(), self.base/'export')
        rows=[json.loads(x) for x in (self.base/'export/approved_registry.jsonl').read_text().splitlines()]
        self.assertEqual(out['count'],1);self.assertEqual(rows[0]['correction_magnitude']['changed_pixels'],1)
        self.assertAlmostEqual(rows[0]['correction_magnitude']['changed_fraction'],1/12)
        self.assertEqual(sha(Path(rows[0]['image_path']).read_bytes()),refs['original']['sha256'])
        self.assertEqual(sha(Path(rows[0]['mask_path']).read_bytes()),refs['approved_mask']['sha256'])
        with self.assertRaises(FileExistsError): m.export_web(Service(), self.base/'export')


class TestRetrainingPreparation(RegistryFixtures):
    def prep(self,m,registry,output,**options):
        options.setdefault('validation_lock_dir',self.base/'lock_state')
        options.setdefault('public_manifest',self.base/'public_dev.jsonl')
        return m.prepare(registry,output,**options)

    def test_collect_web_command_is_read_only_create_only_and_blocks_without_real_masks(self):
        m=self.module('retrain_lettuce');output=self.base/'collect-run'
        args=['--collect-web','--output',str(output),'--prepare-only','--validation-lock-dir',str(self.base/'durable-locks')]
        with patch.dict(os.environ,{'HYDRO_DATA_DIR':str(self.base/'web')},clear=False):
            for name in ('HYDRO_DATABASE_URL','HYDRO_GCS_BUCKET','HYDRO_AUTH_TOKEN','K_SERVICE'):
                os.environ.pop(name,None)
            try:
                code=m.main(args)
            except SystemExit as exc:
                self.fail('Read-only collect-web CLI is unavailable: '+str(exc))
            self.assertEqual(code,0)
            from src.webapp.service import Service
            from src.webapp.settings import Settings
            service=Service(Settings.from_env())
            try:
                self.assertEqual(service.db.rows(service.db.measurements),[])
                self.assertEqual(service.db.rows(service.db.approved_masks),[])
            finally:service.db.engine.dispose()
            with self.assertRaises(FileExistsError):m.main(args)
        self.assertEqual((self.base/'collect-run-approved/approved_registry.jsonl').read_text(),'')
        report=json.loads((output/'preparation.json').read_text());self.assertEqual(report['new_real_mask_count'],0)
        self.assertEqual(report['status'],'BLOCKED_INSUFFICIENT_REAL_DATA');self.assertFalse(report['training_executed'])

    def records(self, count=10, plants=('p1','p2','p3')):
        m=self.module('approved_registry'); rows=[]
        for i in range(count):
            record,refs=self.approved(i,plant=plants[i%len(plants)]);record['source_ownership']='project_owner';row=m.from_measurement(record,refs)
            row.update(image_path=refs['original']['path'],mask_path=refs['approved_mask']['path'],
                       image_sha256=refs['original']['sha256'],mask_sha256=refs['approved_mask']['sha256'])
            rows.append(row)
        public=dict(id='public-dev',group_id='public/dev',image_sha256='f'*64,split='DEV',license='CC-BY-4.0',license_permits_research=True)
        (self.base/'public_dev.jsonl').write_text(json.dumps(public)+'\n')
        p=self.base/'approved_registry.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return p

    def test_fewer_than_five_new_real_masks_blocks_and_never_creates_champion(self):
        m=self.module('retrain_lettuce');p=self.records(count=4);result=self.prep(m,p,self.base/'run')
        self.assertEqual(result['status'],'BLOCKED_INSUFFICIENT_REAL_DATA');self.assertEqual(result['new_real_mask_count'],4)
        self.assertFalse((self.base/'real_validation_lock.json').exists());self.assertFalse((self.base/'run/train_manifest.jsonl').exists())

    def test_one_plant_is_blocked_despite_many_frames(self):
        m=self.module('retrain_lettuce');p=self.records(plants=('p1',));result=self.prep(m,p,self.base/'run')
        self.assertEqual(result['status'],'BLOCKED_INSUFFICIENT_REAL_DATA');self.assertEqual(result['plant_group_count'],1)

    def test_persistent_validation_lock_keeps_groups_disjoint_and_untouched(self):
        m=self.module('retrain_lettuce');p=self.records();result=self.prep(m,p,self.base/'run1')
        self.assertEqual(result['status'],'READY_FOR_TRAINING');lock=self.base/'lock_state/real_validation_lock.json';original=lock.read_bytes()
        train=[json.loads(x) for x in (self.base/'run1/train_manifest.jsonl').read_text().splitlines()]
        val=[json.loads(x) for x in (self.base/'run1/real_validation_manifest.jsonl').read_text().splitlines()]
        self.assertFalse({r['group_id'] for r in train}&{r['group_id'] for r in val})
        self.assertEqual(len(train)+len(val),11)  # ten real masks plus public checkpoint DEV
        second=self.prep(m,p,self.base/'run2');self.assertEqual(second['status'],'READY_FOR_TRAINING');self.assertEqual(lock.read_bytes(),original)

    def test_tampered_validation_reservation_is_blocked_instead_of_replaced(self):
        m=self.module('retrain_lettuce');p=self.records();self.prep(m,p,self.base/'run1')
        lock=self.base/'lock_state/real_validation_lock.json';value=json.loads(lock.read_text());value['created_at']='tampered';lock.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.prep(m,p,self.base/'run2')

    def test_ready_execution_records_completed_comparison_without_promotion(self):
        m=self.module('retrain_lettuce');p=self.records();prepared=self.prep(m,p,self.base/'run')
        self.assertTrue(callable(getattr(m,'execute_prepared',None)),'Training/comparison hook missing')
        cfg=self.file('run_config.json',{'size':512})
        def native_process_fixture(command,**options):
            # Native training is blocked in this machine; fixture preserves subprocess file contracts.
            destination=Path(command[command.index('--output')+1]);destination.mkdir()
            if command[2]=='src.training.train':
                (destination/'model.safetensors').write_bytes(b'unit-fixture-weights')
                (destination/'config.json').write_text('{"architecture":"unet_resnet18"}')
            else:
                report=dict(status='COMPLETED',scope='REAL_RETRAIN',checkpoint_sha256=sha(b'unit-fixture-weights'),
                            comparison=dict(status='COMPLETED',current_champion_version=None),fixture_only=True)
                (destination/'evaluation.json').write_text(json.dumps(report))
        with patch.object(m.subprocess,'run',side_effect=native_process_fixture):
            result=m.execute_prepared(prepared,cfg['path'],'unet_resnet18',model_registry=self.base/'models')
        self.assertTrue(result['training_executed']);self.assertTrue(result['comparison_executed']);self.assertFalse(result['promotion_executed'])
        self.assertEqual(result['comparison_sha256'],sha(Path(result['comparison_report']).read_bytes()))
        commands=json.loads((self.base/'run/execution_commands.json').read_text())
        self.assertIn('--lock',commands['training'])
        self.assertTrue((self.base/'run/training_lock.json').is_file())
        self.assertNotEqual(commands['training'][commands['training'].index('--config')+1],cfg['path'])
        self.assertFalse((self.base/'models/champion.json').exists())

    def test_reserved_validation_never_uses_historical_training_group(self):
        m=self.module('retrain_lettuce');p=self.records();history=self.file('history.jsonl',b'{"group_id":"exp/p1","image_sha256":"'+b'b'*64+b'"}\n')
        result=self.prep(m,p,self.base/'run',historical_manifests=[history['path']]);self.assertEqual(result['status'],'READY_FOR_TRAINING')
        val=[json.loads(x) for x in (self.base/'run/real_validation_manifest.jsonl').read_text().splitlines()]
        self.assertNotIn('exp/p1',{r['group_id'] for r in val})

    def test_previously_trained_masks_are_not_counted_as_new_real(self):
        m=self.module('retrain_lettuce');p=self.records();rows=[json.loads(x) for x in p.read_text().splitlines()]
        h=self.file('prior_train.jsonl',''.join(json.dumps(r)+'\n' for r in rows[:2]).encode())
        result=self.prep(m,p,self.base/'run',historical_manifests=[h['path']])
        self.assertEqual(result['status'],'BLOCKED_INSUFFICIENT_REAL_DATA');self.assertEqual(result['new_real_mask_count'],8)

    def test_public_dev_remains_checkpoint_selection_and_public_test_stays_out(self):
        m=self.module('retrain_lettuce');p=self.records()
        rows=[dict(id='public-'+split,group_id='public/'+split,image_sha256=str(i)*64,split=split,
                   license='CC-BY-4.0',license_permits_research=True)
              for i,split in enumerate(('TRAIN','DEV','PUBLIC_TEST'),1)]
        public=self.file('public.jsonl',''.join(json.dumps(r)+'\n' for r in rows).encode())
        result=self.prep(m,p,self.base/'run',public_manifest=public['path']);self.assertEqual(result['status'],'READY_FOR_TRAINING')
        manifest=[json.loads(x) for x in (self.base/'run/train_manifest.jsonl').read_text().splitlines()]
        self.assertEqual(next(r for r in manifest if r['id']=='public-DEV')['split'],'DEV')
        self.assertNotIn('public-PUBLIC_TEST',{r['id'] for r in manifest})

    def test_changed_identity_cannot_escape_canonical_reserved_group(self):
        m=self.module('retrain_lettuce');p=self.records();rows=[json.loads(x) for x in p.read_text().splitlines()]
        rows[0]['group_id']='invented/other';p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        with self.assertRaises(ValueError):self.prep(m,p,self.base/'run')

    def test_cached_new_flags_cannot_override_historical_browser_provenance(self):
        m=self.module('retrain_lettuce');p=self.records();rows=[json.loads(x) for x in p.read_text().splitlines()]
        for row in rows:row['annotation_provenance']['review_method']='explicit_browser_APPROVE';row['new_real_eligible']=True
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows));result=self.prep(m,p,self.base/'run')
        self.assertEqual(result['new_real_mask_count'],0);self.assertEqual(result['status'],'BLOCKED_INSUFFICIENT_REAL_DATA')

    def test_cached_eligibility_cannot_override_missing_ownership_permission(self):
        m=self.module('retrain_lettuce');p=self.records();rows=[json.loads(x) for x in p.read_text().splitlines()]
        for row in rows:
            row['source_ownership']='unknown';row['license']=None;row['license_permits_research']=False;row['new_real_eligible']=True
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows));result=self.prep(m,p,self.base/'run')
        self.assertEqual(result['new_real_mask_count'],0);self.assertEqual(result['status'],'BLOCKED_INSUFFICIENT_REAL_DATA')

    def test_missing_checkpoint_development_data_blocks_before_reservation(self):
        m=self.module('retrain_lettuce');p=self.records();result=self.prep(m,p,self.base/'run',public_manifest=None)
        self.assertEqual(result['status'],'BLOCKED_MISSING_CHECKPOINT_DEV')
        self.assertFalse((self.base/'lock_state/real_validation_lock.json').exists())

    def test_diagnostic_source_is_never_emitted_as_regular_train(self):
        m=self.module('retrain_lettuce');p=self.records(count=11);rows=[json.loads(x) for x in p.read_text().splitlines()]
        rows[-1]['diagnostic']=True;rows[-1]['new_real_eligible']=False;p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        result=self.prep(m,p,self.base/'run');self.assertEqual(result['status'],'READY_FOR_TRAINING')
        manifest=[json.loads(x) for x in (self.base/'run/train_manifest.jsonl').read_text().splitlines()]
        diagnostic=[r for r in manifest if r['id']==rows[-1]['id']]
        self.assertTrue(not diagnostic or diagnostic[0]['split']=='TRAIN_ANCHOR')

    def test_fresh_snapshot_reuses_durable_holdout_and_prepared_exposure(self):
        m=self.module('retrain_lettuce');self.assertTrue(hasattr(m,'DEFAULT_VALIDATION_LOCK_DIR'),'Durable default reservation root missing')
        p=self.records();first_rows=[json.loads(x) for x in p.read_text().splitlines()];state=self.base/'durable'
        with patch.object(m,'DEFAULT_VALIDATION_LOCK_DIR',state):
            first=m.prepare(p,self.base/'run1',public_manifest=self.base/'public_dev.jsonl')
            reserved=set(first['reserved_groups']);original=(state/'real_validation_lock.json').read_bytes()
            extra=[]
            for i in range(20,25):
                record,refs=self.approved(i,plant='p'+str(i%2+4));record['source_ownership']='project_owner'
                row=self.module('approved_registry').from_measurement(record,refs)
                row.update(image_path=refs['original']['path'],mask_path=refs['approved_mask']['path'],image_sha256=refs['original']['sha256'],mask_sha256=refs['approved_mask']['sha256']);extra.append(row)
            fresh=self.base/'new-export/approved_registry.jsonl';fresh.parent.mkdir();fresh.write_text(''.join(json.dumps(r)+'\n' for r in first_rows+extra))
            second=m.prepare(fresh,self.base/'run2',public_manifest=self.base/'public_dev.jsonl')
        self.assertEqual(second['status'],'READY_FOR_TRAINING');self.assertEqual(set(second['reserved_groups']),reserved)
        self.assertEqual((state/'real_validation_lock.json').read_bytes(),original)
        manifest=[json.loads(x) for x in (self.base/'run2/train_manifest.jsonl').read_text().splitlines()]
        self.assertFalse({r['group_id'] for r in manifest}&reserved)
        self.assertTrue(list((state/'training_exposure').glob('*.json')))

    def test_duplicate_originals_and_changed_masks_cannot_satisfy_threshold(self):
        m=self.module('retrain_lettuce');p=self.records();rows=[json.loads(x) for x in p.read_text().splitlines()]
        for row in rows:
            row['image_sha256']=rows[0]['image_sha256'];row['image_path']=rows[0]['image_path'];row['assets']['original']=rows[0]['assets']['original']
            row['annotation_provenance']['image_sha256']=rows[0]['image_sha256']
            row['mask_sha256']=rows[0]['mask_sha256'];row['mask_path']=rows[0]['mask_path'];row['assets']['approved_mask']=rows[0]['assets']['approved_mask']
            row['annotation_provenance']['mask_sha256']=rows[0]['mask_sha256']
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows));result=self.prep(m,p,self.base/'run')
        self.assertEqual(result['status'],'BLOCKED_INSUFFICIENT_REAL_DATA');self.assertEqual(result['new_real_mask_count'],1)
        Path(rows[0]['mask_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):self.prep(m,p,self.base/'run2')


if __name__=='__main__':
    unittest.main()
