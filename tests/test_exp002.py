import unittest
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from src.roi_cv import component_labels, infer_roi, adaptive_segment
from src.exp002 import summarize, choose_trial, save_mask, tune, ROOT
from src.verify_exp002 import quantile, direct_summary, direct_metrics, audit


class TestExp002(unittest.TestCase):
    def test_components_join_diagonals_but_separate_islands(self):
        mask=np.array([[1,0,0,1],[0,1,0,0],[0,0,0,1]],bool)
        labels,stats=component_labels(mask)
        self.assertEqual(len(stats),3)
        self.assertEqual(labels[0,0],labels[1,1])
        self.assertNotEqual(labels[0,3],labels[2,3])
        self.assertEqual(sorted(s['area'] for s in stats),[1,1,2])

    def test_rgb_roi_and_card_suppress_green_turf(self):
        im=Image.new('RGB',(180,140),(90,150,80)); draw=ImageDraw.Draw(im)
        draw.rectangle((60,35,120,110),fill=(45,45,45))
        draw.ellipse((76,53,108,92),fill=(90,210,60))
        rgb=np.asarray(im)
        roi,diag,support=infer_roi(rgb,dict(expansion=1.75,fallback_radius=0.48))
        self.assertTrue(roi[70,90]); self.assertFalse(roi[0,0])
        self.assertTrue(diag['green_periphery'])
        cfg=dict(h_max=70,weak_exg=0.025,bright_radius=4)
        pred=adaptive_segment(rgb,roi,support,diag,cfg)
        self.assertTrue(pred[70,90]); self.assertFalse(pred[10,10])
        self.assertLess(int(pred.sum()),2000)

    def test_dark_leaf_and_nearby_white_tissue_recovery(self):
        rgb=np.full((50,50,3),40,np.uint8)
        rgb[15:35,15:35]=[35,65,25]
        rgb[21:27,21:27]=[248,255,248]
        roi=np.ones((50,50),bool)
        diag=dict(green_periphery=False,center_x=25,center_y=25,anchor_radius=12)
        cfg=dict(h_max=70,weak_exg=0.025,bright_radius=4)
        pred=adaptive_segment(rgb,roi,None,diag,cfg)
        self.assertTrue(pred[18,18]); self.assertTrue(pred[24,24])
        self.assertFalse(pred[0,0])

    def test_percentiles_and_signed_counts(self):
        rows=[dict(tag='a',iou=1,dice=1,percentage_area_error=v,signed_percentage_area_error=s,absolute_pixel_area_error=v*10,gt_pixel_area=1000,predicted_pixel_area=1000+s*10,false_positive_pixels=0,false_negative_pixels=0) for v,s in [(0,0),(10,10),(20,-20),(100,100)]]
        stats=summarize(rows)
        self.assertEqual(stats['mean_area_error'],32.5)
        self.assertEqual(stats['median_area_error'],15)
        self.assertAlmostEqual(stats['p90_area_error'],76)
        self.assertAlmostEqual(stats['p95_area_error'],88)
        self.assertEqual(stats['overestimated_images'],2)
        self.assertEqual(stats['underestimated_images'],1)

    def test_trial_selector_rejects_evaluation(self):
        with self.assertRaises(ValueError):
            choose_trial([dict(split='evaluation',tag='50GA2',iou=1,config_index=0)], [{}])

    def test_tuning_weights_plants_equally(self):
        rows=[]
        for i in range(2):
            for name,tag,score in [('x','a',.9 if i==0 else .7),('y','a',.9 if i==0 else .7),('z','b',.2 if i==0 else .6)]:
                rows.append(dict(filename=name,tag=tag,split='tuning',config_index=i,iou=score))
        winner,_=choose_trial(rows,[dict(choice=0),dict(choice=1)])
        self.assertEqual(winner['choice'],1)

    def test_existing_outputs_are_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'experiments/EXP-002') as tmp:
            d=Path(tmp); p=d/'mask.png'
            save_mask(p,np.ones((3,3),bool));original=p.read_bytes()
            with self.assertRaises(FileExistsError):save_mask(p,np.zeros((3,3),bool))
            self.assertEqual(p.read_bytes(),original)
            (d/'config.json').write_text('existing')
            with self.assertRaises(FileExistsError):tune(d)
            self.assertEqual((d/'config.json').read_text(),'existing')

    def test_independent_audit_counts_and_percentiles(self):
        gt=np.array([[1,1,0],[0,0,0]],bool)
        pred=np.array([[1,0,0],[1,1,0]],bool)
        actual=direct_metrics(gt,pred)
        self.assertEqual(actual['iou'],.25)
        self.assertEqual(actual['dice'],.4)
        self.assertEqual(actual['false_positive_pixels'],2)
        self.assertEqual(actual['false_negative_pixels'],1)
        self.assertEqual(actual['signed_percentage_area_error'],50)
        self.assertAlmostEqual(quantile([0,10,20,100],.95),88)
        summary=direct_summary([dict(tag='a',**actual)])
        self.assertEqual(summary['overestimated_images'],1)
        self.assertEqual(summary['mean_overestimation'],50)
        self.assertEqual(summary['underestimated_images'],0)
        with tempfile.TemporaryDirectory(dir=ROOT/'experiments/EXP-002') as tmp:
            d=Path(tmp);(d/'independent_verification.json').write_text('preserved')
            with self.assertRaises(FileExistsError):audit(d)
            self.assertEqual((d/'independent_verification.json').read_text(),'preserved')


if __name__=='__main__': unittest.main()
