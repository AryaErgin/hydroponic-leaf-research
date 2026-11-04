"""Plant-keyed daily captures. No averaging, gap interpolation or QC substitution."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import io
import numpy as np
from PIL import __version__ as pillow_version
from .config import load_profiles
from .pipeline import fingerprint, output_directory, process_file, save_result, source_provenance

REQUIRED={'image_path','species','plant_id','captured_at'}
OPTIONAL={'calibration_path','acquisition_path','species_profile_path','color_config_path'}


def preflight(manifest,acquisition_path=None):
    path=Path(manifest).resolve();prepared=[];seen=set()
    data=path.read_bytes()
    with io.StringIO(data.decode('utf-8-sig'),newline='') as f:
        reader=csv.DictReader(f)
        fields=reader.fieldnames or []
        if len(set(fields))!=len(fields) or not REQUIRED<=set(fields) or set(fields)-REQUIRED-OPTIONAL:
            raise ValueError('Manifest requires image_path,species,plant_id,captured_at; unknown or duplicate columns')
        for line,row in enumerate(reader,2):
            if None in row or any(not isinstance(v,str) or not v.strip() for k,v in row.items() if k in REQUIRED):
                raise ValueError(f'Missing manifest metadata on line {line}')
            row={k:(v or '').strip() for k,v in row.items()}
            try:time=datetime.fromisoformat(row['captured_at'].replace('Z','+00:00'))
            except ValueError as exc:raise ValueError(f'Invalid timestamp on line {line}') from exc
            if time.tzinfo is None or time.utcoffset() is None:raise ValueError('Timezone-aware captured_at required')
            time=time.astimezone(timezone.utc);key=(row['species'],row['plant_id'],time)
            if key in seen:raise ValueError('Duplicate species/plant capture timestamp')
            seen.add(key)
            def resolve(k):return (path.parent/row[k]).resolve() if row.get(k) else None
            s,a=load_profiles(row['species'],resolve('acquisition_path') or acquisition_path,
                resolve('species_profile_path'),resolve('color_config_path'))
            prepared.append(dict(row=row,time=time,species=s,acquisition=a,
                image_path=resolve('image_path'),calibration_path=resolve('calibration_path')))
    if not prepared:raise ValueError('Empty capture manifest')
    return sorted(prepared,key=lambda x:(x['row']['species'],x['row']['plant_id'],x['time'])),path,data


def daily_observations(captures):
    chosen={}
    for c in captures:
        key=(c['species'],c['plant_id'],c['utc_date'])
        if key not in chosen or c['captured_at']>chosen[key]['captured_at']:chosen[key]=c
    previous={};daily=[]
    for key,c in sorted(chosen.items()):
        plant=key[:2];last=previous.get(plant);record=dict(c)
        record.update(pixel_area_change=None,percentage_pixel_area_change=None,cm2_area_change=None,
            change_reason='first_observation',cm2_change_reason='pixel_change_unavailable')
        if last:
            gap=(datetime.fromisoformat(c['utc_date'])-datetime.fromisoformat(last['utc_date'])).days
            if gap!=1:reason='nonconsecutive_day'
            elif c['reported_pixel_area'] is None or last['reported_pixel_area'] is None:reason='qc_or_input_unavailable'
            elif (c['config_sha256'],c['image_size_px'])!=(last['config_sha256'],last['image_size_px']):reason='configuration_or_canvas_changed'
            elif not c['stable_geometry_validated'] or not last['stable_geometry_validated']:reason='stable_geometry_unvalidated'
            elif c['geometry_validation_id']!=last['geometry_validation_id']:reason='geometry_evidence_changed'
            else:reason='comparable'
            record['change_reason']=reason
            if reason=='comparable':
                delta=c['reported_pixel_area']-last['reported_pixel_area'];record['pixel_area_change']=delta
                record['percentage_pixel_area_change']=100*delta/last['reported_pixel_area'] if last['reported_pixel_area'] else None
                if c['projected_area_cm2'] is None or last['projected_area_cm2'] is None:cm_reason='scale_or_physical_area_unavailable'
                elif (c['calibration_plane_id'],c['calibration_protocol_id'])!=(last['calibration_plane_id'],last['calibration_protocol_id']):cm_reason='calibration_plane_or_protocol_changed'
                else:
                    cm_reason='comparable';record['cm2_area_change']=c['projected_area_cm2']-last['projected_area_cm2']
                record['cm2_change_reason']=cm_reason
        daily.append(record);previous[plant]=c
    return daily


def write_csv(path,rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,sort_keys=True,allow_nan=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})


def run_batch(manifest,output,acquisition_path=None):
    prepared,path,manifest_bytes=preflight(manifest,acquisition_path)
    out=output_directory(output);captures=[]
    for i,item in enumerate(prepared,1):
        s,a=item['species'],item['acquisition'];destination=out/'captures'/f'{i:05d}'
        try:
            result=process_file(item['image_path'],s,a,item['calibration_path'])
        except (OSError,ValueError) as exc:
            meta=dict(species=s['species'],source_path=str(item['image_path']),qc_status='INPUT_ERROR',
                qc_reasons=[str(exc)],confidence=0.,image_sha256=None,image_size_px=None,
                raw_pixel_area=None,reported_pixel_area=None,projected_area_cm2=None,
                scale_status='unavailable',calibration=None,acquisition_id=a['acquisition_id'],
                config_sha256=fingerprint({'species':s,'acquisition':a}),effective_profiles={'species':s,'acquisition':a})
            destination.mkdir(parents=True)
            (destination/'measurement.json').write_text(json.dumps(meta,indent=2,allow_nan=False),encoding='utf-8')
        else:
            meta=result['metadata'];save_result(result,destination)
        evidence=meta['calibration']['validation'] if meta['scale_status']=='valid' else {}
        record={k:meta.get(k) for k in ('species','source_path','image_sha256','image_size_px','acquisition_id','config_sha256',
            'qc_status','qc_reasons','confidence','raw_pixel_area','reported_pixel_area','projected_area_cm2','scale_status')}
        record.update(plant_id=item['row']['plant_id'],captured_at=item['time'].isoformat(),
            utc_date=item['time'].date().isoformat(),capture_directory=str(destination.relative_to(out)),
            **a['longitudinal'],calibration_plane_id=evidence.get('plane_id'),calibration_protocol_id=evidence.get('protocol_id'))
        captures.append(record)
    daily=daily_observations(captures)
    write_csv(out/'captures.csv',captures);write_csv(out/'daily.csv',daily)
    (out/'input_manifest.csv').write_bytes(manifest_bytes)
    provenance=dict(schema_version=1,source_manifest=str(path),manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        captures=len(captures),daily_observations=len(daily),day_basis='UTC',
        daily_selection='latest capture, including QC/input failures',
        source_sha256=source_provenance(),versions={'python':platform.python_version(),'numpy':np.__version__,'pillow':pillow_version})
    (out/'run.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    return provenance
