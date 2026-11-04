"""Synthetic fixtures only; source-label semantics are audited separately."""
import unittest
import numpy as np


class TestExp006Data(unittest.TestCase):
    def test_mots_native_instance_union_does_not_include_other_classes(self):
        from src.training.datasets import lettuce_mots_union
        instance=np.array([[0,1001,1001],[1002,0,1999]],dtype=np.uint16)
        self.assertTrue(np.array_equal(lettuce_mots_union(instance),instance>0))
        with self.assertRaises(ValueError):lettuce_mots_union(np.array([[2001]],dtype=np.uint16))
        with self.assertRaises(ValueError):lettuce_mots_union(np.array([[1000]],dtype=np.uint16))

    def test_locked_real_sources_keep_license_exclusions_and_disjoint_plant_tags(self):
        from pathlib import Path
        import json
        from src.training.common import read_manifest
        manifest=Path('data/processed/exp006/v1/manifest.jsonl')
        if not manifest.exists():self.skipTest('Manifest has not been prepared')
        rows=read_manifest(manifest);oak=[r for r in rows if r['dataset']=='oak1']
        partitions={s:{r['group_id'] for r in oak if r['split']==s} for s in ['TRAIN','DEV','PUBLIC_TEST']}
        self.assertEqual(len(oak),127)
        self.assertFalse(partitions['TRAIN']&partitions['DEV'] or partitions['TRAIN']&partitions['PUBLIC_TEST'] or partitions['DEV']&partitions['PUBLIC_TEST'])
        self.assertTrue(all(r['license_permits_research'] for r in rows))
        self.assertFalse(any(r['dataset'] in ['lettucemots','fieldcropweed','rumex','aalto'] for r in rows))
        anchor=[r for r in rows if r.get('diagnostic')]
        self.assertEqual(len(anchor),1);self.assertEqual(anchor[0]['split'],'TRAIN_ANCHOR')
        excluded=read_manifest('data/processed/exp006/v1/exclusions.jsonl')
        counts={d:sum(r['dataset']==d for r in excluded) for d in ['oak1','lettucemots','fieldcropweed','aalto','rumex']}
        self.assertEqual(counts,dict(oak1=1395,lettucemots=1308,fieldcropweed=3,aalto=727,rumex=809))
    def test_yolo_native_union_matches_exact_interior_and_rejects_other_classes(self):
        from src.training.datasets import rasterize_yolo
        mask=rasterize_yolo('0 .2 .2 .6 .2 .6 .6 .2 .6',10,10)
        self.assertEqual(mask.shape,(10,10));self.assertEqual(mask.dtype,np.bool_)
        self.assertTrue(mask[3,3]);self.assertFalse(mask[0,0])
        self.assertEqual(int(mask.sum()),16) # Existing project convention: round(x*(W-1)), inclusive fill.
        with self.assertRaises(ValueError):rasterize_yolo('1 .2 .2 .6 .2 .6 .6',10,10)
        with self.assertRaises(ValueError):rasterize_yolo('0 -2 .2 .6 .2 .6 .6',10,10)

    def test_split_is_deterministic_group_disjoint_and_merges_duplicate_identity(self):
        from src.training.datasets import grouped_split
        rows=[dict(id=str(i),group_id='plant-'+str(i//2),image_sha256=str(i)) for i in range(24)]
        rows[2]['image_sha256']=rows[0]['image_sha256']
        first=grouped_split(rows,6006);second=grouped_split(list(reversed(rows)),6006)
        self.assertEqual({r['id']:r['split'] for r in first},{r['id']:r['split'] for r in second})
        self.assertEqual(first[0]['split'],first[2]['split'])
        groups={r['group_id']:{x['split'] for x in first if x['group_id']==r['group_id']} for r in first}
        self.assertTrue(all(len(s)==1 for s in groups.values()));self.assertEqual({r['split'] for r in first},{'TRAIN','DEV','PUBLIC_TEST'})

    def test_letterbox_and_native_restore_keep_alignment_without_stretch(self):
        from src.training.datasets import letterbox,restore_probability
        mask=np.zeros((40,80),bool);mask[10:30,20:60]=True
        rgb=np.repeat((mask*255).astype(np.uint8)[...,None],3,axis=2)
        image,target,geometry=letterbox(rgb,mask,80)
        self.assertEqual(image.shape,(80,80,3));self.assertTrue(np.array_equal(image[...,0]>128,target))
        restored=restore_probability(target.astype(np.float32),geometry)>.5
        self.assertTrue(np.array_equal(restored,mask))

    def test_annotation_localized_crop_preserves_target_pixels_and_records_bounds(self):
        from src.training.datasets import target_crop
        mask=np.zeros((80,100),bool);mask[20:40,30:50]=True;rgb=np.zeros((80,100,3),np.uint8)
        image,target,bounds=target_crop(rgb,mask,.1)
        self.assertEqual(int(target.sum()),400);self.assertEqual(image.shape[:2],target.shape)
        x0,y0,x1,y1=bounds;self.assertTrue(np.array_equal(target,mask[y0:y1,x0:x1]))

    def test_metrics_are_independent_literal_counts_and_percentiles(self):
        from src.training.metrics import binary_metrics,aggregate
        gt=np.zeros((4,5),bool);gt.reshape(-1)[:10]=True
        pred=np.zeros_like(gt);pred.reshape(-1)[2:14]=True
        row=binary_metrics(pred,gt)
        self.assertEqual(row['false_positive_pixels'],4);self.assertEqual(row['false_negative_pixels'],2)
        self.assertAlmostEqual(row['iou'],8/14);self.assertAlmostEqual(row['dice'],16/22)
        self.assertEqual(row['absolute_percentage_area_error'],20)
        self.assertEqual(row['signed_percentage_area_error'],20)
        stats=aggregate([row,binary_metrics(gt,gt)])
        self.assertEqual(stats['mean_abs_percentage_area_error'],10);self.assertEqual(stats['p90_area_error_percent'],18)

    def test_augmentation_is_seeded_and_photometry_keeps_pale_target_pixels(self):
        from src.training.augmentation import augment,foreground_appearance
        mask=np.zeros((64,64),bool);mask[15:50,20:45]=True
        rgb=np.full((64,64,3),30,np.uint8);rgb[mask]=[50,160,40]
        pale=foreground_appearance(rgb,mask,np.random.default_rng(1),'chlorotic')
        self.assertTrue(np.array_equal(pale[~mask],rgb[~mask]));self.assertFalse(np.array_equal(pale[mask],rgb[mask]))
        first=augment(rgb,mask,6006,64);second=augment(rgb,mask,6006,64)
        self.assertTrue(np.array_equal(first[0],second[0]));self.assertTrue(np.array_equal(first[1],second[1]))
        self.assertEqual(first[1].dtype,np.bool_);self.assertTrue(first[1].any());self.assertEqual(first[0].shape[:2],first[1].shape)
        self.assertEqual(first[2]['scope'],'TRAINING_AUGMENTATION_ONLY')


if __name__=='__main__':unittest.main()
