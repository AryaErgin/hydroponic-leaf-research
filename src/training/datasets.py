"""Native visible-canopy adapters; no edits or implicit labels in raw sources."""
import hashlib
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from .common import bounded


def rasterize_yolo(text,width,height):
    # Reuse the project's established coordinate convention, preserving native archived counts.
    from ..lettuce_cv import rasterize_yolo as existing_rasterizer
    return existing_rasterizer(text,width,height)


def grouped_split(rows,seed=6006,ratios=(.7,.15,.15),merge_groups=()):
    if len(ratios)!=3 or not all(math.isfinite(r) for r in ratios) or not math.isclose(sum(ratios),1) or min(ratios)<=0:raise ValueError('Three positive split ratios must sum to one')
    parents={r['group_id']:r['group_id'] for r in rows}
    def find(group):
        while parents[group]!=group:group=parents[group]
        return group
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:parents[max(a,b)]=min(a,b)
    seen={}
    for row in rows:
        digest=row['image_sha256']
        if digest in seen:union(row['group_id'],seen[digest])
        seen[digest]=row['group_id']
    for a,b in merge_groups:union(a,b)
    groups=sorted({find(group) for group in parents},key=lambda group:hashlib.sha256(f'{seed}:{group}'.encode()).hexdigest())
    if len(groups)<3:raise ValueError('At least three independent groups are required')
    train=min(len(groups)-2,max(1,round(len(groups)*ratios[0])))
    dev=min(len(groups)-train-1,max(1,round(len(groups)*ratios[1])))
    assignments={group:('TRAIN' if i<train else 'DEV' if i<train+dev else 'PUBLIC_TEST') for i,group in enumerate(groups)}
    return [{**r,'split':assignments[find(r['group_id'])],'split_group_id':find(r['group_id'])} for r in rows]


def near_duplicate_pairs(rows,max_hamming=2,max_mean_absolute_difference=1.5):
    """Conservative RGB duplicate screen, not a guarantee of plant identity."""
    thumbnails=[]
    for row in rows:
        with Image.open(bounded(row['image_path'])) as source:
            thumbnail=np.array(source.convert('RGB').resize((32,32),Image.Resampling.BILINEAR),dtype=np.int16)
            gray=np.array(source.convert('L').resize((9,8),Image.Resampling.BILINEAR))
            bits=(gray[:,1:]>gray[:,:-1]).reshape(-1)
            thumbnails.append((row,thumbnail,bits,source.size))
    pairs=[]
    for i,(a,x,h,size) in enumerate(thumbnails):
        for b,y,k,other_size in thumbnails[i+1:]:
            if a['group_id']==b['group_id'] or size!=other_size:continue
            distance=int(np.count_nonzero(h!=k));mad=float(np.abs(x-y).mean())
            if distance<=max_hamming and mad<=max_mean_absolute_difference:
                pairs.append(dict(a=a['id'],b=b['id'],groups=[a['group_id'],b['group_id']],hamming=distance,mean_absolute_difference=mad))
    return pairs


def letterbox(rgb,mask,size=512):
    height,width=rgb.shape[:2]
    if mask.shape!=(height,width):raise ValueError('Native image/mask alignment required')
    factor=size/max(width,height);nw=max(1,round(width*factor));nh=max(1,round(height*factor))
    left,top=(size-nw)//2,(size-nh)//2
    image=np.zeros((size,size,3),np.uint8);target=np.zeros((size,size),bool)
    image[top:top+nh,left:left+nw]=np.array(Image.fromarray(rgb).resize((nw,nh),Image.Resampling.BILINEAR))
    target[top:top+nh,left:left+nw]=np.array(Image.fromarray(mask.astype(np.uint8)*255).resize((nw,nh),Image.Resampling.NEAREST))>0
    geometry=dict(native_size=[width,height],resized_size=[nw,nh],padding=[left,top,size-nw-left,size-nh-top],size=size)
    return image,target,geometry


def restore_probability(probability,geometry):
    nw,nh=geometry['resized_size'];left,top,_,_=geometry['padding']
    cropped=np.asarray(probability,dtype=np.float32)[top:top+nh,left:left+nw]
    if cropped.shape!=(nh,nw):raise ValueError('Probability canvas does not match recorded preprocessing')
    return np.array(Image.fromarray(cropped).resize(tuple(geometry['native_size']),Image.Resampling.BILINEAR),dtype=np.float32)


def target_crop(rgb,mask,padding=.08):
    ys,xs=np.where(mask)
    if not len(xs):raise ValueError('Cannot localize an empty annotation')
    px=math.ceil((xs.max()-xs.min()+1)*padding);py=math.ceil((ys.max()-ys.min()+1)*padding)
    x0,y0=max(0,int(xs.min())-px),max(0,int(ys.min())-py)
    x1,y1=min(mask.shape[1],int(xs.max())+1+px),min(mask.shape[0],int(ys.max())+1+py)
    return rgb[y0:y1,x0:x1].copy(),mask[y0:y1,x0:x1].copy(),[x0,y0,x1,y1]


def load_sample(row,verify=True):
    from .common import sha
    image_path=bounded(row['image_path']);mask_path=bounded(row['mask_path'])
    if verify and (sha(image_path)!=row['image_sha256'] or sha(mask_path)!=row['mask_sha256']):
        raise ValueError('Training sample changed since manifest lock')
    with Image.open(image_path) as source:rgb=np.array(source.convert('RGB'))
    with Image.open(mask_path) as source:
        values=np.array(source)
    if values.ndim!=2 or not np.isin(values,[0,1,255]).all() or (1 in values and 255 in values):raise ValueError('Expected native binary mask')
    mask=values!=0
    if mask.shape!=rgb.shape[:2] or not mask.any():raise ValueError('Nonempty aligned target required')
    return rgb,mask


def lettuce_mots_union(instance_map):
    """Inspection adapter only: class 1 encoded as 1000+track ID, 0 background.

    This operation grants no training permission and does not reconstruct occluded tissue.
    Original uint16 maps and track identifiers remain unchanged.
    """
    values=np.asarray(instance_map)
    if values.ndim!=2 or not np.issubdtype(values.dtype,np.integer) or np.any(values<0):
        raise ValueError('Native integer instance map required')
    foreground=values!=0
    if np.any(values[foreground]//1000!=1) or np.any(values[foreground]%1000==0):
        raise ValueError('Unexpected class or invalid source track ID')
    return foreground
