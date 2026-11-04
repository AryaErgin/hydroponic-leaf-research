import unittest
import xml.etree.ElementTree as ET
import numpy as np


class TestRumexContract(unittest.TestCase):
    def test_shadow_and_exg_rejections_are_reported_without_double_counting(self):
        from src.analyze_expr01 import threshold_diagnostics
        rgb=np.array([[[0,100,0],[50,50,50],[0,20,0],[100,0,100]]],dtype=np.uint8)
        d=threshold_diagnostics(rgb,np.ones((1,4),bool),dict(h_min=25,h_max=80,s_min=20,v_min=40,exg_min=40,morphology_radius=0))
        self.assertEqual(d['gt_exg_rejected_pct'],75)
        self.assertEqual(d['gt_dark_rejected_pct'],25)
        self.assertEqual(d['gt_any_color_rejected_pct'],75)

    def test_independent_verifier_catches_spatial_error_despite_equal_area(self):
        from src.verify_expr01 import independent_metrics, independent_summary
        gt=np.array([[1,1,0],[0,0,0]],bool); pred=np.array([[1,0,1],[0,0,0]],bool)
        m=independent_metrics(gt,pred)
        self.assertAlmostEqual(m['iou'],1/3); self.assertEqual(m['dice'],.5)
        self.assertEqual(m['absolute_pixel_area_error'],0)
        self.assertEqual(m['false_positive_pixels'],1)
        s=independent_summary([m,independent_metrics(gt,np.zeros_like(gt))])
        self.assertEqual(s['p90_area_error_pct'],90)
        self.assertEqual(s['p95_area_error_pct'],95)

    def test_stage_guard_and_selection_lock_reject_reuse_and_drift(self):
        import tempfile
        from pathlib import Path
        from src.expr01 import guard_new, verify_selection_lock, save_json
        from src.inspect_rumex_dataset import digest
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d); (p/'pred').mkdir(); (p/'pred/a.png').write_bytes(b'old')
            with self.assertRaises(FileExistsError): guard_new([p/'pred'])
            for name in ['selected_images.csv','excluded_images.csv','screen_decisions.json','protocol.md']:
                (p/name).write_text('original')
            save_json(p/'selection_lock.json',dict(selected_sha256=digest(p/'selected_images.csv'),excluded_sha256=digest(p/'excluded_images.csv'),decisions_sha256=digest(p/'screen_decisions.json'),protocol_sha256=digest(p/'protocol.md')))
            verify_selection_lock(p)
            (p/'selected_images.csv').write_text('changed')
            with self.assertRaises(ValueError): verify_selection_lock(p)

    def test_review_budget_deterministic_and_complete_screen_required(self):
        from src.expr01 import candidate_entries, validate_screen
        rows=[dict(archive_entry=f'RumexLeaves/iNaturalist/{i}.jpg',subset='iNaturalist',creator=f'person{i//2}') for i in range(140)]
        rows.append(dict(archive_entry='RumexLeaves/RoboRumex/a.png',subset='RoboRumex',creator=''))
        chosen=candidate_entries(rows)
        self.assertEqual(len(chosen),61)
        self.assertEqual(chosen,candidate_entries(list(reversed(rows))))
        with self.assertRaises(ValueError): validate_screen([dict(review_id=0),dict(review_id=1)],{'0':{'include':True,'reason':'clean'}})
        with self.assertRaises(ValueError): validate_screen([dict(review_id=0)],{'0':{'include':False,'reason':''}})

    def test_refuses_to_overwrite_artifacts(self):
        import tempfile
        from pathlib import Path
        from src.expr01 import save_json
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d)/'a.json'; save_json(p,dict(a=1))
            with self.assertRaises(FileExistsError): save_json(p,dict(a=2))
            self.assertIn('1',p.read_text())

    def test_pixel_union_ignores_stems_and_accepts_continuous_edge(self):
        from src.rumex_cv import cvat_mask
        im = ET.fromstring('<image width="5" height="5"><polygon label="leaf_blade" points="0,0;5,0;5,5;0,5"/><polygon label="leaf_blade" points="1,1;3,1;3,3;1,3"/><polyline label="leaf_stem" points="0,0;4,4"/></image>')
        self.assertEqual(int(cvat_mask(im).sum()), 25)

    def test_invalid_reference_is_rejected(self):
        from src.rumex_cv import cvat_mask
        for points in ['0,0;nan,2;2,0', '0,0;6,2;2,0', '1,1;1,1;1,1', '0,0;1,1;2,2']:
            with self.subTest(points=points), self.assertRaises(ValueError):
                cvat_mask(ET.fromstring(f'<image width="5" height="5"><polygon label="leaf_blade" points="{points}"/></image>'))
        with self.assertRaises(ValueError):
            cvat_mask(ET.fromstring('<image width="5" height="5"/>'))

    def test_site_and_creator_groups_cannot_cross_splits(self):
        from src.rumex_cv import metadata_group, assert_groups
        a = metadata_group('RumexLeaves/RoboRumex/20210806_stengard/seq0/imgs/a.png', '')
        b = metadata_group('RumexLeaves/RoboRumex/20211006_stengard/seq0/imgs/b.png', '')
        c = metadata_group('RumexLeaves/RoboRumex/20210806_stengard/seq1/imgs/c.png', '')
        self.assertEqual(a, b); self.assertEqual(a, c)
        self.assertEqual(a[1], 'tuning')
        with self.assertRaises(ValueError):
            assert_groups([dict(group='g', split='tuning'), dict(group='g', split='evaluation')])
        with self.assertRaises(ValueError):
            metadata_group('RumexLeaves/iNaturalist/a.jpg', '')

    def test_tuning_is_group_macro_and_rejects_eval(self):
        from src.rumex_cv import tuning_winner
        rows = [dict(config_index=i, group=g, split='tuning', iou=x)
                for i,g,x in [(0,'a',.9),(0,'a',.9),(0,'b',.1),(1,'a',.6),(1,'a',.6),(1,'b',.6)]]
        self.assertEqual(tuning_winner(rows)[0], 1)
        with self.assertRaises(ValueError):
            tuning_winner(rows + [dict(config_index=0,group='x',split='evaluation',iou=1)])

    def test_area_errors_report_empty_predictions_and_tails(self):
        from src.rumex_cv import summarize
        from src.lettuce_cv import pixel_metrics
        gt=np.ones((2,2),dtype=bool)
        rows=[pixel_metrics(gt,gt),pixel_metrics(gt,np.zeros_like(gt))]
        s=summarize(rows)
        self.assertEqual(s['mean_area_error_pct'],50)
        self.assertEqual(s['catastrophic_under'],1)
        self.assertEqual(s['p95_area_error_pct'],95)


if __name__ == '__main__': unittest.main()
