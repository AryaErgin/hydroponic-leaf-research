import base64
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import logging
import math
from pathlib import Path
import re
import tempfile
from time import perf_counter
import uuid

import numpy as np
from PIL import Image, PngImagePlugin
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from google.api_core.exceptions import GoogleAPIError

from ..hydroponic.config import ROOT, load_profiles
from ..manual_pot import cached_review_path
from ..manual_target import mask_overlay, save_reviewed_sample
from ..quick_test import process_single_plant_rgb
from ..training.model_registry import ModelRegistry
from .calibration import decode_image, import_pot, reuse_context, scalar_calibration, image_provenance
from .store import Database, MediaStore
from . import leaf_instances, lamina, instance_identity, calibration_reuse

REVIEW_KEYS=('visible_target_only','visible_tissue_complete','moss_excluded','growing_medium_excluded',
             'dead_tissue_excluded','pot_and_background_excluded','scale_approximation_accepted')
ANALYSIS_LEASE_SECONDS=300
REVIEW_RETENTION_SECONDS=24*3600
DEFAULT_SETUP_CALIBRATION_ID='17f65406308e2303919c3032dec8664d6f85c402e8625f9e6198f8be732311a5'
logger=logging.getLogger('uvicorn.error.candidates')


class Conflict(ValueError):pass
class CandidateUnavailable(RuntimeError):pass


def stamp():return datetime.now(timezone.utc).isoformat()


def png_bytes(image,status=None):
    stream=io.BytesIO();info=PngImagePlugin.PngInfo()
    if status:info.add_text('measurement_status',status)
    image.save(stream,format='PNG',pnginfo=info);return stream.getvalue()


def decode_mask(payload,size):
    width,height=size
    if (not isinstance(payload,dict) or type(payload.get('width')) is not int or
        type(payload.get('height')) is not int or payload['width']!=width or payload['height']!=height):
        raise ValueError('Mask must match exact original image dimensions')
    try:raw=base64.b64decode(payload.get('mask_base64'),validate=True)
    except (TypeError,ValueError) as exc:raise ValueError('Invalid binary mask encoding') from exc
    if len(raw)!=width*height:raise ValueError('Wrong native mask length')
    mask=np.frombuffer(raw,dtype=np.uint8).reshape(height,width)
    if not np.isin(mask,[0,1]).all():raise ValueError('Mask must contain only binary 0/1 pixels')
    return mask.astype(bool)


def growth(rows):
    result=dict(current_area_cm2=None,previous_area_cm2=None,absolute_growth_cm2=None,
        daily_growth_cm2=None,percentage_growth=None,relative_growth_rate_per_day=None,delta_days=None,reason='no_measurements')
    if not rows:return result
    current=rows[-1];result['current_area_cm2']=current['projected_area_cm2']
    result['reason']='no_previous_measurement'
    if len(rows)<2:return result
    previous=rows[-2];result['previous_area_cm2']=previous['projected_area_cm2']
    days=(date.fromisoformat(current['date'])-date.fromisoformat(previous['date'])).days
    result['delta_days']=days
    if current.get('measurement_target','visible_plant_foreground')!=previous.get('measurement_target','visible_plant_foreground'):
        result['reason']='measurement_target_changed';return result
    if current.get('calibration_id')!=previous.get('calibration_id'):
        result['reason']='calibration_changed';return result
    a,b=result['current_area_cm2'],result['previous_area_cm2']
    if days<=0 or a is None or b is None or not all(math.isfinite(x) and x>0 for x in (a,b)):
        result['reason']='unscaled_or_invalid_interval';return result
    change=a-b
    result.update(absolute_growth_cm2=change,daily_growth_cm2=change/days,percentage_growth=100*change/b,
        relative_growth_rate_per_day=(math.log(a)-math.log(b))/days,reason=None)
    return result


