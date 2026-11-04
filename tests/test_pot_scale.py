import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from src.hydroponic.config import ROOT
from test_quick_test import rows


def pot_scene(path):
    canvas = Image.new('RGB', (240, 240), (45, 40, 35))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((30, 30, 210, 210), fill=(170, 170, 170))
    draw.ellipse((36, 36, 204, 204), fill=(55, 35, 20))
    draw.rectangle((80, 70, 160, 150), fill=(40, 160, 35))
    draw.rectangle((45, 130, 70, 155), fill=(40, 160, 35))  # equally green moss: unchanged mask includes it
    canvas.save(path)


def reviewed_rim(path, **changes):
    angles = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    sidecar = dict(schema_version=1, image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        image_size_px=[240, 240], rim_points_px=np.column_stack((120 + 90*np.cos(angles), 120 + 90*np.sin(angles))).tolist(),
        review=dict(outer_rim_confirmed=True, top_view_confirmed=True,
                    distortion_checked=True, pot_plane_approximation_accepted=True))
    sidecar.update(changes)
    path.with_name(path.name + '.pot.json').write_text(json.dumps(sidecar))
    return sidecar


class TestPotScale(unittest.TestCase):
    def test_picker_maps_resized_display_to_native_coordinates(self):
        from src.manual_pot import display_to_native
        self.assertEqual(display_to_native(150,200,(1200,1600),(300,400)),[600.,800.])
        self.assertEqual(display_to_native(0,0,(1200,1600),(300,400)),[0.,0.])
        with self.assertRaises(ValueError):display_to_native(300,200,(1200,1600),(300,400))

    def test_manual_save_requires_review_preserves_history_and_rejects_changed_source(self):
        from src.manual_pot import save_reviewed_diameter,cached_review_path
        from datetime import datetime,timezone
        # Windows wall-clock ticks can repeat across two fast saves. Exercise
        # history preservation with deterministic distinct fixture timestamps.
        clock=patch('src.manual_pot.datetime');mock_clock=clock.start();self.addCleanup(clock.stop)
        mock_clock.now.side_effect=[datetime(2026,10,9,12,0,i,tzinfo=timezone.utc)for i in (0,1)]
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base=Path(directory);source=base/'lettuce.png';pot_scene(source);cache=base/'cache'
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            points=[[30,120],[210,120]]
            with self.assertRaises(ValueError):save_reviewed_diameter(source,points,5.82,cache_root=cache)
            with self.assertRaises(ValueError):save_reviewed_diameter(source,points,5.82,reviewed=True,cache_root=cache,expected_sha256='0'*64)
            self.assertFalse(cache.exists())
            path=save_reviewed_diameter(source,points,5.82,reviewed=True,cache_root=cache,expected_sha256=digest)
            first_history=list((cache/'history').glob('*.json'))[0];preserved=first_history.read_bytes()
            save_reviewed_diameter(source,[[120,30],[120,210]],5.82,reviewed=True,cache_root=cache)
            self.assertEqual(len(list((cache/'history').glob('*.json'))),2)
            self.assertEqual(first_history.read_bytes(),preserved)
            self.assertEqual(json.loads(path.read_text())['diameter_points_px'],[[120,30],[120,210]])
            Image.new('RGB',(240,240),(80,80,80)).save(source)
            self.assertNotEqual(cached_review_path(source,cache),path)

    def test_two_point_diameter_overrides_auto_and_full_circle_constraints(self):
        from src.pot_scale import calibrate_pot
        rgb = np.full((100, 160, 3), 70, np.uint8)
        marker = dict(schema_version=1, image_sha256='a'*64, image_size_px=[160,100],
            known_pot_diameter_cm=5.82, diameter_points_px=[[10,10],[130,90]], rim_points_px=[],
            review=dict(outer_rim_confirmed=True, opposite_points_confirmed=True, pot_plane_approximation_accepted=True))
        with patch('src.pot_scale.propose_rim',side_effect=AssertionError('Manual diameter must win')), \
             patch('src.pot_scale.fit_rim',side_effect=AssertionError('No full-circle requirement for reviewed diameter')):
            result = calibrate_pot(rgb,5.82,'a'*64,marker)
        distance = np.hypot(120,80)
        self.assertEqual(result['status'],'ACCEPTED_MANUAL_REVIEW')
        self.assertAlmostEqual(result['diameter_pixels'],distance)
        self.assertAlmostEqual(result['cm_per_pixel'],5.82/distance)
        self.assertEqual(result['candidate']['method'],'manual_two_point_diameter')
        self.assertEqual(result['candidate']['diameter_points_px'],marker['diameter_points_px'])

    def test_two_point_invalid_unreviewed_or_wrong_diameter_is_not_accepted(self):
        from src.pot_scale import calibrate_pot
        rgb=np.full((100,160,3),70,np.uint8)
        good=dict(schema_version=1,image_sha256='a'*64,image_size_px=[160,100],known_pot_diameter_cm=5.82,
            diameter_points_px=[[10,50],[150,50]],
            review=dict(outer_rim_confirmed=True,opposite_points_confirmed=True,pot_plane_approximation_accepted=True))
        bad_cases=[dict(good,diameter_points_px=[[10,50],[10,50]]),
            dict(good,diameter_points_px=[[10,50],[200,50]]),dict(good,diameter_points_px=[[10,50]]),
            dict(good,diameter_points_px=[[10,50],[float('nan'),50]]),dict(good,image_sha256='b'*64),
            dict(good,review={**good['review'],'opposite_points_confirmed':False}),dict(good,known_pot_diameter_cm=18.)]
        for marker in bad_cases:
            with self.subTest(marker=marker):
                result=calibrate_pot(rgb,5.82,'a'*64,marker)
                self.assertEqual(result['status'],'REVIEW_REQUIRED')
                self.assertIsNone(result['cm_per_pixel'])

    def test_manual_review_persistence_reuse_and_overlay(self):
        from src.manual_pot import save_reviewed_diameter, cached_review_path
        from src.quick_test import run_folder, main
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base=Path(directory);incoming=base/'incoming';incoming.mkdir()
            source=incoming/'lettuce_pot.png';pot_scene(source)
            before=source.read_bytes();cache=base/'cache'
            path=save_reviewed_diameter(source,[[30,120],[210,120]],5.82,reviewed=True,cache_root=cache)
            self.assertEqual(path,cached_review_path(source,cache))
            self.assertTrue(path.with_suffix('.overlay.png').exists())
            self.assertEqual(source.read_bytes(),before)
            manual=json.loads(path.read_text())
            self.assertEqual(manual['known_pot_diameter_cm'],5.82)
            # A conflicting legacy circle sidecar must lose to this reviewed cache.
            reviewed_rim(source)
            with patch('src.quick_test.cached_review_path',return_value=path), redirect_stdout(io.StringIO()):
                first=run_folder(incoming,base/'results',single_plant=True,calibration_mode='real_data',pot_diameter_cm=5.82)
                second=run_folder(incoming,base/'results',single_plant=True,calibration_mode='real_data',pot_diameter_cm=5.82)
            for output in (first,second):
                row=rows(output/'measurements.csv')[0]
                self.assertAlmostEqual(float(row['cm_per_pixel']),5.82/180)
                self.assertAlmostEqual(float(row['foreground_area_cm2']),int(row['raw_pixel_area'])*(5.82/180)**2)
                self.assertEqual(row['calibration_path'],str(path))
                self.assertTrue((output/row['output_directory']/'pot_overlay.png').exists())
                self.assertEqual(row['projected_area_cm2'],'')
            with patch('src.quick_test.run_folder') as runner:
                main(['--single-plant','--calibration-mode','real_data','--pot-diameter-cm','5.82','--manual-pot-diameter'])
                runner.assert_called_once_with(ROOT/'data/incoming',ROOT/'results/quick_test',None,
                    single_plant=True,calibration_mode='real_data',pot_diameter_cm=5.82,manual_pot_diameter=True)

    def test_manual_picker_forwarding_and_cancel_withholds_scale(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base=Path(directory);incoming=base/'incoming';incoming.mkdir()
            source=incoming/'lettuce_pot.png';pot_scene(source);reviewed_rim(source)
            with patch('src.quick_test.select_manual_diameter',return_value=None) as picker, redirect_stdout(io.StringIO()):
                output=run_folder(incoming,base/'results',single_plant=True,calibration_mode='real_data',
                    pot_diameter_cm=5.82,manual_pot_diameter=True)
            picker.assert_called_once_with(source,5.82)
            row=rows(output/'measurements.csv')[0]
            self.assertEqual(row['cm_per_pixel'],'')
            self.assertEqual(row['foreground_area_cm2'],'')
            with self.assertRaises(ValueError):
                run_folder(incoming,base/'results',manual_pot_diameter=True)

    def test_reviewed_circle_scale_and_contaminated_foreground_area(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory); incoming = base / 'incoming'; incoming.mkdir()
            source = incoming / 'lettuce_pot.png'; pot_scene(source); reviewed_rim(source)
            before = {p.name: p.read_bytes() for p in incoming.iterdir()}
            with redirect_stdout(io.StringIO()):
                normal = run_folder(incoming, base / 'results', single_plant=True)
                scaled = run_folder(incoming, base / 'results', single_plant=True,
                                    calibration_mode='real_data', pot_diameter_cm=18.)
            normal_row = rows(normal / 'measurements.csv')[0]
            row = rows(scaled / 'measurements.csv')[0]; saved = scaled / row['output_directory']
            self.assertAlmostEqual(float(row['pot_diameter_pixels']), 180., places=7)
            self.assertAlmostEqual(float(row['cm_per_pixel']), .1, places=7)
            self.assertAlmostEqual(float(row['foreground_area_cm2']), int(row['raw_pixel_area']) * .01, places=7)
            self.assertEqual(row['raw_pixel_area'], normal_row['raw_pixel_area'])
            self.assertEqual(row['projected_area_cm2'], '')  # not validated lettuce-only area
            self.assertEqual(row['reported_pixel_area'], '')
            self.assertEqual(row['qc_status'], 'REVIEW_REQUIRED')
            self.assertIn('target_domain_green_contamination', row['qc_reasons'])
            self.assertEqual(row['pot_scale_status'], 'ACCEPTED_MANUAL_REVIEW')
            self.assertTrue((saved / 'pot_overlay.png').is_file())
            meta = json.loads((saved / 'measurement.json').read_text())
            self.assertEqual(meta['segmentation_qc_status'], normal_row['qc_status'])
            self.assertIn('not lettuce-only', meta['physical_area_definition'])
            with Image.open(normal / normal_row['output_directory'] / 'mask.png') as a, Image.open(saved / 'mask.png') as b:
                np.testing.assert_array_equal(np.asarray(a), np.asarray(b))
                self.assertTrue(np.asarray(b)[140, 55])  # contamination retained and explicitly flagged
            self.assertEqual(before, {p.name: p.read_bytes() for p in incoming.iterdir()})

    def test_automatic_candidate_requires_review_and_has_overlay(self):
        from src.pot_scale import calibrate_pot
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            source = Path(directory) / 'lettuce_pot.png'; pot_scene(source)
            with Image.open(source) as im: rgb = np.array(im)
            result = calibrate_pot(rgb, 18., hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result['status'], 'REVIEW_REQUIRED')
            self.assertIsNone(result['cm_per_pixel'])
            self.assertIsNotNone(result['candidate'])
            self.assertAlmostEqual(result['candidate']['diameter_pixels'], 180., delta=5.)
            self.assertTrue(result['review_template']['rim_points_px'])
            self.assertFalse(result['review_template']['review']['outer_rim_confirmed'])

    def test_invalid_partial_ellipse_or_unreviewed_sidecars_withhold_scale(self):
        from src.pot_scale import calibrate_pot
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            source = Path(directory) / 'lettuce_pot.png'; pot_scene(source)
            sidecar = reviewed_rim(source)
            with Image.open(source) as im: rgb = np.array(im)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            bad_hash = dict(sidecar, image_sha256='0'*64)
            unreviewed = dict(sidecar, review={**sidecar['review'], 'outer_rim_confirmed':False})
            partial_angles = np.linspace(0, np.pi/2, 16)
            partial = dict(sidecar, rim_points_px=np.column_stack((120+90*np.cos(partial_angles),120+90*np.sin(partial_angles))).tolist())
            angles = np.linspace(0, 2*np.pi, 16, endpoint=False)
            ellipse = dict(sidecar, rim_points_px=np.column_stack((120+90*np.cos(angles),120+50*np.sin(angles))).tolist())
            for marker in (bad_hash, unreviewed, partial, ellipse, {}, dict(sidecar, image_size_px=[1,2])):
                with self.subTest(marker=marker):
                    result = calibrate_pot(rgb, 18., digest, marker)
                    self.assertEqual(result['status'], 'REVIEW_REQUIRED')
                    self.assertIsNone(result['cm_per_pixel'])
                    self.assertTrue(result['reasons'])
            blank = calibrate_pot(np.full((240,240,3),70,np.uint8),18.,'0'*64)
            self.assertEqual(blank['status'], 'REVIEW_REQUIRED')
            self.assertIsNone(blank['cm_per_pixel'])

    def test_no_circle_withholds_all_physical_outputs(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory); incoming = base / 'incoming'; incoming.mkdir()
            source = incoming / 'lettuce_no_rim.png'
            Image.new('RGB', (100,100), (40,160,35)).save(source)
            # A valid marker cannot silently rescue an uncertain pot in real_data mode.
            marker = dict(schema_version=1,image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                image_size_px=[100,100],acquisition_id='exp003_oak1',
                corners_px=[[10,10],[30,10],[30,30],[10,30]],width_cm=2.,height_cm=2.,
                validation=dict(plane_validated=True,distortion_validated=True,plane_id='synthetic',protocol_id='synthetic'))
            source.with_name(source.name+'.calibration.json').write_text(json.dumps(marker))
            with redirect_stdout(io.StringIO()):
                output = run_folder(incoming,base/'results',single_plant=True,calibration_mode='real_data',pot_diameter_cm=18.)
            row = rows(output / 'measurements.csv')[0]
            self.assertEqual(row['foreground_area_cm2'], '')
            self.assertEqual(row['projected_area_cm2'], '')
            self.assertEqual(row['cm_per_pixel'], '')
            self.assertEqual(row['pot_scale_status'], 'REVIEW_REQUIRED')
            self.assertEqual(row['qc_status'], 'REVIEW_REQUIRED')
            self.assertTrue((output / row['output_directory'] / 'pot_overlay.png').exists())

    def test_reviewed_scale_does_not_bypass_unvalidated_species_or_acquisition(self):
        from src.quick_test import run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base=Path(directory);incoming=base/'incoming';incoming.mkdir()
            for name in ('lettuce_pot.png','rumex_pot.png'):
                source=incoming/name;pot_scene(source);reviewed_rim(source)
            with redirect_stdout(io.StringIO()):
                output=run_folder(incoming,base/'results',ROOT/'configs/acquisition/hydroponic_unvalidated.json',
                    single_plant=True,experimental_rumex=True,calibration_mode='real_data',pot_diameter_cm=18.)
                unconfigured=run_folder(incoming,base/'results',single_plant=True,
                    calibration_mode='real_data',pot_diameter_cm=18.)
            for row in rows(output/'measurements.csv'):
                self.assertEqual(row['pot_scale_status'],'ACCEPTED_MANUAL_REVIEW')
                self.assertAlmostEqual(float(row['cm_per_pixel']),.1)
                self.assertEqual(row['foreground_area_cm2'],'')
                self.assertEqual(row['projected_area_cm2'],'')
                self.assertEqual(row['reported_pixel_area'],'')
                self.assertIn('REVIEW_REQUIRED',row['qc_status'])
            rumex=next(row for row in rows(unconfigured/'measurements.csv') if row['species']=='rumex_acetosa')
            self.assertEqual(rumex['qc_status'],'UNCONFIGURED')
            self.assertEqual(rumex['raw_pixel_area'],'')
            self.assertEqual(rumex['foreground_area_cm2'],'')

    def test_mode_validation_before_output_and_cli(self):
        from src.quick_test import main, run_folder
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory); incoming=base/'incoming'; incoming.mkdir()
            for diameter in (0., -1., float('nan'), float('inf'), 1e308, None):
                with self.assertRaises(ValueError):
                    run_folder(incoming,base/'results',single_plant=True,calibration_mode='real_data',pot_diameter_cm=diameter)
            with self.assertRaises(ValueError):
                run_folder(incoming,base/'results',calibration_mode='real_data',pot_diameter_cm=18.)
            with self.assertRaises(ValueError):
                run_folder(incoming,base/'results',pot_diameter_cm=18.)
            self.assertFalse((base/'results').exists())
            with patch('src.quick_test.run_folder') as runner:
                main(['--single-plant','--calibration-mode','real_data','--pot-diameter-cm','18'])
                runner.assert_called_once_with(ROOT/'data/incoming', ROOT/'results/quick_test', None,
                    single_plant=True, calibration_mode='real_data', pot_diameter_cm=18.)


if __name__ == '__main__': unittest.main()
