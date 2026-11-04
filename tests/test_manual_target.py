import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from src.hydroponic.config import ROOT
from src.manual_pot import save_reviewed_diameter


class TestManualTarget(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'lettuce_sample.png'
        Image.new('RGB', (120, 100), (40, 130, 30)).save(self.source)
        self.calibration = save_reviewed_diameter(self.source, [[10, 50], [110, 50]], 5.82,
            reviewed=True, cache_root=self.base / 'calibration')

    def review(self):
        from src.manual_target import REVIEW_KEYS
        return dict.fromkeys(REVIEW_KEYS, True)

    def test_save_uses_only_manual_pixels_and_saved_scale_without_inference(self):
        from src.manual_target import save_ground_truth
        mask = np.zeros((100, 120), bool)
        mask[20:30, 30:40] = True  # Exactly 100 visible target pixels.
        before = self.source.read_bytes(), self.calibration.read_bytes()
        with patch('src.pot_scale.propose_rim', side_effect=AssertionError('No new rim detection')), \
             patch('src.hydroponic.color.segment', side_effect=AssertionError('No automatic segmentation')):
            output = save_ground_truth(self.source, mask, self.review(), self.base / 'outputs',
                                       calibration_path=self.calibration)
        meta = json.loads((output / 'measurement.json').read_text())
        self.assertEqual(meta['measurement_status'], 'MANUAL_GROUND_TRUTH')
        self.assertEqual(meta['species'], 'lettuce')
        self.assertEqual(meta['pixel_area'], 100)
        self.assertAlmostEqual(meta['cm_per_pixel'], .0582)
        self.assertAlmostEqual(meta['projected_area_cm2'], .338724)
        with Image.open(output / 'reviewed_mask.png') as image:
            self.assertEqual(image.size, (120, 100))
            self.assertEqual(image.info['measurement_status'], 'MANUAL_GROUND_TRUTH')
            self.assertTrue(np.array_equal(np.array(image), mask.astype(np.uint8)*255))
        with Image.open(output / 'overlay.png') as image:
            self.assertEqual(image.info['measurement_status'], 'MANUAL_GROUND_TRUTH')
        self.assertEqual((output / 'reviewed_pot_calibration.json').read_bytes(), before[1])
        self.assertEqual((self.source.read_bytes(), self.calibration.read_bytes()), before)
        self.assertEqual(meta['mask_sha256'], hashlib.sha256((output / 'reviewed_mask.png').read_bytes()).hexdigest())
        with (output / 'measurements.csv').open(newline='') as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['measurement_status'], 'MANUAL_GROUND_TRUTH')
        self.assertEqual(row['pixel_area'], '100')
        self.assertAlmostEqual(float(row['projected_area_cm2']), .338724)
        second = save_ground_truth(self.source, mask, self.review(), self.base / 'outputs',
                                   calibration_path=self.calibration)
        self.assertNotEqual(output, second)
        self.assertEqual(json.loads((output / 'measurement.json').read_text()), meta)

    def test_rejects_unreviewed_invalid_masks_and_changed_image_or_calibration(self):
        from src.manual_target import save_ground_truth
        mask = np.ones((100, 120), bool)
        review = self.review()
        invalid = [(mask, {}), (mask, {**review, 'moss_excluded': False}),
            (mask, {**review, 'dead_tissue_excluded': 'true'}),
            (np.ones((50, 60), bool), review), (np.full((100, 120), 128, np.uint8), review),
            (np.zeros((100, 120), bool), review), (np.full((100, 120), np.nan), review)]
        for candidate, flags in invalid:
            with self.subTest(flags=flags, shape=candidate.shape):
                with self.assertRaises(ValueError):
                    save_ground_truth(self.source, candidate, flags, self.base / 'outputs',
                                      calibration_path=self.calibration)
        self.assertFalse((self.base / 'outputs').exists())
        for kwargs in [dict(expected_source_sha256='0'*64), dict(expected_calibration_sha256='0'*64)]:
            with self.assertRaises(ValueError):
                save_ground_truth(self.source, mask, review, self.base / 'outputs',
                                  calibration_path=self.calibration, **kwargs)
        payload = json.loads(self.calibration.read_text())
        for update in [dict(image_sha256='f'*64), dict(review={}), dict(known_pot_diameter_cm=None)]:
            self.calibration.write_text(json.dumps({**payload, **update}))
            with self.assertRaises(ValueError):
                save_ground_truth(self.source, mask, review, self.base / 'outputs',
                                  calibration_path=self.calibration)
        self.assertFalse((self.base / 'outputs').exists())

    def test_import_binary_mask_preserves_native_geometry_and_rejects_antialiasing(self):
        from src.manual_target import load_target_mask
        native = np.zeros((100, 120), np.uint8); native[40:45, 50:60] = 255
        path = self.base / 'target.png'; Image.fromarray(native).save(path)
        self.assertTrue(np.array_equal(load_target_mask(path, (120, 100)), native != 0))
        Image.fromarray(native != 0).save(path)
        self.assertTrue(np.array_equal(load_target_mask(path, (120, 100)), native != 0))
        for data in [np.full((100, 120), 100, np.uint8), np.zeros((50, 60), np.uint8),
                     np.zeros((100, 120, 3), np.uint8)]:
            Image.fromarray(data).save(path)
            with self.assertRaises(ValueError):load_target_mask(path, (120, 100))

    def test_native_edits_include_erase_without_touching_hidden_pixels(self):
        from src.manual_target import edit_polygon, edit_brush, canvas_to_native
        empty = np.zeros((100, 120), bool)
        added = edit_polygon(empty, [[10, 10], [19, 10], [19, 19], [10, 19]], True)
        self.assertEqual(int(added.sum()), 100)
        erased = edit_polygon(added, [[10, 10], [14, 10], [14, 19], [10, 19]], False)
        self.assertEqual(int(erased.sum()), 50)
        self.assertFalse(empty.any())
        stroke = edit_brush(empty, [30, 30], [35, 30], 1, True)
        self.assertTrue(stroke[30, 30:36].all())
        self.assertFalse(stroke[90, 90])
        self.assertFalse(edit_brush(stroke, [30, 30], [35, 30], 1, False)[30, 30:36].any())
        self.assertEqual(canvas_to_native(25, 40, (120, 100), (60, 50)), [50., 80.])
        with self.assertRaises(ValueError):canvas_to_native(60, 10, (120, 100), (60, 50))

    def test_editor_flow_reuses_hash_cache_and_cancel_saves_no_sample(self):
        from src.manual_target import review_target_image
        mask = np.zeros((100, 120), bool); mask[0:2, 0:2] = True
        with patch('src.manual_target.cached_review_path', return_value=self.calibration), \
             patch('src.manual_target.edit_target_mask', return_value=None):
            self.assertIsNone(review_target_image(self.source, output_root=self.base / 'outputs'))
        self.assertFalse((self.base / 'outputs').exists())
        with patch('src.manual_target.cached_review_path', return_value=self.calibration), \
             patch('src.manual_target.edit_target_mask', return_value=(mask, self.review())):
            output = review_target_image(self.source, output_root=self.base / 'outputs')
        meta = json.loads((output / 'measurement.json').read_text())
        self.assertEqual(meta['pixel_area'], 4)
        self.assertAlmostEqual(meta['projected_area_cm2'], .01354896)

    def test_quick_test_manual_mode_does_not_run_automatic_folder_pipeline(self):
        from src.quick_test import main
        with patch('src.quick_test.run_folder', side_effect=AssertionError('Automatic path must not run')), \
             patch('src.manual_target.review_target_image', return_value=None) as review:
            main(['--calibration-mode', 'real_data', '--manual-target-mask', str(self.source)])
        self.assertEqual(review.call_args.args[0], self.source)
        with self.assertRaises(SystemExit):main(['--manual-target-mask', str(self.source)])
        with self.assertRaises(SystemExit):main(['--target-mask', str(self.base/'mask.png')])

    def test_explicit_external_mask_review_saves_without_gui_and_requires_mask(self):
        from src.manual_target import review_target_image
        mask = np.zeros((100, 120), np.uint8); mask[10:20, 10:20] = 255
        path = self.base/'target.png'; Image.fromarray(mask).save(path)
        with patch('src.manual_target.cached_review_path', return_value=self.calibration), \
             patch('src.manual_target.edit_target_mask', side_effect=AssertionError('Explicit external review needs no GUI')):
            output = review_target_image(self.source, path, self.base/'outputs', confirm_external_review=True)
        meta = json.loads((output/'measurement.json').read_text())
        self.assertEqual(meta['pixel_area'], 100)
        self.assertAlmostEqual(meta['projected_area_cm2'], .338724)
        self.assertEqual(meta['imported_mask']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(meta['review_method'], 'explicit_cli_external_mask_attestation')
        with self.assertRaises(ValueError):
            review_target_image(self.source, output_root=self.base/'bad', confirm_external_review=True)
        self.assertFalse((self.base/'bad').exists())

    def test_editor_explains_tcl_resource_failure_instead_of_claiming_no_desktop(self):
        import tkinter as tk
        from src.manual_target import edit_target_mask
        with patch('tkinter.Tk', side_effect=tk.TclError('Cannot load init.tcl')):
            with self.assertRaisesRegex(ValueError, 'init.tcl'):
                edit_target_mask(np.zeros((10,10,3), np.uint8), np.zeros((10,10), bool), {})


if __name__ == '__main__':
    unittest.main()
