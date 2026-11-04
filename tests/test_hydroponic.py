import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


class TestConfigAndCore(unittest.TestCase):
    def test_profiles_do_not_invent_rumex_colors(self):
        from src.hydroponic.config import load_profiles, validate_profiles
        s,a=load_profiles('lettuce')
        frozen=json.loads((ROOT/'experiments/EXP-003/config.json').read_text())
        self.assertEqual(s['color'],frozen['A'])
        self.assertEqual(a['roi']['envelope_margin'],frozen['C']['envelope_margin'])
        r,_=load_profiles('rumex_acetosa')
        self.assertIsNone(r['color']); self.assertIsNone(r['seed_color'])
        self.assertEqual(r['validation']['status'],'unvalidated')
        for value in [float('nan'),-1,1.01,True]:
            broken=copy.deepcopy(a);broken['qc']['threshold']=value
            with self.assertRaises(ValueError): validate_profiles(s,broken)
        broken=copy.deepcopy(s);broken['color']['h_max']=181
        with self.assertRaises(ValueError): validate_profiles(broken,a)
        broken=copy.deepcopy(a);broken['longitudinal']['geometry_validation_id']={'unexpected':'object'}
        with self.assertRaises(ValueError):validate_profiles(s,broken)

    def test_external_acquisition_is_used_and_profile_not_mutated(self):
        from src.hydroponic.config import load_profiles
        s,a=load_profiles('lettuce'); a['roi']['fallback_radius']=.2
        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            p=Path(d)/'acquisition.json';p.write_text(json.dumps(a))
            _,loaded=load_profiles('lettuce',acquisition_path=p)
            self.assertEqual(loaded['roi']['fallback_radius'],.2)
            _,original=load_profiles('lettuce')
            self.assertEqual(original['roi']['fallback_radius'],.48)

    def test_frozen_exp003_synthetic_mask_roi_and_qc_parity(self):
        from src.hydroponic.config import load_profiles
        from src.hydroponic.segmentation import segment_plant, quality_check
        from src.robust_roi import robust_segment, quality_check as old_qc
        s,a=load_profiles('lettuce');cfg=json.loads((ROOT/'experiments/EXP-003/config.json').read_text())
        backgrounds=[(60,40,20),(30,130,30),(0,0,0)]
        for bg in backgrounds:
            rgb=np.full((100,120,3),bg,dtype=np.uint8);rgb[30:70,40:80]=[40,160,35]
            rgb[25:30,35:85]=[85,85,85]
            actual=segment_plant(rgb,s,a); expected=robust_segment(rgb,cfg['A'],cfg['C'])
            for got,want in zip(actual[:3],expected[:3]):
                if want is None:self.assertIsNone(got)
                else:self.assertTrue(np.array_equal(got,want))
            self.assertEqual(actual[3],expected[3])
            self.assertEqual(quality_check(actual[0],actual[3],a['qc']),old_qc(expected[0],expected[0],expected[3],cfg['qc_threshold']))


if __name__=='__main__':unittest.main()
