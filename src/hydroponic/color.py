"""Frozen classical RGB features, independent of species names."""
import numpy as np
from PIL import Image,ImageFilter

def color_features(rgb):
    """HSV scaled to OpenCV convention H 0..179, S/V 0..255; ExG raw signed RGB."""
    hsv = np.asarray(Image.fromarray(rgb).convert('HSV'))
    h = hsv[:, :, 0].astype(np.float32) * (180/256)
    r, g, b = [rgb[:, :, i].astype(np.int16) for i in range(3)]
    return h, hsv[:, :, 1], hsv[:, :, 2], 2*g-r-b


def segment_features(features, config):
    h, s, v, exg = features
    mask = ((h >= config['h_min']) & (h <= config['h_max']) &
            (s >= config['s_min']) & (v >= config['v_min']) & (exg > config['exg_min']))
    radius = config.get('morphology_radius', 0)
    if radius:
        im = Image.fromarray(mask.astype(np.uint8)*255)
        # Square-kernel opening once, closing once; no hole filling/ROI/GT input.
        k = 2*radius+1
        im = im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
        im = im.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
        mask = np.asarray(im) > 0
    return mask


def segment(rgb, config):
    return segment_features(color_features(rgb), config)


