"""Conservative, image-bound reuse of an immutable reference-plane calibration.

This is registration evidence, not a camera pose estimator. Equal dimensions or
aspect ratio alone cannot verify geometry. Ambiguous cases withhold physical area.
Coordinates use the existing native-image convention: x'=s*x, y'=s*y.
"""
import copy
import hashlib
import math

import numpy as np
from PIL import Image

VERSION='reference-rim-patches-v1'
ACCEPTED=('VERIFIED','CONFIRMED')


def reference_frame(cal):
    points=cal.get('provenance',{}).get('reviewed_calibration',{}).get('diameter_points_px')
    size=cal.get('reference_size_px')
    return dict(size_px=size,cm_per_pixel=cal.get('cm_per_pixel'),pot_diameter_cm=cal.get('pot_diameter_cm'),
        diameter_pixels=cal.get('pot_diameter_pixels'),diameter_points_px=points,
        normalized_diameter_points=([[p[0]/size[0],p[1]/size[1]] for p in points] if points and size else None),
        coordinate_convention='native image coordinates; image coordinates = reference coordinates * resize factor')


def _gray(rgb,size):
    # Luminance texture, deliberately independent of leaf/stem color semantics.
    return np.asarray(Image.fromarray(rgb).convert('L').resize(size,Image.Resampling.LANCZOS),dtype=np.float32)


def _sample(image,x,y):
    h,w=image.shape
    if np.any(x<0) or np.any(y<0) or np.any(x>=w-1) or np.any(y>=h-1):return None
    ix=x.astype(int);iy=y.astype(int);dx=x-ix;dy=y-iy
    return ((1-dx)*(1-dy)*image[iy,ix]+dx*(1-dy)*image[iy,ix+1]+(1-dx)*dy*image[iy+1,ix]+dx*dy*image[iy+1,ix+1])


