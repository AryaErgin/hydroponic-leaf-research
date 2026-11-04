import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from ..hydroponic.pipeline import fingerprint
from ..pot_scale import calibrate_pot
from .calibration_reuse import reference_frame, assess, effective_calibration


def prepare_image(data,filename,content_type,settings):
    """Make the displayed coordinate frame explicit before selecting rim points.

    Existing native RGB JPEG/PNG bytes remain exact. Preparation is a new
    artifact, never an in-place rewrite of historical uploads or references.
    """
    if not data or len(data)>settings.max_upload_bytes:raise ValueError('Photo is empty or too large. Choose a smaller photo.')
    suffix=Path(filename or '').suffix.lower()
    formats={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.heic':'HEIF','.heif':'HEIF'}
    if suffix not in formats:raise ValueError('Choose a JPEG, PNG or HEIC photo.')
    if suffix in ('.heic','.heif'):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError as exc:raise ValueError('HEIC support is unavailable. Export this photo as JPEG and try again.') from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format!=formats[suffix] or getattr(image,'n_frames',1)!=1:
                raise ValueError('Choose a single still photo in JPEG, PNG or HEIC format.')
            if image.width*image.height>60_000_000:raise ValueError('Photo exceeds the 60 megapixel preparation limit. Choose a smaller photo.')
            orientation=image.getexif().get(274,1)
            # pillow-heif applies HEIF orientation on decode and exposes its
            # pre-transform EXIF orientation separately.
            original_orientation=image.info.get('original_orientation',orientation)
            provenance=dict(original_sha256=hashlib.sha256(data).hexdigest(),original_format=image.format,
                original_mode=image.mode,original_exif_orientation=original_orientation,
                original_size_px=[image.width,image.height],normalization='unchanged')
            image.load()
            resized=image.width*image.height>settings.max_image_pixels
            provenance['resized']=resized
            if image.format in ('JPEG','PNG') and image.mode=='RGB' and orientation==1 and not resized:
                prepared=data;mime='image/jpeg' if image.format=='JPEG' else 'image/png';size=[image.width,image.height]
                provenance['oriented_size_px']=size
            else:
                normalized=ImageOps.exif_transpose(image)
                provenance['oriented_size_px']=list(normalized.size)
                if normalized.mode in ('RGBA','LA') or 'transparency' in normalized.info:
                    rgba=normalized.convert('RGBA');background=Image.new('RGBA',rgba.size,'white')
                    normalized=Image.alpha_composite(background,rgba).convert('RGB')
                else:normalized=normalized.convert('RGB')
                if resized:
                    factor=math.sqrt(settings.max_image_pixels/(normalized.width*normalized.height))
                    normalized=normalized.resize((max(1,int(normalized.width*factor)),max(1,int(normalized.height*factor))),Image.Resampling.LANCZOS)
                stream=io.BytesIO();normalized.save(stream,format='PNG');prepared=stream.getvalue();mime='image/png'
                size=list(normalized.size);provenance['normalization']='exif_transpose_and_rgb_png_v1'
                if len(prepared)>settings.max_upload_bytes:
                    for quality in (95,90,85,80):
                        stream=io.BytesIO();normalized.save(stream,format='JPEG',quality=quality)
                        prepared=stream.getvalue();mime='image/jpeg'
                        provenance.update(normalization='exif_transpose_and_rgb_jpeg_v1',jpeg_quality=quality)
                        if len(prepared)<=settings.max_upload_bytes:break
            if len(prepared)>settings.max_upload_bytes:raise ValueError('Prepared photo is too large. Choose a smaller photo.')
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise ValueError('This photo could not be opened. Try another photo or export it as JPEG.') from exc
    provenance.update(normalized_sha256=hashlib.sha256(prepared).hexdigest(),normalized_size_px=size,
        resize_factors=[size[i]/provenance['oriented_size_px'][i] for i in (0,1)],
        raw_original_retained=prepared==data,provenance_scope='observational_client_roundtrip_metadata')
    return prepared,mime,provenance


def image_provenance(raw,data,size):
    if raw is None or raw=='':return None
    try:record=json.loads(raw) if isinstance(raw,str) else raw
    except (ValueError,TypeError) as exc:raise ValueError('Photo preparation metadata is invalid. Choose the photo again.') from exc
    if (not isinstance(record,dict) or len(json.dumps(record))>4096 or
        record.get('normalized_sha256')!=hashlib.sha256(data).hexdigest() or record.get('normalized_size_px')!=size):
        raise ValueError('Photo preparation metadata belongs to another image. Choose the photo again.')
    return {**record,'provenance_scope':'observational_client_roundtrip_metadata'}


def decode_image(data,filename,content_type,settings):
    suffix=Path(filename or '').suffix.lower()
    formats={'.jpg':('JPEG','image/jpeg'),'.jpeg':('JPEG','image/jpeg'),'.png':('PNG','image/png')}
    if suffix not in formats or content_type not in (formats[suffix][1],'application/octet-stream'):
        raise ValueError('Choose an RGB JPEG or PNG image')
    if not data or len(data)>settings.max_upload_bytes:
        raise ValueError('Image is empty or too large')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format!=formats[suffix][0] or image.mode!='RGB' or getattr(image,'n_frames',1)!=1 or
                image.getexif().get(274,1)!=1 or image.width*image.height>settings.max_image_pixels):
                raise ValueError('Requires single-frame native RGB, normal orientation and allowed dimensions')
            image.load();rgb=np.array(image)
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise ValueError('Invalid or corrupt image') from exc
    return rgb


