import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]


def scene():
    rgb=np.full((100,120,3),(60,40,20),dtype=np.uint8)
    rgb[30:70,40:80]=[40,160,35]
    return rgb


def marker(rgb,acq):
    from src.hydroponic.pipeline import rgb_digest
    return dict(schema_version=1,image_sha256=rgb_digest(rgb),image_size_px=[120,100],
        acquisition_id=acq['acquisition_id'],corners_px=[[10,10],[30,10],[30,30],[10,30]],
        width_cm=2.,height_cm=2.,validation=dict(plane_validated=True,distortion_validated=True,
            plane_id='synthetic_plane',protocol_id='synthetic_test_evidence'))


class TestMeasurements(unittest.TestCase):
    def test_unconfigured_rumex_has_no_fabricated_mask_or_area(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.pipeline import process_rgb
        s,a=load_profiles('rumex_acetosa');result=process_rgb(scene(),s,a)
        self.assertIsNone(result['mask']);self.assertIsNone(result['metadata']['raw_pixel_area'])
        self.assertEqual(result['metadata']['qc_status'],'UNCONFIGURED')
        self.assertEqual(result['preview'].size,(120,100))

    def test_pixels_no_scale_and_explicit_unvalidated_research(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.pipeline import process_rgb
        s,a=load_profiles('lettuce');r=process_rgb(scene(),s,a)
        m=r['metadata'];self.assertEqual(m['raw_pixel_area'],int(r['mask'].sum()))
        self.assertEqual(m['qc_status'],'PASS_DEVELOPMENT');self.assertIsNone(m['projected_area_cm2'])
        self.assertEqual(m['scale_status'],'absent')
        s['species']='rumex_acetosa';s['validation']={'status':'unvalidated','evidence_id':None}
        r=process_rgb(scene(),s,a)
        self.assertGreater(r['metadata']['raw_pixel_area'],0)
        self.assertIsNone(r['metadata']['reported_pixel_area'])
        self.assertIn('unvalidated_species_profile',r['metadata']['qc_reasons'])

    def test_image_bound_calibration_and_geometric_area(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.pipeline import process_rgb
        s,a=load_profiles('lettuce');rgb=scene();cal=marker(rgb,a)
        r=process_rgb(rgb,s,a,calibration=cal);m=r['metadata']
        self.assertEqual(m['scale_status'],'valid')
        self.assertAlmostEqual(m['projected_area_cm2'],m['raw_pixel_area']*.01,places=8)
        for key,value in [('image_sha256','0'*64),('image_size_px',[1,2]),('acquisition_id','other'),
                          ('corners_px',[[10,10],[30,30],[30,10],[10,30]]),('width_cm',float('nan'))]:
            bad=copy.deepcopy(cal);bad[key]=value
            result=process_rgb(rgb,s,a,calibration=bad)['metadata']
            self.assertEqual(result['scale_status'],'invalid');self.assertIsNone(result['projected_area_cm2'])
        bad=copy.deepcopy(cal);bad['validation']['plane_validated']=False
        self.assertEqual(process_rgb(rgb,s,a,calibration=bad)['metadata']['scale_status'],'invalid')
        s['validation']={'status':'unvalidated','evidence_id':None}
        self.assertIsNone(process_rgb(rgb,s,a,calibration=cal)['metadata']['projected_area_cm2'])

    def test_native_outputs_create_only_and_bad_rgb(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.pipeline import process_rgb, save_result
        s,a=load_profiles('lettuce');rgb=scene();r=process_rgb(rgb,s,a)
        for invalid in [rgb.astype(float),rgb[:,:,0],np.zeros((1,1,3),np.uint8)]:
            with self.assertRaises(ValueError):process_rgb(invalid,s,a)
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            out=Path(d)/'capture';save_result(r,out)
            with Image.open(out/'mask.png') as im:
                mask=np.asarray(im);self.assertEqual(mask.shape,(100,120));self.assertEqual(set(np.unique(mask)),{0,255})
            self.assertTrue((out/'preview.png').exists());self.assertTrue((out/'measurement.json').exists())
            with self.assertRaises(FileExistsError):save_result(r,out)
        with self.assertRaises(ValueError):save_result(r,ROOT.parent/'forbidden_pipeline_output')

    def test_invalid_nonfinite_marker_still_saves_pixel_outputs(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.pipeline import process_rgb, save_result
        s,a=load_profiles('lettuce');cal=marker(scene(),a);cal['height_cm']=float('nan')
        result=process_rgb(scene(),s,a,calibration=cal)
        self.assertEqual(result['metadata']['scale_status'],'invalid')
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            save_result(result,Path(d)/'capture')
            saved=json.loads((Path(d)/'capture/measurement.json').read_text())
            self.assertIsNone(saved['projected_area_cm2']);self.assertGreater(saved['raw_pixel_area'],0)

    def test_extreme_marker_integer_is_rejected_without_losing_pixels(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.pipeline import process_rgb
        s,a=load_profiles('lettuce');cal=marker(scene(),a);cal['width_cm']=10**1000
        result=process_rgb(scene(),s,a,calibration=cal)['metadata']
        self.assertEqual(result['scale_status'],'invalid');self.assertIsNone(result['projected_area_cm2'])
        self.assertGreater(result['reported_pixel_area'],0)

    def test_perspective_pixel_sum_matches_transformed_rectangle(self):
        from src.hydroponic.calibration import homography, projected_area
        from src.hydroponic.config import load_profiles
        s,a=load_profiles('lettuce');cal=marker(scene(),a)
        cal['corners_px']=[[10,10],[35,10],[34,35],[11,35]]
        H=homography(cal,(100,120),cal['image_sha256'],a['acquisition_id'])
        mask=np.zeros((100,120),bool);mask[15:25,17:29]=True
        bounds=np.array([[16.5,14.5,1],[28.5,14.5,1],[28.5,24.5,1],[16.5,24.5,1]])@H.T
        p=bounds[:,:2]/bounds[:,2:];q=np.roll(p,-1,axis=0)
        expected=abs((p[:,0]*q[:,1]-p[:,1]*q[:,0]).sum())*.5
        self.assertAlmostEqual(projected_area(mask,H),expected,places=9)
        cal['corners_px']=[[10,10],[35,12],[30,35],[12,30]]
        with self.assertRaisesRegex(ValueError,'pole'):homography(cal,(100,120),cal['image_sha256'],a['acquisition_id'])


if __name__=='__main__':unittest.main()
