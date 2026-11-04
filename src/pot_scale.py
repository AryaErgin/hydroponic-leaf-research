"""Classical circle proposals and reviewed, image-bound pot-rim scale estimates.

Automatic proposals are never silently accepted as the outer rim. No plant masks
or segmentation thresholds enter this module. Scalar scale assumes a top view.
"""
import math
import numpy as np
from PIL import Image, ImageDraw

REVIEW_KEYS = ('outer_rim_confirmed', 'top_view_confirmed', 'distortion_checked',
               'pot_plane_approximation_accepted')


def validate_diameter(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1e6:
        raise ValueError('Pot outer diameter must be finite, positive and at most 1e6 cm')


def fit_rim(points, shape):
    """Native-coordinate least-squares circle, with coverage and shape checks."""
    h, w = shape
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 8 or not np.isfinite(points).all():
        raise ValueError('At least 8 finite native outer-rim points required')
    if (points < 0).any() or (points[:, 0] > w-1).any() or (points[:, 1] > h-1).any():
        raise ValueError('Rim points outside native image')
    if len(np.unique(points, axis=0)) < 8:
        raise ValueError('At least 8 distinct rim points required')
    origin = points.mean(axis=0)
    centered = points-origin
    coefficients, _, rank, _ = np.linalg.lstsq(np.column_stack((2*centered, np.ones(len(points)))),
                                              (centered**2).sum(axis=1), rcond=None)
    if rank != 3:
        raise ValueError('Degenerate rim points')
    center = coefficients[:2]+origin
    radius_squared = coefficients[2]+(coefficients[:2]**2).sum()
    if radius_squared <= 0:
        raise ValueError('Invalid circle radius')
    radius = float(np.sqrt(radius_squared))
    if radius < 4 or center[0]-radius < 0 or center[1]-radius < 0 or center[0]+radius > w-1 or center[1]+radius > h-1:
        raise ValueError('Complete outer circle not inside image; cropped rim requires review')
    distances = np.linalg.norm(points-center, axis=1)
    residual = float(np.sqrt(np.mean((distances-radius)**2)))
    if residual > max(.75, .015*radius) or np.max(np.abs(distances-radius)) > max(2., .04*radius):
        raise ValueError('Rim not sufficiently circular; oblique/elliptical or inconsistent points')
    angles = np.sort(np.mod(np.arctan2(points[:, 1]-center[1], points[:, 0]-center[0]), 2*np.pi))
    max_gap = float(np.diff(np.r_[angles, angles[0]+2*np.pi]).max())
    if max_gap > np.pi/2 + 1e-6:
        raise ValueError('Incomplete angular rim coverage')
    return dict(center_px=center.tolist(), radius_pixels=radius, diameter_pixels=2*radius,
                residual_rms_pixels=residual, max_angular_gap_degrees=float(np.degrees(max_gap)),
                rim_points_px=points.tolist(), confidence=float(np.exp(-residual/max(.75,.02*radius))))


def manual_diameter(points, shape):
    """Two opposite native endpoints; no inferred circle/coverage requirement."""
    h,w = shape
    points = np.asarray(points,dtype=float)
    if points.shape != (2,2) or not np.isfinite(points).all():
        raise ValueError('Exactly two finite native diameter endpoints required')
    if (points < 0).any() or (points[:,0] > w-1).any() or (points[:,1] > h-1).any():
        raise ValueError('Diameter endpoints outside native image')
    distance = float(np.linalg.norm(points[1]-points[0]))
    if distance < 1:
        raise ValueError('Diameter endpoints must be at least one pixel apart')
    return dict(method='manual_two_point_diameter',diameter_points_px=points.tolist(),
                center_px=points.mean(axis=0).tolist(),diameter_pixels=distance,
                confidence=0.,confidence_basis='manual review attestation, not automatic rim confidence',
                circle_geometry_verified=False)


def propose_rim(rgb):
    """Deterministic edge/RANSAC circle proposal; outer-rim identity needs review."""
    h, w = rgb.shape[:2]
    factor = min(1., 384/max(h,w))
    sw, sh = round(w*factor), round(h*factor)
    if min(sw,sh) < 20:
        return None
    small = np.array(Image.fromarray(rgb).resize((sw,sh), Image.Resampling.BILINEAR))
    luminance = np.asarray(Image.fromarray(small).convert('L'), dtype=float)
    gy, gx = np.gradient(luminance)
    magnitude = np.hypot(gx,gy)
    # Calibrator-only edge selection: suppress highly green tissue, not plant pixels.
    channels = small.astype(float)
    green_tissue = (channels[:,:,1]-channels[:,:,0] > 25) & (channels[:,:,1]-channels[:,:,2] > 25)
    eligible = ~green_tissue
    eligible[:2] = False; eligible[-2:] = False; eligible[:,:2] = False; eligible[:,-2:] = False
    if not eligible.any():
        return None
    threshold = max(8., float(np.quantile(magnitude[eligible], .8)))
    ys, xs = np.where(eligible & (magnitude >= threshold))
    points = np.column_stack((xs,ys)).astype(float)
    if len(points) < 24:
        return None
    rng = np.random.default_rng(0)
    if len(points) > 2000:
        points = points[np.sort(rng.choice(len(points),2000,replace=False))]
    candidates = []
    tolerance = 1.5
    for _ in range(1800):
        sample = points[rng.choice(len(points),3,replace=False)]
        matrix = 2*(sample[1:]-sample[0])
        det = matrix[0,0]*matrix[1,1]-matrix[0,1]*matrix[1,0]
        if abs(det) < 1e-6:
            continue
        values = (sample[1:]**2).sum(axis=1)-(sample[0]**2).sum()
        center = np.array([(values[0]*matrix[1,1]-values[1]*matrix[0,1])/det,
                           (matrix[0,0]*values[1]-matrix[1,0]*values[0])/det])
        radius = float(np.linalg.norm(sample[0]-center))
        if not .2*min(sh,sw) <= radius <= .5*min(sh,sw)-2:
            continue
        if center[0]-radius < 2 or center[1]-radius < 2 or center[0]+radius > sw-3 or center[1]+radius > sh-3:
            continue
        residuals = np.abs(np.linalg.norm(points-center,axis=1)-radius)
        inliers = points[residuals <= tolerance]
        if len(inliers) < 24:
            continue
        angles = np.mod(np.arctan2(inliers[:,1]-center[1],inliers[:,0]-center[0]),2*np.pi)
        bins = np.floor(angles*36/(2*np.pi)).astype(int)
        coverage = len(np.unique(bins))/36
        if coverage < .8:
            continue
        # One observed edge point per sector avoids dense local arcs dominating fits.
        chosen = []
        for sector in np.unique(bins):
            group = inliers[bins == sector]
            chosen.append(group[np.argmin(np.abs(np.linalg.norm(group-center,axis=1)-radius))])
        native = np.asarray(chosen)*[w/sw,h/sh]
        try:
            candidate = fit_rim(native,(h,w))
        except ValueError:
            continue
        candidate.update(angular_support_fraction=coverage, method='automatic_edge_ransac_proposal')
        candidates.append(candidate)
    if not candidates:
        return None
    strongest = max(candidates,key=lambda c:c['confidence']*c['angular_support_fraction'])
    # Favor the outside edge only among nearby, similarly supported concentric fits.
    nearby = [c for c in candidates if np.linalg.norm(np.array(c['center_px'])-strongest['center_px']) <= .04*strongest['radius_pixels']
              and c['confidence']*c['angular_support_fraction'] >= .9*strongest['confidence']*strongest['angular_support_fraction']
              and .9 <= c['radius_pixels']/strongest['radius_pixels'] <= 1.15]
    return max(nearby,key=lambda c:c['radius_pixels'])


def calibrate_pot(rgb, known_diameter_cm, image_sha256, sidecar=None):
    validate_diameter(known_diameter_cm)
    h,w = rgb.shape[:2]
    result = dict(status='REVIEW_REQUIRED', known_pot_diameter_cm=known_diameter_cm,
                  diameter_pixels=None, cm_per_pixel=None, confidence=0., candidate=None, reasons=[], review_template=None)
    accepted = False
    if sidecar is None:
        candidate = propose_rim(rgb)
        result['reasons'] = ['automatic_outer_rim_and_view_require_manual_confirmation' if candidate else 'no_reliable_complete_rim_found']
    else:
        candidate = None
        try:
            if not isinstance(sidecar,dict) or type(sidecar.get('schema_version')) is not int or sidecar['schema_version'] != 1:
                raise ValueError('Invalid pot sidecar schema')
            if sidecar.get('image_sha256') != image_sha256 or sidecar.get('image_size_px') != [w,h]:
                raise ValueError('Pot sidecar does not match original image/canvas')
            if 'known_pot_diameter_cm' in sidecar:
                validate_diameter(sidecar['known_pot_diameter_cm'])
                if not math.isclose(sidecar['known_pot_diameter_cm'],known_diameter_cm,rel_tol=1e-9,abs_tol=1e-9):
                    raise ValueError('Reviewed physical diameter differs from --pot-diameter-cm')
            if 'diameter_points_px' in sidecar:
                candidate = manual_diameter(sidecar['diameter_points_px'],(h,w))
                required_review = ('outer_rim_confirmed','opposite_points_confirmed','pot_plane_approximation_accepted')
            else:
                candidate = fit_rim(sidecar.get('rim_points_px'),(h,w))
                candidate['method'] = 'reviewed_native_rim_points'
                required_review = REVIEW_KEYS
            review = sidecar.get('review')
            if not isinstance(review,dict) or any(review.get(key) is not True for key in required_review):
                raise ValueError('Required pot review confirmations missing: '+', '.join(required_review))
            if candidate['method']=='manual_two_point_diameter':
                candidate['confidence'] = 1.  # Reviewed operator input, never an automatic confidence estimate.
            accepted = True
        except (ValueError,TypeError,np.linalg.LinAlgError) as exc:
            result['reasons'] = [str(exc)]
    if candidate is not None:
        result.update(candidate=candidate, diameter_pixels=candidate['diameter_pixels'], confidence=candidate['confidence'])
        if candidate['method']=='manual_two_point_diameter':
            result['review_template'] = dict(schema_version=1,image_sha256=image_sha256,image_size_px=[w,h],
                known_pot_diameter_cm=known_diameter_cm,diameter_points_px=candidate['diameter_points_px'],
                review={key:False for key in ('outer_rim_confirmed','opposite_points_confirmed','pot_plane_approximation_accepted')})
        else:
            result['review_template'] = dict(schema_version=1,image_sha256=image_sha256,image_size_px=[w,h],
                rim_points_px=candidate['rim_points_px'],review={key:False for key in REVIEW_KEYS})
    if accepted:
        result.update(status='ACCEPTED_MANUAL_REVIEW',cm_per_pixel=known_diameter_cm/candidate['diameter_pixels'])
    return result


def pot_overlay(rgb, calibration):
    canvas = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(canvas)
    candidate = calibration['candidate']
    if candidate:
        color = 'lime' if calibration['status']=='ACCEPTED_MANUAL_REVIEW' else 'orange'
        if candidate['method']=='manual_two_point_diameter':
            a,b = candidate['diameter_points_px']
            draw.line(tuple(a+b),fill=color,width=3)
            for number,(px,py) in enumerate((a,b),1):
                draw.ellipse((px-3,py-3,px+3,py+3),fill='cyan')
                draw.text((px+5,py+5),f'P{number}',fill='yellow')
        else:
            x,y = candidate['center_px'];r = candidate['radius_pixels']
            draw.ellipse((x-r,y-r,x+r,y+r),outline=color,width=2)
            draw.line((x-r,y,x+r,y),fill='yellow',width=2)
            for px,py in candidate['rim_points_px']:
                draw.ellipse((px-1,py-1,px+1,py+1),fill='cyan')
    diameter = f"{calibration['diameter_pixels']:.2f} px" if calibration['diameter_pixels'] is not None else 'unavailable'
    known = calibration['known_pot_diameter_cm']
    known_text = f'{known} cm' if known is not None else 'not supplied'
    measurement = 'Manual two-point outer diameter' if candidate and candidate['method']=='manual_two_point_diameter' else 'Outer-rim candidate diameter'
    text = f"POT SCALE: {calibration['status']}\n{measurement}: {diameter}\nKnown outer diameter: {known_text}\n" + '; '.join(calibration['reasons'])
    bounds = draw.multiline_textbbox((0,0),text)
    annotated = Image.new('RGB',(max(canvas.width,bounds[2]+12),canvas.height+bounds[3]+12))
    annotated.paste(canvas,(0,0))
    ImageDraw.Draw(annotated).multiline_text((6,canvas.height+6),text,fill='white')
    return annotated
