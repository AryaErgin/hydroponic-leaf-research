import inspect
import json
import unittest
import numpy as np

from src.exp005 import ROOT, grid, parse_biomass, valid_mass_join, position_predict
from src.exp005_analysis import correlations, temporal_changes, average_ranks


class LongitudinalTests(unittest.TestCase):
    def test_grid_disjoint_complete_and_position_orientation(self):
        g=grid(); coverage=np.zeros((720,1280),int)
        for r in g:
            x0,y0,x1,y1=r['box'];coverage[y0:y1,x0:x1]+=1
        self.assertTrue(np.all(coverage==1))
        self.assertEqual(g[0]['position'],'B42');self.assertEqual(g[-1]['position'],'B27')
        self.assertEqual(len({r['position'] for r in g}),18)

    def test_native_xlsx_missing_harvest_and_date_not_repaired(self):
        rows=parse_biomass(ROOT/'data/raw/AaltoLettuce/4_Biomass.xlsx')
        self.assertEqual(len(rows),558)
        self.assertEqual(sum(r['fresh_mass_g'] is not None for r in rows),540)
        self.assertEqual(rows[0]['date_as_stored'],'2022-02-21')
        self.assertAlmostEqual(rows[0]['fresh_mass_g'],2.4)
        self.assertTrue(all(r['fresh_mass_g'] is None for r in rows if r['date_as_stored']=='2022-03-23'))

    def test_mass_join_only_unique_flagged_present(self):
        masses=[dict(date_as_stored='2022-02-21',position='B22',fresh_mass_g=2.4)]
        im=dict(date_as_stored='2022-02-21',biomass_flagged=True)
        self.assertEqual(valid_mass_join(im,'B22','present',masses)['fresh_mass_g'],2.4)
        self.assertIsNone(valid_mass_join(dict(im,biomass_flagged=False),'B22','present',masses))
        self.assertIsNone(valid_mass_join(im,'B22','absent',masses))
        with self.assertRaises(ValueError):valid_mass_join(im,'B22','present',masses*2)

    def test_tied_rank_correlations_constant_and_missing(self):
        np.testing.assert_array_equal(average_ranks([1,1,3]),[1.5,1.5,3])
        self.assertAlmostEqual(correlations([1,2,3],[2,4,6])['pearson'],1.)
        self.assertAlmostEqual(correlations([1,2,3],[6,4,2])['spearman'],-1.)
        self.assertIsNone(correlations([2,2,2],[1,2,3])['pearson'])
        self.assertEqual(correlations([1,None,3],[3,2,1])['n'],2)

    def test_temporal_gaps_zero_and_flags_not_hidden(self):
        rows=[dict(dat=1,area=100),dict(dat=2,area=40),dict(dat=4,area=500),dict(dat=5,area=0),dict(dat=6,area=5)]
        changes=temporal_changes(rows)
        self.assertEqual(len(changes),4)
        self.assertEqual(changes[0]['signed_change_percent'],-60)
        self.assertIsNone(changes[1]['signed_change_percent'])
        self.assertEqual(changes[-1]['zero_to_positive'],True)
        self.assertIsNone(changes[-1]['signed_change_percent'])

    def test_frozen_crop_C_parity_and_no_mass_input(self):
        from src.robust_roi import robust_segment,quality_check
        cfg=json.loads((ROOT/'experiments/EXP-003/config.json').read_text())
        rgb=np.full((100,130,3),80,np.uint8);rgb[25:75,40:90]=[40,160,30]
        p,r,d,q,a=position_predict(rgb,cfg)
        p2,r2,_,d2=robust_segment(rgb,cfg['A'],cfg['C'])
        np.testing.assert_array_equal(p,p2);np.testing.assert_array_equal(r,r2)
        self.assertEqual(q,quality_check(p2,p2,d2,cfg['qc_threshold']))
        self.assertEqual(list(inspect.signature(position_predict).parameters),['rgb','cfg'])


if __name__=='__main__':unittest.main()
