"""Aalto acquisition wrapper. Frozen EXP-003 C receives only native RGB crops.

Run --prepare, inspect screening pages, then --freeze, then --run. All writes
are create-only except the live progress file; predictions never tune the method.
"""
import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .robust_roi import robust_segment, quality_check, measurement

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/EXP-005'
ARCHIVE = ROOT / 'Lettuce_growth.zip'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def put_json(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def put_csv(path, rows, fields=None):
    rows = list(rows)
    if fields is None: fields = list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('x', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def grid():
    # Fixed BEFORE predictions: midpoint separators from native empty-bed layout.
    xs = [0, 250, 460, 678, 895, 1120, 1280]
    ys = [0, 295, 538, 720]
    return [dict(position=f'B{4-row}{col+2}', row=row, column=col,
                 box=[xs[col], ys[row], xs[col+1], ys[row+1]])
            for row in range(3) for col in range(6)]


def parse_biomass(path):
    """Native XLSX XML, retaining dates/DAT/cells; no guessed year correction."""
    rows = []
    with zipfile.ZipFile(path) as z:
        strings = [''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        for sheet_num, sheet in enumerate(['B2', 'B3', 'B4'], 1):
            root = ET.fromstring(z.read(f'xl/worksheets/sheet{sheet_num}.xml'))
            cells = {}
            for c in root.findall('.//s:c', NS):
                v = c.find('s:v', NS)
                if v is not None:
                    cells[c.attrib['r']] = strings[int(v.text)] if c.attrib.get('t') == 's' else float(v.text)
            for row in range(2, 33):
                stored = (datetime(1899, 12, 30) + timedelta(days=cells[f'A{row}'])).date().isoformat()
                for col in 'CDEFGH':
                    position = re.search(r'B\d+', cells[f'{col}1']).group()
                    dat_source=cells[f'B{row}']
                    dat=int(re.fullmatch(r'\s*(\d+) \(harvest\)',dat_source)[1]) if isinstance(dat_source,str) else int(dat_source)
                    rows.append(dict(source_sheet=sheet, source_cell=f'{col}{row}',
                                     date_as_stored=stored, dat=dat, dat_as_stored=dat_source,
                                     position=position, fresh_mass_g=cells.get(f'{col}{row}'),
                                     source_time_fraction=cells.get(f'J{row}'),
                                     source_time_suffix=cells.get(f'K{row}')))
    return rows


def valid_mass_join(image, position, presence, masses):
    if not image['biomass_flagged'] or presence != 'present': return None
    found = [r for r in masses if r['date_as_stored'] == image['date_as_stored'] and r['position'] == position]
    if len(found) > 1: raise ValueError('Ambiguous date/position biomass rows')
    if not found or found[0]['fresh_mass_g'] is None: return None
    if 'dat' in image and image['dat'] != found[0]['dat']: raise ValueError('DAT conflict')
    return found[0]


def position_predict(rgb, cfg):
    p, roi, _, d = robust_segment(rgb, cfg['A'], cfg['C'])
    qc = quality_check(p, p, d, cfg['qc_threshold'])
    return p, roi, d, qc, measurement(p, qc)


def font(size):
    try: return ImageFont.truetype('C:/Windows/Fonts/arial.ttf', size)
    except OSError: return ImageFont.load_default(size=size)


def prepare():
    inventory = []
    with zipfile.ZipFile(ARCHIVE) as z:
        for member in z.namelist():
            m = re.search(r'/(\d+)_(2022-\d\d-\d\d)(_biomass_measured)?\.png$', member)
            if not m: continue
            b = z.read(member)
            row = dict(serial=int(m[1]), member=member, filename=Path(member).name,
                       date_as_stored=m[2], dat=(date.fromisoformat(m[2])-date(2022,2,21)).days+1,
                       biomass_flagged=bool(m[3]), sha256=hashlib.sha256(b).hexdigest())
            try:
                with Image.open(io.BytesIO(b)) as im:
                    im.load(); row.update(width=im.width, height=im.height, mode=im.mode)
                row.update(usable=row['mode']=='RGB' and (row['width'],row['height'])==(1280,720),
                           exclusion_reason='' if row['mode']=='RGB' and (row['width'],row['height'])==(1280,720) else 'unexpected_native_canvas_or_mode')
            except Exception as e:
                row.update(usable=False, exclusion_reason=f'decode_failure:{type(e).__name__}')
            inventory.append(row)
        inventory.sort(key=lambda r:r['serial'])
        if len(inventory) != 727 or [r['serial'] for r in inventory] != list(range(1,728)):
            raise ValueError('Unexpected source image inventory')
        put_csv(OUT/'image_inventory.csv', inventory)
        put_json(OUT/'inventory.json', inventory)
        put_json(OUT/'grid.json', grid())
        masses = parse_biomass(ROOT/'data/raw/AaltoLettuce/4_Biomass.xlsx')
        put_csv(OUT/'biomass_source.csv', masses)
        put_json(OUT/'biomass_source.json', masses)
        flagged = [r for r in inventory if r['biomass_flagged']]
        if len(flagged)!=30 or len({r['date_as_stored'] for r in flagged})!=30:
            raise ValueError('Flagged date ambiguity')
        (OUT/'screening').mkdir()
        for start in range(0,len(flagged),4):
            page = Image.new('RGB',(1280,790),'white'); draw=ImageDraw.Draw(page)
            for k,r in enumerate(flagged[start:start+4]):
                im=Image.open(io.BytesIO(z.read(r['member']))); im.load()
                d=ImageDraw.Draw(im)
                for cell in grid():
                    x0,y0,x1,y1=cell['box'];d.rectangle((x0,y0,x1-1,y1-1),outline='red',width=2)
                    d.text((x0+4,y0+3),cell['position'],font=font(25),fill='red',stroke_width=1,stroke_fill='white')
                x,y=(k%2)*640,(k//2)*395
                page.paste(im.resize((640,360)),(x,y+30))
                draw.text((x+4,y+3),f"DAT {r['dat']} | {r['filename']}",font=font(18),fill='black')
            page.save(OUT/f'screening/page_{start//4+1:02}.png')


def protected_paths():
    folders=['experiments/EXP-001','experiments/EXP-002','experiments/EXP-003',
             'experiments/EXP-R01','experiments/EXP-004','artifacts/hydroponic_pipeline',
             'data/raw/AaltoLettuce','docs/dataset_inspection/new_public_datasets_audit']
    paths=[p for folder in folders for p in (ROOT/folder).rglob('*') if p.is_file()]
    paths.extend(p for folder in ['src','tests','configs','docs/pipeline'] for p in (ROOT/folder).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and 'exp005' not in p.name)
    paths.extend([ROOT/'docs/dataset_inspection/Aalto_lettuce_report.md'])
    return sorted(set(paths))


def freeze():
    inventory=json.loads((OUT/'inventory.json').read_text())
    masses=json.loads((OUT/'biomass_source.json').read_text())
    screens=list(csv.DictReader((OUT/'association_screen.csv').open(encoding='utf-8',newline='')))
    keys={(r['serial'],r['position']) for r in screens}
    expected={(str(r['serial']),c['position']) for r in inventory if r['biomass_flagged'] for c in grid()}
    if keys!=expected or len(screens)!=540 or any(r['presence'] not in ['present','absent','ambiguous'] for r in screens):
        raise ValueError('Incomplete/duplicate/undecided pre-prediction screen')
    for r in screens:
        image=next(i for i in inventory if str(i['serial'])==r['serial'])
        valid_mass_join(image,r['position'],r['presence'],masses)
    old=list(csv.DictReader((ROOT/'docs/dataset_inspection/new_public_datasets_audit/Aalto_biomass_long_inspection.csv').open(encoding='utf-8',newline='')))
    ours={(r['date_as_stored'],r['position']):r['fresh_mass_g'] for r in masses if r['fresh_mass_g'] is not None}
    theirs={(r['date_as_stored'],r['plant_id']):float(r['fresh_biomass_g']) for r in old}
    if ours.keys()!=theirs.keys() or any(abs(ours[k]-theirs[k])>1e-9 for k in ours):
        raise ValueError('Native XLSX and earlier independent inventory disagree')
    snapshot=OUT/'code_snapshot';snapshot.mkdir()
    for name in ['robust_roi.py','lettuce_cv.py','roi_cv.py','exp003.py']:
        p=ROOT/'src'/name; original=ROOT/'experiments/EXP-003/code_snapshot'/name
        if p.read_bytes()!=original.read_bytes(): raise ValueError(f'Model changed: {name}')
        (snapshot/name).write_bytes(p.read_bytes())
    (snapshot/'__init__.py').write_text('',encoding='utf-8')
    with (OUT/'config.json').open('xb') as f:f.write((ROOT/'experiments/EXP-003/config.json').read_bytes())
    with (OUT/'EXP-004_LATEST_REPORT.md').open('xb') as f:f.write((ROOT/'docs/reports/LATEST_REPORT.md').read_bytes())
    immutable={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in protected_paths()}
    immutable['Lettuce_growth.zip']=sha(ARCHIVE)
    put_json(OUT/'protected_before.json',immutable)
    lock_files=['methodology.md','grid.json','association_screen.csv','inventory.json','biomass_source.json','config.json']
    lock={n:sha(OUT/n) for n in lock_files}
    lock.update({f'code_snapshot/{p.name}':sha(p) for p in snapshot.iterdir()})
    # Evaluation helpers can be extended later; inference implementation is frozen now.
    lock['../../src/exp005.py']=sha(Path(__file__))
    put_json(OUT/'freeze.json',dict(utc=datetime.now(timezone.utc).isoformat(),files=lock,
        archive_sha256=immutable['Lettuce_growth.zip'],positions=18,frames=727,
        model='byte-identical EXP-003 Method C / original self-reference QC',
        acquisition='fixed native layout cells, no resizing or color adjustment',
        date_conflict='UNRESOLVED 2022 image/biomass versus 2024 README/environment',
        numpy=np.__version__,pillow=Image.__version__,biomass_numeric_rows=540))


def run():
    lock=json.loads((OUT/'freeze.json').read_text())
    if any(sha(OUT/n)!=h for n,h in lock['files'].items()):raise ValueError('Freeze hash mismatch')
    if sha(ARCHIVE)!=lock['archive_sha256']:raise ValueError('Source archive changed')
    cfg=json.loads((OUT/'config.json').read_text())
    inventory=json.loads((OUT/'inventory.json').read_text())
    screens={(int(r['serial']),r['position']):r for r in csv.DictReader((OUT/'association_screen.csv').open(encoding='utf-8',newline=''))}
    # Strict create-only: partial runs remain reviewable and cannot be silently resumed.
    masks=OUT/'masks';rois=OUT/'rois';masks.mkdir();rois.mkdir()
    rows=[]
    with zipfile.ZipFile(ARCHIVE) as z:
        for i,image in enumerate(inventory):
            if not image['usable']:continue
            rgb=np.asarray(Image.open(io.BytesIO(z.read(image['member']))))
            mosaic=np.zeros((720,1280),bool);roi_mosaic=mosaic.copy()
            for cell in grid():
                x0,y0,x1,y1=cell['box'];crop=rgb[y0:y1,x0:x1]
                p,roi,d,q,a=position_predict(crop,cfg)
                mosaic[y0:y1,x0:x1]=p;roi_mosaic[y0:y1,x0:x1]=roi
                edge=np.zeros_like(p);edge[:3]=True;edge[-3:]=True;edge[:,:3]=True;edge[:,-3:]=True
                boundary=int((p&edge).sum());presence=screens.get((image['serial'],cell['position']),{}).get('presence','unknown_nonflagged')
                if image['serial'] in [1,727]:presence='empty_bed_control'
                rows.append(dict(serial=image['serial'],filename=image['filename'],date_as_stored=image['date_as_stored'],
                    dat=image['dat'],biomass_flagged=image['biomass_flagged'],position=cell['position'],
                    x0=x0,y0=y0,x1=x1,y1=y1,crop_pixel_area=p.size,presence=presence,
                    crop_boundary_foreground_pixels=boundary,crop_boundary_foreground_fraction=boundary/max(1,int(p.sum())),
                    prediction_touches_crop_edge=bool(boundary)) | d | q | a)
            Image.fromarray(mosaic.astype(np.uint8)*255).save(masks/image['filename'])
            Image.fromarray(roi_mosaic.astype(np.uint8)*255).save(rois/image['filename'])
            if (i+1)%24==0 or i+1==len(inventory):
                progress=dict(frames=i+1,total=727,position_predictions=len(rows),last=image['filename'])
                (OUT/'progress.json').write_text(json.dumps(progress),encoding='utf-8');print(json.dumps(progress),flush=True)
    put_csv(OUT/'predictions.csv',rows)
    put_json(OUT/'predictions.json',rows)
    put_json(OUT/'inference_complete.json',dict(frames=len(list(masks.glob('*.png'))),position_predictions=len(rows),
        finished_utc=datetime.now(timezone.utc).isoformat(),prediction_sha256=sha(OUT/'predictions.csv')))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','freeze','run']);a=p.parse_args()
    {'prepare':prepare,'freeze':freeze,'run':run}[a.action]()
