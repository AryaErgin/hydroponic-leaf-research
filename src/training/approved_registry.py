"""Canonical approved-mask lineage and a read-only native-mask export."""
import copy
import io
import json
from pathlib import Path
import re
from urllib.parse import quote

import numpy as np
from PIL import Image

from .model_registry import bounded, sha256, write_json

REVIEW_KEYS = ('visible_target_only','visible_tissue_complete','moss_excluded','growing_medium_excluded',
              'dead_tissue_excluded','pot_and_background_excluded','scale_approximation_accepted')
DIAGNOSTIC_IMAGE_SHA256 = '9326ecb572827af62272a140e35fa5b052f410ef2c5a83bd0dc1193d686a323f'


def from_measurement(record, raw_asset_references):
    """Called inside the existing explicit approval transaction; makes no approval."""
    if record.get('measurement_status') != 'MANUAL_GROUND_TRUTH' or record.get('species') not in ('lettuce','rumex_acetosa'):
        raise ValueError('Only human-approved supported species measurements enter this registry')
    provenance = record.get('annotation_provenance', {})
    if (provenance.get('review_method') not in ('explicit_web_APPROVE','explicit_browser_APPROVE') or
        any(provenance.get('review',{}).get(k) is not True for k in REVIEW_KEYS)):
        raise ValueError('Explicit complete human review provenance required')
    assets = copy.deepcopy(raw_asset_references)
    for name in ('original','candidate_mask','approved_mask'):
        ref = assets.get(name, {})
        if not isinstance(ref, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(ref.get('sha256',''))) or not (ref.get('key') or ref.get('path')):
            raise ValueError('Original/candidate/approved raw hashed media references required')
    if provenance.get('image_sha256') != assets['original']['sha256'] or provenance.get('mask_sha256') != assets['approved_mask']['sha256']:
        raise ValueError('Approval provenance does not match raw image/mask hashes')
    experiment, plant = record.get('experiment_id'), record.get('plant_id')
    if not isinstance(experiment,str) or not experiment.strip() or not isinstance(plant,str) or not plant.strip():
        raise ValueError('Stable experiment and plant identity required')
    origin = record.get('data_origin', 'real')
    synthetic = origin in ('synthetic','test','demo') or record.get('synthetic') is True or record.get('test_only')is True or record.get('demo')is True
    diagnostic = assets['original']['sha256'] == DIAGNOSTIC_IMAGE_SHA256 or record.get('diagnostic') is True
    exposed = record.get('historically_exposed') is True or diagnostic or provenance.get('review_method') == 'explicit_browser_APPROVE'
    ownership=record.get('source_ownership',provenance.get('source_ownership'))
    owned_permission=ownership=='project_owner' and origin=='real' and not synthetic
    license_name='OWN_HUMAN_APPROVED_RESEARCH' if owned_permission else record.get('license')
    # The generated own-capture label is not an external license grant. Cached
    # export labels cannot substitute for the explicit ownership declaration.
    permission=owned_permission or (bool(license_name) and license_name!='OWN_HUMAN_APPROVED_RESEARCH'
        and record.get('license_permits_research') is True)
    pipeline = record.get('segmentation_qc',record.get('qc',{}).get('segmentation_qc',{})) or {}
    candidate_model = record.get('candidate_model', {}) or {}
    version = candidate_model.get('version') or record.get('model_version') or pipeline.get('model_version') or 'frozen_classical_exp003'
    result = dict(schema_version=1, dataset='real_approved', id=record['id'], analysis_id=record.get('analysis_id'),
        profile_id=record.get('profile_id'), experiment_id=experiment, plant_id=plant,
        group_id=quote(experiment,safe='')+'/'+quote(plant,safe=''), species=record['species'], date=record['date'],
        approved_at=record.get('timestamp',provenance.get('reviewed_at_utc')), measurement_status='MANUAL_GROUND_TRUTH',
        review_status='HUMAN_APPROVED', annotation_provenance=copy.deepcopy(provenance), assets=assets,
        model_version=version, candidate_model=copy.deepcopy(candidate_model),
        model_architecture=candidate_model.get('architecture') or record.get('model_architecture',pipeline.get('architecture','classical_cv')),
        calibration=copy.deepcopy(record.get('calibration')), calibration_id=record.get('calibration_id'),
        cm_per_pixel=record.get('cm_per_pixel'), projected_area_cm2=record.get('projected_area_cm2'), pixel_area=record['pixel_area'],
        correction_required=record.get('correction_required'), correction_magnitude=copy.deepcopy(record.get('correction_magnitude')),
        data_origin=origin, synthetic=synthetic, diagnostic=diagnostic, historically_exposed=exposed,
        source_ownership=ownership,license=license_name,license_permits_research=permission,
        research_permission_provenance=copy.deepcopy(record.get('research_permission_provenance',
            {'basis':'Explicit project-owner research permission for real capture'} if owned_permission else None)),
        new_real_eligible=permission and origin == 'real' and not synthetic and not exposed and record.get('new_real_eligible',True) is True)
    result['measurement_target']=record.get('measurement_target','visible_plant_foreground')
    # The existing promoted models/training cohort target whole foreground.
    # Lamina annotations are retained with lineage, but must not silently train
    # that different target. A dedicated compatible cohort requires a future task.
    if result['measurement_target']=='visible_leaf_lamina':
        result['new_real_eligible']=False
        result['training_ineligibility_reason']='LAMINA_TARGET_DIFFERS_FROM_WHOLE_FOREGROUND_COHORT'
    json.dumps(result, allow_nan=False)
    return result


