import base64
import unittest
import numpy as np
from src.webapp import service

class TestLeafInstances(unittest.TestCase):
    def engine(self):
        self.assertTrue(hasattr(service,'leaf_instances'),'Instance engine missing')
        return service.leaf_instances

    def test_single_ellipse_and_disconnected_leaves_match_hand_ground_truth(self):
        e=self.engine();y,x=np.ogrid[:80,:100]
        one=((x-25)/14)**2+((y-40)/22)**2<=1
        labels=e.propose_instances(one,np.zeros((80,100,3),np.uint8))
        self.assertEqual(set(np.unique(labels)),{0,1});np.testing.assert_array_equal(labels>0,one)
        two=one|(((x-75)/12)**2+((y-40)/18)**2<=1)
        labels=e.propose_instances(two,np.zeros((80,100,3),np.uint8))
        self.assertEqual(len(np.unique(labels[labels>0])),2)
        np.testing.assert_array_equal(labels>0,two)

    def test_touching_lobes_use_shape_seeds_not_just_components(self):
        e=self.engine();y,x=np.ogrid[:80,:100]
        mask=((x-33)**2+(y-40)**2<=20**2)|((x-67)**2+(y-40)**2<=20**2)
        labels=e.propose_instances(mask,np.zeros((80,100,3),np.uint8))
        self.assertEqual(len(np.unique(labels[labels>0])),2)
        self.assertNotEqual(labels[40,25],labels[40,75])
        np.testing.assert_array_equal(labels>0,mask)

    def test_split_selected_instance_preserves_every_pixel_and_other_instance(self):
        e=self.engine();mask=np.zeros((20,30),bool);mask[2:18,2:20]=True;mask[2:8,23:28]=True
        labels=np.zeros(mask.shape,np.uint16);labels[2:18,2:20]=1;labels[2:8,23:28]=7
        cut=e.split_instance(labels,1,[[11,1],[11,18]])
        self.assertEqual(set(np.unique(cut)),{0,1,7,8})
        self.assertEqual(cut[8,3],1);self.assertEqual(cut[8,18],8)
        np.testing.assert_array_equal(cut[labels==7],labels[labels==7])
        np.testing.assert_array_equal(cut>0,mask)
        with self.assertRaises(ValueError):e.split_instance(labels,1,[[5,5],[5,6]])

    def test_native_tiny_fragments_full_mask_empty_and_scaled_summary(self):
        e=self.engine();mask=np.zeros((701,900),bool);mask[20:600,10:350]=True;mask[700,899]=True
        labels=e.propose_instances(mask,np.zeros((701,900,3),np.uint8));np.testing.assert_array_equal(labels>0,mask)
        self.assertEqual(len(np.unique(labels[labels>0])),2)
        m=e.summary(labels,mask,.1,reviewed=True)
        self.assertEqual(sum(m['per_leaf_pixel_area']),197201);self.assertAlmostEqual(m['total_projected_area_cm2'],1972.01)
        self.assertEqual(m['leaf_count'],2)
        self.assertIsNone(e.summary(labels,mask,None)['mean_leaf_area_cm2'])
        full=np.ones((16,16),bool);self.assertTrue((e.propose_instances(full,np.zeros((16,16,3),np.uint8))>0).all())
        self.assertEqual(e.summary(np.zeros((2,2),np.uint16),np.zeros((2,2),bool),None)['leaf_count'],0)

if __name__=='__main__':unittest.main()
