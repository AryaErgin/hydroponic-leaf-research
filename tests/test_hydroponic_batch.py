import csv
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from tests.test_hydroponic_measurements import scene, ROOT


def read_csv(path):
    with path.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))


class TestBatch(unittest.TestCase):
    def setup_data(self,d,stable=True):
        from src.hydroponic.config import load_profiles
        d=Path(d);Image.fromarray(scene()).save(d/'rgb.png')
        _,a=load_profiles('lettuce')
        if stable:a['longitudinal']={'stable_geometry_validated':True,'geometry_validation_id':'synthetic_only'}
        (d/'acq.json').write_text(json.dumps(a))
        return d

    def manifest(self,d,rows):
        p=d/'input.csv'
        with p.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['image_path','species','plant_id','captured_at','acquisition_path'])
            writer.writeheader();writer.writerows(rows)
        return p

    def row(self,day,image='rgb.png',species='lettuce',hour='10',acq=''):
        return dict(image_path=image,species=species,plant_id='same_id',captured_at=f'2026-10-{day}T{hour}:00:00+00:00',acquisition_path=acq)

    def test_grouping_latest_failed_capture_and_gaps(self):
        from src.hydroponic.batch import run_batch
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            d=self.setup_data(tmp)
            manifest=self.manifest(d,[self.row('01'),self.row('02'),self.row('02','missing.png',hour='11'),self.row('03'),self.row('05'),self.row('01',species='rumex_acetosa')])
            out=d/'results';run_batch(manifest,out,acquisition_path=d/'acq.json')
            captures=read_csv(out/'captures.csv')
            daily=read_csv(out/'daily.csv')
            self.assertEqual(len(captures),6);self.assertEqual(len(daily),5)
            lettuce=[r for r in daily if r['species']=='lettuce']
            self.assertEqual(lettuce[1]['qc_status'],'INPUT_ERROR')
            self.assertEqual(lettuce[2]['pixel_area_change'],'')
            self.assertEqual(lettuce[3]['change_reason'],'nonconsecutive_day')
            self.assertEqual([r for r in daily if r['species']=='rumex_acetosa'][0]['qc_status'],'UNCONFIGURED')
            with self.assertRaises(FileExistsError):run_batch(manifest,out,acquisition_path=d/'acq.json')

    def test_consecutive_same_geometry_vs_no_evidence_and_config_drift(self):
        from src.hydroponic.batch import run_batch
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            d=self.setup_data(tmp)
            p=self.manifest(d,[self.row('01'),self.row('02')])
            run_batch(p,d/'stable',acquisition_path=d/'acq.json')
            daily=read_csv(d/'stable/daily.csv')
            self.assertEqual(float(daily[1]['pixel_area_change']),0.)
            self.assertEqual(daily[1]['change_reason'],'comparable')
            a=json.loads((d/'acq.json').read_text());a['longitudinal']['stable_geometry_validated']=False
            (d/'unstable.json').write_text(json.dumps(a))
            run_batch(p,d/'unstable',acquisition_path=d/'unstable.json')
            daily=read_csv(d/'unstable/daily.csv')
            self.assertEqual(daily[1]['pixel_area_change'],'');self.assertEqual(daily[1]['change_reason'],'stable_geometry_unvalidated')
            p=self.manifest(d,[self.row('01'),self.row('02',acq='unstable.json')])
            run_batch(p,d/'drift',acquisition_path=d/'acq.json')
            daily=read_csv(d/'drift/daily.csv')
            self.assertEqual(daily[1]['change_reason'],'configuration_or_canvas_changed')

    def test_metadata_preflight_duplicate_timezone_and_unknown_species(self):
        from src.hydroponic.batch import run_batch
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            d=self.setup_data(tmp)
            for rows in [[self.row('01'),self.row('01')],
                         [{**self.row('01'),'captured_at':'2026-10-01T10:00:00'}],
                         [self.row('01',species='rumex_obtusifolius')]]:
                p=self.manifest(d,rows)
                with self.assertRaises(ValueError):run_batch(p,d/'bad',acquisition_path=d/'acq.json')
                self.assertFalse((d/'bad').exists())

    def test_physical_daily_change_requires_matching_plane_evidence(self):
        from src.hydroponic.batch import daily_observations
        common=dict(species='lettuce',plant_id='a',reported_pixel_area=100,raw_pixel_area=100,
            projected_area_cm2=1.,config_sha256='same',image_size_px=[100,100],
            stable_geometry_validated=True,geometry_validation_id='camera_evidence',
            calibration_plane_id='plane_a',calibration_protocol_id='protocol')
        first={**common,'utc_date':'2026-10-01','captured_at':'2026-10-01T10:00:00+00:00'}
        second={**common,'utc_date':'2026-10-02','captured_at':'2026-10-02T10:00:00+00:00','projected_area_cm2':1.2}
        result=daily_observations([first,second])[1]
        self.assertAlmostEqual(result['cm2_area_change'],.2)
        second['calibration_plane_id']='plane_b'
        result=daily_observations([first,second])[1]
        self.assertIsNone(result['cm2_area_change'])
        self.assertEqual(result['cm2_change_reason'],'calibration_plane_or_protocol_changed')

    def test_failed_roi_withholds_daily_area_change(self):
        from src.hydroponic.batch import run_batch
        import numpy as np
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            d=self.setup_data(tmp)
            Image.fromarray(np.zeros((100,120,3),np.uint8)).save(d/'blank.png')
            p=self.manifest(d,[self.row('01'),self.row('02','blank.png')])
            run_batch(p,d/'results',acquisition_path=d/'acq.json')
            result=read_csv(d/'results/daily.csv')[1]
            self.assertEqual(result['qc_status'],'REVIEW_REQUIRED');self.assertEqual(result['raw_pixel_area'],'0')
            self.assertEqual(result['reported_pixel_area'],'');self.assertEqual(result['pixel_area_change'],'')


if __name__=='__main__':unittest.main()
