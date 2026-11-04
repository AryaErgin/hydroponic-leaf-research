"""Visible-only instance proposals. Geometry heuristics, never biological truth.

No change to total foreground; coarse suggestions are lifted to native pixels.
"""
import base64
import hashlib
import heapq
import math
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from ..roi_cv import component_labels

VERSION='visible-shape-gradient-watershed-v1'
MAX_ID=4096
PALETTE=((0,114,178),(230,159,0),(0,158,115),(204,51,102),(86,180,233),(213,94,0),(153,102,204),(240,228,66),(0,170,170),(180,80,0),(190,110,210),(90,150,20))


def validate_labeling(labels,mask,complete=False):
    if labels.shape!=mask.shape or not np.issubdtype(labels.dtype,np.integer):raise ValueError('Invalid native leaf-label dimensions or type')
    if np.any(labels<0) or labels.max(initial=0)>MAX_ID:raise ValueError('Invalid leaf IDs')
    if np.any(labels[~mask]):raise ValueError('Leaf assignments cannot extend outside lamina mask')
    missing=int(np.count_nonzero(mask&(labels==0)))
    if complete and missing:raise ValueError(f'Cannot approve: {missing} lamina pixels are unassigned.')
    return missing


def encode(labels):
    return base64.b64encode(np.asarray(labels,dtype='<u2').tobytes()).decode()


def decode(payload,mask,analysis_id):
    if payload.get('instance_analysis_id')!=analysis_id:
        raise ValueError('Leaf assignments belong to another analysis')
    try:raw=base64.b64decode(payload.get('instance_base64'),validate=True)
    except (TypeError,ValueError) as exc:raise ValueError('Invalid leaf label encoding') from exc
    if len(raw)!=mask.size*2:raise ValueError('Leaf labels must match native dimensions')
    labels=np.frombuffer(raw,dtype='<u2').reshape(mask.shape).copy()
    if labels.max(initial=0)>MAX_ID:raise ValueError('Too many leaf IDs; merge or reuse existing leaves')
    validate_labeling(labels,mask)
    return labels


def fingerprint(labels):
    return hashlib.sha256(np.asarray(labels,dtype='<u2').tobytes()).hexdigest()


def summary(labels,mask,cm_per_pixel,reviewed=False,measurement_target='visible_plant_foreground',instance_metadata=None,analysis_id=None):
    from .instance_identity import resolve,decorate
    validate_labeling(labels,mask)
    counts=np.bincount(labels.ravel(),minlength=1);ids=np.flatnonzero(counts[1:])+1
    scale=cm_per_pixel**2 if cm_per_pixel is not None else None
    leaves=[dict(leaf_id=int(i),pixel_area=int(counts[i]),
        projected_area_cm2=float(counts[i]*scale) if scale is not None else None,
        status='HUMAN_REVIEWED' if reviewed else 'REVIEW_REQUIRED',confidence=None) for i in ids]
    identity=resolve(labels,analysis_id,instance_metadata)
    leaves=decorate(leaves,identity)
    total=int(mask.sum());unassigned=int(np.count_nonzero(mask&(labels==0)))
    median=float(np.median([r['pixel_area'] for r in leaves])) if leaves and not unassigned else None
    return dict(instance_metadata=identity,per_leaf_internal_instance_ids=[r['internal_instance_id'] for r in leaves],per_leaf_display_numbers=[r['display_leaf_number'] for r in leaves],leaf_count=len(leaves),per_leaf_pixel_area=[r['pixel_area'] for r in leaves],
        per_leaf_projected_area_cm2=[r['projected_area_cm2'] for r in leaves],leaves=leaves,
        total_pixel_area=total,total_projected_area_cm2=float(total*scale) if scale is not None else None,
        mean_leaf_pixel_area=float((total-unassigned)/len(leaves)) if leaves and not unassigned else None,
        mean_leaf_area_cm2=float(total*scale/len(leaves)) if scale is not None and leaves and not unassigned else None,
        median_leaf_pixel_area=median,median_leaf_area_cm2=float(median*scale) if median is not None and scale is not None else None,
        median_leaf_area=float(median*scale) if median is not None and scale is not None else median,
        unassigned_pixel_area=unassigned,leaf_measurement_status='HUMAN_REVIEWED' if reviewed else 'REVIEW_REQUIRED',
        measurement_target=measurement_target,
        total_leaf_area=(float(total*scale) if scale is not None else total) if measurement_target=='visible_leaf_lamina' else None,
        mean_leaf_area=(float(total*scale/len(leaves)) if scale is not None else float(total/len(leaves))) if measurement_target=='visible_leaf_lamina' and leaves and not unassigned else None,
        leaf_area_unit='cm2' if scale is not None else 'pixels',
        area_definition='visible_projected_leaf_lamina' if measurement_target=='visible_leaf_lamina' else 'visible_projected_leaf_area',instance_method_version=VERSION)


