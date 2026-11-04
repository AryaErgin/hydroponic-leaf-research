"""Classical Rumex development contracts. No physical plant ID assumptions."""
import hashlib
from collections import defaultdict
from pathlib import PurePosixPath
import numpy as np
from PIL import Image, ImageDraw


def cvat_mask(image):
    """Native-pixel union of leaf_blade polygons; no stems or boxes."""
    w,h=int(image.attrib['width']),int(image.attrib['height'])
    if min(w,h)<1: raise ValueError('Invalid canvas')
    canvas=Image.new('L',(w,h)); draw=ImageDraw.Draw(canvas)
    for polygon in image.findall('polygon'):
        if polygon.attrib.get('label') != 'leaf_blade': continue
        xy=np.asarray([tuple(map(float,p.split(','))) for p in polygon.attrib['points'].split(';')])
        if xy.ndim!=2 or xy.shape[1]!=2 or len(xy)<3 or not np.isfinite(xy).all():
            raise ValueError('Invalid polygon coordinates')
        if (xy<0).any() or (xy>[w,h]).any(): raise ValueError('Polygon outside continuous canvas')
        pts=np.clip(np.rint(xy),[0,0],[w-1,h-1]).astype(int)
        if len(np.unique(pts,axis=0))<3 or abs(np.sum(pts[:,0]*np.roll(pts[:,1],-1)-pts[:,1]*np.roll(pts[:,0],-1)))==0:
            raise ValueError('Degenerate polygon')
        draw.polygon([tuple(p) for p in pts.tolist()],fill=255)
    result=np.asarray(canvas)>0
    if not result.any(): raise ValueError('Empty blade GT')
    return result


def stable_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def metadata_group(entry,creator):
    parts=PurePosixPath(entry).parts
    if parts[1]=='RoboRumex':
        site=parts[2].split('_',1)[1]
        if site not in ('stengard','skaevinge'): raise ValueError('Unknown robot site')
        return 'robot_site:'+site, 'tuning' if site=='stengard' else 'evaluation'
    if parts[1]!='iNaturalist' or not creator.strip(): raise ValueError('Unknown or uncredited source')
    creator=creator.strip()
    return 'creator:'+creator, 'tuning' if int(stable_hash(creator),16)%3==0 else 'evaluation'


def assert_groups(rows):
    groups=defaultdict(set)
    for row in rows:
        if row['split'] not in ('tuning','evaluation'): raise ValueError('Unknown split')
        groups[row['group']].add(row['split'])
    if any(len(s)!=1 for s in groups.values()): raise ValueError('Metadata group crosses splits')


def tuning_winner(rows):
    if not rows or any(r['split']!='tuning' for r in rows): raise ValueError('Only tuning rows allowed')
    scores=defaultdict(lambda:defaultdict(list))
    for r in rows: scores[int(r['config_index'])][r['group']].append(float(r['iou']))
    group_sets=[set(groups) for groups in scores.values()]
    if any(g!=group_sets[0] for g in group_sets): raise ValueError('Unequal trial coverage')
    means={i:float(np.mean([np.mean(values) for values in groups.values()])) for i,groups in scores.items()}
    return max(means,key=lambda i:(means[i],-i)),means


def summarize(rows):
    if not rows: raise ValueError('Empty summary')
    error=np.array([float(r['percentage_area_error']) for r in rows])
    signed=np.array([float(r['signed_percentage_area_error']) for r in rows])
    return dict(n=len(rows),mean_iou=float(np.mean([float(r['iou']) for r in rows])),
        mean_dice=float(np.mean([float(r['dice']) for r in rows])),
        mean_area_error_pct=float(error.mean()),median_area_error_pct=float(np.median(error)),
        p90_area_error_pct=float(np.percentile(error,90)),p95_area_error_pct=float(np.percentile(error,95)),
        mean_absolute_pixel_area_error=float(np.mean([float(r['absolute_pixel_area_error']) for r in rows])),
        mean_signed_area_error_pct=float(signed.mean()),overestimates=int((signed>0).sum()),
        underestimates=int((signed<0).sum()),exact_area=int((signed==0).sum()),
        catastrophic_over=int((signed>50).sum()),catastrophic_under=int((signed< -50).sum()))
