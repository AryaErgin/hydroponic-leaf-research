"""Classical binary lettuce masks; no physical calibration or leaf counting."""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def rasterize_yolo(text, width, height):
    """Union all class-0 polygons. round(x*(W-1)), inclusive Pillow pixel fill."""
    if width < 1 or height < 1:
        raise ValueError('Invalid canvas')
    canvas = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if fields[0] != '0' or len(fields) < 7 or (len(fields)-1) % 2:
            raise ValueError('Expected class 0 and at least three coordinate pairs')
        xy = np.asarray([float(v) for v in fields[1:]], dtype=np.float64).reshape(-1, 2)
        if not np.isfinite(xy).all() or np.any(xy < 0) or np.any(xy > 1):
            raise ValueError('Coordinates must be finite and in [0,1]')
        pts = np.rint(xy * [width-1, height-1]).astype(int)
        if len(np.unique(pts, axis=0)) < 3:
            raise ValueError('Degenerate polygon')
        draw.polygon([tuple(p) for p in pts.tolist()], fill=255)
    return np.asarray(canvas) > 0


def pixel_metrics(gt, predicted):
    if gt.shape != predicted.shape:
        raise ValueError('Canvas mismatch')
    gt, predicted = gt.astype(bool), predicted.astype(bool)
    g, p = int(gt.sum()), int(predicted.sum())
    if g == 0:
        raise ValueError('Empty ground truth cannot be trusted area reference')
    intersection = int(np.logical_and(gt, predicted).sum())
    union = g + p - intersection
    return dict(iou=intersection/union, dice=2*intersection/(g+p), gt_pixel_area=g,
                predicted_pixel_area=p, absolute_pixel_area_error=abs(p-g),
                percentage_area_error=100*abs(p-g)/g, signed_percentage_area_error=100*(p-g)/g,
                intersection_pixels=intersection, union_pixels=union,
                false_positive_pixels=p-intersection, false_negative_pixels=g-intersection)


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


def assign_split(tag):
    """One plant per five-tag treatment prefix is reserved for tuning."""
    return 'tuning' if tag.endswith('1') else 'evaluation'


def capture_stage(index, count):
    return ('early', 'mid', 'late')[min(2, index*3//count)]
