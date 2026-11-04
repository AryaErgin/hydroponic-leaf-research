"""Regression only: reproduce frozen EXP-003 outputs, without tuning or new GT.

python -m src.hydroponic.verify_frozen --output <new-project-directory>
"""
import argparse
import csv
import hashlib
import json
import platform
import numpy as np
from PIL import Image, __version__ as pillow_version
from .config import ROOT, load_profiles
from .pipeline import output_directory, process_file, source_provenance


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def verify(output):
    out=output_directory(output);frozen=ROOT/'experiments/EXP-003'
    selected=read_csv(frozen/'selected_images.csv')
    diagnostics={r['filename']:r for r in read_csv(frozen/'roi_diagnostics.csv')}
    metrics={r['filename']:r for r in read_csv(frozen/'metrics.csv') if r['method']=='C'}
    s,a=load_profiles('lettuce');rows=[];checks=0
    for i,row in enumerate(selected,1):
        name=row['filename'];image=ROOT/'data/raw/IoT4Lettuce/04_OAK-1/images'/name
        digest=hashlib.sha256(image.read_bytes()).hexdigest()
        assert digest==row['image_sha256'],f'Source changed: {name}'
        result=process_file(image,s,a);m=result['metadata'];checks+=1
        for key,folder in [('mask','masks'),('roi','roi_masks')]:
            with Image.open(frozen/folder/'C'/name) as im:reference=np.asarray(im)>0
            assert np.array_equal(result[key],reference),f'{key} mismatch: {name}'
            checks+=1
        for k,v in m['diagnostics'].items():
            reference=metrics[name] if k in metrics[name] else diagnostics[name]
            saved=reference[k]
            if isinstance(v,bool):want=saved=='True'
            elif isinstance(v,(int,float)):want=float(saved)
            else:want=saved
            assert v==want,f'Diagnostic mismatch: {name} {k} {v} != {want}'
            checks+=1
        report=metrics[name]['reported_pixel_area']
        assert m['reported_pixel_area']==(int(report) if report else None),f'Reportability mismatch: {name}'
        assert m['projected_area_cm2'] is None
        checks+=2
        rows.append(dict(filename=name,split=row['split'],plant_id=row['tag'],image_sha256=digest,
            raw_pixel_area=m['raw_pixel_area'],reported_pixel_area=m['reported_pixel_area'],
            confidence=m['confidence'],qc_status=m['qc_status'],mask_equal=True,roi_equal=True,diagnostics_equal=True))
        if i%10==0:print(f'Regression {i}/{len(selected)}',flush=True)
    with (out/'parity.csv').open('x',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    summary=dict(status='PASS',images=len(rows),checks=checks,species='lettuce',
        scope='Native mask/ROI, exact diagnostics/QC and reportability parity; not new accuracy evidence',
        benchmark='100 exposed development images plus 27 tuning images; not unbiased confirmation',
        profiles={'species':s,'acquisition':a},source_sha256=source_provenance(),
        versions={'python':platform.python_version(),'numpy':np.__version__,'pillow':pillow_version})
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ('status','images','checks','scope')},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    verify(parser.parse_args().output)
