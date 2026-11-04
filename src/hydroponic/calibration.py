"""Image-bound planar marker calibration, supplied explicitly by the operator.

The evidence flags are attestations, not something a marker image can prove.
Units describe equivalent projection onto that validated plane, never 3D area.
"""
import numpy as np
from .config import keys, number


def homography(marker,shape,image_sha256,acquisition_id):
    keys(marker,{'schema_version','image_sha256','image_size_px','acquisition_id','corners_px',
        'width_cm','height_cm','validation'},'calibration')
    h,w=shape
    if type(marker['schema_version']) is not int or marker['schema_version']!=1:raise ValueError('Unsupported calibration schema')
    if marker['image_sha256']!=image_sha256 or marker['image_size_px']!=[w,h] or marker['acquisition_id']!=acquisition_id:
        raise ValueError('Calibration does not match image/canvas/acquisition')
    evidence=marker['validation']
    keys(evidence,{'plane_validated','distortion_validated','plane_id','protocol_id'},'calibration validation')
    if evidence['plane_validated'] is not True or evidence['distortion_validated'] is not True:
        raise ValueError('Plane and distortion evidence required')
    if any(not isinstance(evidence[k],str) or not evidence[k].strip() for k in ('plane_id','protocol_id')):
        raise ValueError('Plane/protocol evidence IDs required')
    for k in ('width_cm','height_cm'):
        number(marker[k],1e-6,1e6,k)
    corners=marker['corners_px']
    if not isinstance(corners,list) or len(corners)!=4 or any(not isinstance(p,list) or len(p)!=2 for p in corners):
        raise ValueError('Four ordered marker corners required')
    for x,y in corners:
        number(x,0,w-1,'corner x');number(y,0,h-1,'corner y')
    xy=np.array(corners,dtype=float)
    edges=np.roll(xy,-1,axis=0)-xy
    cross=edges[:,0]*np.roll(edges,-1,axis=0)[:,1]-edges[:,1]*np.roll(edges,-1,axis=0)[:,0]
    if not (np.all(cross>1e-8) or np.all(cross< -1e-8)):raise ValueError('Marker must be convex, ordered and nondegenerate')
    target=[(0,0),(marker['width_cm'],0),(marker['width_cm'],marker['height_cm']),(0,marker['height_cm'])]
    matrix=[];values=[]
    for (x,y),(u,v) in zip(xy,target):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]])
        values.extend([u,v])
    try:
        transform=np.append(np.linalg.solve(matrix,values),1).reshape(3,3)
    except np.linalg.LinAlgError as exc:raise ValueError('Singular marker mapping') from exc
    bounds=np.array([[-.5,-.5,1],[w-.5,-.5,1],[w-.5,h-.5,1],[-.5,h-.5,1]])
    denominator=bounds@transform[2]
    if not np.isfinite(transform).all() or abs(np.linalg.det(transform))<1e-14 or not (np.all(denominator>1e-8) or np.all(denominator< -1e-8)):
        raise ValueError('Invalid calibration or projective pole on image')
    return transform


def projected_area(mask,transform):
    """Sum transformed pixel-cell areas; perspective scale may vary by pixel."""
    ys,xs=np.where(mask);total=0.
    # Bounded memory even for large images.
    for start in range(0,len(xs),100000):
        centers=np.column_stack((xs[start:start+100000],ys[start:start+100000]))
        corners=centers[:,None,:]+np.array([[-.5,-.5],[.5,-.5],[.5,.5],[-.5,.5]])
        homogeneous=np.concatenate((corners,np.ones((*corners.shape[:2],1))),axis=2)@transform.T
        points=homogeneous[:,:,:2]/homogeneous[:,:,2:]
        following=np.roll(points,-1,axis=1)
        total+=float((np.abs((points[:,:,0]*following[:,:,1]-points[:,:,1]*following[:,:,0]).sum(axis=1))*.5).sum())
    if not np.isfinite(total) or total<0:raise ValueError('Nonfinite physical area')
    return total
