"""Calibration geometry tests use synthetic references, never biological approvals."""
import hashlib
import io
import unittest

import numpy as np
from PIL import Image, ImageDraw


def encoded(image, fmt='PNG', **kwargs):
    stream=io.BytesIO();image.save(stream,format=fmt,**kwargs);return stream.getvalue()


def fixture():
    rng=np.random.default_rng(218)
    a=rng.integers(30,210,(400,300,3),dtype=np.uint8)
    image=Image.fromarray(a).filter(__import__('PIL.ImageFilter',fromlist=['GaussianBlur']).GaussianBlur(1))
    draw=ImageDraw.Draw(image);draw.ellipse((60,110,240,290),outline=(235,235,235),width=5)
    draw.ellipse((100,150,200,250),fill=(60,150,45))
    raw=encoded(image)
    cal=dict(id='fixture-reference',calibration_type='reviewed_pot',reference_size_px=[300,400],
        cm_per_pixel=5.82/180,pot_diameter_cm=5.82,pot_diameter_pixels=180,
        provenance=dict(reference_image_sha256=hashlib.sha256(raw).hexdigest(),
            reviewed_calibration=dict(diameter_points_px=[[60,200],[240,200]])))
    return image,raw,cal


class TestCalibrationGeometry(unittest.TestCase):
    def assess(self,image,cal=None,reference=True,geometry=True,**kwargs):
        from src.webapp.calibration_reuse import assess
        original,raw,base=fixture()
        data=encoded(image)
        return assess(np.array(image),data,cal or base,geometry,reference_rgb=np.array(original) if reference else None,**kwargs)

    def test_identical_reference(self):
        image,_,cal=fixture();r=self.assess(image,cal)
        self.assertEqual(r['status'],'VERIFIED');self.assertEqual(r['effective_cm_per_pixel'],cal['cm_per_pixel'])

    def test_proportional_resize_transforms_reference_points_and_scale(self):
        image,_,cal=fixture();r=self.assess(image.resize((384,512),Image.Resampling.LANCZOS),cal)
        self.assertEqual(r['status'],'VERIFIED',r)
        self.assertAlmostEqual(r['effective_cm_per_pixel'],cal['cm_per_pixel']/1.28)
        self.assertEqual(r['reference_frame']['size_px'],[300,400])
        np.testing.assert_allclose(r['image_reference_points_px'],[[76.8,256],[307.2,256]])
        self.assertEqual(cal['reference_size_px'],[300,400])

    def test_whatsapp_resize_and_jpeg_recompression(self):
        image,_,cal=fixture();small=image.resize((240,320),Image.Resampling.LANCZOS)
        jpeg=Image.open(io.BytesIO(encoded(small,'JPEG',quality=72))).convert('RGB')
        r=self.assess(jpeg,cal);self.assertEqual(r['status'],'VERIFIED',r)
        self.assertAlmostEqual(r['effective_cm_per_pixel'],cal['cm_per_pixel']/.8)

    def test_same_aspect_ratio_without_reference_is_not_evidence(self):
        image,_,_=fixture();r=self.assess(image.resize((384,512)),reference=False)
        self.assertEqual(r['status'],'CONFIRMATION_REQUIRED');self.assertIsNone(r['effective_cm_per_pixel'])

    def test_different_aspect_crop_requires_recalibration(self):
        image,_,_=fixture();r=self.assess(image.crop((0,0,250,400)))
        self.assertEqual(r['status'],'RECALIBRATION_REQUIRED');self.assertIsNone(r['effective_cm_per_pixel'])

    def test_crop_same_aspect_changed_zoom_or_distance_never_auto_reuses(self):
        image,_,_=fixture()
        variants={'same_aspect_crop':image.crop((30,40,270,360)).resize(image.size),
                  'zoom':image.crop((15,20,285,380)).resize((384,512)),
                  'distance':image.resize((240,320))}
        distant=Image.new('RGB',image.size,(80,80,80));distant.paste(variants['distance'],(30,40));variants['distance']=distant
        for name,changed in variants.items():
            with self.subTest(name=name):
                r=self.assess(changed);self.assertNotEqual(r['status'],'VERIFIED',r)
                self.assertIsNone(r['effective_cm_per_pixel'])

    def test_different_physical_pot_diameter_rejected_even_if_image_matches(self):
        image,_,_=fixture();r=self.assess(image,observed_pot_diameter_cm=8)
        self.assertEqual(r['status'],'RECALIBRATION_REQUIRED');self.assertIsNone(r['effective_cm_per_pixel'])

    def test_changed_rim_without_background_change_never_auto_reuses(self):
        image,_,_=fixture();a=np.array(image);original=np.array(fixture()[0])
        # Replace reference pot and rim with a differently sized pot at unchanged camera framing.
        a[100:301,50:251]=110
        changed=Image.fromarray(a);draw=ImageDraw.Draw(changed);draw.ellipse((80,130,220,270),outline='white',width=5)
        r=self.assess(changed);self.assertNotEqual(r['status'],'VERIFIED',r)

    def test_daily_leaf_changes_do_not_invalidate_rim_match(self):
        image,_,_=fixture();draw=ImageDraw.Draw(image);draw.ellipse((90,140,210,260),fill=(190,190,70))
        r=self.assess(image.resize((384,512)));self.assertEqual(r['status'],'VERIFIED',r)

    def test_confirmation_is_image_bound_and_requires_all_explicit_claims(self):
        from src.webapp.calibration_reuse import confirm
        image,_,cal=fixture();r=self.assess(image.resize((384,512)),reference=False)
        claims=dict(image_sha256=r['image_sha256'],calibration_id=cal['id'],same_physical_pot=True,
            same_camera_geometry=True,full_frame_resize_only=True)
        confirmed=confirm(r,claims)
        self.assertEqual(confirmed['status'],'CONFIRMED');self.assertAlmostEqual(confirmed['effective_cm_per_pixel'],cal['cm_per_pixel']/1.28)
        for key in claims:
            wrong={**claims,key:False if isinstance(claims[key],bool) else 'wrong'}
            with self.subTest(key=key),self.assertRaises(ValueError):confirm(r,wrong)
        bad=self.assess(image.crop((0,0,250,400)))
        with self.assertRaises(ValueError):confirm(bad,{**claims,'image_sha256':bad['image_sha256']})

    def test_invalid_reference_scale_is_blocked(self):
        image,_,cal=fixture();cal['cm_per_pixel']=float('nan')
        r=self.assess(image,cal);self.assertEqual(r['status'],'RECALIBRATION_REQUIRED')

    def test_elliptical_pot_background_is_not_rim_evidence(self):
        image,_,cal=fixture();a=np.array(image);a[100:301,50:251]=110
        ref=Image.fromarray(a);ImageDraw.Draw(ref).ellipse((60,170,240,230),outline='white',width=5)
        cal['provenance']['reference_image_sha256']=hashlib.sha256(encoded(ref)).hexdigest()
        new=Image.fromarray(a);ImageDraw.Draw(new).ellipse((80,175,220,225),outline='white',width=5)
        from src.webapp.calibration_reuse import assess
        r=assess(np.array(new),encoded(new),cal,True,reference_rgb=np.array(ref))
        self.assertNotEqual(r['status'],'VERIFIED',r)
        self.assertIsNone(r['effective_cm_per_pixel'])

    def test_elliptical_plane_shape_change_requires_confirmation(self):
        from PIL import ImageFilter
        base=Image.fromarray(np.random.default_rng(218).integers(30,210,(400,300,3),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(1))
        _,_,cal=fixture();ref=base.copy()
        ImageDraw.Draw(ref).ellipse((60,170,240,230),fill=(70,70,70),outline=(240,240,240),width=5)
        cal['provenance']['reference_image_sha256']=hashlib.sha256(encoded(ref)).hexdigest()
        changed=base.copy();ImageDraw.Draw(changed).ellipse((60,163,240,237),fill=(70,70,70),outline=(240,240,240),width=5)
        from src.webapp.calibration_reuse import assess
        r=assess(np.array(changed),encoded(changed),cal,True,reference_rgb=np.array(ref))
        self.assertNotEqual(r['status'],'VERIFIED',r)
