"""Geometry fixtures, not independent biological validation."""
import unittest
import numpy as np
from src.webapp.lamina import refine_lamina, propose_lamina
from src.webapp.service import growth


def blade_with_connector():
    y,x=np.indices((180,180))
    blade=((x-90)/27)**2+((y-65)/38)**2<=1
    parent=blade.copy();parent[100:160,88:93]=True
    return blade,parent


class TestLamina(unittest.TestCase):
    def test_long_narrow_connector_excluded_without_eroding_blade(self):
        blade,parent=blade_with_connector()
        mask,meta=refine_lamina(parent)
        self.assertTrue(mask[blade].all())
        self.assertFalse(mask[125:155,88:93].any())
        self.assertFalse((mask&~parent).any())
        self.assertEqual(meta['excluded_pixel_area'],int((parent&~mask).sum()))

    def test_color_does_not_control_tissue_exclusion(self):
        blade,parent=blade_with_connector();expected=None
        for color in ([60,160,40],[210,210,135],[220,190,40],[140,110,90],[175,175,175]):
            rgb=np.broadcast_to(np.array(color,np.uint8),(*parent.shape,3)).copy()
            mask,labels,meta=propose_lamina(parent,rgb)
            if expected is None:expected=mask
            np.testing.assert_array_equal(mask,expected)
            self.assertTrue(mask[blade].all())
            np.testing.assert_array_equal(labels>0,mask)

    def test_small_young_and_narrow_blades_preserved(self):
        y,x=np.indices((90,90));parent=(((x-25)/3)**2+((y-40)/25)**2<=1)
        parent|=((x-65)/3)**2+((y-20)/4)**2<=1
        mask,meta=refine_lamina(parent)
        np.testing.assert_array_equal(mask,parent)
        rgb=np.full((*mask.shape,3),180,np.uint8)
        _,labels,_=propose_lamina(parent,rgb)
        self.assertEqual(len(np.unique(labels[labels>0])),2)

    def test_short_uncertain_connector_is_kept(self):
        blade,parent=blade_with_connector();parent[115:]=False
        mask,_=refine_lamina(parent)
        np.testing.assert_array_equal(mask,parent)

    def test_one_pixel_orphan_is_reassigned_not_counted_or_lost(self):
        y,x=np.indices((100,100));parent=((x-45)/20)**2+((y-45)/25)**2<=1
        parent[45,68]=True
        mask,labels,meta=propose_lamina(parent,np.full((100,100,3),130,np.uint8))
        self.assertEqual(len(np.unique(labels[labels>0])),1)
        self.assertEqual(int(mask.sum()),int(parent.sum()))
        self.assertEqual(len(meta['micro_instance_reassignments']),1)

    def test_isolated_tiny_tissue_is_uncertain_and_kept(self):
        mask=np.zeros((30,30),bool);mask[10,10]=True
        lamina,labels,_=propose_lamina(mask,np.full((30,30,3),130,np.uint8))
        np.testing.assert_array_equal(lamina,mask)
        self.assertEqual(labels[10,10],1)

    def test_growth_never_compares_foreground_to_lamina(self):
        rows=[dict(date='2026-10-01',projected_area_cm2=10,calibration_id='same'),
              dict(date='2026-10-02',projected_area_cm2=8,calibration_id='same',measurement_target='visible_leaf_lamina')]
        result=growth(rows)
        self.assertEqual(result['reason'],'measurement_target_changed')
        self.assertIsNone(result['daily_growth_cm2'])
