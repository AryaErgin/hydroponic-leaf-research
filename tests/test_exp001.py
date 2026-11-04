import unittest
import numpy as np
from src.lettuce_cv import rasterize_yolo, pixel_metrics, segment, assign_split, capture_stage
from src.exp001 import select_config, assert_subset_integrity, verify_aggregates


class TestScientificContract(unittest.TestCase):
    def test_inventory_cli_honors_new_output_and_preserves_original(self):
        from pathlib import Path
        import tempfile, csv
        from unittest.mock import patch
        from PIL import Image
        from src.exp001 import main
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d); raw=p/'raw'; (raw/'images').mkdir(parents=True)
            (raw/'segmentation/labels/train').mkdir(parents=True)
            Image.new('RGB',(10,10),'green').save(raw/'images/example.png')
            (raw/'segmentation/labels/train/example.txt').write_text('0 0.2 0.2 0.8 0.2 0.8 0.8 0.2 0.8')
            row=dict(filename='example.png',sensor='oak',data='rgb',tag='50GA1',width=10,height=10,date='01/09/2024',hour=1,minute=2,second=3)
            with open(raw/'metadata.csv','w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            dest=p/'new_inventory'
            with patch('src.exp001.RAW',raw), patch('sys.argv',['exp001','inventory','--output',str(dest)]):
                main()
            self.assertTrue((dest/'inventory.csv').exists())
            self.assertTrue((dest/'review_queue.csv').exists())
            with open(dest/'inventory.csv',newline='') as f:
                result=list(csv.DictReader(f))
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['tag'],'50GA1')

    def test_aggregate_integrity_rejects_forged_mean(self):
        from src.exp001 import aggregate
        metrics = pixel_metrics(np.array([[True,False]]), np.array([[True,False]]))
        rows=[dict(split='evaluation',stage='early',tag='50GA2',**metrics)]
        stats=aggregate(rows)
        verify_aggregates(stats,rows)
        stats['evaluation_all']['mean_iou']=0.1
        with self.assertRaises(AssertionError):
            verify_aggregates(stats,rows)

    def test_existing_visuals_are_never_overwritten(self):
        from src.exp001 import review_sheet, case_image
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d); (p/'review_sheets').mkdir()
            target=p/'review_sheets/existing.jpg'; target.write_bytes(b'old')
            with patch('src.exp001.OUT',p):
                with self.assertRaises(FileExistsError):
                    review_sheet(0,1,'existing.jpg')
            with self.assertRaises(FileExistsError):
                case_image({},None,None,target)
            self.assertEqual(target.read_bytes(),b'old')

    def test_tuning_rejects_evaluation_and_chooses_measured_best(self):
        from src.lettuce_cv import color_features
        rgb = np.array([[[0,255,0],[90,100,90]]], np.uint8)
        gt = np.array([[True,False]])
        configs = [dict(h_min=15,h_max=100,s_min=0,v_min=0,exg_min=v,morphology_radius=0) for v in [10,25]]
        best, scores = select_config([('tuning',gt,color_features(rgb))],configs)
        self.assertEqual(best['exg_min'],25)
        self.assertEqual(scores[0]['tuning_mean_iou'],0.5)
        self.assertEqual(scores[1]['tuning_mean_iou'],1.0)
        with self.assertRaises(ValueError):
            select_config([('evaluation',gt,color_features(rgb))],configs)

    def test_integrity_rejects_changed_frozen_selection(self):
        from pathlib import Path
        import tempfile, json
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d)
            (p/'selected_images.csv').write_text('changed')
            (p/'subset_freeze.json').write_text(json.dumps({'selected_sha256':'wrong'}))
            with self.assertRaises(ValueError):
                assert_subset_integrity(p)

    def test_union_and_native_canvas(self):
        label = '0 0.1 0.1 0.5 0.1 0.5 0.5 0.1 0.5'
        a = rasterize_yolo(label, 100, 60)
        b = rasterize_yolo(label + '\n' + label, 100, 60)
        self.assertEqual(a.shape, (60, 100))
        self.assertTrue(np.array_equal(a, b))
        self.assertTrue(a[20, 25])
        self.assertFalse(a[55, 90])

    def test_invalid_and_empty_labels(self):
        self.assertFalse(rasterize_yolo('', 10, 10).any())
        for label in ['1 0 0 1 0 1 1', '0 0 0 1', '0 nan 0 1 0 1 1', '0 0 0 2 0 1 1']:
            with self.assertRaises(ValueError):
                rasterize_yolo(label, 10, 10)

    def test_metrics_independent_counts(self):
        g = np.array([[1, 1], [0, 0]], bool)
        p = np.array([[1, 0], [1, 1]], bool)
        m = pixel_metrics(g, p)
        self.assertAlmostEqual(m['iou'], 0.25)
        self.assertAlmostEqual(m['dice'], 0.4)
        self.assertEqual(m['gt_pixel_area'], 2)
        self.assertEqual(m['predicted_pixel_area'], 3)
        self.assertEqual(m['absolute_pixel_area_error'], 1)
        self.assertEqual(m['percentage_area_error'], 50)
        with self.assertRaises(ValueError):
            pixel_metrics(np.zeros((2, 2), bool), p)

    def test_color_segmentation_and_no_int_overflow(self):
        rgb = np.array([[[0, 255, 0], [255, 0, 0], [255, 255, 255], [0, 0, 0]]], np.uint8)
        cfg = dict(h_min=25, h_max=100, s_min=35, v_min=25, exg_min=25, morphology_radius=0)
        self.assertEqual(segment(rgb, cfg).tolist(), [[True, False, False, False]])

    def test_groups_and_stages(self):
        self.assertEqual(assign_split('50GA1'), 'tuning')
        self.assertEqual(assign_split('50GA2'), 'evaluation')
        self.assertEqual(capture_stage(0, 30), 'early')
        self.assertEqual(capture_stage(10, 30), 'mid')
        self.assertEqual(capture_stage(29, 30), 'late')


if __name__ == '__main__':
    unittest.main()