def color(leaf_id):
    # Categorical identity only; no biological meaning. Matched by the browser.
    return list(PALETTE[(leaf_id-1)%len(PALETTE)])


def instance_colors(labels):
    neighbors={int(i):set() for i in np.unique(labels[labels>0])}
    for a,b in [(labels[:,:-1],labels[:,1:]),(labels[:-1,:],labels[1:,:])]:
        edge=(a!=b)&(a>0)&(b>0)
        for i,j in np.unique(np.column_stack((a[edge],b[edge])),axis=0):
            neighbors[int(i)].add(int(j));neighbors[int(j)].add(int(i))
    chosen={}
    for i in sorted(neighbors):
        near=[chosen[j] for j in neighbors[i] if j in chosen];preferred=color(i)
        if any(sum((a-b)**2 for a,b in zip(preferred,c))<100**2 for c in near):
            preferred=list(max(PALETTE,key=lambda c:min(sum((a-b)**2 for a,b in zip(c,n)) for n in near)))
        chosen[i]=preferred
    return chosen


def overlay(rgb,labels,mask,instance_metadata=None):
    pixels=rgb.copy().astype(float)
    colors=instance_colors(labels)
    for i in np.unique(labels[labels>0]):
        region=labels==i;pixels[region]=pixels[region]*.35+np.asarray(colors[int(i)])*.65
    padded=np.pad(labels,1);boundary=mask&((labels!=padded[:-2,1:-1])|(labels!=padded[2:,1:-1])|(labels!=padded[1:-1,:-2])|(labels!=padded[1:-1,2:]))
    pixels[boundary]=[20,20,20]
    region=mask&(labels==0);pixels[region]=pixels[region]*.5+np.asarray([255,160,40])*.5
    result=Image.fromarray(pixels.astype(np.uint8));draw=ImageDraw.Draw(result)
    order=[i for i in (instance_metadata or {}).get('display_order',[]) if i in colors]
    order.extend(sorted(set(colors)-set(order)))
    for number,i in enumerate(order,1):
        y,x=np.where(labels==i);at=np.argmin((x-x.mean())**2+(y-y.mean())**2)
        draw.text((int(x[at]),int(y[at])),str(number),fill='white',stroke_fill='black',stroke_width=2,anchor='mm')
    return result


def _distance(mask):
    """Two-pass 8-neighbor chamfer distance, with an explicit exterior border."""
    m=np.pad(mask,1);h,w=m.shape;x=np.arange(w,dtype=float)
    d=np.where(m,1e6,0.)
    for y in range(1,h):
        prev=d[y-1];row=np.minimum(d[y],prev+1)
        row[1:]=np.minimum(row[1:],prev[:-1]+math.sqrt(2))
        row[:-1]=np.minimum(row[:-1],prev[1:]+math.sqrt(2))
        d[y]=np.minimum.accumulate(row-x)+x
    for y in range(h-2,-1,-1):
        prev=d[y+1];row=np.minimum(d[y],prev+1)
        row[1:]=np.minimum(row[1:],prev[:-1]+math.sqrt(2))
        row[:-1]=np.minimum(row[:-1],prev[1:]+math.sqrt(2))
        d[y]=np.minimum.accumulate((row+x)[::-1])[::-1]-x
    return d[1:-1,1:-1]


