"""Native geometry, leakage, finite evidence and optional real runtime coverage."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from src.hydroponic.config import ROOT


class LearnedInterfaces(unittest.TestCase):
    def test_native_geometry_padding_removed_and_nonfinite_rejected(self):
        from src.training.inference import predict_native
        image = np.zeros((90, 210, 3), np.uint8)
        result = predict_native(image, lambda canvas: np.ones(canvas.shape[:2], np.float32))
        self.assertEqual(result['mask'].shape, (90, 210))
        self.assertTrue(result['mask'].all())
        self.assertEqual(result['metadata']['status'], 'UNVALIDATED / REVIEW REQUIRED')
        with self.assertRaises(ValueError):
            predict_native(image, lambda canvas: np.full(canvas.shape[:2], np.nan))
        with self.assertRaises(ValueError):
            predict_native(image, lambda canvas: np.full(canvas.shape[:2], 1.2))

    def test_training_rows_holdouts_never_sampled_anchor_rate_fixed(self):
        from src.training.train import partition_rows, epoch_samples
        rows = [dict(id='a', split='TRAIN', group_id='a', image_sha256='a', license_permits_research=True),
                dict(id='b', split='DEV', group_id='b', image_sha256='b', license_permits_research=True),
                dict(id='c', split='PUBLIC_TEST', group_id='c', image_sha256='c'),
                dict(id='d', split='TRAIN_ANCHOR', group_id='d', image_sha256='d', license_permits_research=True)]
        train, dev, anchors = partition_rows(rows)
        samples = epoch_samples(train, anchors, 100, seed=6006, epoch=1)
        self.assertEqual(sum(r['id'] == 'd' for r in samples), 10)
        self.assertEqual({r['id'] for r in samples}, {'a', 'd'})
        self.assertEqual(samples, epoch_samples(train, anchors, 100, seed=6006, epoch=1))
        bad = [dict(r) for r in rows]; bad[1]['group_id'] = 'a'
        with self.assertRaises(ValueError): partition_rows(bad)
        bad = [dict(r) for r in rows]; bad[0]['license_permits_research'] = False
        with self.assertRaises(ValueError): partition_rows(bad)

    def test_training_configuration_architecture_cannot_be_silently_overridden(self):
        from src.training.train import training_config
        with self.assertRaisesRegex(ValueError,'architecture'):
            training_config({'architecture':'segformer_b0'},'unet_resnet18')
        self.assertEqual(training_config({},'unet_resnet18')['architecture'],'unet_resnet18')

    def test_public_evidence_diagnostic_does_not_select(self):
        from src.training.evaluate import evaluate_rows
        sample = dict(id='dev', dataset='oak1', split='DEV', group_id='b', image_sha256='b')
        diagnostic = dict(id='anchor', dataset='real_development', split='TRAIN_ANCHOR', group_id='a', image_sha256='a', diagnostic=True)
        rgb = np.zeros((7, 11, 3), np.uint8); gt = np.ones((7, 11), bool)
        rows = [(sample, rgb, gt), (diagnostic, rgb, gt)]
        report = evaluate_rows(rows, lambda canvas: np.ones(canvas.shape[:2], np.float32),
                               baseline_predictor=lambda rgb: np.zeros(rgb.shape[:2], bool))
        self.assertEqual(report['selection']['sample_count'], 1)
        self.assertEqual(report['selection']['mean_iou'], 1.)
        self.assertEqual(report['selection']['baseline']['mean_iou'], 0.)
        self.assertEqual(report['diagnostic']['sample_count'], 1)
        self.assertEqual(report['target_validation'], 'UNVALIDATED / REVIEW REQUIRED')
        json.dumps(report, allow_nan=False)

    def test_real_validation_evidence_does_not_invent_independence(self):
        from src.training.evaluate import evaluate_rows
        row = dict(id='real', dataset='real_approved', split='real_validation', group_id='one', image_sha256='x')
        rgb = np.zeros((8, 8, 3), np.uint8); gt = np.ones((8, 8), bool)
        report = evaluate_rows([(row, rgb, gt)], lambda canvas: np.ones(canvas.shape[:2]),
                               baseline_predictor=lambda rgb: gt, real_validation=True,
                               training_rows=[dict(group_id='one',image_sha256='z')])
        self.assertFalse(report['validation']['plant_disjoint'])
        self.assertGreater(report['validation']['training_overlap_count'], 0)
        self.assertEqual(report['validation']['plant_count'], 1)

    def test_missing_champion_never_instantiates_runtime(self):
        from src.training.inference import predict_version
        class Registry:
            def champion(self): return None
        with self.assertRaises(ValueError): predict_version(np.zeros((8, 8, 3), np.uint8), registry=Registry())

    def test_blocker_classification_does_not_hide_other_errors(self):
        from src.training.models import runtime_error_status
        self.assertEqual(runtime_error_status(OSError('[WinError 4551] c10.dll')), 'BLOCKED_WINDOWS_APPLICATION_CONTROL')
        self.assertEqual(runtime_error_status(ImportError('missing torch')), 'BLOCKED_MISSING_RUNTIME')
        self.assertEqual(runtime_error_status(ValueError('bad shape')), 'FAILED_RUNTIME')

    def test_blocked_trainer_creates_no_checkpoint_and_rejects_reuse(self):
        from src.training.train import train_run, create_training_lock
        from src.training.models import RuntimeUnavailable
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
            directory=Path(temp); manifest=directory/'manifest.jsonl'; config=directory/'config.json'
            manifest.write_text('{}\n', encoding='utf-8'); config.write_text('{}', encoding='utf-8')
            run=directory/'run'
            lock=create_training_lock(manifest, {'unet_resnet18':config}, directory/'lock.json')
            with patch('src.training.train.require_torch', side_effect=RuntimeUnavailable(OSError('[WinError 4551] c10.dll'))):
                result=train_run(manifest,config,'unet_resnet18',run,lock=lock)
            self.assertEqual(result['status'],'BLOCKED_WINDOWS_APPLICATION_CONTROL')
            self.assertFalse((run/'model.safetensors').exists())
            with self.assertRaises(FileExistsError): train_run(manifest,config,'unet_resnet18',run,lock=lock)

    def test_changed_locked_artifacts_fail_before_runtime(self):
        from src.training.train import train_run, create_training_lock
        for artifact in ('manifest','config','augmentation'):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
                directory=Path(temp); manifest=directory/'manifest.jsonl'; config=directory/'config.json'
                manifest.write_text('{}\n',encoding='utf-8'); config.write_text('{}',encoding='utf-8')
                lock=create_training_lock(manifest,{'unet_resnet18':config},directory/'lock.json')
                changed={'manifest':manifest,'config':config,'augmentation':directory/'lock_augmentation_config.json'}[artifact]
                changed.write_text(changed.read_text()+' ',encoding='utf-8')
                with patch('src.training.train.require_torch') as runtime:
                    result=train_run(manifest,config,'unet_resnet18',directory/'run',lock=lock)
                    runtime.assert_not_called()
                self.assertEqual(result['status'],'BLOCKED_TRAINING_LOCK')
                self.assertFalse((directory/'run/model.safetensors').exists())

    def test_baseline_cache_rejects_metric_tampering_before_use(self):
        from src.training.evaluate import _baseline_cache, REQUIRED_BASELINE_SOURCES
        from src.training.common import sha, write_json, write_manifest, relative
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
            directory=Path(temp); manifest=directory/'manifest.jsonl'; metrics=directory/'per_sample_metrics.jsonl'
            row=dict(id='a',image_sha256='im',mask_sha256='mask')
            write_manifest(manifest,[row]); write_manifest(metrics,[dict(id='a',iou=.7)])
            source=directory/'frozen.py'; source.write_text('frozen source',encoding='utf-8')
            report=directory/'baseline_report.json'
            write_json(report,dict(status='COMPLETED',manifest_sha256=sha(manifest),sources={}))
            sources={key:sha(ROOT/key) for key in REQUIRED_BASELINE_SOURCES}
            sources[relative(source)]=sha(source)
            write_json(directory/'cache_binding.json',dict(report_sha256=sha(report),
                per_sample_metrics_sha256=sha(metrics),manifest_sha256=sha(manifest),
                sources=sources,samples={'a':dict(image_sha256='im',mask_sha256='mask')}))
            original=metrics.read_bytes()
            self.assertEqual(_baseline_cache(report,manifest)['a']['iou'],.7)
            metrics.write_text(metrics.read_text().replace('.7','.8'),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'metrics SHA256'): _baseline_cache(report,manifest)
            metrics.write_bytes(original); source.write_text('changed source',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'source changed'): _baseline_cache(report,manifest)

    def test_case_writer_receives_native_masks_and_diagnostic_individual_score(self):
        from src.training.evaluate import evaluate_rows
        rgb=np.zeros((9,13,3),np.uint8); mask=np.ones((9,13),bool); calls=[]
        row=dict(id='own',dataset='real_development',split='TRAIN_ANCHOR',group_id='own',diagnostic=True)
        report=evaluate_rows([(row,rgb,mask)],lambda canvas:np.ones(canvas.shape[:2]),
            baseline_predictor=lambda native:mask,artifact_writer=lambda *values:calls.append(values))
        self.assertEqual(calls[0][3].shape,mask.shape)
        self.assertEqual(report['diagnostic']['individual_scores'][0]['id'],'own')
        self.assertTrue(report['diagnostic']['training_anchor'])

    def test_saved_diagnostic_artifacts_preserve_native_binary_dimensions(self):
        from src.training.evaluate import CaseArtifacts
        from src.training.metrics import binary_metrics
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
            rgb=np.zeros((17,29,3),np.uint8)
            gt=np.zeros((17,29),bool); gt[3:10,4:15]=True
            pred=gt.copy(); pred[13,21]=True; pred[3,4]=False
            row=dict(id='own',dataset='real_development',split='TRAIN_ANCHOR',
                     group_id='own',diagnostic=True)
            writer=CaseArtifacts(Path(temp))
            writer(row,rgb,gt,pred,binary_metrics(pred,gt))
            records=writer.save()
            self.assertEqual(len(records),1)
            directory=ROOT/records[0]['artifacts']
            for name in ('mask.png','overlay.png','errors.png'):
                with Image.open(directory/name) as image:
                    self.assertEqual(image.size,(29,17))
            actual=np.asarray(Image.open(directory/'mask.png'))
            self.assertEqual(set(np.unique(actual)),{0,255})
            self.assertTrue(np.array_equal(actual>0,pred))
            self.assertEqual(records[0]['metrics']['false_positive_pixels'],1)
            self.assertEqual(records[0]['metrics']['false_negative_pixels'],1)

    def test_training_curve_preview_requires_observed_finite_epochs(self):
        from src.training.train import save_training_curves
        from src.training.common import write_manifest
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
            directory=Path(temp); source=directory/'curves.jsonl'; image=directory/'curves.png'
            source.write_text('',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'actual epoch'): save_training_curves(source,image)
            self.assertFalse(image.exists())
            source.unlink()
            write_manifest(source,[dict(epoch=1,training_loss=1.2,dev={'mean_iou':.3}),
                                   dict(epoch=2,training_loss=.8,dev={'mean_iou':.5})])
            record=save_training_curves(source,image)
            self.assertEqual(record['observed_epochs'],2)
            self.assertFalse(record['extrapolated'])
            self.assertTrue(image.is_file())
            with self.assertRaises(FileExistsError): save_training_curves(source,image)


class InstalledRuntime(unittest.TestCase):
    def test_actual_model_forward_if_policy_allows(self):
        runtime=ROOT/'.venv-exp006/Scripts/python.exe' if os.name=='nt' else Path(sys.executable)
        self.assertTrue(runtime.exists(),'Declared experiment runtime missing')
        code=('from src.training.models import build_model,require_torch; '
              't=require_torch(); m=build_model("unet_resnet18",pretrained=False); m.eval(); '
              'y=m(t.zeros(1,3,64,64)); assert tuple(y.shape)==(1,1,64,64); '
              's=build_model("segformer_b0",pretrained=False); s.eval(); '
              'z=s(t.zeros(1,3,64,64)); assert tuple(z.shape)==(1,1,64,64)')
        completed=subprocess.run([str(runtime),'-c',code],cwd=ROOT,capture_output=True,text=True)
        evidence=completed.stdout+completed.stderr
        if completed.returncode and ('WinError 4551' in evidence or '[WinError 4551]' in evidence):
            self.skipTest('Verified Windows Application Control blocks installed torch c10.dll: WinError 4551')
        self.assertEqual(completed.returncode,0,evidence)


if __name__ == '__main__': unittest.main()
