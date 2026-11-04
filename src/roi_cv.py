"""RGB-only pot ROI and interpretable seeded color masks (NumPy/Pillow)."""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from .lettuce_cv import color_features


def component_labels(mask):
    """8-connected run-length union-find. No per-pixel Python flood fill."""
    h, w = mask.shape
    parent = [0]
    runs = []
    previous = []

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y in range(h):
        edges = np.flatnonzero(np.diff(np.r_[False, mask[y].astype(bool), False]))
        current = []
        j = 0
        for x0, stop in edges.reshape(-1, 2):
            x0, x1 = int(x0), int(stop)-1
            i = len(parent)
            parent.append(i)
            while j < len(previous) and previous[j][1] < x0-1:
                j += 1
            k = j
            while k < len(previous) and previous[k][0] <= x1+1:
                a, b = root(i), root(previous[k][2])
                if a != b:
                    parent[a] = b
                k += 1
            current.append((x0, x1, i))
            runs.append((y, x0, x1, i))
        previous = current
    labels = np.zeros((h, w), np.int32)
    stats = []
    ids = {}
    for y, x0, x1, i in runs:
        r = root(i)
        if r not in ids:
            ids[r] = len(stats)+1
            stats.append(dict(label=ids[r], area=0, x0=x0, x1=x1, y0=y, y1=y,
                              sum_x=0, sum_y=0))
        s = stats[ids[r]-1]
        n = x1-x0+1
        s['area'] += n
        s['sum_x'] += n*(x0+x1)/2
        s['sum_y'] += n*y
        s['x0'], s['x1'] = min(s['x0'], x0), max(s['x1'], x1)
        s['y0'], s['y1'] = min(s['y0'], y), max(s['y1'], y)
        labels[y, x0:x1+1] = ids[r]
    for s in stats:
        s['center_x'] = s['sum_x']/s['area']
        s['center_y'] = s['sum_y']/s['area']
    return labels, stats


def convex_hull(points):
    """Monotone-chain polygon; boundary evidence comes only from RGB."""
    pts = sorted(set(map(tuple, points)))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]


def infer_roi(rgb, config):
    """Return native ROI, diagnostics, optional RGB support; no GT input."""
    height, width = rgb.shape[:2]
    scale = min(1., 384/max(width, height))
    small = np.asarray(Image.fromarray(rgb).resize(
        (round(width*scale), round(height*scale)), Image.Resampling.BILINEAR)
        .filter(ImageFilter.MedianFilter(3)))
    sh, sw = small.shape[:2]
    hue, sat, val, exg = color_features(small)
    norm = exg.astype(float)/np.maximum(small.astype(float).sum(axis=2), 1)
    perimeter = np.zeros((sh, sw), bool)
    band = max(1, round(min(sh, sw)*.08))
    perimeter[:band] = True; perimeter[-band:] = True
    perimeter[:, :band] = True; perimeter[:, -band:] = True
    green = (hue >= 10) & (hue <= 85) & (sat >= 30) & (norm > .08)
    fraction = float(green[perimeter].mean())
    labels, stats = component_labels((val < 100) & (sat < 95))
    short = min(sw, sh)
    candidates = []
    for s in stats:
        bw, bh = s['x1']-s['x0']+1, s['y1']-s['y0']+1
        cx, cy = (s['x0']+s['x1'])/2, (s['y0']+s['y1'])/2
        distance = np.hypot(cx-sw/2, cy-sh/2)
        edge = s['x0'] == 0 or s['y0'] == 0 or s['x1'] == sw-1 or s['y1'] == sh-1
        if (not edge and .004*sw*sh <= s['area'] <= .30*sw*sh
                and min(bw, bh) >= .07*short and .6 <= bw/bh <= 1.8
                and distance <= .22*short):
            candidates.append((s['area']/(1+distance/short), s))
    support = None
    if candidates:
        s = max(candidates, key=lambda p:p[0])[1]
        cx, cy = (s['x0']+s['x1'])/2/scale, (s['y0']+s['y1'])/2/scale
        rx = max((s['x1']-s['x0']+1)/2/scale, .12*min(width, height))*config['expansion']
        ry = max((s['y1']-s['y0']+1)/2/scale, .12*min(width, height))*config['expansion']
        source = 'rgb_dark_object'
        if fraction >= .35:
            ys, xs = np.where(labels == s['label'])
            # Only horizontal endpoints are needed for the convex hull.
            points = []
            for y in np.unique(ys):
                row = xs[ys == y]
                points.extend([(int(row.min()), int(y)), (int(row.max()), int(y))])
            hull = convex_hull(points)
            canvas = Image.new('L', (width, height))
            ImageDraw.Draw(canvas).polygon([(round(x/scale), round(y/scale)) for x,y in hull], fill=255)
            support = np.asarray(canvas) > 0
    else:
        cx, cy = (width-1)/2, (height-1)/2
        rx = ry = config['fallback_radius']*min(width, height)
        source = 'central_acquisition_fallback'
    yy, xx = np.ogrid[:height, :width]
    roi = ((xx-cx)/rx)**2+((yy-cy)/ry)**2 <= 1
    diag = dict(roi_source=source, center_x=cx, center_y=cy, radius_x=rx, radius_y=ry,
                anchor_radius=.65*min(rx, ry), green_periphery=fraction >= .35,
                green_periphery_fraction=fraction, support_found=support is not None)
    return roi, diag, support


def adaptive_segment(rgb, roi, support, diagnostics, config):
    """Normalized ExG + HSV, local highlights, seeded coherent components."""
    hue, sat, val, exg = color_features(rgb)
    norm = exg.astype(np.float32)/np.maximum(rgb.astype(np.float32).sum(axis=2), 1)
    domain = roi.copy()
    if diagnostics['green_periphery'] and support is not None:
        domain &= support
    weak = domain & (hue >= 10) & (hue <= config['h_max']) & (sat >= 15) & (val >= 8) & (norm > config['weak_exg'])
    strong = weak & (hue >= 15) & (sat >= 60) & (val >= 15) & (norm > .12)
    radius = config['bright_radius']
    if radius:
        nearby = np.asarray(Image.fromarray(weak.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(2*radius+1))) > 0
        r, g, b = [rgb[:,:,i] for i in range(3)]
        weak |= domain & nearby & (val >= 220) & (sat <= 70) & (g >= r) & (g >= b)
    im = Image.fromarray(weak.astype(np.uint8)*255)
    im = im.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    im = im.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    clean = (np.asarray(im) > 0) & domain
    labels, stats = component_labels(clean)
    counts = np.bincount(labels[strong].ravel(), minlength=len(stats)+1)
    cx, cy, ar = diagnostics['center_x'], diagnostics['center_y'], diagnostics['anchor_radius']
    eligible = [s for s in stats if counts[s['label']] >= 9 and
                np.hypot(s['center_x']-cx, s['center_y']-cy) <= ar]
    if not eligible:
        return np.zeros(roi.shape, bool)
    main = max(eligible, key=lambda s:counts[s['label']])
    keep = [s['label'] for s in eligible if s['area'] >= max(9, .02*main['area'])]
    return np.isin(labels, keep)