def _seeds(mask,distance):
    peak=float(distance.max());radius=max(2,min(15,round(peak*.5)))
    quantized=np.rint(distance/max(peak,1)*255).astype(np.uint8)
    smooth=np.asarray(Image.fromarray(quantized).filter(ImageFilter.GaussianBlur(1)))
    maxima=(smooth==np.asarray(Image.fromarray(smooth).filter(ImageFilter.MaxFilter(2*radius+1))))&mask&(distance>=max(1,peak*.25))
    regions,stats=component_labels(maxima);candidates=[]
    for s in stats:
        ys,xs=np.where(regions==s['label']);order=np.argmax(distance[ys,xs]);candidates.append((int(ys[order]),int(xs[order])))
    candidates.sort(key=lambda p:(-distance[p],p));kept=[]
    for y,x in candidates:
        duplicate=False
        for ky,kx in kept:
            steps=max(abs(ky-y),abs(kx-x),1)+1
            yy=np.rint(np.linspace(y,ky,steps)).astype(int);xx=np.rint(np.linspace(x,kx,steps)).astype(int)
            # A broad high ridge belongs to one blade; a deep neck suggests two.
            if np.hypot(ky-y,kx-x)<radius or distance[yy,xx].min()>=.72*min(distance[y,x],distance[ky,kx]):
                duplicate=True;break
        if not duplicate:kept.append((y,x))
        if len(kept)>=64:break
    if not kept:kept=[tuple(map(int,np.unravel_index(distance.argmax(),distance.shape)))]
    return kept


def _watershed(mask,rgb,seeds,distance):
    """Masked minimax flooding with gradient/neck cost and path-length tie break."""
    if len(seeds)==1:return mask.astype(np.uint16)
    h,w=mask.shape;gray=np.asarray(Image.fromarray(rgb).convert('L').filter(ImageFilter.GaussianBlur(1)),float)
    gy,gx=np.gradient(gray) if min(h,w)>1 else (np.zeros_like(gray),np.zeros_like(gray))
    gradient=np.hypot(gx,gy);gradient/=max(float(np.percentile(gradient[mask],95)),1.)
    cost=np.rint(180*(1-distance/max(float(distance.max()),1))+60*np.minimum(gradient,1)).astype(np.int16)
    labels=np.zeros((h,w),np.uint16);best=np.full((h,w),1e9);length=np.full((h,w),1e9);queue=[]
    for i,(y,x) in enumerate(seeds,1):
        best[y,x]=int(cost[y,x]);length[y,x]=0;labels[y,x]=i;heapq.heappush(queue,(int(cost[y,x]),0,i,y,x))
    while queue:
        level,steps,leaf,y,x=heapq.heappop(queue)
        if level!=best[y,x] or steps!=length[y,x] or leaf!=labels[y,x]:continue
        for dy,dx in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)]:
            ny,nx=y+dy,x+dx
            if not(0<=ny<h and 0<=nx<w) or not mask[ny,nx]:continue
            score=max(level,int(cost[ny,nx]));distance_steps=steps+(14 if dy and dx else 10)
            if score<best[ny,nx] or (score==best[ny,nx] and distance_steps<length[ny,nx]):
                best[ny,nx]=score;length[ny,nx]=distance_steps;labels[ny,nx]=leaf
                heapq.heappush(queue,(score,distance_steps,leaf,ny,nx))
    return labels


