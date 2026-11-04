"""Conservative, color-independent visible lamina proposals.

Only long, narrow, nearly uniform-width extensions of a broader body are
removed. Unknown anatomy stays in the mask for human inspection. No 3D or
occluded tissue is inferred; this heuristic is not a validated anatomy model.
"""
import math
import numpy as np
from ..roi_cv import component_labels
from .leaf_instances import _distance, propose_instances

VERSION='geometry-lamina-conservative-v1'
TARGET='visible_leaf_lamina'


def _distance_to(core):
    # Unlike foreground width, exterior pixels MUST NOT be distance seeds.
    h,w=core.shape;x=np.arange(w,dtype=float)
    d=np.where(core,0.,1e6)
    for y in range(h):
        row=d[y].copy()
        if y:
            prev=d[y-1];row=np.minimum(row,prev+1)
            row[1:]=np.minimum(row[1:],prev[:-1]+math.sqrt(2))
            row[:-1]=np.minimum(row[:-1],prev[1:]+math.sqrt(2))
        d[y]=np.minimum.accumulate(row-x)+x
    for y in range(h-1,-1,-1):
        row=d[y].copy()
        if y<h-1:
            prev=d[y+1];row=np.minimum(row,prev+1)
            row[1:]=np.minimum(row[1:],prev[:-1]+math.sqrt(2))
            row[:-1]=np.minimum(row[:-1],prev[1:]+math.sqrt(2))
        d[y]=np.minimum.accumulate((row+x)[::-1])[::-1]-x
    return d


def refine_lamina(plant_mask):
    """Return subset of foreground plus explicit geometry decisions; no RGB."""
    plant=np.asarray(plant_mask,bool);lamina=plant.copy();decisions=[]
    cc,components=component_labels(plant)
    for component in components:
        box=np.s_[component['y0']:component['y1']+1,component['x0']:component['x1']+1]
        body=cc[box]==component['label'];width=_distance(body)
        radius=max(1.,float(width.max())*.20)
        # Preserve a generous collar around the broad core. Narrow whole blades
        # and uncertain tapered tips are retained, rather than eroding outlines.
        core=body&(width>=radius)
        candidates=body&(_distance_to(core)>2*radius)
        tails,parts=component_labels(candidates)
        for part in parts:
            region=tails==part['label'];yy,xx=np.where(region)
            if len(xx)<10:continue
            points=np.column_stack((xx,yy));centered=points-points.mean(axis=0)
            eig,axes=np.linalg.eigh(centered.T@centered/max(len(xx),1))
            axis=axes[:,-1];along=centered@axis;across=centered@axes[:,0]
            length=float(np.ptp(along)+1);breadth=float(np.ptp(across)+1)
            aspect=float(eig[-1]/max(eig[0],.25))
            # Local medial width sampled along the connector, not color or area.
            cuts=np.linspace(along.min(),along.max()+1e-6,7)
            widths=[float(width[yy[(along>=a)&(along<b)],xx[(along>=a)&(along<b)]].max(initial=0))
                    for a,b in zip(cuts[:-1],cuts[1:])]
            cv=float(np.std(widths)/max(np.mean(widths),1))
            clear=(length>=max(6*radius,6*breadth) and aspect>=16 and
                   max(widths)<=radius and cv<=.25)
            if not clear:continue
            lamina[box][region]=False
            decisions.append(dict(component=int(component['label']),pixel_area=int(region.sum()),
                length_px=length,transverse_span_px=breadth,local_half_widths_px=widths,
                width_variation=cv,rule='long_narrow_uniform_connector_outside_broad_body_collar'))
    return lamina,dict(method_version=VERSION,measurement_target=TARGET,
        plant_foreground_pixel_area=int(plant.sum()),lamina_pixel_area=int(lamina.sum()),
        excluded_pixel_area=int((plant&~lamina).sum()),geometry_decisions=decisions,
        exclusion_uses_color=False,uncertain_tissue_policy='KEEP_AS_LAMINA_FOR_HUMAN_REVIEW',
        scientific_status='UNVALIDATED_GEOMETRY_PROPOSAL',hidden_tissue_inferred=False)


def propose_lamina(plant_mask,rgb):
    lamina,meta=refine_lamina(plant_mask)
    labels=propose_instances(lamina,rgb);counts=np.bincount(labels.ravel());edits=[]
    # A one-pixel orphan adjacent to a much broader blade is not a new leaf.
    # Keep its pixel (uncertain tissue) and reassign; compact young leaves and
    # isolated tiny tissue remain possible. No global count or size quota.
    major=np.flatnonzero(counts>=100);major=major[major>0]
    for leaf in np.flatnonzero(counts==1):
        if not leaf or not len(major):continue
        y,x=np.argwhere(labels==leaf)[0];best=None
        for target in major:
            yy,xx=np.where(labels==target);dist=(yy-y)**2+(xx-x)**2;at=dist.argmin()
            candidate=(int(dist[at]),int(target))
            if best is None or candidate<best:best=candidate
        if best and best[0]<=min(32.,math.sqrt(int(counts[best[1]]))*.5)**2:
            labels[y,x]=best[1];edits.append(dict(from_leaf_id=int(leaf),to_leaf_id=best[1],
                pixel_area=1,rule='nearby_single_pixel_orphan_reassigned_without_area_removal'))
    # Stable compact IDs; record reassignment in pre-compaction coordinates.
    ids=np.unique(labels[labels>0]);lookup=np.zeros(int(labels.max(initial=0))+1,np.uint16)
    lookup[ids]=np.arange(1,len(ids)+1,dtype=np.uint16);labels=lookup[labels]
    meta.update(micro_instance_reassignments=edits,
        compact_id_mapping={str(int(i)):int(lookup[i]) for i in ids})
    return lamina,labels,meta
