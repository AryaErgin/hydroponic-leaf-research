import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from src.exp004 import inventory, union_masks, complete_frames, pixel_metrics, aggregate, frozen_predict, verify_lock, sha


class ExternalValidationTests(unittest.TestCase):
    def test_shared_pairing_and_orphan(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p = Path(d) / '2025-05-21' / 'N0I'
            p.mkdir(parents=True)
            Image.new('RGB', (20, 10)).save(p / 'color_1-2.png')
            for tag in (1, 2):
                Image.new('1', (20, 10), 1).save(p / f'plant_mask_{tag}.png')
            frames, pairs = inventory(Path(d))
            self.assertEqual((len(frames), len(pairs)), (1, 2))
            self.assertEqual([r['tag'] for r in pairs], ['1', '2'])
            Image.new('1', (20, 10), 1).save(p / 'plant_mask_3.png')
            with self.assertRaises(ValueError): inventory(Path(d))

    def test_overlap_union_and_incomplete_cotarget(self):
        a = np.array([[1, 1, 0]], bool); b = np.array([[0, 1, 1]], bool)
        self.assertEqual(int(union_masks([a, b]).sum()), 3)
        rows = [dict(frame_id='f', pair_id='1', include=True), dict(frame_id='f', pair_id='2', include=False)]
        self.assertEqual(complete_frames(rows), set())
        rows[1]['include'] = True
        self.assertEqual(complete_frames(rows), {'f'})

    def test_catastrophe_boundaries_and_empty_denominator(self):
        gt = np.zeros((1, 10), bool); gt[0, :2] = True
        pred = np.zeros_like(gt); pred[0, :4] = True
        self.assertFalse(pixel_metrics(gt, pred)['catastrophic_over'])
        pred[0, 4] = True
        self.assertTrue(pixel_metrics(gt, pred)['catastrophic_over'])
        pred[:] = False; pred[0, 0] = True
        self.assertFalse(pixel_metrics(gt, pred)['catastrophic_under'])
        pred[:] = False
        row = pixel_metrics(gt, pred)
        self.assertEqual((row['iou'], row['dice'], row['signed_percentage_area_error']), (0, 0, -100))
        self.assertTrue(row['catastrophic_under'])
        row['qc_flag'] = True
        summary = aggregate([row])
        self.assertEqual((summary['n'], summary['mean_area_error_percent']), (1, 100))

    def test_exact_original_C_and_no_reference_input(self):
        from src.exp003 import infer
        cfg = json.loads((Path(__file__).resolve().parents[1] / 'experiments/EXP-004/config.json').read_text())
        for green in (False, True):
            rgb = np.full((80, 120, 3), 90, np.uint8)
            if green: rgb[20:60, 40:80] = [40, 170, 30]
            masks, rois, card, diag, qc = infer(rgb, cfg)
            pred, roi, diag2, qc2, area = frozen_predict(rgb, cfg)
            np.testing.assert_array_equal(pred, masks['C'])
            np.testing.assert_array_equal(roi, rois['C'])
            self.assertEqual(qc2, qc['C'])
        import inspect
        self.assertEqual(list(inspect.signature(frozen_predict).parameters), ['rgb', 'cfg'])

    def test_screen_lock_detects_tamper(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            d = Path(d); f = d / 'decisions.csv'; f.write_text('before')
            (d / 'screen_lock.json').write_text(json.dumps({'files': {str(f): sha(f)}}))
            verify_lock(d)
            f.write_text('after')
            with self.assertRaises(ValueError): verify_lock(d)

    def test_diagnostic_raw_area_collision(self):
        from src import exp004
        row = exp004.diagnostic_record('f', {'raw_pixel_area': 17, 'localization_source': 's'},
                                      {'qc_flag': True}, {'raw_pixel_area': 17, 'reported_pixel_area': None})
        self.assertEqual(row['raw_pixel_area'], 17)
        self.assertIsNone(row['reported_pixel_area'])

    def test_pixel_weighting_does_not_equal_image_macro(self):
        from src.exp004_analysis import pixel_weighted
        a=pixel_metrics(np.array([[True]]), np.array([[True]]))
        b=pixel_metrics(np.ones((1,3),bool), np.zeros((1,3),bool))
        result=pixel_weighted([a,b])
        self.assertEqual(result['pixel_weighted_iou'], .25)
        self.assertEqual(result['pixel_weighted_dice'], .4)
        self.assertEqual(result['total_gt_pixel_area'],4)


if __name__ == '__main__': unittest.main()
