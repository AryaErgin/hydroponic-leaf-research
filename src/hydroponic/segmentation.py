"""Canopy-first ROI, RGB card fragments, overhang and conservative QC."""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from .color import color_features, segment
from ..roi_cv import component_labels, convex_hull


def buffered(mask, radius, roi_config):
    """Spatial buffer on configured working canvas; retain original pixels."""
    h,w=mask.shape;scale=min(1.,roi_config['working_size_px']/max(h,w))
    size=(round(w*scale),round(h*scale))
    im=Image.fromarray(mask.astype(np.uint8)*255).resize(size,Image.Resampling.NEAREST)
    rr=max(roi_config['buffer_min_radius'],round(radius*scale))
    im=im.filter(ImageFilter.MaxFilter(2*rr+1)).resize((w,h),Image.Resampling.NEAREST)
    return (np.asarray(im)>0) | mask


def envelope(mask, radius, roi_config):
    h,w=mask.shape
    points=[]
    for y in np.flatnonzero(mask.any(axis=1)):
        xs=np.flatnonzero(mask[y])
        points.extend([(int(xs[0]),int(y)),(int(xs[-1]),int(y))])
    hull=convex_hull(points)
    canvas=Image.new('L',(w,h))
    if len(hull)>=3:ImageDraw.Draw(canvas).polygon(hull,fill=255)
    return buffered((np.asarray(canvas)>0) | mask,radius,roi_config)


def coherent(mask, roi_config):
    im=Image.fromarray(mask.astype(np.uint8)*255)
    k=2*roi_config['coherence_radius']+1
    return np.asarray(im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k)))>0


def central_components(mask, margin, roi_config, support=None):
    """Full connected extent; nearby substantial fragments retained."""
    h,w=mask.shape;short=min(h,w);labels,stats=component_labels(mask)
    eligible=[s for s in stats if s['area']>=max(roi_config['anchor_min_pixels'],roi_config['anchor_min_fraction']*h*w)
        and np.hypot(s['center_x']-(w-1)/2,s['center_y']-(h-1)/2)<=roi_config['anchor_center_radius']*short
        and s['x0']>0 and s['y0']>0 and s['x1']<w-1 and s['y1']<h-1]
    if not eligible:return np.zeros_like(mask),dict(anchor_found=False,anchor_dominance=0.)
    support_counts=np.bincount(labels[support].ravel(),minlength=len(stats)+1) if support is not None else None
    def score(s):
        dist=np.hypot(s['center_x']-(w-1)/2,s['center_y']-(h-1)/2)
        anchor=1 if support_counts is None or support_counts[s['label']]>=roi_config['support_seed_min_pixels'] else roi_config['unsupported_anchor_weight']
        return anchor*s['area']/(1+(dist/(roi_config['anchor_score_radius']*short))**2)
    main=max(eligible,key=score)
    main_mask=labels==main['label'];near=buffered(main_mask,margin,roi_config)
    touched=set(np.unique(labels[near]))
    keep=[s['label'] for s in eligible if s['label'] in touched and s['area']>=max(roi_config['anchor_min_pixels'],roi_config['nearby_fragment_min_fraction']*main['area'])]
    selected=np.isin(labels,keep)
    return selected,dict(anchor_found=True,anchor_dominance=main['area']/sum(s['area'] for s in eligible),
        anchor_center_x=main['center_x'],anchor_center_y=main['center_y'],anchor_components=len(keep))


def card_support(rgb, roi_config, support_config):
    """Merge neutral RGB support pieces; do not fit a leaf-occluded crescent."""
    h,w=rgb.shape[:2];scale=min(1.,roi_config['working_size_px']/max(h,w))
    small=np.asarray(Image.fromarray(rgb).resize((round(w*scale),round(h*scale)),Image.Resampling.BILINEAR).filter(ImageFilter.MedianFilter(2*support_config['median_radius']+1)))
    sh,sw=small.shape[:2];_,s,v,_=color_features(small)
    neutral=(s<=support_config['s_max'])&(v<=support_config['v_max'])
    k=2*support_config['closing_radius']+1
    im=Image.fromarray(neutral.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    labels,stats=component_labels(np.asarray(im)>0);short=min(sh,sw)
    pieces=[c for c in stats if c['area']>=support_config['piece_min_fraction']*sh*sw and c['area']<=support_config['piece_max_fraction']*sh*sw
        and c['x0']>0 and c['y0']>0 and c['x1']<sw-1 and c['y1']<sh-1
        and max(c['x1']-c['x0']+1,c['y1']-c['y0']+1)<support_config['piece_max_span_fraction']*short
        and np.hypot((c['x0']+c['x1'])/2-sw/2,(c['y0']+c['y1'])/2-sh/2)<=support_config['center_radius']*short]
    if not pieces:return None,dict(card_valid=False,card_pieces=0)
    pixels=np.isin(labels,[c['label'] for c in pieces]);ys,xs=np.where(pixels)
    points=[]
    for y in np.unique(ys):
        row=xs[ys==y];points.extend([(int(row.min()),int(y)),(int(row.max()),int(y))])
    hull=convex_hull(points);canvas=Image.new('L',(sw,sh));ImageDraw.Draw(canvas).polygon(hull,fill=255)
    smask=np.asarray(canvas)>0
    bw,bh=int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1)
    area=float(smask.sum());coverage=float(pixels.sum()/max(1,area))
    valid=(support_config['hull_min_fraction']*sh*sw<=area<=support_config['hull_max_fraction']*sh*sw and support_config['aspect_min']<=bw/bh<=support_config['aspect_max'] and coverage>=support_config['coverage_min'])
    diag=dict(card_valid=bool(valid),card_pieces=len(pieces),card_fraction=area/(sh*sw),
        card_neutral_coverage=coverage,card_rectangularity=area/(bw*bh))
    if not valid:return None,diag
    native=Image.new('L',(w,h))
    ImageDraw.Draw(native).polygon([(round(x/scale),round(y/scale)) for x,y in hull],fill=255)
    return np.asarray(native)>0,diag


