"""Single-image RGB inference, provenance, guarded measurements and previews."""
import copy
import hashlib
import json
import io
import math
from pathlib import Path
import re
import platform
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, __version__ as pillow_version
from .config import ROOT, validate_profiles
from .segmentation import segment_plant, quality_check
from .calibration import homography, projected_area


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def rgb_digest(rgb):
    """Array API identity: SHA256 of shape prefix plus contiguous uint8 RGB bytes."""
    return hashlib.sha256(f'{rgb.shape}:uint8:RGB:'.encode()+rgb.tobytes()).hexdigest()


def safe_snapshot(value):
    """Retain invalid marker inputs as JSON-safe diagnostics, never as evidence."""
    if isinstance(value,dict):return {str(k):safe_snapshot(v) for k,v in value.items()}
    if isinstance(value,list):return [safe_snapshot(v) for v in value]
    if type(value) is float and not math.isfinite(value):return repr(value)
    if value is None or type(value) in (str,bool,int,float):return value
    return repr(value)


def source_provenance():
    paths=list(Path(__file__).parent.glob('*.py'))+[ROOT/'src/roi_cv.py',ROOT/'src/lettuce_cv.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def output_directory(path):
    path=Path(path).resolve()
    if path==ROOT or ROOT not in path.parents:raise ValueError('Output must be a new directory below project root')
    path.mkdir(parents=True,exist_ok=False)
    return path


def preview(rgb,mask,roi,metadata):
    canvas=Image.fromarray(rgb.copy())
    if mask is not None:
        overlay=np.array(canvas)
        overlay[mask]=(overlay[mask].astype(float)*.6+np.array([0,255,80])*.4).astype(np.uint8)
        if roi is not None:
            eroded=np.asarray(Image.fromarray(roi.astype(np.uint8)*255).filter(ImageFilter.MinFilter(3)))>0
            overlay[roi & ~eroded]=[255,190,0]
        canvas=Image.fromarray(overlay)
    draw=ImageDraw.Draw(canvas)
    text=f"{metadata['species']} {metadata['qc_status']} q={metadata['confidence']:.2f}\nraw pixels={metadata['raw_pixel_area']} reported={metadata['reported_pixel_area']}"
    if metadata['projected_area_cm2'] is not None:text+=f"\nreference-plane cm2={metadata['projected_area_cm2']:.4f}"
    else:text+=f"\nscale={metadata['scale_status']}"
    bounds=draw.multiline_textbbox((3,3),text)
    draw.rectangle((0,0,bounds[2]+3,bounds[3]+3),fill=(0,0,0));draw.multiline_text((3,3),text,fill='white')
    return canvas


def process_rgb(rgb,species,acquisition,calibration=None,image_sha256=None):
    validate_profiles(species,acquisition)
    if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or min(rgb.shape[:2])<8:
        raise ValueError('Expected native uint8 HxWx3 RGB with both dimensions >=8')
    h,w=rgb.shape[:2];scale=min(1.,acquisition['roi']['working_size_px']/max(h,w))
    if min(round(h*scale),round(w*scale))<1:raise ValueError('Image aspect ratio incompatible with working canvas')
    digest=image_sha256 or rgb_digest(rgb)
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid image SHA256')
    effective=copy.deepcopy({'species':species,'acquisition':acquisition})
    meta=dict(schema_version=1,species=species['species'],measurement_target=species['measurement_target'],
        image_sha256=digest,image_size_px=[w,h],acquisition_id=acquisition['acquisition_id'],
        effective_profiles=effective,config_sha256=fingerprint(effective),
        raw_pixel_area=None,reported_pixel_area=None,projected_area_cm2=None,
        physical_area_definition='equivalent projected area on validated reference plane, not true 3D leaf area',
        scale_status='absent',scale_reason=None,calibration=safe_snapshot(calibration),
        source_sha256=source_provenance(),image_identity_encoding='SHA256 of shape prefix plus contiguous uint8 RGB bytes',
        versions={'python':platform.python_version(),'numpy':np.__version__,'pillow':pillow_version},
        qc_status='UNCONFIGURED',confidence=0.,qc_reasons=['species_colors_unconfigured'],diagnostics={})
    transform=None
    if calibration is not None:
        try:
            transform=homography(calibration,(h,w),digest,acquisition['acquisition_id'])
            meta['scale_status']='valid'
        except (ValueError,TypeError,KeyError) as exc:
            meta['scale_status']='invalid';meta['scale_reason']=str(exc)
    mask=roi=support=None
    if species['color'] is not None:
        mask,roi,support,diagnostics=segment_plant(rgb,species,acquisition)
        qc=quality_check(mask,diagnostics,acquisition['qc'])
        reasons=list(filter(None,qc['qc_reasons'].split(';')))
        for label,profile in [('species',species),('acquisition',acquisition)]:
            if profile['validation']['status']=='unvalidated':reasons.append(f'unvalidated_{label}_profile')
        meta.update(raw_pixel_area=int(mask.sum()),confidence=qc['confidence'],qc_reasons=reasons,
            diagnostics={**diagnostics,**qc},qc_status='REVIEW_REQUIRED' if reasons else
            ('PASS_DEVELOPMENT' if any(p['validation']['status']=='development_only' for p in (species,acquisition)) else 'PASS'))
        if not reasons:
            meta['reported_pixel_area']=meta['raw_pixel_area']
            if transform is not None:meta['projected_area_cm2']=projected_area(mask,transform)
    return dict(mask=mask,roi=roi,support=support,metadata=meta,preview=preview(rgb,mask,roi,meta))


def process_file(path,species,acquisition,calibration_path=None):
    path=Path(path);data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as im:
        if im.getexif().get(274,1)!=1:raise ValueError('Normalize EXIF orientation and bind calibration to normalized image first')
        if im.mode!='RGB':raise ValueError('Input must be an RGB image')
        rgb=np.array(im)
    calibration=None
    if calibration_path:
        try:calibration=json.loads(Path(calibration_path).read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:calibration={'invalid_calibration_input':str(exc)}
    result=process_rgb(rgb,species,acquisition,calibration,digest)
    result['metadata']['source_path']=str(path.resolve())
    result['metadata']['image_identity_encoding']='SHA256 of source file bytes'
    return result


def save_result(result,path):
    path=output_directory(path)
    for name in ('mask','roi','support'):
        if result[name] is not None:Image.fromarray(result[name].astype(np.uint8)*255).save(path/f'{name}.png')
    result['preview'].save(path/'preview.png')
    (path/'measurement.json').write_text(json.dumps(result['metadata'],indent=2,allow_nan=False),encoding='utf-8')
    return path