def native_binary(data, size):
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.size != tuple(size) or image.mode not in ('1','L'):
            raise ValueError('Approved mask must be native single-channel binary')
        array = np.asarray(image)
    if not np.isin(array, [0,1,255]).all() or (1 in array and 255 in array):
        raise ValueError('Mask is not strictly binary 0/1 or 0/255')
    return array != 0


def export_web(service, destination, species='lettuce'):
    """Read only committed approved lettuce records and export verified originals."""
    destination = bounded(destination)
    if destination.exists():
        raise FileExistsError('Approved export is create-only: '+str(destination))
    records = []
    if species not in ('lettuce','rumex_acetosa'):raise ValueError('Unsupported species')
    registry_table=getattr(service.db,'approved_masks'if species=='lettuce'else'approved_sorrel_masks',None)
    registered = service.db.rows(registry_table)if registry_table is not None else []
    source_rows = registered if registered else service.db.rows(service.db.measurements)
    for row in source_rows:
        record = row['payload']
        if record.get('species') != species or record.get('measurement_status') != 'MANUAL_GROUND_TRUTH':
            continue
        canonical = from_measurement(record, record['assets'])
        blobs = {}
        for name, ref in canonical['assets'].items():
            blob = service.media.read(ref)
            if sha256(blob) != ref['sha256']:
                raise ValueError('Export media SHA256 mismatch: '+name)
            blobs[name] = blob
        with Image.open(io.BytesIO(blobs['original'])) as original:
            original.load();size = original.size
        if list(size) != canonical['annotation_provenance'].get('image_size_px'):
            raise ValueError('Approval has different original native dimensions')
        approved = native_binary(blobs['approved_mask'],size)
        candidate = native_binary(blobs['candidate_mask'],size)
        if not approved.any() or int(approved.sum()) != canonical['pixel_area']:
            raise ValueError('Approved mask pixel count is inconsistent')
        canonical['correction_magnitude'] = dict(changed_pixels=int(np.count_nonzero(approved != candidate)),
            changed_fraction=float(np.mean(approved != candidate)),
            foreground_delta_pixels=int(approved.sum())-int(candidate.sum()),
            removed_candidate_pixels=int(np.count_nonzero(candidate & ~approved)),
            added_target_pixels=int(np.count_nonzero(approved & ~candidate)),
            changed_fraction_of_union=float(np.count_nonzero(approved != candidate)/np.count_nonzero(approved | candidate)))
        records.append((canonical,blobs))
    destination.mkdir(parents=True, exist_ok=False)
    exported = []
    for record, blobs in records:
        # Stable hashed folder names avoid accepting record IDs as filesystem paths.
        folder = bounded(destination/'samples'/sha256(str(record['id']).encode()))
        folder.mkdir(parents=True, exist_ok=False)
        local_refs = {}
        for name, blob in blobs.items():
            suffix = '.png' if name in ('candidate_mask','approved_mask') else '.bin'
            path = bounded(folder/(name+suffix))
            with path.open('xb') as stream:stream.write(blob)
            local_refs[name] = dict(path=str(path), sha256=sha256(blob))
        record.update(image_path=local_refs['original']['path'], mask_path=local_refs['approved_mask']['path'],
            image_sha256=local_refs['original']['sha256'],mask_sha256=local_refs['approved_mask']['sha256'],
            exported_assets=local_refs, source_kind='human_approved')
        exported.append(record)
    with (destination/'approved_registry.jsonl').open('x',encoding='utf8') as stream:
        stream.writelines(json.dumps(r,allow_nan=False)+'\n' for r in exported)
    result = dict(count=len(exported), registry_path=str(destination/'approved_registry.jsonl'),
                  new_real_mask_count=sum(r['new_real_eligible'] for r in exported), no_new_approval=True)
    write_json(destination/'export.json',result)
    return result
