import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
from PIL import Image

from src.hydroponic.config import ROOT, load_profiles
from src.hydroponic.pipeline import process_file


def image(path):
    rgb = np.full((100, 120, 3), (60, 40, 20), dtype=np.uint8)
    rgb[30:70, 40:80] = (40, 160, 35)
    Image.fromarray(rgb).save(path)


def rows(path):
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


class TestQuickTest(unittest.TestCase):
    def test_single_plant_bypasses_localization_and_uses_full_image_color_mask(self):
        from unittest.mock import patch
        from src.quick_test import run_folder
        from src.hydroponic.color import segment
        from src.hydroponic.segmentation import quality_check
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            source = incoming / 'lettuce_full_crop.png'
            rgb = np.full((100, 120, 3), (40, 160, 35), dtype=np.uint8)
            Image.fromarray(rgb).save(source)
            species, acquisition = load_profiles('lettuce')
            expected = segment(rgb, species['color'])
            default = process_file(source, species, acquisition)
            self.assertLess(default['metadata']['raw_pixel_area'], int(expected.sum()))
            with patch('src.hydroponic.pipeline.segment_plant', side_effect=AssertionError('Localization must be bypassed')), \
                    patch('src.quick_test.quality_check', wraps=quality_check) as qc, \
                    redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results', single_plant=True)
            self.assertEqual(qc.call_count, 1)
            np.testing.assert_array_equal(qc.call_args.args[0], expected)
            row = rows(output / 'measurements.csv')[0]
            saved = output / row['output_directory']
            self.assertEqual(int(row['raw_pixel_area']), int(expected.sum()))
            self.assertEqual(row['qc_status'], 'PASS_DEVELOPMENT')
            self.assertTrue((saved / 'annotated.png').exists())
            self.assertTrue((saved / 'qc_status.txt').exists())
            with Image.open(saved / 'mask.png') as mask:
                np.testing.assert_array_equal(np.asarray(mask) > 0, expected)
            with Image.open(saved / 'roi.png') as roi:
                self.assertTrue((np.asarray(roi) == 255).all())
            self.assertFalse((saved / 'support.png').exists())
            meta = json.loads((saved / 'measurement.json').read_text())
            self.assertEqual(meta['processing_mode'], 'single_plant')
            self.assertEqual(meta['effective_profiles'], {'species': species, 'acquisition': acquisition})
            self.assertEqual(meta['diagnostics']['localization_source'], 'full_image_assumed_single_plant')
            self.assertTrue(meta['diagnostics']['localization_bypassed'])
            self.assertEqual(meta['diagnostics']['roi_pixel_area'], 12000)
            self.assertEqual(meta['diagnostics']['qc_threshold'], acquisition['qc']['threshold'])
            self.assertIsNone(meta['projected_area_cm2'])
            self.assertTrue(json.loads((output / 'run.json').read_text())['single_plant'])

    def test_single_plant_empty_mask_still_fails_existing_qc_and_cli_flag_is_forwarded(self):
        from unittest.mock import patch
        from src.quick_test import main, run_folder
        from src.hydroponic.segmentation import quality_check
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            Image.new('L', (120, 100), 90).save(incoming / 'lettuce_empty.png')
            with patch('src.quick_test.quality_check', wraps=quality_check) as qc, redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results', single_plant=True)
            self.assertEqual(qc.call_count, 1)
            row = rows(output / 'measurements.csv')[0]
            self.assertEqual(row['raw_pixel_area'], '0')
            self.assertEqual(row['qc_status'], 'REVIEW_REQUIRED')
            self.assertIn('empty_foreground', row['qc_reasons'])
            self.assertEqual(row['reported_pixel_area'], '')
            self.assertEqual(row['projected_area_cm2'], '')
            with patch('src.quick_test.run_folder') as runner:
                main(['--single-plant'])
                runner.assert_called_once_with(ROOT / 'data/incoming', ROOT / 'results/quick_test', None,
                                               single_plant=True)
            with patch('src.quick_test.run_folder') as runner:
                main(['--single-plant', '--experimental-rumex'])
                runner.assert_called_once_with(ROOT / 'data/incoming', ROOT / 'results/quick_test', None,
                                               single_plant=True, experimental_rumex=True)

    def test_single_plant_rgba_calibration_keeps_original_binding_and_gates(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            _, acquisition = load_profiles('lettuce')
            for name in ('lettuce_valid.png', 'lettuce_invalid.png'):
                source = incoming / name
                Image.new('RGBA', (120, 100), (40, 160, 35, 255)).save(source)
                marker = dict(schema_version=1, image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    image_size_px=[120, 100], acquisition_id=acquisition['acquisition_id'],
                    corners_px=[[10, 10], [30, 10], [30, 30], [10, 30]], width_cm=2., height_cm=2.,
                    validation=dict(plane_validated=True, distortion_validated=True,
                        plane_id='synthetic', protocol_id='synthetic_arithmetic_only'))
                if name == 'lettuce_invalid.png':
                    marker['image_sha256'] = '0' * 64
                source.with_name(source.name + '.calibration.json').write_text(json.dumps(marker))
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results', single_plant=True)
            indexed = {row['filename']: row for row in rows(output / 'measurements.csv')}
            valid = indexed['lettuce_valid.png']
            self.assertEqual(valid['scale_status'], 'valid')
            self.assertAlmostEqual(float(valid['projected_area_cm2']), int(valid['raw_pixel_area']) * .01)
            invalid = indexed['lettuce_invalid.png']
            self.assertEqual(invalid['scale_status'], 'invalid')
            self.assertEqual(invalid['projected_area_cm2'], '')
            self.assertEqual(invalid['raw_pixel_area'], valid['raw_pixel_area'])
            with redirect_stdout(io.StringIO()):
                unvalidated = run_folder(incoming, base / 'results', ROOT / 'configs/acquisition/hydroponic_unvalidated.json',
                                         single_plant=True)
            for row in rows(unvalidated / 'measurements.csv'):
                self.assertEqual(row['qc_status'], 'REVIEW_REQUIRED')
                self.assertEqual(row['reported_pixel_area'], '')
                self.assertEqual(row['projected_area_cm2'], '')

    def test_single_plant_preserves_rumex_default_and_experimental_labels(self):
        from datetime import datetime,timezone
        # Distinct deterministic output timestamps avoid Windows clock-tick
        # collisions without changing the frozen analyzer or test assertions.
        from unittest.mock import patch
        clock=patch('src.quick_test.datetime');mock_clock=clock.start();self.addCleanup(clock.stop)
        mock_clock.now.side_effect=[datetime(2026,10,9,12,0,i,tzinfo=timezone.utc)for i in (0,1)]
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            Image.new('RGB', (120, 100), (40, 160, 35)).save(incoming / 'rumex_day.png')
            with redirect_stdout(io.StringIO()):
                default = run_folder(incoming, base / 'results', single_plant=True)
                experimental = run_folder(incoming, base / 'results', single_plant=True, experimental_rumex=True)
            row = rows(default / 'measurements.csv')[0]
            self.assertEqual(row['qc_status'], 'UNCONFIGURED')
            self.assertEqual(row['raw_pixel_area'], '')
            exp = rows(experimental / 'measurements.csv')[0]
            self.assertEqual(exp['raw_pixel_area'], '12000')
            self.assertIn('EXPERIMENTAL_UNVALIDATED', exp['qc_status'])
            self.assertEqual(exp['reported_pixel_area'], '')
            self.assertEqual(exp['projected_area_cm2'], '')
            meta = json.loads((experimental / exp['output_directory'] / 'measurement.json').read_text())
            self.assertEqual(meta['processing_mode'], 'single_plant')
            self.assertEqual(meta['measurement_status'], 'EXPERIMENTAL / UNVALIDATED')

    def check_input_mode(self, mode):
        from src.quick_test import run_folder
        from src.hydroponic.pipeline import process_rgb
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            source = incoming / f'lettuce_{mode}.png'
            image(source)
            with Image.open(source) as original:
                converted = original.convert(mode)
            if mode == 'RGBA':
                alpha = np.full((100, 120), 255, dtype=np.uint8)
                alpha[30:70, 40:80] = 0  # conversion must discard alpha, not composite a matte
                converted.putalpha(Image.fromarray(alpha))
            converted.save(source)
            original_bytes = source.read_bytes()
            with Image.open(source) as original:
                normalized = np.array(original.convert('RGB'))
            expected = process_rgb(normalized, *load_profiles('lettuce'))
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results')
            row = rows(output / 'measurements.csv')[0]
            self.assertNotEqual(row['qc_status'], 'INPUT_ERROR')
            saved = output / row['output_directory']
            with Image.open(saved / 'mask.png') as mask:
                np.testing.assert_array_equal(np.asarray(mask) > 0, expected['mask'])
                self.assertEqual(mask.size, (120, 100))
            with Image.open(saved / 'annotated.png') as preview:
                np.testing.assert_array_equal(np.asarray(preview), np.asarray(expected['preview']))
            meta = json.loads((saved / 'measurement.json').read_text())
            self.assertEqual(meta['raw_pixel_area'], expected['metadata']['raw_pixel_area'])
            self.assertEqual(meta['qc_status'], expected['metadata']['qc_status'])
            self.assertEqual(meta['image_sha256'], hashlib.sha256(original_bytes).hexdigest())
            self.assertEqual(meta['image_identity_encoding'], 'SHA256 of source file bytes')
            self.assertEqual(meta['image_size_px'], [120, 100])
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertIsNone(meta['projected_area_cm2'])
            if mode == 'RGB':
                self.assertNotIn('input_normalization', meta)
            else:
                self.assertEqual(meta['input_normalization']['source_mode'], mode)
                self.assertEqual(meta['input_normalization']['target_mode'], 'RGB')

    def test_rgb_input_is_unchanged(self):
        self.check_input_mode('RGB')

    def test_rgba_input_is_converted_to_rgb(self):
        self.check_input_mode('RGBA')

    def test_grayscale_input_is_converted_to_rgb(self):
        self.check_input_mode('L')

    def test_rgba_conversion_preserves_calibration_binding_and_orientation_guard(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            _, acquisition = load_profiles('lettuce')
            for name in ('lettuce_valid.png', 'lettuce_wrong_hash.png', 'lettuce_rotated.png'):
                source = incoming / name
                image(source)
                with Image.open(source) as original:
                    rgba = original.convert('RGBA')
                if name == 'lettuce_rotated.png':
                    exif = Image.Exif()
                    exif[274] = 6
                    rgba.save(source, exif=exif)
                    continue
                rgba.save(source)
                marker = dict(schema_version=1, image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    image_size_px=[120, 100], acquisition_id=acquisition['acquisition_id'],
                    corners_px=[[10, 10], [30, 10], [30, 30], [10, 30]], width_cm=2., height_cm=2.,
                    validation=dict(plane_validated=True, distortion_validated=True,
                        plane_id='synthetic', protocol_id='synthetic_arithmetic_only'))
                if name == 'lettuce_wrong_hash.png':
                    marker['image_sha256'] = '0' * 64
                source.with_name(source.name + '.calibration.json').write_text(json.dumps(marker))
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results')
            indexed = {row['filename']: row for row in rows(output / 'measurements.csv')}
            valid = indexed['lettuce_valid.png']
            self.assertEqual(valid['scale_status'], 'valid')
            self.assertAlmostEqual(float(valid['projected_area_cm2']), int(valid['raw_pixel_area']) * .01)
            invalid = indexed['lettuce_wrong_hash.png']
            self.assertEqual(invalid['scale_status'], 'invalid')
            self.assertEqual(invalid['projected_area_cm2'], '')
            self.assertEqual(indexed['lettuce_rotated.png']['qc_status'], 'INPUT_ERROR')

    def test_experimental_rumex_has_labeled_real_mask_and_does_not_change_lettuce(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            for name in ('rumex_day.png', 'lettuce_day.png'):
                image(incoming / name)
            before = (ROOT / 'configs/species/rumex_acetosa.json').read_bytes()
            console = io.StringIO()
            with redirect_stdout(console):
                output = run_folder(incoming, base / 'results', experimental_rumex=True)
            indexed = {r['filename']: r for r in rows(output / 'measurements.csv')}
            row = indexed['rumex_day.png']
            saved = output / row['output_directory']
            self.assertGreater(int(row['raw_pixel_area']), 0)
            self.assertEqual(row['reported_pixel_area'], '')
            self.assertEqual(row['projected_area_cm2'], '')
            self.assertEqual(row['mask_available'], 'True')
            self.assertIn('EXPERIMENTAL_UNVALIDATED', row['qc_status'])
            self.assertIn('EXPERIMENTAL_UNVALIDATED', row['mask_status'])
            self.assertIn('EXPERIMENTAL_UNVALIDATED', (saved / 'qc_status.txt').read_text())
            meta = json.loads((saved / 'measurement.json').read_text())
            self.assertEqual(meta['measurement_status'], 'EXPERIMENTAL / UNVALIDATED')
            self.assertEqual(meta['pipeline_qc_status'], 'REVIEW_REQUIRED')
            self.assertEqual(meta['effective_profiles']['species']['validation']['status'], 'unvalidated')
            self.assertIn('lettuce', meta['experimental_baseline_source'])
            expected = process_file(incoming / 'lettuce_day.png', *load_profiles('lettuce'))
            with Image.open(saved / 'mask.png') as im:
                self.assertEqual(im.info['measurement_status'], 'EXPERIMENTAL / UNVALIDATED')
                np.testing.assert_array_equal(np.asarray(im) > 0, expected['mask'])
            with Image.open(saved / 'annotated.png') as im:
                self.assertEqual(im.info['measurement_status'], 'EXPERIMENTAL / UNVALIDATED')
                self.assertGreater(im.height, expected['preview'].height)  # visible label footer
            self.assertIn('rumex_day.png | rumex_acetosa |', console.getvalue())
            self.assertIn('EXPERIMENTAL_UNVALIDATED', console.getvalue())
            lettuce = indexed['lettuce_day.png']
            lettuce_meta = json.loads((output / lettuce['output_directory'] / 'measurement.json').read_text())
            self.assertEqual(lettuce['qc_status'], expected['metadata']['qc_status'])
            self.assertEqual(lettuce_meta['effective_profiles'], expected['metadata']['effective_profiles'])
            self.assertNotIn('measurement_status', lettuce_meta)
            with Image.open(output / lettuce['output_directory'] / 'annotated.png') as im:
                np.testing.assert_array_equal(np.asarray(im), np.asarray(expected['preview']))
            self.assertEqual(before, (ROOT / 'configs/species/rumex_acetosa.json').read_bytes())
            run = json.loads((output / 'run.json').read_text())
            self.assertTrue(run['experimental_rumex'])

    def test_experimental_rumex_keeps_calibration_gates_and_labels_input_errors(self):
        from src.quick_test import main, run_folder
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            source = incoming / 'rumex_valid.png'
            image(source)
            _, acquisition = load_profiles('rumex_acetosa')
            marker = dict(schema_version=1, image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                image_size_px=[120, 100], acquisition_id=acquisition['acquisition_id'],
                corners_px=[[10, 10], [30, 10], [30, 30], [10, 30]], width_cm=2., height_cm=2.,
                validation=dict(plane_validated=True, distortion_validated=True,
                    plane_id='synthetic', protocol_id='synthetic_arithmetic_only'))
            source.with_name(source.name + '.calibration.json').write_text(json.dumps(marker))
            image(incoming / 'rumex_invalid.png')
            (incoming / 'rumex_invalid.png.calibration.json').write_text('{}')
            (incoming / 'rumex_bad.png').write_bytes(b'not a PNG')
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results', experimental_rumex=True)
            indexed = {r['filename']: r for r in rows(output / 'measurements.csv')}
            self.assertEqual(indexed['rumex_valid.png']['scale_status'], 'valid')
            self.assertEqual(indexed['rumex_invalid.png']['scale_status'], 'invalid')
            for row in indexed.values():
                self.assertEqual(row['projected_area_cm2'], '')
                self.assertEqual(row['reported_pixel_area'], '')
                self.assertIn('EXPERIMENTAL_UNVALIDATED', row['qc_status'])
            self.assertIn('INPUT_ERROR', indexed['rumex_bad.png']['qc_status'])
            with patch('src.quick_test.run_folder') as runner:
                main(['--experimental-rumex'])
                runner.assert_called_once_with(ROOT / 'data/incoming', ROOT / 'results/quick_test', None,
                                               experimental_rumex=True)

    def test_mixed_files_use_frozen_pipeline_and_keep_unconfigured_area_missing(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            for name in ('lettuce_same.jpg', 'lettuce_same.JPEG', 'LETTUCE_same.PNG', 'rumex_day.png'):
                image(incoming / name)
            (incoming / 'notes.txt').write_text('ignored')
            before = {p.name: p.read_bytes() for p in incoming.iterdir()}
            console = io.StringIO()
            with redirect_stdout(console):
                output = run_folder(incoming, base / 'results')
            captures = rows(output / 'measurements.csv')
            self.assertEqual(len(captures), 4)
            self.assertEqual(len({r['output_directory'] for r in captures}), 4)
            for row in captures:
                saved = output / row['output_directory']
                self.assertTrue((saved / 'mask.png').is_file())
                self.assertTrue((saved / 'annotated.png').is_file())
                self.assertEqual(rows(saved / 'measurements.csv'), [row])
                metadata = json.loads((saved / 'measurement.json').read_text())
                self.assertEqual(metadata['scale_status'], 'absent')
                self.assertEqual(row['projected_area_cm2'], '')
                self.assertEqual((saved / 'qc_status.txt').read_text().splitlines()[0], row['qc_status'])
                with Image.open(saved / 'mask.png') as mask_image:
                    mask = np.asarray(mask_image)
                    self.assertEqual(mask.shape, (100, 120))
                    self.assertTrue(set(np.unique(mask)).issubset({0, 255}))
                if row['species'] == 'lettuce':
                    expected = process_file(incoming / row['filename'], *load_profiles('lettuce'))
                    np.testing.assert_array_equal(mask > 0, expected['mask'])
                    self.assertEqual(int(row['raw_pixel_area']), expected['metadata']['raw_pixel_area'])
                    self.assertEqual(row['qc_status'], expected['metadata']['qc_status'])
                    self.assertEqual(metadata['effective_profiles'], expected['metadata']['effective_profiles'])
                    self.assertEqual(row['mask_available'], 'True')
                else:
                    self.assertEqual(row['species'], 'rumex_acetosa')
                    self.assertEqual(row['qc_status'], 'UNCONFIGURED')
                    self.assertEqual(row['raw_pixel_area'], '')
                    self.assertEqual(row['reported_pixel_area'], '')
                    self.assertEqual(row['mask_available'], 'False')
                    self.assertEqual(metadata['mask_status'], 'UNAVAILABLE_PLACEHOLDER')
                    self.assertIsNone(metadata['raw_pixel_area'])
                    self.assertFalse(mask.any())
            self.assertIn('filename | species | pixel area | QC', console.getvalue())
            self.assertIn('rumex_day.png | rumex_acetosa | N/A | UNCONFIGURED', console.getvalue())
            self.assertEqual(before, {p.name: p.read_bytes() for p in incoming.iterdir()})
            with redirect_stdout(io.StringIO()):
                second = run_folder(incoming, base / 'results')
            self.assertNotEqual(output, second)
            self.assertTrue((output / 'measurements.csv').is_file())

    def test_bad_inputs_and_unknown_prefix_do_not_stop_remaining_images(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            (incoming / 'lettuce_bad.png').write_bytes(b'not a PNG')
            image(incoming / 'unknown.png')
            image(incoming / 'lettuce_good.png')
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results')
            indexed = {r['filename']: r for r in rows(output / 'measurements.csv')}
            self.assertEqual(indexed['lettuce_bad.png']['qc_status'], 'INPUT_ERROR')
            self.assertEqual(indexed['unknown.png']['qc_status'], 'SKIPPED_PREFIX')
            self.assertEqual(indexed['lettuce_good.png']['qc_status'], 'PASS_DEVELOPMENT')
            self.assertEqual(indexed['lettuce_bad.png']['raw_pixel_area'], '')
            self.assertEqual(indexed['unknown.png']['raw_pixel_area'], '')
            self.assertTrue((output / indexed['lettuce_bad.png']['output_directory'] / 'error.json').is_file())

    def test_calibration_is_image_bound_and_invalid_marker_keeps_pixels(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            _, acquisition = load_profiles('lettuce')
            for name in ('lettuce_valid.png', 'lettuce_invalid.png'):
                source = incoming / name
                image(source)
                marker = dict(schema_version=1, image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    image_size_px=[120, 100], acquisition_id=acquisition['acquisition_id'],
                    corners_px=[[10, 10], [30, 10], [30, 30], [10, 30]], width_cm=2., height_cm=2.,
                    validation=dict(plane_validated=True, distortion_validated=True,
                        plane_id='synthetic', protocol_id='synthetic_arithmetic_only'))
                if name == 'lettuce_invalid.png':
                    marker['image_sha256'] = '0' * 64
                source.with_name(source.name + '.calibration.json').write_text(json.dumps(marker))
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results')
            indexed = {r['filename']: r for r in rows(output / 'measurements.csv')}
            valid = indexed['lettuce_valid.png']
            self.assertEqual(valid['scale_status'], 'valid')
            self.assertAlmostEqual(float(valid['projected_area_cm2']), int(valid['raw_pixel_area']) * .01)
            invalid = indexed['lettuce_invalid.png']
            self.assertEqual(invalid['scale_status'], 'invalid')
            self.assertEqual(invalid['projected_area_cm2'], '')
            self.assertGreater(int(invalid['raw_pixel_area']), 0)

    def test_empty_folder_and_cli_default_locations(self):
        from src.quick_test import main, run_folder
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            incoming = base / 'incoming'
            incoming.mkdir()
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming, base / 'results')
            self.assertEqual(rows(output / 'measurements.csv'), [])
            with patch('src.quick_test.run_folder') as runner:
                main([])
                runner.assert_called_once_with(ROOT / 'data/incoming', ROOT / 'results/quick_test', None)


if __name__ == '__main__':
    unittest.main()