class Service:
    def __init__(self,settings):
        self.settings=settings.validate();self.db=Database(settings);self.media=MediaStore(settings)
        self.work=settings.data_dir/'work';self.work.mkdir(exist_ok=True)
        import os
        if os.name=='nt' and not str(self.work).startswith('\\\\?\\'):
            self.work=Path('\\\\?\\'+str(self.work.resolve()))
        self.models=ModelRegistry()

    def model_options(self):
        """Expose research previews; only an explicitly promoted model is default."""
        try:
            champion=self.models.champion()
            records=self.models.list()
            models=[dict(version=r['version'],architecture=r['architecture'],
                checkpoint_sha256=r['artifacts']['checkpoint']['sha256'],preview_engine='learned:'+r['version'],
                is_champion=bool(champion and champion['version']==r['version']),
                validation_scope='UNVALIDATED / REVIEW REQUIRED',recommendation=self.models.recommend(r['version'])) for r in records]
            return dict(champion_version=champion['version'] if champion else None,models=models,
                        engines=['auto','classical']+['learned:'+r['version'] for r in records],registry_error=None)
        except (OSError,ValueError,KeyError) as exc:
            return dict(champion_version=None,models=[],engines=['auto','classical'],
                        registry_error='Model registry unavailable: '+type(exc).__name__)

    def _candidate(self,rgb,payload,engine):
        fallback=None;record=None
        if engine!='classical' and payload['species']=='lettuce':
            try:
                record=self.models.champion() if engine=='auto' else self.models.get(engine.split(':',1)[1])
                if record is None:
                    raise ValueError('No promoted lettuce model is available')
                else:
                    # Runtime imports stay lazy so missing/blocked native ML libraries cannot disable review.
                    from ..training.inference import predict_version
                    started=perf_counter()
                    learned=predict_version(rgb,version=record['version'],registry=self.models,device='cpu')
                    elapsed=perf_counter()-started
                    mask=np.asarray(learned['mask'])
                    if mask.shape!=rgb.shape[:2] or not np.isin(mask,[0,1]).all():
                        raise ValueError('Learned predictor returned a nonnative or nonbinary mask')
                    pixel_area=int(mask.sum());empty=pixel_area==0;full=pixel_area==mask.size
                    # These are mechanical sanity flags, never semantic accuracy
                    # or an approval. Physical area remains withheld until review.
                    reasons=['unvalidated_target_domain','human_review_required']
                    if empty:reasons.append('empty_candidate')
                    if full:reasons.append('full_image_candidate')
                    metadata=dict(learned.get('metadata',{}),qc_status='UNREVIEWED_MODEL_CANDIDATE',
                        measurement_status='UNREVIEWED_CANDIDATE',target_validation='UNVALIDATED / REVIEW REQUIRED',
                        model_version=record['version'],architecture=record['architecture'],
                        raw_pixel_area=pixel_area,reported_pixel_area=None,projected_area_cm2=None,
                        qc_reasons=reasons,diagnostics=dict(empty_mask=empty,full_image_mask=full,
                            semantic_accuracy_established=False),
                        device='cpu',inference_seconds=elapsed,inference_timing_scope='wall_time_including_cold_model_load',
                        checkpoint_sha256=record['artifacts']['checkpoint']['sha256'])
                    model=dict(version=record['version'],architecture=record['architecture'],
                        checkpoint_sha256=record['artifacts']['checkpoint']['sha256'],candidate_kind='AUTOMATIC_PREDICTION',
                        validation_scope='UNVALIDATED / REVIEW REQUIRED',requested_engine=engine,fallback_reason=None,
                        device='cpu',inference_seconds=elapsed,inference_timing_scope='wall_time_including_cold_model_load',
                        staged_preview=engine.startswith('learned:'))
                    logger.info('learned_candidate %s',json.dumps(dict(model=model,image_sha256=payload['image_sha256'],
                        native_dimensions=[rgb.shape[1],rgb.shape[0]],preprocessing=metadata.get('geometry'),
                        threshold=metadata.get('threshold'),predicted_pixels=pixel_area,
                        mask_sha256=hashlib.sha256(mask.astype(np.uint8).tobytes()).hexdigest(),checkpoint_loaded=True)))
                    return dict(mask=mask.astype(bool),metadata=metadata),model
            except Exception as exc:
                logger.exception('learned_candidate_failed engine=%s model=%s image=%s',engine,
                    record.get('version') if record else None,payload['image_sha256'])
                raise CandidateUnavailable('The promoted lettuce model is unavailable. Analysis failed; no substitute mask was generated. '+
                    type(exc).__name__+': '+str(exc)[:240]) from exc
        elif payload['species']!='lettuce':
            fallback='Rumex has no validated learned model; manual initialization remains required'
        species,acquisition=load_profiles(payload['species'])
        result=process_single_plant_rgb(rgb,species,acquisition,image_sha256=payload['image_sha256'])
        available=result['mask'] is not None
        model=dict(version='frozen_classical_exp003' if available else None,
            architecture='classical_cv' if available else 'unconfigured',checkpoint_sha256=None,
            candidate_kind='AUTOMATIC_PREDICTION' if available else 'UNAVAILABLE_MANUAL_INITIALIZATION',
            validation_scope='UNVALIDATED / REVIEW REQUIRED',requested_engine=engine,fallback_reason=fallback,
            staged_preview=False,source_sha256=result['metadata'].get('source_sha256',{}))
        return result,model

    def add_calibration(self,record,reference=None,raw=None):
        table=self.db.calibrations
        with self.db.engine.begin() as conn:
            existing=conn.execute(select(table).where(table.c.id==record['id'])).mappings().first()
            if existing:return existing['payload']
            if reference is not None:
                prefix='calibrations/'+record['id']+'/'+str(uuid.uuid4())
                record={**record,'assets':{
                    'reference_image':self.media.put(prefix+'/reference.png',reference,'application/octet-stream'),
                    'reviewed_calibration':self.media.put(prefix+'/reviewed_calibration.json',raw,'application/json')}}
            conn.execute(insert(table).values(id=record['id'],payload=record))
        return record

    def import_calibration(self,raw,reference,filename,content_type):
        return self.add_calibration(import_pot(raw,reference,filename,content_type,self.settings),reference,raw)

    def existing_calibration(self,import_it=False):
        # A legacy local reference has no verified institutional ownership.
        if self.settings.organization_id:return None
        source=ROOT/'data/incoming/lettuce_test.jpeg'
        if not source.is_file():return None
        path=cached_review_path(source)
        if not path.is_file():return None
        record=import_pot(path.read_bytes(),source.read_bytes(),source.name,'image/jpeg',self.settings)
        return self.add_calibration(record,source.read_bytes(),path.read_bytes()) if import_it else record

    def default_setup(self):
        try:calibration=self.db.one(self.db.calibrations,DEFAULT_SETUP_CALIBRATION_ID)['payload']
        except KeyError:calibration=None
        # Local reviewed artifact can initialize a fresh local setup. Production
        # uses its persistent canonical calibration, never another plant's scale.
        if calibration is None and not self.settings.cloud_run:
            try:calibration=self.existing_calibration(import_it=True)
            except ValueError:calibration=None
        profiles=self.profiles()
        return dict(default_calibration=calibration,experiment_id=profiles[0]['experiment_id'] if profiles else 'Hydroponic research')

    def create_plant(self,payload):
        choice=payload.get('calibration_choice','default');request=payload.get('request_id')
        signature=hashlib.sha256(json.dumps(dict(name=payload['name'],species=payload['species'],
            calibration_choice=choice,calibration_id=payload.get('calibration_id')),sort_keys=True).encode()).hexdigest()
        id=str(uuid.uuid5(uuid.NAMESPACE_URL,'hydroponic:plant-create:'+str(request))) if request else None
        if id:
            try:
                previous=self.db.one(self.db.profiles,id)
                if previous['payload'].get('creation_request_sha256')!=signature:raise Conflict('This save request was already used for a different plant. Start a new plant entry.')
                return self.profile(id)
            except KeyError:pass
        # A scale-free new plant needs no default calibration import or lookup.
        profiles=self.profiles() if choice=='none' else None
        setup=(dict(default_calibration=None,experiment_id=profiles[0]['experiment_id'] if profiles else 'Hydroponic research')
            if choice=='none' else self.default_setup())
        calibration_id=(setup['default_calibration'] or {}).get('id') if choice=='default' else payload.get('calibration_id')
        if choice=='none':calibration_id=None
        if choice=='custom' and not calibration_id:raise ValueError('Review and save the pot calibration first')
        internal_name=payload['name'] if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._ -]*',payload['name']) else 'plant-'+(id or str(uuid.uuid4()))
        values=dict(experiment_id=setup['experiment_id'],plant_id=internal_name,species=payload['species'],calibration_id=calibration_id)
        if not id:
            created=self.create_profile(values)
            if internal_name!=payload['name']:return self.edit_plant(created['id'],dict(name=payload['name'],species=payload['species']))
            return created
        cal=self.calibration(values);table=self.db.profiles
        try:
            with self.db.engine.begin() as conn:
                existing=conn.execute(select(table).where(table.c.id==id)).mappings().first()
                if existing:
                    if existing['payload'].get('creation_request_sha256')!=signature:raise Conflict('This save request was already used for a different plant.')
                else:conn.execute(insert(table).values(id=id,experiment_id=values['experiment_id'],plant_id=values['plant_id'],
                    species=values['species'],calibration_id=calibration_id,payload=dict(created_at=stamp(),
                        calibration_type=cal['calibration_type'] if cal else 'pixels_only',pot_diameter_cm=cal.get('pot_diameter_cm') if cal else None,
                        display_name=payload['name'],creation_request_sha256=signature,creation_request_id=str(request))))
        except IntegrityError as exc:
            try:
                existing=self.db.one(table,id)
                if existing['payload'].get('creation_request_sha256')==signature:return self.profile(id)
            except KeyError:pass
            raise Conflict('A plant with this name already exists. Reopen it or choose another name.') from exc
        return self.profile(id)

    def edit_plant(self,id,payload):
        table=self.db.profiles
        with self.db.engine.begin()as conn:
            row=self.db.one(table,id,conn)
            conn.execute(update(table).where(table.c.id==id).values(species=payload['species'],
                payload={**row['payload'],'display_name':payload['name'],'identity_updated_at':stamp()}))
            if row['species']!=payload['species']:
                session=self.db.review_sessions
                conn.execute(delete(session).where(session.c.id==id))
        return self.profile(id)

    def reviewed_pot(self,data,filename,content_type,diameter,points,reviewed):
        if reviewed is not True:raise ValueError('Confirm outer rim, opposite diameter points and approximate pot-plane scale')
        rgb=decode_image(data,filename,content_type,self.settings)
        sidecar=dict(schema_version=1,image_sha256=hashlib.sha256(data).hexdigest(),image_size_px=[rgb.shape[1],rgb.shape[0]],
            known_pot_diameter_cm=diameter,calibration_method='manual_two_point_diameter',diameter_points_px=points,
            review=dict(outer_rim_confirmed=True,opposite_points_confirmed=True,pot_plane_approximation_accepted=True),
            reviewed_at_utc=stamp(),review_method='explicit_web_two_point_SAVE_CALIBRATION')
        raw=json.dumps(sidecar,allow_nan=False).encode()
        return self.import_calibration(raw,data,filename,content_type)

    def calibrate_plant_photo(self,id,data,filename,content_type,diameter,points,reviewed,
                             analysis_id=None,image_sha256=None,provenance=None):
        """Commit an immutable reference and its plant/current-review binding once.

        Asset IO happens before metadata writes. A failed upload/transaction may
        leave an unreferenced attempt asset, but cannot expose a partial scale.
        Firestore adapter buffers writes and performs ID reads before commit.
        """
        if reviewed is not True:raise ValueError('Mark two opposite outer-rim points and save to confirm the approximate pot-plane reference.')
        rgb=decode_image(data,filename,content_type,self.settings);digest=hashlib.sha256(data).hexdigest()
        if image_sha256 is not None and image_sha256!=digest:raise ValueError('This reference belongs to another photo. Reopen the current photo and try again.')
        if analysis_id and not image_sha256:raise ValueError('The current photo hash is required. Reopen this review and try again.')
        observational=image_provenance(provenance,data,[rgb.shape[1],rgb.shape[0]])
        # Canonical geometry/JSON gives identical retries the same immutable ID.
        from ..pot_scale import manual_diameter
        endpoints=sorted(manual_diameter(points,rgb.shape[:2])['diameter_points_px'])
        sidecar=dict(schema_version=1,image_sha256=digest,image_size_px=[rgb.shape[1],rgb.shape[0]],
            known_pot_diameter_cm=float(diameter),calibration_method='manual_two_point_diameter',diameter_points_px=endpoints,
            review=dict(outer_rim_confirmed=True,opposite_points_confirmed=True,pot_plane_approximation_accepted=True),
            review_method='explicit_web_two_point_SAVE_CALIBRATION',profile_id=id)
        raw=json.dumps(sidecar,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
        record=import_pot(raw,data,filename,content_type,self.settings)
        if observational:record['image_provenance']=observational
        table=self.db.profiles;analyses=self.db.analyses;calibrations=self.db.calibrations
        with self.db.engine.begin() as conn:
            plant=self.db.one(table,id,conn)
            # SQLite must serialize read/modify/write before reading review
            # payloads; Firestore enforces read versions at atomic commit.
            locked=conn.execute(update(table).where(table.c.id==id).values(payload=plant['payload']))
            if locked.rowcount!=1:raise Conflict('Plant changed. Reopen this plant and try again.')
            current=None;stored=None
            if analysis_id:
                current=self.db.one(analyses,analysis_id,conn);stored=current['payload']
                session=conn.execute(select(self.db.review_sessions).where(self.db.review_sessions.c.id==id)).mappings().first()
                if current['profile_id']!=id:raise Conflict('This review belongs to another plant. Select its plant and try again.')
                if current['status'] not in ('UPLOADED','ANALYZED','DRAFT'):raise Conflict('Only the current unapproved, idle photo can be calibrated.')
                if not session or session['analysis_id']!=analysis_id:raise Conflict('A newer photo is open for this plant. Reopen the current review before saving calibration.')
                if stored['image_sha256']!=digest or stored['image_size_px']!=[rgb.shape[1],rgb.shape[0]]:
                    raise ValueError('Use this exact photo to calibrate the current review. Existing edits are retained.')
            existing=conn.execute(select(calibrations).where(calibrations.c.id==record['id'])).mappings().first()
            if existing:
                record=existing['payload']
                if plant['calibration_id']!=record['id']:raise Conflict('A newer calibration was saved for this plant. Reopen the current photo before trying again.')
            else:
                prefix='calibrations/'+record['id']+'/'+str(uuid.uuid4())
                record={**record,'assets':dict(reference_image=self.media.put(prefix+'/reference'+Path(filename).suffix.lower(),data,content_type),
                    reviewed_calibration=self.media.put(prefix+'/reviewed_calibration.json',raw,'application/json')),
                    'created_at':stamp()}
                conn.execute(insert(calibrations).values(id=record['id'],payload=record))
            if plant['calibration_id']!=record['id']:
                conn.execute(update(table).where(table.c.id==id).values(calibration_id=record['id'],
                    payload={**plant['payload'],'calibration_type':'reviewed_pot','pot_diameter_cm':record['pot_diameter_cm'],'calibration_updated_at':stamp()}))
            if stored is not None and ((stored.get('calibration') or {}).get('id')!=record['id'] or stored.get('geometry_unchanged') is not True):
                updated={**stored,'calibration':record,'geometry_unchanged':True,'calibration_revision':stored.get('calibration_revision',0)+1}
                updated['calibration_reuse']=calibration_reuse.assess(rgb,data,record,True)
                changed=conn.execute(update(analyses).where(analyses.c.id==analysis_id,analyses.c.status==current['status']).values(payload=updated))
                if changed.rowcount!=1:raise Conflict('The photo review changed. Reopen it and retry calibration.')
                self._review_session(conn,id,analysis_id)
        return dict(plant=self.profile(id),calibration=record,analysis=self.analysis(analysis_id) if analysis_id else None)

    def calibration(self,payload):
        id=payload.get('calibration_id')
        if id:return self.db.one(self.db.calibrations,id)['payload']
        record=scalar_calibration(payload)
        return self.add_calibration(record) if record else None

    def create_profile(self,payload):
        calibration=self.calibration(payload)
        id=str(uuid.uuid4());table=self.db.profiles
        data=dict(created_at=stamp(),calibration_type=calibration['calibration_type'] if calibration else 'pixels_only',
            pot_diameter_cm=calibration.get('pot_diameter_cm') if calibration else None)
        try:
            with self.db.engine.begin() as conn:
                conn.execute(insert(table).values(id=id,experiment_id=payload['experiment_id'],plant_id=payload['plant_id'],
                    species=payload['species'],calibration_id=calibration['id'] if calibration else None,payload=data))
        except IntegrityError as exc:raise Conflict('This experiment / plant already exists') from exc
        return self.profile(id)

    def profile(self,id):
        row=self.db.one(self.db.profiles,id)
        calibration=self.db.one(self.db.calibrations,row['calibration_id'])['payload'] if row['calibration_id'] else None
        return dict(**{k:v for k,v in row['payload'].items()if k not in ('display_name','pot_diameter_cm')},id=id,experiment_id=row['experiment_id'],plant_id=row['plant_id'],species=row['species'],
            display_name=row['payload'].get('display_name',row['plant_id']),
            pot_diameter_cm=row['payload'].get('pot_diameter_cm',calibration.get('pot_diameter_cm') if calibration else None),
            calibration=calibration,cm_per_pixel=calibration['cm_per_pixel'] if calibration else None,
            calibration_provenance=calibration['provenance'] if calibration else {})

    def profiles(self):
        return sorted([self.profile(row['id']) for row in self.db.rows(self.db.profiles)],key=lambda p:(p['experiment_id'],p['plant_id']))

    def update_calibration(self,id,payload):
        self.profile(id);cal=self.calibration(payload);table=self.db.profiles
        with self.db.engine.begin() as conn:
            old=self.db.one(table,id,conn)
            conn.execute(update(table).where(table.c.id==id).values(calibration_id=cal['id'] if cal else None,
                payload={**old['payload'],'calibration_type':cal['calibration_type'] if cal else 'pixels_only',
                    'pot_diameter_cm':cal.get('pot_diameter_cm') if cal else old['payload'].get('pot_diameter_cm'),
                    'calibration_updated_at':stamp()}))
        return self.profile(id)

    def upload(self,profile_id,capture_date,geometry_unchanged,data,filename,content_type,source_research_permission=False,permission_basis='source_research_permission',provenance=None,request_id=None):
        profile=self.profile(profile_id);rgb=decode_image(data,filename,content_type,self.settings)
        observational=image_provenance(provenance,data,[rgb.shape[1],rgb.shape[0]])
        signature=hashlib.sha256(json.dumps(dict(profile_id=profile_id,date=capture_date.isoformat(),geometry_unchanged=geometry_unchanged,
            image_sha256=hashlib.sha256(data).hexdigest(),source_research_permission=source_research_permission,permission_basis=permission_basis),sort_keys=True).encode()).hexdigest()
        id=str(uuid.uuid5(uuid.NAMESPACE_URL,'hydroponic:upload:'+str(request_id))) if request_id else str(uuid.uuid4())
        if request_id:
            try:
                previous=self.db.one(self.db.analyses,id)
                if previous['payload'].get('upload_request_sha256')!=signature:raise Conflict('This upload request belongs to another plant, date or photo. Choose the photo again.')
                return self.analysis(id)
            except KeyError:pass
        suffix=Path(filename).suffix.lower()
        asset=self.media.put('analyses/'+id+'/'+str(uuid.uuid4())+'/original'+suffix,data,content_type)
        payload=dict(image_sha256=hashlib.sha256(data).hexdigest(),image_size_px=[rgb.shape[1],rgb.shape[0]],
            original_filename=Path(filename).name,uploaded_at=stamp(),species=profile['species'],plant_id=profile['plant_id'],
            experiment_id=profile['experiment_id'],calibration=profile['calibration'],geometry_unchanged=geometry_unchanged,
            source_ownership='project_owner' if source_research_permission is True else 'unknown',data_origin='real',
            research_permission_provenance=(dict(basis=('Closed academic research: project owner authorizes approved real captures for batch training'
                if permission_basis=='closed_academic_research_policy' else 'Uploader explicitly declares own capture and permits project research training'),
                declaration=permission_basis,declared_at=stamp()) if source_research_permission is True else None),
            assets={'original':asset},pipeline=None,automatic_mask_available=False)
        if observational:payload['image_provenance']=observational
        if request_id:payload.update(upload_request_id=str(request_id),upload_request_sha256=signature)
        payload['calibration_reuse']=self._calibration_assessment(payload,rgb,data)
        try:
            with self.db.engine.begin() as conn:
                existing=conn.execute(select(self.db.analyses).where(self.db.analyses.c.id==id)).mappings().first() if request_id else None
                if existing:
                    if existing['payload'].get('upload_request_sha256')!=signature:raise Conflict('This upload request belongs to another photo.')
                else:
                    conn.execute(insert(self.db.analyses).values(id=id,profile_id=profile_id,date=capture_date,status='UPLOADED',payload=payload))
                    session=self.db.review_sessions
                    previous=conn.execute(select(session).where(session.c.id==profile_id)).mappings().first()
                    values=dict(profile_id=profile_id,analysis_id=id,payload=dict(updated_at=stamp()))
                    if previous:conn.execute(update(session).where(session.c.id==profile_id).values(**values))
                    else:conn.execute(insert(session).values(id=profile_id,**values))
        except IntegrityError as exc:
            if request_id:
                previous=self.db.one(self.db.analyses,id)
                if previous['payload'].get('upload_request_sha256')==signature:return self.analysis(id)
            raise Conflict('This photo changed while saving. Reopen the current review and retry.') from exc
        return self.analysis(id)

    def _review_session(self,conn,profile_id,analysis_id,remove=False,restore=False):
        table=self.db.review_sessions
        row=conn.execute(select(table).where(table.c.id==profile_id)).mappings().first()
        if row and row['analysis_id']==analysis_id:
            if remove:conn.execute(delete(table).where(table.c.id==profile_id))
            else:conn.execute(update(table).where(table.c.id==profile_id).values(payload=dict(updated_at=stamp())))
        elif row is None and restore:
            conn.execute(insert(table).values(id=profile_id,profile_id=profile_id,analysis_id=analysis_id,payload=dict(updated_at=stamp())))

    def pending_review(self,profile_id,active_id=None):
        self.profile(profile_id);table=self.db.review_sessions
        with self.db.engine.begin()as conn:
            session=conn.execute(select(table).where(table.c.id==profile_id)).mappings().first()
            if not session:return []
            row=self.db.one(self.db.analyses,session['analysis_id'],conn);payload=row['payload']
            now=datetime.now(timezone.utc)
            def age(value):
                try:return (now-datetime.fromisoformat(value)).total_seconds()
                except (TypeError,ValueError):return float('inf')
            stale_job=row['status']=='ANALYZING' and age(payload.get('analysis_started_at'))>=ANALYSIS_LEASE_SECONDS
            expired=age(session['payload'].get('updated_at'))>=REVIEW_RETENTION_SECONDS
            if stale_job:
                conn.execute(update(self.db.analyses).where(self.db.analyses.c.id==row['id'],self.db.analyses.c.status=='ANALYZING',
                    self.db.analyses.c.payload['analysis_token'].as_string()==payload.get('analysis_token')).values(
                        status='ERROR',payload={**payload,'analysis_error':'Interrupted analysis expired; upload again','failed_at':stamp()}))
            if expired or stale_job or row['status'] in ('APPROVED','ERROR'):
                conn.execute(delete(table).where(table.c.id==profile_id));return []
            if row['id']==active_id:return []
            return [dict(id=row['id'],date=row['date'].isoformat(),status=row['status'],uploaded_at=payload['uploaded_at'])]

    def _rgb(self,payload):
        data=self.media.read(payload['assets']['original'])
        return decode_image(data,payload['original_filename'],payload['assets']['original']['content_type'],self.settings),data

    def _calibration_assessment(self,payload,rgb=None,data=None):
        existing=payload.get('calibration_reuse')
        cal=payload.get('calibration')
        if existing:
            if (existing.get('image_sha256')!=payload['image_sha256'] or
                existing.get('image_size_px')!=payload['image_size_px'] or
                existing.get('calibration_id')!=(cal.get('id') if cal else None)):
                raise ValueError('Calibration reuse evidence is stale or belongs to another photo/reference')
            return existing
        if rgb is None:rgb,data=self._rgb(payload)
        reference=None
        asset=(cal or {}).get('assets',{}).get('reference_image')
        if asset:
            try:
                raw=self.media.read(asset)
                if hashlib.sha256(raw).hexdigest()!=cal['provenance']['reference_image_sha256']:
                    raise ValueError('Reference image does not match reviewed calibration')
                with Image.open(io.BytesIO(raw)) as im:reference=np.array(im.convert('RGB'))
            except (OSError,ValueError,KeyError,GoogleAPIError):
                # An unavailable reference never certifies a physical scale.
                reference=None
        return calibration_reuse.assess(rgb,data,cal,payload.get('geometry_unchanged'),reference_rgb=reference)

    def confirm_calibration(self,id,claims):
        table=self.db.analyses
        with self.db.engine.begin() as conn:
            current=self.db.one(table,id,conn)
            if current['status'] not in ('UPLOADED','ANALYZED','DRAFT'):raise Conflict('Only an unapproved, idle review can change calibration')
            locked=conn.execute(update(table).where(table.c.id==id,table.c.status==current['status']).values(status=current['status']))
            if locked.rowcount!=1:raise Conflict('Only an unapproved, idle review can confirm calibration')
            row=self.db.one(table,id,conn);stored=row['payload']
            confirmed=calibration_reuse.confirm(self._calibration_assessment(stored),claims)
            confirmed['confirmation']['confirmed_at']=stamp()
            conn.execute(update(table).where(table.c.id==id).values(payload={**stored,'calibration_reuse':confirmed,
                'calibration_revision':stored.get('calibration_revision',0)+1}))
        return self.analysis(id)

    def mark_geometry_changed(self,id):
        table=self.db.analyses
        with self.db.engine.begin() as conn:
            current=self.db.one(table,id,conn)
            if current['status'] not in ('UPLOADED','ANALYZED','DRAFT'):raise Conflict('Only an unapproved, idle review can change calibration')
            locked=conn.execute(update(table).where(table.c.id==id,table.c.status==current['status']).values(status=current['status']))
            if locked.rowcount!=1:raise Conflict('Review state changed; reopen before editing calibration')
            stored=self.db.one(table,id,conn)['payload']
            changed={**stored,'geometry_unchanged':False,'calibration_revision':stored.get('calibration_revision',0)+1}
            changed.pop('calibration_reuse',None)
            changed['calibration_reuse']=self._calibration_assessment(changed)
            conn.execute(update(table).where(table.c.id==id).values(payload=changed))
        return self.analysis(id)

    def recalibrate_review(self,id,claims):
        if not isinstance(claims,dict):raise ValueError('Photo-bound reviewed calibration is required')
        table=self.db.analyses
        with self.db.engine.begin() as conn:
            current=self.db.one(table,id,conn)
            if current['status'] not in ('UPLOADED','ANALYZED','DRAFT'):raise Conflict('Only an unapproved, idle review can change calibration')
            locked=conn.execute(update(table).where(table.c.id==id,table.c.status==current['status']).values(status=current['status']))
            if locked.rowcount!=1:raise Conflict('Only an unapproved, idle review can be recalibrated')
            row=self.db.one(table,id,conn);stored=row['payload']
            cal=self.db.one(self.db.calibrations,claims.get('calibration_id'),conn)['payload']
            if (claims.get('image_sha256')!=stored['image_sha256'] or
                cal.get('provenance',{}).get('reference_image_sha256')!=stored['image_sha256']):
                raise ValueError('Recalibrate using this exact photo; existing edits will be retained')
            updated={**stored,'calibration':cal,'geometry_unchanged':True,'calibration_revision':stored.get('calibration_revision',0)+1}
            updated.pop('calibration_reuse',None)
            updated['calibration_reuse']=self._calibration_assessment(updated)
            conn.execute(update(table).where(table.c.id==id).values(payload=updated))
        return self.analysis(id)

    def analyze(self,id,engine='auto'):
        if engine not in ('auto','classical') and not re.fullmatch(r'learned:[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',str(engine)):
            raise ValueError('Engine must be auto, classical or learned:<registered version>')
        table=self.db.analyses
        with self.db.engine.begin() as conn:
            row=self.db.one(table,id,conn)
            if row['status'] in ('ANALYZED','DRAFT','APPROVED'):return self.analysis(id)
            payload=row['payload'];token=str(uuid.uuid4())
            condition=table.c.status.in_(['UPLOADED','ERROR'])
            if row['status']=='ANALYZING':
                try:started=datetime.fromisoformat(payload['analysis_started_at'])
                except (KeyError,ValueError):started=datetime.min.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc)-started).total_seconds()<ANALYSIS_LEASE_SECONDS:
                    raise Conflict('Analysis is running. Interrupted work can be retried after 5 minutes.')
                old_token=payload.get('analysis_token')
                condition=(table.c.status=='ANALYZING') & (table.c.payload['analysis_token'].as_string()==old_token)
            payload={**payload,'analysis_token':token,'analysis_started_at':stamp(),'requested_engine':engine}
            changed=conn.execute(update(table).where(table.c.id==id,condition).values(status='ANALYZING',payload=payload))
            if changed.rowcount!=1:raise Conflict('Analysis state changed; reload')
        try:
            rgb,data=self._rgb(payload)
            result,candidate_model=self._candidate(rgb,payload,engine)
            available=result['mask'] is not None
            mask=result['mask'] if available else np.zeros(rgb.shape[:2],bool)
            plant_mask=mask.copy()
            mask,labels,refinement=lamina.propose_lamina(plant_mask,rgb)
            status='UNREVIEWED_CANDIDATE' if available else 'UNAVAILABLE_MANUAL_INITIALIZATION'
            prefix='analyses/'+id+'/'+str(uuid.uuid4())
            assets={**payload['assets'],
                'plant_foreground_mask':self.media.put(prefix+'/plant_foreground_mask.png',png_bytes(Image.fromarray(plant_mask.astype(np.uint8)*255),status),'image/png'),
                'plant_foreground_overlay':self.media.put(prefix+'/plant_foreground_overlay.png',png_bytes(mask_overlay(rgb,plant_mask),status),'image/png'),
                'structural_exclusion_mask':self.media.put(prefix+'/structural_exclusion_mask.png',png_bytes(Image.fromarray((plant_mask&~mask).astype(np.uint8)*255),status),'image/png'),
                'candidate_mask':self.media.put(prefix+'/automatic_candidate_mask.png',png_bytes(Image.fromarray(mask.astype(np.uint8)*255),status),'image/png'),
                'candidate_overlay':self.media.put(prefix+'/candidate_overlay.png',png_bytes(mask_overlay(rgb,mask),status),'image/png')}
            assets.update(self._instance_assets(prefix,labels,rgb,mask,status,'candidate_instances','candidate_instance_overlay'))
            pipeline=result['metadata']
            payload={**payload,'measurement_target':lamina.TARGET,'lamina_refinement':refinement,'pipeline':pipeline,'candidate_model':candidate_model,'assets':assets,'analyzed_at':stamp(),'automatic_mask_available':available,
                'candidate_status':status,'species_validation':'UNVALIDATED / REVIEW REQUIRED' if payload['species']=='rumex_acetosa' else 'DEVELOPMENT / HUMAN REVIEW REQUIRED'}
            with self.db.engine.begin() as conn:
                completed=conn.execute(update(table).where(table.c.id==id,table.c.status=='ANALYZING',
                    table.c.payload['analysis_token'].as_string()==token).values(status='ANALYZED',payload=payload))
                if completed.rowcount!=1:raise Conflict('A newer analysis attempt owns this image. Reload its result.')
                self._review_session(conn,row['profile_id'],id,restore=True)
        except Exception as exc:
            with self.db.engine.begin() as conn:
                conn.execute(update(table).where(table.c.id==id,table.c.status=='ANALYZING',
                    table.c.payload['analysis_token'].as_string()==token).values(status='ERROR',payload={**payload,
                        'analysis_error':type(exc).__name__+': '+str(exc)[:500],'failed_at':stamp()}))
                self._review_session(conn,row['profile_id'],id,remove=True)
            raise
        return self.analysis(id)

    def _mask(self,reference):
        with Image.open(io.BytesIO(self.media.read(reference))) as mask:return np.array(mask)!=0

    def _labels(self,reference):
        with Image.open(io.BytesIO(self.media.read(reference))) as image:return np.asarray(image,dtype=np.uint16)

    def _instance_assets(self,prefix,labels,rgb,mask,status,label_name,overlay_name,identity=None):
        return {label_name:self.media.put(prefix+'/'+label_name+'.png',png_bytes(Image.fromarray(labels.astype(np.uint16)),status),'image/png'),
            overlay_name:self.media.put(prefix+'/'+overlay_name+'.png',png_bytes(leaf_instances.overlay(rgb,labels,mask,identity),status),'image/png')}

    def _current_labels(self,stored,mask):
        reference=stored['assets'].get('draft_instances',stored['assets'].get('candidate_instances'))
        labels=self._labels(reference).copy() if reference else leaf_instances.propose_instances(mask,self._rgb(stored)[0])
        labels[~mask]=0
        return labels

    def _instance_input(self,payload,stored,mask,id):
        return leaf_instances.decode(payload,mask,id) if 'instance_base64' in payload else self._current_labels(stored,mask)

    def _instance_trace(self,stored,labels,payload,mask,analysis_id):
        identity=self._identity_input(stored,labels,payload,analysis_id)
        by_label={r['label_id']:r['internal_instance_id'] for r in identity['instances']}
        candidate_ref=stored['assets'].get('candidate_instances')
        baseline=self._labels(candidate_ref) if candidate_ref else np.zeros(labels.shape,np.uint16)
        changed=labels!=baseline
        corrected=sorted(set(map(int,np.unique(baseline[changed])))|set(map(int,np.unique(labels[changed]))))
        edits=payload.get('instance_edits',[])
        if not isinstance(edits,list) or len(edits)>200:raise ValueError('Too many instance edit events; save a draft')
        clean=[]
        for event in edits:
            if not isinstance(event,dict) or event.get('operation') not in ('split','merge','reassign','delete','add','total_mask','undo','redo'):
                raise ValueError('Invalid instance edit event')
            ids=event.get('leaf_ids',[])
            if not isinstance(ids,list) or len(ids)>leaf_instances.MAX_ID or any(type(i) is not int or not 1<=i<=leaf_instances.MAX_ID for i in ids):raise ValueError('Invalid corrected leaf IDs')
            internal=event.get('internal_instance_ids',[by_label.get(i) for i in ids])
            if not isinstance(internal,list) or len(internal)!=len(ids) or any(v is not None and (not isinstance(v,str) or len(v)>160) for v in internal):raise ValueError('Invalid instance identity history')
            item=dict(operation=event['operation'],leaf_ids=ids,internal_instance_ids=internal)
            if event['operation']=='add':
                region=self._add_selection(event,mask.shape)
                item.update(painted_runs=event['painted_runs'],selected_pixel_count=int(region.sum()))
                if 'added_lamina_pixel_count' in event:
                    added=event['added_lamina_pixel_count']
                    if type(added) is not int or not 0<=added<=item['selected_pixel_count']:raise ValueError('Invalid Add lamina expansion count')
                    item.update(added_lamina_pixel_count=added,added_lamina_pixel_count_basis='client_reported_mixed_edit_history')
                for key in ('source_leaf_ids','source_display_leaf_numbers'):
                    values=event.get(key,[])
                    if not isinstance(values,list) or len(values)>leaf_instances.MAX_ID or any(type(i) is not int or not 1<=i<=leaf_instances.MAX_ID for i in values):raise ValueError('Invalid Add source leaf history')
                    item[key]=values
                sources=event.get('source_internal_instance_ids',[])
                if not isinstance(sources,list) or len(sources)>leaf_instances.MAX_ID or any(not isinstance(v,str) or len(v)>160 for v in sources):raise ValueError('Invalid Add source identity history')
                item['source_internal_instance_ids']=sources
            clean.append(item)
        identity['instances'].sort(key=lambda entry:entry['label_id'])
        identity=instance_identity.resolve(labels,analysis_id,identity)
        self._validate_assignment_operations(stored,mask,labels,clean,identity)
        prior=stored.get('instance_provenance',{}).get('edit_events',[])
        return dict(instance_metadata=identity,identity_source_sha256=hashlib.sha256((ROOT/'src/webapp/instance_identity.py').read_bytes()).hexdigest(),corrected_internal_instance_ids=[by_label[i] for i in corrected if i in by_label],method_version=leaf_instances.VERSION,source_sha256=hashlib.sha256((ROOT/'src/webapp/leaf_instances.py').read_bytes()).hexdigest(),
            measurement_target=stored.get('measurement_target','visible_plant_foreground'),
            lamina_refinement=stored.get('lamina_refinement'),
            lamina_source_sha256=hashlib.sha256((ROOT/'src/webapp/lamina.py').read_bytes()).hexdigest(),
            native_label_sha256=leaf_instances.fingerprint(labels),candidate_label_sha256=leaf_instances.fingerprint(baseline),
            edited=bool(changed.any()),editing_performed=bool(changed.any()) or bool(prior+clean),changed_assignment_pixels=int(changed.sum()),corrected_leaf_ids=[i for i in corrected if i],
            edit_events=(prior+clean)[-200:],edit_events_basis='client supplied interaction history; pixel differences computed by server',
            scientific_status='UNVALIDATED_INSTANCE_METHOD',hidden_tissue_inferred=False)

    def _identity_input(self,stored,labels,payload,id):
        parent_mask=self._mask(stored['assets'].get('draft_mask',stored['assets']['candidate_mask']))
        previous=instance_identity.resolve(self._current_labels(stored,parent_mask),id,stored.get('instance_metadata'))
        identity=instance_identity.resolve(labels,id,payload.get('instance_metadata'),previous=previous,strict='instance_metadata' in payload)
        prior=stored.get('instance_provenance',{}).get('edit_events',[])
        edits=payload.get('instance_edits',[])
        if not isinstance(edits,list) or len(edits)>200:raise ValueError('Too many instance edit events; save a draft')
        return instance_identity.retain_history(labels,id,identity,prior+edits)

    @staticmethod
    def _add_selection(event,shape):
        runs=event.get('painted_runs')
        if not isinstance(runs,list) or not runs:raise ValueError('Add requires an explicitly painted region. Select Add Leaf and paint its region again.')
        selected=np.zeros(shape,bool);flat=selected.ravel();end=0;count=0
        if len(runs)>flat.size:raise ValueError('Invalid Add painted region')
        for run in runs:
            if not isinstance(run,list) or len(run)!=2 or any(type(v) is not int for v in run):raise ValueError('Invalid Add painted region')
            start,length=run
            if start<end or length<=0 or start+length>flat.size:raise ValueError('Invalid Add painted region bounds or overlap')
            flat[start:start+length]=True;end=start+length;count+=length
        if type(event.get('selected_pixel_count')) is not int or event['selected_pixel_count']!=count:raise ValueError('Add painted pixel count does not match its region')
        return selected

    def _validate_assignment_operations(self,stored,mask,labels,events,identity=None):
        """Check recorded operation semantics independently of toolbar styling.

        A batch containing mask edits or Undo may legitimately restore a mask;
        native ID/range/coverage checks still apply. Legacy manual payloads
        without operation metadata retain their existing contract.
        """
        if not events or any(e['operation'] in ('total_mask','undo','redo') for e in events):return
        previous_mask=self._mask(stored['assets'].get('draft_mask',stored['assets']['candidate_mask']))
        previous=self._current_labels(stored,previous_mask)
        names={e['operation'] for e in events}
        if names=={'add'}:
            replay=previous.copy();replay_mask=previous_mask.copy();prior=instance_identity.resolve(previous,None,stored.get('instance_metadata'))
            reserved={r['label_id'] for r in prior['instances']};order=prior['display_order'][:];next_id=prior['next_label_id']
            by_label={r['label_id']:r['internal_instance_id'] for r in identity['instances']}
            for event in events:
                ids=event['leaf_ids']
                if len(ids)!=1 or ids[0] in reserved or ids[0]<next_id:raise ValueError('Add must create a fresh internal leaf instance')
                target=ids[0];selected=self._add_selection(event,mask.shape)
                if np.any(selected&~mask):raise ValueError('Every painted Add pixel must join the final lamina mask')
                event.update(added_lamina_pixel_count=int(np.count_nonzero(selected&~replay_mask)),added_lamina_pixel_count_basis='server_replayed_painted_region')
                replay_mask|=selected
                sources=set(map(int,np.unique(replay[selected])))-{0};source_order=[i for i in order if i in sources]
                event.update(source_leaf_ids=source_order,source_display_leaf_numbers=[order.index(i)+1 for i in source_order],source_internal_instance_ids=[by_label[i] for i in source_order])
                replay[selected]=target;active=set(map(int,np.unique(replay)))-{0};order=[i for i in order if i in active]+[target]
                reserved.add(target);next_id=target+1
            if not np.array_equal(mask,replay_mask):raise ValueError('Add may extend lamina only at explicitly painted pixels; existing lamina must stay unchanged')
            if not np.array_equal(labels,replay):raise ValueError('Add may transfer only its explicitly painted pixels; unpainted ownership must stay unchanged')
            if identity['display_order']!=order:raise ValueError('Add must append the new leaf after surviving source leaves')
            return
        permitted={i for e in events if e['operation'] in ('split','merge','delete') for i in e['leaf_ids']}
        add_region=np.zeros(mask.shape,bool);replay_mask=previous_mask.copy()
        for event in events:
            if event['operation']=='add':
                region=self._add_selection(event,mask.shape)
                if np.any(region&~mask):raise ValueError('Every painted Add pixel must join the final lamina mask')
                event.update(added_lamina_pixel_count=int(np.count_nonzero(region&~replay_mask)),added_lamina_pixel_count_basis='server_replayed_painted_region')
                replay_mask|=region
                add_region|=region
        if not np.array_equal(mask,replay_mask):
            raise ValueError('Add may extend lamina only at explicitly painted pixels; otherwise only Edit lamina mask paint/erase may change lamina area')
        if 'add' in names:self._validate_compound_add_lineage(stored,previous,labels,events)
        if 'reassign' not in names:
            protected=(previous>0)&(labels!=previous)&~np.isin(previous,list(permitted))&~add_region
            if protected.any():raise ValueError('Existing leaf pixels were protected. Use Reassign to transfer ownership.')
        if len(events)!=1:return # Compound changes are audited against native state and allowed owners.
        event=events[0];ids=event['leaf_ids'];operation=event['operation'];changed=labels!=previous
        if operation=='reassign' and np.any(changed&~np.isin(labels,ids)):
            raise ValueError('Reassign may transfer pixels only to its selected leaf')
        if operation=='merge':
            if len(set(ids))<2 or any(not np.any(previous==i) for i in ids):raise ValueError('Select 2 or more existing leaves to merge')
            region=np.isin(previous,ids);targets=np.unique(labels[region])
            order=instance_identity.resolve(previous,None,stored.get('instance_metadata'))['display_order']
            expected=next(i for i in order if i in ids)
            if len(targets)!=1 or targets[0]!=expected:raise ValueError('Merge must retain the earliest selected leaf and combine complete IDs')
        if operation=='split':
            if len(ids)!=2 or np.any(changed&(previous!=ids[0])):raise ValueError('Split may change only the selected leaf')
            if np.any(previous==ids[1]):raise ValueError('Split must create one new leaf ID')
            region=previous==ids[0]
            if set(map(int,np.unique(labels[region])))!=set(ids):raise ValueError('Split must create exactly two nonempty leaf IDs')
            for i in ids:
                _,parts=leaf_instances.component_labels(labels==i)
                if len(parts)!=1:raise ValueError('Split would create an invalid disconnected fragment')
        if operation=='delete' and np.any(np.isin(previous,ids)&(labels!=0)):
            raise ValueError('Delete must leave the selected leaf pixels unassigned')

    def _validate_compound_add_lineage(self,stored,previous,labels,events):
        """Keep painted Add pixels in their explicitly edited identity lineage.

        Split boundaries and Reassign brushes are not replayable from old event
        records. Track their allowed identity transitions conservatively rather
        than permitting an unrelated owner just because Merge is also present.
        """
        prior=instance_identity.resolve(previous,None,stored.get('instance_metadata'))
        reserved={r['label_id'] for r in prior['instances']};order=prior['display_order'][:]
        next_id=prior['next_label_id'];introduced=set();merge_targets={}
        for n,event in enumerate(events):
            op=event['operation'];ids=event['leaf_ids']
            if op in ('add','split'):
                if len(ids)!=(1 if op=='add' else 2):raise ValueError('Add/Split must introduce exactly one new leaf identity')
                target=ids[-1]
                if target in reserved or target<next_id:raise ValueError('Add/Split must create a fresh internal leaf instance')
                if op=='split':
                    if ids[0] not in order:raise ValueError('Split must select an existing leaf')
                    order.insert(order.index(ids[0])+1,target)
                else:order.append(target)
                reserved.add(target);introduced.add(target);next_id=target+1
            elif op=='merge':
                if len(set(ids))<2 or any(i not in order for i in ids):raise ValueError('Merge must select existing complete leaf identities')
                target=next(i for i in order if i in ids);merge_targets[n]=target
                order=[i for i in order if i not in ids or i==target]
            elif op=='delete':order=[i for i in order if i not in ids]
        original=set(map(int,np.unique(previous)))-{0}
        if (set(map(int,np.unique(labels)))-{0})-(original|introduced):
            raise ValueError('Add may introduce only its explicitly recorded new leaf identity')
        for n,event in enumerate(events):
            if event['operation']!='add':continue
            region=self._add_selection(event,labels.shape);allowed=set(event['leaf_ids'])
            for m,later in enumerate(events[n+1:],n+1):
                op=later['operation'];ids=later['leaf_ids']
                if op=='add':region&=~self._add_selection(later,labels.shape)
                elif op=='split' and ids[0] in allowed:allowed.add(ids[1])
                elif op=='merge' and allowed.intersection(ids):allowed.difference_update(ids);allowed.add(merge_targets[m])
                elif op=='delete' and allowed.intersection(ids):allowed.difference_update(ids);allowed.add(0)
                elif op=='reassign':allowed.update(ids)
            if np.any(region&~np.isin(labels,list(allowed))):
                raise ValueError('Painted Add pixels must belong to the new leaf or its explicitly edited descendants')

    def analysis(self,id):
        row=self.db.one(self.db.analyses,id);payload=row['payload']
        result=dict(id=id,profile_id=row['profile_id'],status=row['status'],date=row['date'].isoformat(),
            measurement_id=row['measurement_id'],**{k:v for k,v in payload.items() if k!='assets'})
        result['assets']={name:f'/api/assets/analysis/{id}/{name}' for name in payload['assets']}
        result['measurement_status']='MANUAL_GROUND_TRUTH' if row['status']=='APPROVED' else 'UNREVIEWED_CANDIDATE'
        result['human_approval_required']=row['status']!='APPROVED'
        if row['status']=='APPROVED':
            approved=self.db.one(self.db.measurements,row['measurement_id'])['payload']
            assessment=approved.get('calibration_reuse') or dict(status='VERIFIED',
                effective_cm_per_pixel=approved.get('cm_per_pixel'),reason='',evidence=dict(method='historical_approved_measurement'))
        else:assessment=self._calibration_assessment(payload)
        scale=assessment.get('effective_cm_per_pixel')
        valid=assessment.get('status') in calibration_reuse.ACCEPTED
        result.update(calibration_reuse=assessment,effective_cm_per_pixel=scale,
            calibration_valid=valid,calibration_issue='' if valid else assessment['reason'])
        if payload.get('calibration',{}):
            cal=payload['calibration']
            if cal.get('assets',{}).get('reference_image'):
                result['calibration_reference_url']=f'/api/assets/calibration/{cal["id"]}/reference_image'
        if 'candidate_mask' in payload['assets']:
            reference=payload['assets'].get('draft_mask',payload['assets']['candidate_mask'])
            if row['status']=='APPROVED':
                approved=self.db.one(self.db.measurements,row['measurement_id'])['payload']
                reference=approved['assets']['approved_mask']
                for name in ('approved_mask','overlay'):
                    result['assets'][name]=f'/api/assets/measurement/{row["measurement_id"]}/{name}'
            mask=self._mask(reference)
            result['mask_base64']=base64.b64encode(mask.astype(np.uint8).tobytes()).decode()
            labels=self._current_labels(payload,mask)
            if row['status']=='APPROVED' and 'leaf_instances' in approved['assets']:
                labels=self._labels(approved['assets']['leaf_instances'])
                for name in ('leaf_instances','leaf_instance_overlay'):
                    result['assets'][name]=f'/api/assets/measurement/{row["measurement_id"]}/{name}'
            if row['status']=='APPROVED':
                # Historical scientific values are immutable; never re-evaluate
                # a previously accepted calibration using today's verifier.
                scale=approved.get('cm_per_pixel')
                result.update(effective_cm_per_pixel=scale,calibration_valid=scale is not None,
                    calibration_reuse=approved.get('calibration_reuse'),calibration_issue='')
            identity=(approved if row['status']=='APPROVED' else payload).get('instance_metadata')
            result.update(leaf_instances.summary(labels,mask,scale,measurement_target=payload.get('measurement_target','visible_plant_foreground'),instance_metadata=identity,analysis_id=id))
            result.update(instance_base64=leaf_instances.encode(labels),instance_analysis_id=id)
            if row['status']=='APPROVED':
                for key in ('leaf_count','per_leaf_pixel_area','per_leaf_projected_area_cm2','leaves','total_projected_area_cm2','mean_leaf_area_cm2','mean_leaf_pixel_area','median_leaf_pixel_area','median_leaf_area_cm2','median_leaf_area','measurement_target','area_definition','total_leaf_area','mean_leaf_area','leaf_area_unit','leaf_measurement_status','instance_provenance'):
                    if key in approved:result[key]=approved[key]
                if approved.get('instances_reviewed') is not True:
                    result.update(leaf_count=None,per_leaf_pixel_area=[],per_leaf_projected_area_cm2=[],leaves=[],
                        mean_leaf_area_cm2=None,mean_leaf_pixel_area=None,mean_leaf_area=None,median_leaf_pixel_area=None,median_leaf_area_cm2=None,median_leaf_area=None,leaf_measurement_status='NOT_REVIEWED')
                elif result.get('leaves'):
                    result['leaves']=instance_identity.decorate(result['leaves'],result['instance_metadata'])
        return result

    def draft(self,id,payload):
        table=self.db.analyses
        with self.db.engine.begin() as conn:
            # State update acquires a DB write lock, shared with the approval path.
            locked=conn.execute(update(table).where(table.c.id==id,table.c.status.in_(['ANALYZED','DRAFT'])).values(status='DRAFT'))
            if locked.rowcount!=1:raise Conflict('Only an unapproved analysis can be edited')
            row=self.db.one(table,id,conn);stored=row['payload']
            mask=decode_mask(payload,stored['image_size_px']);rgb,_=self._rgb(stored)
            labels=self._instance_input(payload,stored,mask,id)
            trace=self._instance_trace(stored,labels,payload,mask,id)
            prefix='analyses/'+id+'/drafts/'+str(uuid.uuid4())
            assets={**stored['assets'],
                'draft_mask':self.media.put(prefix+'/draft_mask.png',png_bytes(Image.fromarray(mask.astype(np.uint8)*255),'UNREVIEWED_DRAFT'),'image/png'),
                'draft_overlay':self.media.put(prefix+'/draft_overlay.png',png_bytes(mask_overlay(rgb,mask),'UNREVIEWED_DRAFT'),'image/png')}
            assets.update(self._instance_assets(prefix,labels,rgb,mask,'UNREVIEWED_DRAFT','draft_instances','draft_instance_overlay',trace['instance_metadata']))
            conn.execute(update(table).where(table.c.id==id).values(payload={**stored,'assets':assets,'instance_metadata':trace['instance_metadata'],'instance_provenance':trace,'draft_saved_at':stamp()}))
            self._review_session(conn,row['profile_id'],id)
        return self.analysis(id)

    def split_leaf(self,id,payload):
        row=self.db.one(self.db.analyses,id)
        if row['status'] not in ('ANALYZED','DRAFT'):raise Conflict('Only an unapproved analysis can be edited')
        mask=decode_mask(payload,row['payload']['image_size_px'])
        labels=self._instance_input(payload,row['payload'],mask,id)
        identity=self._identity_input(row['payload'],labels,payload,id)
        split=leaf_instances.split_instance(labels,payload.get('leaf_id'),payload.get('points'),new_id=identity['next_label_id'])
        identity=instance_identity.resolve(split,id,previous=identity)
        new_id=int(split.max());identity['display_order'].remove(new_id)
        identity['display_order'].insert(identity['display_order'].index(payload['leaf_id'])+1,new_id)
        return dict(instance_base64=leaf_instances.encode(split),instance_analysis_id=id,
            new_leaf_id=new_id,**leaf_instances.summary(split,mask,None,instance_metadata=identity,analysis_id=id))

    def approve(self,id,payload):
        if payload.get('approval')!='APPROVE':raise ValueError('Explicit human APPROVE required')
        review=payload.get('review')
        if not isinstance(review,dict) or any(review.get(k) is not True for k in REVIEW_KEYS):
            raise ValueError('Confirm every visible-tissue, exclusion, completeness and scale statement')
        table=self.db.analyses;measurements=self.db.measurements;measurement_id=str(uuid.uuid4());owner_event=None
        try:
            with self.db.engine.begin() as conn:
                locked=conn.execute(update(table).where(table.c.id==id,table.c.status.in_(['ANALYZED','DRAFT'])).values(status='APPROVING'))
                if locked.rowcount!=1:raise Conflict('This analysis is not ready or is already approved')
                row=self.db.one(table,id,conn);stored=row['payload']
                if (type(payload.get('calibration_revision',0)) is not int or
                    payload.get('calibration_revision',0)!=stored.get('calibration_revision',0)):
                    raise Conflict('Photo calibration changed in another request. Reopen this review and check the updated scale before approval.')
                mask=decode_mask(payload,stored['image_size_px'])
                if not mask.any():raise ValueError('An empty mask cannot be approved as visible living tissue')
                labels=self._instance_input(payload,stored,mask,id)
                instances_reviewed=payload.get('instances_reviewed') is True
                if 'instance_base64' in payload and not instances_reviewed:
                    raise ValueError('Confirm human review of the individual leaf assignments before approval')
                if instances_reviewed:
                    if 'instance_base64' not in payload:raise ValueError('Reviewed native leaf assignments are required')
                    leaf_instances.validate_labeling(labels,mask,complete=True)
                trace=self._instance_trace(stored,labels,payload,mask,id)
                rgb,data=self._rgb(stored)
                assessment=self._calibration_assessment(stored,rgb,data)
                context=reuse_context(rgb,data,stored['calibration'],stored['geometry_unchanged'],assessment)
                # Reserve the unique plant/date before writing any approved files.
                conn.execute(insert(measurements).values(id=measurement_id,analysis_id=id,profile_id=row['profile_id'],date=row['date'],payload={}))
                candidate=self._mask(stored['assets']['candidate_mask'])
                corrected=not np.array_equal(mask,candidate)
                correction_magnitude=dict(changed_pixels=int(np.count_nonzero(mask!=candidate)),
                    removed_candidate_pixels=int(np.count_nonzero(candidate & ~mask)),
                    added_target_pixels=int(np.count_nonzero(mask & ~candidate)),
                    changed_fraction_of_union=float(np.count_nonzero(mask!=candidate)/np.count_nonzero(mask|candidate)))
                with tempfile.TemporaryDirectory(dir=self.work) as temp:
                    source=Path(temp)/(stored['species']+Path(stored['original_filename']).suffix.lower());source.write_bytes(data)
                    folder=save_reviewed_sample(source,mask,review,context,Path(temp)/'approved',
                        species=stored['species'],review_keys=REVIEW_KEYS,review_method='explicit_web_APPROVE',
                        label_origin='Human-reviewed/edited candidate; explicit web APPROVE; never automatic ground truth',
                        imported_mask=dict(analysis_id=id,candidate_sha256=stored['assets']['candidate_mask']['sha256'],
                            source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                                ['src/webapp/service.py','src/webapp/calibration.py','src/webapp/calibration_reuse.py','src/webapp/static/mask-editor.js']}))
                    metadata=json.loads((folder/'measurement.json').read_text(encoding='utf-8'))
                    # Paths in persisted provenance refer to immutable media, never temporary work files.
                    metadata['source_path']=stored['assets']['original']['key']
                    target=stored.get('measurement_target','visible_plant_foreground')
                    leaf_metrics=leaf_instances.summary(labels,mask,metadata['cm_per_pixel'],reviewed=instances_reviewed,measurement_target=target,instance_metadata=trace['instance_metadata'],analysis_id=id)
                    if not instances_reviewed:
                        leaf_metrics.update(leaf_count=None,per_leaf_pixel_area=[],per_leaf_projected_area_cm2=[],leaves=[],
                            mean_leaf_area_cm2=None,mean_leaf_pixel_area=None,leaf_measurement_status='NOT_REVIEWED')
                        leaf_metrics['mean_leaf_area']=None
                        leaf_metrics.update(median_leaf_pixel_area=None,median_leaf_area_cm2=None,median_leaf_area=None)
                    metadata.update(**leaf_metrics,instance_provenance=trace,instances_reviewed=instances_reviewed)
                    plant_ref=stored['assets'].get('plant_foreground_mask')
                    if plant_ref:
                        plant_mask=self._mask(plant_ref)
                        metadata['approved_lamina_partition']=dict(original_foreground_asset=plant_ref,
                            original_foreground_pixel_area=int(plant_mask.sum()),approved_lamina_pixel_area=int(mask.sum()),
                            excluded_foreground_pixels=int(np.count_nonzero(plant_mask&~mask)),
                            added_lamina_pixels=int(np.count_nonzero(mask&~plant_mask)))
                    metadata.update(plant_id=stored['plant_id'],experiment_id=stored['experiment_id'],date=row['date'].isoformat(),
                        calibration_id=stored['calibration']['id'] if stored['calibration'] else None,
                        calibration_reuse=assessment,
                        correction_required=corrected,correction_magnitude=correction_magnitude,
                        candidate_model=stored.get('candidate_model'),segmentation_qc=stored['pipeline'],geometry_unchanged=stored['geometry_unchanged'],
                        source_ownership=stored.get('source_ownership','unknown'),
                        research_permission_provenance=stored.get('research_permission_provenance'))
                    (folder/'measurement.json').write_text(json.dumps(metadata,indent=2,allow_nan=False)+'\n',encoding='utf-8')
                    prefix='measurements/'+measurement_id
                    assets={'original':stored['assets']['original'],'candidate_mask':stored['assets']['candidate_mask'],
                        'candidate_overlay':stored['assets']['candidate_overlay']}
                    for name in ('plant_foreground_mask','plant_foreground_overlay','structural_exclusion_mask'):
                        if name in stored['assets']:assets[name]=stored['assets'][name]
                    if plant_ref:
                        assets['approved_foreground_exclusion_mask']=self.media.put(prefix+'/approved_foreground_exclusion_mask.png',
                            png_bytes(Image.fromarray((plant_mask&~mask).astype(np.uint8)*255),'HUMAN_REVIEWED_EXCLUSIONS'),'image/png')
                    assets.update(self._instance_assets(prefix,labels,rgb,mask,'HUMAN_REVIEWED' if instances_reviewed else 'NOT_REVIEWED','leaf_instances','leaf_instance_overlay',trace['instance_metadata']))
                    assets['leaf_table']=self.media.put(prefix+'/leaves.json',json.dumps(dict(**leaf_metrics,instance_provenance=trace),allow_nan=False).encode(),'application/json')
                    for name,filename in [('approved_mask','reviewed_mask.png'),('overlay','overlay.png'),
                        ('provenance','measurement.json'),('calibration_snapshot','reviewed_pot_calibration.json'),
                        ('validated_calibration','pot_calibration.json'),('csv','measurements.csv'),('qc','qc_status.txt')]:
                        kind='image/png' if filename.endswith('.png') else 'application/json' if filename.endswith('.json') else 'text/csv' if filename.endswith('.csv') else 'text/plain'
                        assets[name]=self.media.put(prefix+'/'+filename,(folder/filename).read_bytes(),kind)
                record=dict(id=measurement_id,analysis_id=id,profile_id=row['profile_id'],plant_id=stored['plant_id'],
                    experiment_id=stored['experiment_id'],species=stored['species'],date=row['date'].isoformat(),timestamp=stamp(),
                    measurement_status='MANUAL_GROUND_TRUTH',pixel_area=metadata['pixel_area'],
                    projected_area_cm2=metadata['projected_area_cm2'],cm_per_pixel=metadata['cm_per_pixel'],
                    calibration=stored['calibration'],calibration_id=metadata['calibration_id'],calibration_reuse=assessment,
                    species_validation=stored['species_validation'],correction_required=corrected,
                    correction_magnitude=correction_magnitude,candidate_model=stored.get('candidate_model'),
                    source_ownership=stored.get('source_ownership'),data_origin=stored.get('data_origin','real'),
                    research_permission_provenance=stored.get('research_permission_provenance'),
                    qc=dict(status='MANUAL_REVIEWED',segmentation_qc=stored['pipeline']),
                    annotation_provenance=metadata,assets=assets)
                # Explicit aliases: capture has date precision; approval has UTC time.
                # Existing date/timestamp, historical records and CSV remain intact.
                record.update(**leaf_metrics,instance_provenance=trace,instances_reviewed=instances_reviewed)
                record['lamina_refinement']=stored.get('lamina_refinement')
                record.update(captured_at=record['date'],approved_at=record['timestamp'])
                conn.execute(update(measurements).where(measurements.c.id==measurement_id).values(payload=record))
                if stored['species'] in ('lettuce','rumex_acetosa'):
                    from ..training.approved_registry import from_measurement
                    canonical=from_measurement(record,assets)
                    registry_table=self.db.approved_masks if stored['species']=='lettuce'else self.db.approved_sorrel_masks
                    conn.execute(insert(registry_table).values(id=measurement_id,measurement_id=measurement_id,
                        profile_id=row['profile_id'],payload=canonical))
                    from ..training.owner_notifications import record_approval
                    owner_event=record_approval(self.db,conn,canonical,self.models)
                conn.execute(update(table).where(table.c.id==id).values(status='APPROVED',measurement_id=measurement_id))
                self._review_session(conn,row['profile_id'],id,remove=True)
        except IntegrityError as exc:raise Conflict('A final measurement already exists for this plant/date') from exc
        if owner_event:
            from ..training.owner_notifications import emit_email_event
            emit_email_event(owner_event,self.settings.firestore_prefix)
        return self.measurement(measurement_id)

    def measurement(self,id):
        record=self.db.one(self.db.measurements,id)['payload']
        if record.get('leaves'):
            record=dict(record)
            identity=instance_identity.resolve(np.array([r['leaf_id'] for r in record['leaves']],np.uint16),record['analysis_id'],record.get('instance_metadata'))
            record['instance_metadata']=identity
            record['leaves']=instance_identity.decorate(record['leaves'],identity)
            record['per_leaf_display_numbers']=[r['display_leaf_number'] for r in record['leaves']]
            record['per_leaf_internal_instance_ids']=[r['internal_instance_id'] for r in record['leaves']]
            record['per_leaf_pixel_area']=[r['pixel_area'] for r in record['leaves']]
            record['per_leaf_projected_area_cm2']=[r['projected_area_cm2'] for r in record['leaves']]
        return {**record,'assets':{name:f'/api/assets/measurement/{id}/{name}' for name in record['assets']}}

    def history(self,profile_id):
        self.profile(profile_id)
        rows=sorted([self.measurement(r['id']) for r in self.db.rows(self.db.measurements,self.db.measurements.c.profile_id==profile_id)],key=lambda m:m.get('captured_at') or m['date'])
        return dict(measurements=rows,growth=growth(rows))

    def export(self,profile_id):
        rows=self.history(profile_id)['measurements'];stream=io.StringIO(newline='')
        fields=['experiment_id','plant_id','species','date','timestamp','measurement_status','pixel_area','cm_per_pixel',
            'projected_area_cm2','calibration_id','calibration_reuse','correction_required','species_validation','segmentation_qc',
            'original_image','candidate_mask','approved_mask','overlay','annotation_provenance',
            'candidate_model_version','candidate_model_architecture','candidate_checkpoint_sha256','correction_magnitude',
            'leaf_count','total_projected_area_cm2','mean_leaf_area_cm2','leaf_measurement_status',
            'per_leaf_pixel_area','per_leaf_projected_area_cm2','leaf_instances','instance_provenance',
            'measurement_target','total_leaf_area','mean_leaf_area','median_leaf_area','median_leaf_area_cm2','median_leaf_pixel_area','leaf_area_unit','plant_foreground_mask','captured_at','approved_at','instance_metadata','per_leaf_display_numbers','per_leaf_internal_instance_ids']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for measurement in rows:
            data={k:measurement.get(k) for k in fields}
            model=measurement.get('candidate_model') or {}
            data.update(candidate_model_version=model.get('version'),candidate_model_architecture=model.get('architecture'),
                candidate_checkpoint_sha256=model.get('checkpoint_sha256'),
                correction_magnitude=json.dumps(measurement.get('correction_magnitude'),sort_keys=True,allow_nan=False))
            for key in ('calibration_reuse','per_leaf_pixel_area','per_leaf_projected_area_cm2','instance_provenance','instance_metadata','per_leaf_display_numbers','per_leaf_internal_instance_ids'):
                data[key]=json.dumps(measurement.get(key),sort_keys=True,allow_nan=False)
            data['segmentation_qc']=measurement['qc']['segmentation_qc']['qc_status']
            raw=self.db.one(self.db.measurements,measurement['id'])['payload']
            for column,asset in [('original_image','original'),('candidate_mask','candidate_mask'),('approved_mask','approved_mask'),('overlay','overlay'),('annotation_provenance','provenance')]:
                data[column]=raw['assets'][asset]['key']
            data['leaf_instances']=(raw['assets'].get('leaf_instances') or {}).get('key')
            data['measurement_target']=measurement.get('measurement_target','visible_plant_foreground')
            data['plant_foreground_mask']=(raw['assets'].get('plant_foreground_mask') or {}).get('key')
            writer.writerow({k:("'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@','\t','\r') else v) for k,v in data.items()})
        return stream.getvalue()

    def asset(self,kind,id,name):
        tables={'analysis':self.db.analyses,'measurement':self.db.measurements,'calibration':self.db.calibrations}
        if kind not in tables:raise KeyError('Unknown asset type')
        payload=self.db.one(tables[kind],id)['payload']
        if name not in payload.get('assets',{}):raise KeyError('Asset not found')
        reference=payload['assets'][name]
        return self.media.read(reference),reference['content_type']