def propose_instances(mask,rgb):
    mask=np.asarray(mask,bool);labels=np.zeros(mask.shape,np.uint16)
    if not mask.any():return labels
    components,stats=component_labels(mask);next_id=1
    for s in stats:
        if next_id>MAX_ID:break # Overflow remains explicitly unassigned and blocks leaf approval.
        box=np.s_[s['y0']:s['y1']+1,s['x0']:s['x1']+1];native=components[box]==s['label'];h,w=native.shape
        scale=min(1.,384/max(h,w));size=(max(1,round(w*scale)),max(1,round(h*scale)))
        small=np.asarray(Image.fromarray(native).resize(size,Image.Resampling.NEAREST))
        image=np.asarray(Image.fromarray(rgb[box]).resize(size,Image.Resampling.BILINEAR))
        if not small.any():labels[box][native]=next_id;next_id+=1;continue
        d=_distance(small);seeds=_seeds(small,d);small_labels=_watershed(small,image,seeds,d)
        # Aliasing may disconnect thin tissue; seed each coarse component.
        cc,parts=component_labels(small&(small_labels==0))
        for part in parts:small_labels[cc==part['label']]=int(small_labels.max())+1
        lifted=np.asarray(Image.fromarray(small_labels).resize((w,h),Image.Resampling.NEAREST)).copy()
        missing=native&(lifted==0)
        if missing.any():
            yy,xx=np.where(missing);centers=np.array(seeds)/scale
            for start in range(0,len(yy),8192):
                y=yy[start:start+8192];x=xx[start:start+8192]
                near=np.argmin((y[:,None]-centers[:,0])**2+(x[:,None]-centers[:,1])**2,axis=1)+1
                lifted[y,x]=near
        for local in np.unique(lifted[native]):
            if next_id>MAX_ID:break
            labels[box][native&(lifted==local)]=next_id;next_id+=1
    return labels


def split_instance(labels,leaf_id,points,new_id=None):
    if type(leaf_id) is not int or not 1<=leaf_id<=MAX_ID or not np.any(labels==leaf_id):raise ValueError('Select an existing leaf')
    if not isinstance(points,list) or not 2<=len(points)<=2048:raise ValueError('Draw a boundary across the selected leaf')
    h,w=labels.shape
    if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in p) or not(0<=p[0]<w and 0<=p[1]<h) for p in points):raise ValueError('Boundary must be inside the native image')
    region=labels==leaf_id;line=Image.new('L',(w,h));ImageDraw.Draw(line).line([tuple(p) for p in points],fill=1,width=2)
    _,original_parts=component_labels(region)
    if len(original_parts)!=1:raise ValueError('Selected leaf contains disconnected regions; reassign fragments before splitting')
    remaining=region&~np.asarray(line,bool);cc,stats=component_labels(remaining);stats.sort(key=lambda s:(-s['area'],s['label']))
    if len(stats)<2:raise ValueError('Cut did not fully cross the selected leaf')
    if len(stats)!=2:raise ValueError('Split must create exactly two regions; this cut creates an invalid fragment')
    if stats[1]['area']<max(2,int(region.sum()*.01)):raise ValueError('Split would create an invalid fragment; move the cut away from the edge')
    new_id=int(labels.max())+1 if new_id is None else new_id
    if type(new_id) is not int or not int(labels.max())<new_id<=MAX_ID:raise ValueError('Leaf identity limit reached')
    result=labels.copy();a,b=stats;result[region]=0;result[cc==a['label']]=leaf_id;result[cc==b['label']]=new_id
    # Deterministic multi-source 8-neighbor fill: cut pixels attach to a region
    # by a connected path, rather than a centroid guess that can create islands.
    queue=deque(map(tuple,np.argwhere((result>0)&region)))
    while queue:
        y,x=queue.popleft()
        for dy,dx in [(-1,0),(0,-1),(0,1),(1,0),(-1,-1),(-1,1),(1,-1),(1,1)]:
            ny,nx=y+dy,x+dx
            if 0<=ny<h and 0<=nx<w and region[ny,nx] and not result[ny,nx]:
                result[ny,nx]=result[y,x];queue.append((ny,nx))
    validate_labeling(result,labels>0,complete=True)
    return result