def import_pot(data,reference,filename,content_type,settings):
    rgb=decode_image(reference,filename,content_type,settings)
    try:sidecar=json.loads(data)
    except (ValueError,UnicodeError) as exc:raise ValueError('Invalid calibration JSON') from exc
    if not isinstance(sidecar,dict):raise ValueError('Calibration JSON must be an object')
    digest=hashlib.sha256(reference).hexdigest()
    calibrated=calibrate_pot(rgb,sidecar.get('known_pot_diameter_cm'),digest,sidecar)
    if calibrated['status']!='ACCEPTED_MANUAL_REVIEW':
        raise ValueError('A valid image-bound reviewed pot calibration is required: '+'; '.join(calibrated['reasons']))
    record=dict(calibration_type='reviewed_pot',cm_per_pixel=calibrated['cm_per_pixel'],
        reference_size_px=[rgb.shape[1],rgb.shape[0]],pot_diameter_cm=calibrated['known_pot_diameter_cm'],
        pot_diameter_pixels=calibrated['diameter_pixels'],approximate=True,
        label='Approximate projected-area calibration at the reviewed pot plane',
        provenance=dict(reference_image_sha256=digest,reviewed_calibration_sha256=hashlib.sha256(data).hexdigest(),
            reviewed_calibration=sidecar,validated_calibration=calibrated,
            calibration_bytes_base64=__import__('base64').b64encode(data).decode()))
    record['id']=fingerprint(record)
    record['reference_frame']=reference_frame(record)
    return record


def scalar_calibration(payload):
    mode=payload.get('calibration_type','pixels_only')
    if mode=='pixels_only':return None
    if mode!='reviewed_scalar':raise ValueError('Import a reviewed pot calibration or choose reviewed_scalar')
    scale=payload.get('cm_per_pixel');size=payload.get('reference_size_px');note=payload.get('calibration_provenance','')
    if (type(scale) not in (int,float) or not math.isfinite(scale) or not 0<scale<=100 or
        not isinstance(size,list) or len(size)!=2 or any(type(x) is not int or not 1<=x<=20000 for x in size) or
        not isinstance(note,str) or not note.strip() or len(note)>2000 or payload.get('calibration_reviewed') is not True):
        raise ValueError('A finite positive reviewed scale, native reference dimensions and provenance are required')
    record=dict(calibration_type=mode,cm_per_pixel=scale,reference_size_px=size,pot_diameter_cm=None,
        pot_diameter_pixels=None,approximate=True,label='Reviewed approximate reference-plane projected-area calibration',
        provenance=dict(operator_note=note,reviewed=True))
    record['id']=fingerprint(record);record['reference_frame']=reference_frame(record);return record


def reuse_context(rgb,data,calibration,geometry_unchanged,assessment=None):
    assessment=assessment or assess(rgb,data,calibration,geometry_unchanged)
    if (assessment.get('image_sha256')!=hashlib.sha256(data).hexdigest() or
        assessment.get('image_size_px')!=[rgb.shape[1],rgb.shape[0]] or
        assessment.get('calibration_id')!=(calibration.get('id') if calibration else None)):
        raise ValueError('Calibration reuse evidence does not belong to this photo/reference')
    snapshot=effective_calibration(calibration,assessment) or dict(id=None,calibration_type='pixels_only',cm_per_pixel=None,
        pot_diameter_cm=None,pot_diameter_pixels=None,provenance={})
    provenance=snapshot.get('provenance',{})
    if 'calibration_bytes_base64' in provenance:
        import base64
        raw=base64.b64decode(provenance['calibration_bytes_base64'])
    else:raw=json.dumps(snapshot,sort_keys=True,allow_nan=False).encode()
    validated=dict(cm_per_pixel=snapshot['cm_per_pixel'],known_pot_diameter_cm=snapshot['pot_diameter_cm'],
        diameter_pixels=snapshot['pot_diameter_pixels'],status='REVIEWED_PROFILE_REUSE' if calibration else 'PIXELS_ONLY',
        calibration_id=snapshot['id'],geometry_unchanged_attested=geometry_unchanged,
        reference_size_px=snapshot.get('reference_size_px'),profile_calibration=snapshot,
        calibration_reuse=assessment,
        interpretation=(snapshot.get('label','Approximate projected-area reference plane')+'; not true 3D leaf area')
            if calibration else 'Pixels only; physical projected area unavailable')
    return dict(rgb=rgb,image_sha256=hashlib.sha256(data).hexdigest(),calibration=validated,
        calibration_path=None,calibration_bytes=raw,calibration_sha256=hashlib.sha256(raw).hexdigest())
