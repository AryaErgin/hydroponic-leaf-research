import unittest
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from src.robust_roi import robust_segment, quality_check, measurement
from src.confirmation_audit import identity_overlap, canonical_capture, match_capture
from src.exp003 import failure_detection,tune,OUT
from src.verify_exp003 import independent_detection, trial_identity_matches

BASE=dict(h_min=25,h_max=100,s_min=60,v_min=15,exg_min=40,morphology_radius=1)
CFG=dict(background_quantile=.95,contrast_margin=.03,envelope_margin=.04)


class TestExp003(unittest.TestCase):
    def test_unknown_identity_is_not_zero_overlap(self):
        result=identity_overlap([dict(filename='rgb.png')],{'50GA1'})
        self.assertIsNone(result['overlap_count'])
        self.assertEqual(result['unknown_identity_rows'],1)
        known=identity_overlap([dict(tag='50GA1'),dict(tag='75RA5')],{'50GA1'})
        self.assertEqual(known['overlap_ids'],['50GA1'])
        self.assertEqual(known['unused_ids'],['75RA5'])

    def test_camera_prefix_is_not_plant_identity(self):
        self.assertEqual(canonical_capture('oakd_rgb_2024-8-28T11_31_32Z.png'),canonical_capture('oak_rgb_2024-8-28T11_31_32Z.txt'))

    def test_archive_image_prefix_mismatch_and_duplicate_guard(self):
        name='oakd_rgb_2024-8-28T11_31_32Z.png'
        actual='images/oak_rgb_2024-8-28T11_31_32Z.png'
        self.assertEqual(match_capture(name,[actual]),actual)
        with self.assertRaises(ValueError):match_capture(name,[actual,'images/'+name])

    def test_bright_gray_card_and_overhang_preserve_canopy(self):
        im=Image.new('RGB',(240,200),(90,145,85));d=ImageDraw.Draw(im)
        d.rectangle((82,50,167,160),fill=(135,135,135))
        d.ellipse((55,65,155,142),fill=(80,195,30))
        rgb=np.asarray(im)
        pred,roi,support,diag=robust_segment(rgb,BASE,CFG)
        self.assertIsNotNone(support)
        self.assertTrue(pred[100,60]);self.assertTrue(roi[100,60])
        self.assertFalse(pred[5,5]);self.assertLess(int(pred.sum()),10000)
        self.assertGreater(diag['overhang_pixel_area'],0)

    def test_dark_pot_crescent_cannot_crop_big_leaf(self):
        im=Image.new('RGB',(240,200),(110,110,110));d=ImageDraw.Draw(im)
        d.ellipse((75,35,143,100),fill=(20,20,20))
        d.ellipse((75,60,180,175),fill=(75,185,30))
        rgb=np.asarray(im)
        pred,roi,support,diag=robust_segment(rgb,BASE,CFG)
        self.assertTrue(pred[150,140]);self.assertTrue(pred[115,175])
        self.assertEqual(diag['localization_source'],'canopy_envelope')

    def test_bad_mask_is_flagged_before_area_reporting(self):
        reference=np.zeros((40,40),bool);reference[8:32,8:32]=True
        bad=np.zeros_like(reference);bad[8:12,8:12]=True
        q=quality_check(bad,reference,dict(localization_confidence=.95))
        self.assertTrue(q['qc_flag']);self.assertLess(q['confidence'],.7)
        out=measurement(bad,q)
        self.assertEqual(out['raw_pixel_area'],16)
        self.assertIsNone(out['reported_pixel_area'])

    def test_ambiguous_empty_image_withholds_area(self):
        rgb=np.full((80,100,3),80,np.uint8)
        pred,roi,support,diag=robust_segment(rgb,BASE,CFG)
        self.assertLess(diag['localization_confidence'],.7)
        self.assertTrue(quality_check(pred,pred,diag)['qc_flag'])

    def test_displaced_unlocalized_plant_is_not_confident(self):
        rgb=np.full((120,180,3),100,np.uint8);rgb[5:30,5:30]=[70,190,30]
        pred,roi,support,diag=robust_segment(rgb,BASE,CFG)
        self.assertTrue(quality_check(pred,pred,diag)['qc_flag'])

    def test_outputs_are_create_only_and_clipping_bool_is_parsed(self):
        with tempfile.TemporaryDirectory(dir=OUT) as tmp:
            d=Path(tmp);(d/'config.json').write_text('preserve')
            with self.assertRaises(FileExistsError):tune(d)
            self.assertEqual((d/'config.json').read_text(),'preserve')
        stats=failure_detection([dict(qc_flag='True',material_roi_clipping='False')],'material_roi_clipping')
        self.assertEqual(stats['false_positive'],1)

    def test_qc_precision_recall_and_undefined_denominators(self):
        rows=[dict(qc_flag=True,signed_percentage_area_error=150),dict(qc_flag=True,signed_percentage_area_error=3),dict(qc_flag=False,signed_percentage_area_error=-80),dict(qc_flag=False,signed_percentage_area_error=2)]
        result=failure_detection(rows)
        self.assertEqual([result[k] for k in ['true_positive','false_positive','false_negative','true_negative']],[1,1,1,1])
        self.assertEqual(result['precision'],.5);self.assertEqual(result['recall'],.5)
        result=failure_detection([dict(qc_flag=False,signed_percentage_area_error=2)])
        self.assertIsNone(result['precision']);self.assertIsNone(result['recall'])

    def test_independent_QC_auditor_uses_failure_truth(self):
        rows=[dict(qc_flag='True',signed_percentage_area_error='-70',gt_outside_roi_percent='20'),
              dict(qc_flag='False',signed_percentage_area_error='120',gt_outside_roi_percent='0'),
              dict(qc_flag='True',signed_percentage_area_error='3',gt_outside_roi_percent='0')]
        result=independent_detection(rows)
        self.assertEqual(result['precision'],.5);self.assertEqual(result['recall'],.5)
        clipping=independent_detection(rows,clipping=True)
        self.assertEqual(clipping['recall'],1)

    def test_independent_trial_audit_rejects_wrong_plant_tag(self):
        selected=[dict(filename='x',tag='50GA1',split='tuning')]
        trial=[dict(filename='x',tag='50GB1',split='tuning')]
        self.assertFalse(trial_identity_matches(trial,selected))
        trial[0]['tag']='50GA1'
        self.assertTrue(trial_identity_matches(trial,selected))


if __name__=='__main__':unittest.main()