def segment_plant(rgb,species,acquisition):
    """Return raw mask, canopy ROI, optional card and image-only diagnostics."""
    baseline_config=species['color'];config=acquisition['background'];roi_config=acquisition['roi'];confidence_config=acquisition['confidence']
    if baseline_config is None: raise ValueError('Species colors are unconfigured')
    a=segment(rgb,baseline_config);h,w=a.shape;short=min(h,w)
    hue,sat,val,exg=color_features(rgb)
    norm=exg.astype(np.float32)/np.maximum(rgb.astype(np.float32).sum(axis=2),1)
    perimeter=np.zeros((h,w),bool);band=max(1,round(config['perimeter_band_fraction']*short))
    perimeter[:band]=True;perimeter[-band:]=True;perimeter[:,:band]=True;perimeter[:,-band:]=True
    green=(hue>=config['h_min'])&(hue<=config['h_max'])&(sat>=config['s_min'])&(norm>config['normalized_exg_min'])
    fraction=float(green[perimeter].mean());background_green=fraction>=config['green_fraction_threshold']
    margin=roi_config['envelope_margin']*short
    support=None;diag=dict(background_green=background_green,green_periphery_fraction=fraction)
    if background_green:
        support,card=card_support(rgb,roi_config,acquisition['support']);diag.update(card)
        cutoff=float(np.quantile(norm[perimeter & green],config['background_quantile']))+config['contrast_margin']
        contrast=a & (norm>cutoff)
        allowed=a & (contrast | support) if support is not None else contrast
        seeds,anchor=central_components(allowed,margin,roi_config,support)
        diag.update(anchor);diag['background_normalized_exg_cutoff']=cutoff
        if anchor['anchor_found']:
            roi=envelope(seeds | (support if support is not None else False),margin,roi_config)
            # Preserve A inside the support, allow full connected contrast extent
            # outside it, and recover only a 3px local edge neighborhood there.
            near=np.asarray(Image.fromarray(seeds.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(2*roi_config['local_edge_radius']+1)))>0
            candidate=a & roi & (allowed | near)
            pred,_=central_components(candidate,margin,roi_config,support)
            source='neutral_card_union' if support is not None else 'contrast_canopy_fallback'
            confidence=confidence_config['supported'] if support is not None else confidence_config['contrast_fallback']
        else:
            roi=central_fallback((h,w),roi_config);pred=a & roi & contrast
            source='unlocalized_contrast_fallback';confidence=confidence_config['unlocalized']
    else:
        strong=coherent(a & (hue<=species['seed_color']['h_max'])&(norm>species['seed_color']['normalized_exg_min']),roi_config)
        seeds,anchor=central_components(strong,margin,roi_config);diag.update(anchor)
        if anchor['anchor_found']:
            roi=envelope(seeds,margin,roi_config);pred=a & roi;source='canopy_envelope'
            confidence=confidence_config['supported']*min(1.,anchor['anchor_dominance']/confidence_config['dominance_for_full'])
        else:
            roi=central_fallback((h,w),roi_config);pred=a & roi
            source='central_acquisition_fallback';confidence=confidence_config['unlocalized']
    erosion=np.asarray(Image.fromarray(roi.astype(np.uint8)*255).filter(ImageFilter.MinFilter(2*roi_config['boundary_erosion_radius']+1)))>0
    boundary_fraction=float((pred & ~erosion).sum()/max(1,pred.sum()))
    if boundary_fraction>roi_config['boundary_fraction_limit']:confidence=min(confidence,confidence_config['boundary_cap'])
    if not pred.any():confidence=0.
    diag.update(localization_source=source,localization_confidence=confidence,
        roi_boundary_foreground_fraction=boundary_fraction,roi_pixel_area=int(roi.sum()),
        overhang_pixel_area=int((pred & ~support).sum()) if support is not None else 0,
        raw_pixel_area=int(pred.sum()))
    return pred,roi,support,diag


def central_fallback(shape,roi_config):
    h,w=shape;yy,xx=np.ogrid[:h,:w];radius=roi_config['fallback_radius']*min(h,w)
    return (xx-(w-1)/2)**2+(yy-(h-1)/2)**2<=radius**2


def quality_check(predicted,diagnostics,config):
    """Heuristic RGB-reference consistency, never a calibrated probability."""
    threshold=config['threshold'];reference=predicted
    p=int(predicted.sum());r=int(reference.sum());hit=int((predicted & reference).sum())
    retention=hit/r if r else 0.;purity=hit/p if p else 0.
    geometry=float(diagnostics['localization_confidence'])
    confidence=min(geometry,retention,purity)
    reasons=[]
    if geometry<threshold:reasons.append('uncertain_localization')
    if retention<threshold:reasons.append('missing_canopy_evidence')
    if purity<threshold:reasons.append('background_disagreement')
    if p==0:reasons.append('empty_foreground')
    return dict(confidence=confidence,qc_flag=confidence<threshold,qc_reasons=';'.join(reasons),
        reference_retention=retention,reference_purity=purity,qc_threshold=threshold)