def _rim_shape(reference,current,center,radius):
    """Establish actual coherent radial edges; never call background a rim.

    A two-point diameter does not establish an ellipse or camera plane. Only a
    near-circular, multi-direction contour can use the circular anchor model.
    Unknown/occluded shapes need dense image correspondence or human confirmation.
    """
    radii=radius+np.linspace(-2,2,17)
    supported=[];displacements=[]
    for i,angle in enumerate(np.linspace(0,2*math.pi,16,endpoint=False)):
        angles=angle+np.linspace(-.10,.10,15)
        def profile(image):
            values=_sample(image,center[0]+(radii[:,None]+np.array([-1.,1.])[:,None,None])*np.cos(angles),
                center[1]+(radii[:,None]+np.array([-1.,1.])[:,None,None])*np.sin(angles))
            return None if values is None else np.abs(np.mean(values[1]-values[0],axis=1))
        ref=profile(reference);new=profile(current)
        if ref is None or new is None:continue
        j=int(np.argmax(ref))
        if ref[j]<12:continue
        supported.append(i)
        displacements.append(abs(float(radii[int(np.argmax(new))]-radii[j])))
    established=len(supported)>=12 and all(sum(i//4==q for i in supported)>=2 for q in range(4))
    stable=established and all(d<=.5 for d in displacements)
    return dict(established=established,stable=stable,supported_directions=len(supported),
        max_radial_displacement_px=max(displacements,default=None),working_long_edge_px=max(reference.shape),
        displacement_tolerance_px=.5)


def _same_reference_image(reference,current):
    """Strict local correspondence for unknown rim shapes, including interior.

    Every textured tile must agree, rather than allowing a large unchanged
    background to hide a small but important reference-plane shape change.
    """
    checked=0
    for y in range(0,reference.shape[0]-7,4):
        for x in range(0,reference.shape[1]-7,4):
            a=reference[y:y+8,x:x+8];b=current[y:y+8,x:x+8]
            sa=float(a.std());sb=float(b.std())
            if max(sa,sb)<4:continue
            checked+=1
            if min(sa,sb)<2:return False
            a=a-a.mean();b=b-b.mean()
            if float(np.sum(a*b)/(np.linalg.norm(a)*np.linalg.norm(b)))<.97:return False
    return checked>=30


def _evidence(reference,rgb,frame):
    points=frame.get('diameter_points_px')
    if not points:return dict(method=VERSION,reason='Reference has no reviewed rim endpoints',verified=False)
    rw,rh=frame['size_px'];factor=min(1.,320/max(rw,rh));size=(round(rw*factor),round(rh*factor))
    if list(reference.shape[1::-1])!=[rw,rh]:
        return dict(method=VERSION,reason='Reference asset dimensions do not match calibration',verified=False)
    ref=_gray(reference,size);new=_gray(rgb,size)
    p=np.asarray(points,dtype=float)*factor;center=p.mean(axis=0);radius=np.linalg.norm(p[1]-p[0])/2
    if radius<18:return dict(method=VERSION,reason='Reference rim is too small for reliable registration',verified=False)
    half=max(3,min(8,round(radius*.09)));gy,gx=np.mgrid[-half:half+1,-half:half+1]
    candidates=[]
    # The reviewed endpoints are actual rim evidence. The rest of an assumed
    # circle may instead be background when the pot appears elliptical.
    for i,pos in enumerate(p):candidates.append(('reviewed_endpoint',i,pos))
    for i,t in enumerate(np.linspace(0,2*math.pi,16,endpoint=False)):
        candidates.append(('rim',i//4,center+radius*np.array([math.cos(t),math.sin(t)])))
    # Spatially distributed background patches, outside the pot/plant interior.
    for y in np.linspace(half+2,size[1]-half-3,5):
        for x in np.linspace(half+2,size[0]-half-3,5):
            if np.linalg.norm(np.array([x,y])-center)>radius*1.3:
                candidates.append(('background',int(x>center[0])+2*int(y>center[1]),np.array([x,y])))
    anchors=[]
    for kind,sector,pos in candidates:
        x=pos[0]+gx;y=pos[1]+gy;patch=_sample(ref,x,y)
        if patch is None or float(patch.std())<4:continue
        patch=patch-patch.mean();norm=np.linalg.norm(patch)
        anchors.append((kind,sector,x,y,patch,norm))
    def correlations(scale=1.,tx=0.,ty=0.):
        scores=[]
        for kind,sector,x,y,patch,norm in anchors:
            target=_sample(new,center[0]+scale*(x-center[0])+tx,center[1]+scale*(y-center[1])+ty)
            value=-1.
            if target is not None:
                target=target-target.mean();den=norm*np.linalg.norm(target)
                if den>0:value=float(np.sum(patch*target)/den)
            scores.append((kind,sector,value))
        return scores
    def supported(scores,threshold=.90):
        rim=[x for x in scores if x[0]=='rim' and x[2]>=threshold]
        bg=[x for x in scores if x[0]=='background' and x[2]>=threshold]
        endpoints=[x for x in scores if x[0]=='reviewed_endpoint' and x[2]>=threshold]
        return len(endpoints)==2 and len(rim)>=6 and len({x[1] for x in rim})>=3 and len(bg)>=3 and len({x[1] for x in bg})>=2
    scores=correlations()
    evidence=dict(method=VERSION,verified=supported(scores),threshold=.90,
        textured_rim_patches=sum(x[0]=='rim' for x in anchors),textured_background_patches=sum(x[0]=='background' for x in anchors),
        matched_rim_patches=sum(k=='rim' and v>=.90 for k,_,v in scores),
        matched_background_patches=sum(k=='background' and v>=.90 for k,_,v in scores),
        matched_reviewed_endpoints=sum(k=='reviewed_endpoint' and v>=.90 for k,_,v in scores),
        expected_transform_patch_scores=[dict(region=k,sector=s,correlation=round(v,4)) for k,s,v in scores])
    shape=_rim_shape(ref,new,center,radius);evidence['rim_shape']=shape
    if evidence['verified']:
        if shape['stable']:return evidence
        if not shape['established'] and _same_reference_image(ref,new):
            evidence['same_reference_image_registered']=True
            return evidence
        evidence.update(verified=False,reason='Rim shape/plane is not established or does not match; confirmation or recalibration is required')
    # Bounded conservative contradiction search. A strong alternative transform
    # demonstrates that the reference-frame scale cannot safely be reused.
    for scale in (.8,.9,1.,1.1,1.25):
        for tx,ty in ((0,0),(-8,0),(8,0),(0,-8),(0,8)):
            if scale==1. and tx==ty==0:continue
            if supported(correlations(scale,tx,ty),.86):
                evidence.update(contradiction=True,alternative_normalized_transform=dict(scale=scale,translation=[tx,ty]))
                return evidence
    evidence['reason']='Insufficient independent rim/background agreement; geometry is unverified'
    return evidence


def assess(rgb,data,calibration,geometry_unchanged,reference_rgb=None,observed_pot_diameter_cm=None):
    digest=hashlib.sha256(data).hexdigest();size=[rgb.shape[1],rgb.shape[0]]
    result=dict(version=VERSION,image_sha256=digest,calibration_id=calibration.get('id') if calibration else None,
        image_size_px=size,status='PIXELS_ONLY',reason='No reviewed calibration: pixel areas only.',
        effective_cm_per_pixel=None)
    if not calibration:return result
    frame=reference_frame(calibration);result['reference_frame']=frame
    def state(status,reason):
        result.update(status=status,reason=reason);return result
    scale=frame['cm_per_pixel'];native=frame['size_px']
    if (type(scale) not in (int,float) or not math.isfinite(scale) or scale<=0 or
        not isinstance(native,list) or len(native)!=2 or any(type(x) is not int or x<=0 for x in native)):
        return state('RECALIBRATION_REQUIRED','The reviewed reference scale is invalid. Recalibrate this photo.')
    if geometry_unchanged is not True:
        return state('RECALIBRATION_REQUIRED','Camera/setup was marked changed. Recalibrate this photo.')
    if observed_pot_diameter_cm is not None and (type(observed_pot_diameter_cm) not in (int,float) or
        not math.isfinite(observed_pot_diameter_cm) or observed_pot_diameter_cm!=frame['pot_diameter_cm']):
        return state('RECALIBRATION_REQUIRED','Physical pot diameter changed. Recalibrate for this pot.')
    sx,sy=size[0]/native[0],size[1]/native[1]
    # Allow only integer rounding of a uniform resize, never anisotropic stretching.
    s=(sx+sy)/2
    if max(abs(size[i]-native[i]*s) for i in (0,1))>.5:
        return state('RECALIBRATION_REQUIRED','Photo dimensions indicate changed aspect/framing (crop or stretch). Recalibrate this photo.')
    result.update(resize_factor=s,reference_to_image=[[s,0,0],[0,s,0],[0,0,1]],
        image_reference_points_px=([[s*x,s*y] for x,y in frame['diameter_points_px']] if frame['diameter_points_px'] else None),
        image_pot_diameter_pixels=frame['diameter_pixels']*s if frame['diameter_pixels'] else None)
    evidence=dict(method='unverified',verified=False)
    if digest==calibration.get('provenance',{}).get('reference_image_sha256'):
        evidence=dict(method='exact_reviewed_reference_sha256',verified=True)
    elif reference_rgb is not None:
        evidence=_evidence(reference_rgb,rgb,frame)
    elif calibration.get('calibration_type')=='reviewed_scalar' and size==native:
        # Existing explicitly reviewed scalar profiles have no reference photo.
        # Preserve their native-resolution operator attestation, never infer resize.
        evidence=dict(method='legacy_native_scalar_operator_attestation',verified=True,operator_attested=True)
    result['evidence']=evidence
    if evidence.get('contradiction'):
        return state('RECALIBRATION_REQUIRED','Reference registration detected changed framing/scale. Recalibrate this photo.')
    if evidence['verified']:
        result['effective_cm_per_pixel']=scale/s
        return state('VERIFIED','Reference geometry verified; proportional resolution scale applied.' if s!=1 else 'Reviewed reference geometry verified.')
    return state('CONFIRMATION_REQUIRED','Confirm the same physical pot, camera distance/zoom/plane and full uncropped frame, or recalibrate. Physical areas are withheld until confirmed.')


def confirm(assessment,claims):
    if not isinstance(claims,dict):raise ValueError('Explicit calibration confirmation is required')
    if assessment.get('status')!='CONFIRMATION_REQUIRED':raise ValueError('This photo requires recalibration or is already confirmed')
    if any(claims.get(k)!=assessment.get(k) for k in ('image_sha256','calibration_id')):
        raise ValueError('Calibration confirmation is stale or belongs to a different photo/reference')
    if any(claims.get(k) is not True for k in ('same_physical_pot','same_camera_geometry','full_frame_resize_only')):
        raise ValueError('Confirm the same physical pot, unchanged camera distance/zoom/plane and full uncropped frame')
    if claims.get('pot_diameter_cm') is not None and claims['pot_diameter_cm']!=assessment['reference_frame']['pot_diameter_cm']:
        raise ValueError('Physical pot diameter differs: recalibration is required')
    result=copy.deepcopy(assessment)
    result.update(status='CONFIRMED',reason='Same pot and full-frame camera geometry explicitly confirmed by reviewer.',
        confirmation={k:claims[k] for k in ('image_sha256','calibration_id','same_physical_pot','same_camera_geometry','full_frame_resize_only')},
        effective_cm_per_pixel=result['reference_frame']['cm_per_pixel']/result['resize_factor'])
    return result


def effective_calibration(calibration,assessment):
    if not calibration:return None
    if assessment.get('status') not in ACCEPTED:
        raise ValueError(assessment.get('reason','Calibration geometry must be confirmed or recalibrated'))
    snapshot=copy.deepcopy(calibration)
    snapshot.update(cm_per_pixel=assessment['effective_cm_per_pixel'],reference_size_px=assessment['image_size_px'],
        pot_diameter_pixels=assessment['image_pot_diameter_pixels'],reference_frame=assessment['reference_frame'],calibration_reuse=assessment)
    return snapshot
