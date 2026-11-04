"""EXP-R01 reproducible, create-only archive-to-classical-mask experiment.

Run from project root: python -m src.expr01 prepare|freeze|tune|evaluate|cases
Manual screen_decisions.json must be recorded between prepare and freeze.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import io
import itertools
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
import zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from src.inspect_rumex_dataset import digest
from src.lettuce_cv import color_features, segment_features, pixel_metrics
from src.rumex_cv import cvat_mask, metadata_group, assert_groups, stable_hash, tuning_winner, summarize

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'experiments/EXP-R01'
ARCHIVE=ROOT/'RumexLeaves.zip'


def guard_new(paths):
    for path in paths:
        if path.exists() and (path.is_file() or any(path.iterdir())):
            raise FileExistsError(f'Existing stage artifact: {path}; choose a separate experiment run')


def verify_selection_lock(folder=OUT):
    lock=json.loads((folder/'selection_lock.json').read_text())
    for key,name in [('selected','selected_images.csv'),('excluded','excluded_images.csv'),
                     ('decisions','screen_decisions.json'),('protocol','protocol.md')]:
        if digest(folder/name)!=lock[key+'_sha256']: raise ValueError('Frozen selection/protocol drift')


def save_json(path,value):
    with path.open('x',encoding='utf-8') as f: json.dump(value,f,indent=2,ensure_ascii=False)


def read_csv(path):
    with path.open(encoding='utf-8',newline='') as f: return list(csv.DictReader(f))


def save_csv(path,rows):
    if not rows: raise ValueError('Empty output table')
    with path.open('x',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def candidate_entries(rows):
    authors=defaultdict(list)
    result={r['archive_entry'] for r in rows if r['subset']=='RoboRumex'}
    for r in rows:
        if r['subset']=='iNaturalist': authors[r['creator']].append(r['archive_entry'])
    for creator in sorted(authors,key=lambda c:(stable_hash(c),c))[:60]:
        result.add(min(authors[creator],key=lambda n:(stable_hash(n),n)))
    return result


def validate_screen(queue,decisions):
    if set(decisions)!={str(r['review_id']) for r in queue}: raise ValueError('Screen must cover exactly all candidates')
    for d in decisions.values():
        if type(d.get('include')) is not bool or not d.get('reason','').strip(): raise ValueError('Decision/reason missing')


def annotations(z):
    result={}
    for name in sorted(z.namelist()):
        if not name.endswith('/annotations.xml'): continue
        for im in ET.fromstring(z.read(name)).findall('image'):
            key=im.attrib['name']
            if key in result: raise ValueError('Nonunique annotation name')
            result[key]=im
    return result


def rgb_and_gt(z,ann,row):
    raw=z.read(row['archive_entry'])
    if hashlib.sha256(raw).hexdigest()!=row['image_sha256']: raise ValueError('RGB hash mismatch')
    rgb=np.array(Image.open(io.BytesIO(raw)).convert('RGB'))
    gt=cvat_mask(ann[PurePosixPath(row['archive_entry']).name])
    if gt.shape!=rgb.shape[:2]: raise ValueError('Canvas mismatch')
    return rgb,gt


def panel(rgb,gt,pred=None,size=(800,410),label=''):
    image=Image.fromarray(rgb)
    annotated=rgb.astype(float).copy()
    annotated[gt]=.65*annotated[gt]+.35*np.array([255,0,255])
    panes=[image,Image.fromarray(annotated.astype(np.uint8))]
    if pred is not None:
        overlay=rgb.astype(float).copy()
        overlay[gt & pred]=.5*overlay[gt & pred]+.5*np.array([0,210,0])
        overlay[pred & ~gt]=.4*overlay[pred & ~gt]+.6*np.array([255,40,0])
        overlay[gt & ~pred]=.4*overlay[gt & ~pred]+.6*np.array([0,80,255])
        panes.append(Image.fromarray(overlay.astype(np.uint8)))
    canvas=Image.new('RGB',size,'white'); draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
    draw.text((8,4),label,fill='black',font=font)
    pw=size[0]//len(panes)
    for i,pane in enumerate(panes):
        pane.thumbnail((pw-8,size[1]-45))
        canvas.paste(pane,(i*pw+(pw-pane.width)//2,38+(size[1]-45-pane.height)//2))
    return canvas


def prepare():
    guard_new([OUT/'protected_before.json',OUT/'screening',OUT/'inventory.csv',OUT/'review_queue.csv'])
    before={str(p.relative_to(ROOT)).replace('\\','/'):digest(p)
        for folder in ('EXP-001','EXP-002','EXP-003') for p in (ROOT/'experiments'/folder).rglob('*') if p.is_file()}
    save_json(OUT/'protected_before.json',dict(raw_sha256=digest(ARCHIVE),raw_size=ARCHIVE.stat().st_size,
        prior_artifacts=before,prior_latest_sha256=digest(ROOT/'docs/reports/LATEST_REPORT.md')))
    inventory=read_csv(ROOT/'docs/dataset_inspection/rumex_inventory/inventory.csv')
    with zipfile.ZipFile(ARCHIVE) as z:
        ref=list(csv.DictReader(io.StringIO(z.read('RumexLeaves/iNaturalist/references.txt').decode()),delimiter=';'))
        credits={r['File Name'].strip():r[' Name of Creator'].strip() for r in ref}
        for r in inventory:
            r['creator']=credits[PurePosixPath(r['archive_entry']).stem] if r['subset']=='iNaturalist' else ''
            r['group'],r['split']=metadata_group(r['archive_entry'],r['creator'])
            parts=PurePosixPath(r['archive_entry']).parts
            r['sequence']='/'.join(parts[2:4]) if r['subset']=='RoboRumex' else 'unavailable'
        candidates=candidate_entries(inventory)
        queue=[]
        for r in sorted(inventory,key=lambda r:r['archive_entry']):
            if r['archive_entry'] in candidates: queue.append(dict(review_id=len(queue),**r))
        save_csv(OUT/'inventory.csv',inventory); save_csv(OUT/'review_queue.csv',queue)
        ann=annotations(z); folder=OUT/'screening'; folder.mkdir()
        for start in range(0,len(queue),8):
            sheet=Image.new('RGB',(1600,1640),'white')
            for j,row in enumerate(queue[start:start+8]):
                rgb,gt=rgb_and_gt(z,ann,row)
                tile=panel(rgb,gt,label=f"{row['review_id']}  {PurePosixPath(row['archive_entry']).name}")
                sheet.paste(tile,((j%2)*800,(j//2)*410))
            sheet.save(folder/f'page_{start//8:02d}.jpg',quality=94)
    print(json.dumps(dict(inventory=len(inventory),review_candidates=len(queue),pages=(len(queue)+7)//8)))


def freeze():
    guard_new([OUT/'selected_images.csv',OUT/'excluded_images.csv',OUT/'gt_masks',OUT/'predictions',OUT/'selection_lock.json'])
    queue=read_csv(OUT/'review_queue.csv'); inventory=read_csv(OUT/'inventory.csv')
    decisions=json.loads((OUT/'screen_decisions.json').read_text(encoding='utf-8')); validate_screen(queue,decisions)
    by_entry={r['archive_entry']:r for r in queue}; selected=[]; excluded=[]
    for row in inventory:
        candidate=by_entry.get(row['archive_entry'])
        decision=decisions[str(candidate['review_id'])] if candidate else dict(include=False,reason='not_screened_budget')
        r=dict(row,review_id=candidate['review_id'] if candidate else '',screen_reason=decision['reason'])
        (selected if decision['include'] else excluded).append(r)
    assert_groups(selected)
    if not any(r['split']=='tuning' for r in selected) or not any(r['split']=='evaluation' for r in selected):
        raise ValueError('No usable tuning/evaluation split')
    save_csv(OUT/'selected_images.csv',selected); save_csv(OUT/'excluded_images.csv',excluded)
    for folder in ('gt_masks','predictions','success_cases','failure_cases','label_quality_cases'): (OUT/folder).mkdir()
    with zipfile.ZipFile(ARCHIVE) as z:
        ann=annotations(z)
        for row in selected:
            _,gt=rgb_and_gt(z,ann,row); Image.fromarray(gt.astype(np.uint8)*255).save(OUT/'gt_masks'/f"{row['review_id']}.png")
    save_json(OUT/'selection_lock.json',dict(selected_sha256=digest(OUT/'selected_images.csv'),
        excluded_sha256=digest(OUT/'excluded_images.csv'),decisions_sha256=digest(OUT/'screen_decisions.json'),
        protocol_sha256=digest(OUT/'protocol.md'),prediction_files_present=False))
    print(json.dumps(dict(selected=len(selected),tuning=sum(r['split']=='tuning' for r in selected),
        evaluation=sum(r['split']=='evaluation' for r in selected))))


def grid():
    keys=['h_min','h_max','s_min','v_min','exg_min','morphology_radius']
    return [dict(zip(keys,v)) for v in itertools.product([15,25],[80,95],[20,50],[10,40],[0,20,40],[0,1])]


def tune():
    # No evaluation images loaded or scored in this phase.
    verify_selection_lock(); guard_new([OUT/'config_grid.json',OUT/'tuning_trials.csv',OUT/'locked_config.json'])
    rows=[r for r in read_csv(OUT/'selected_images.csv') if r['split']=='tuning']; trials=[]; configs=grid()
    save_json(OUT/'config_grid.json',configs)
    with zipfile.ZipFile(ARCHIVE) as z:
        ann=annotations(z)
        for row in rows:
            rgb,gt=rgb_and_gt(z,ann,row); features=color_features(rgb)
            for index,config in enumerate(configs):
                m=pixel_metrics(gt,segment_features(features,config))
                trials.append(dict(review_id=row['review_id'],group=row['group'],split='tuning',config_index=index,**m))
    index,scores=tuning_winner(trials)
    save_csv(OUT/'tuning_trials.csv',trials)
    save_json(OUT/'locked_config.json',dict(config_index=index,config=configs[index],group_macro_iou=scores[index],
        all_objectives=scores,selection_lock_sha256=digest(OUT/'selection_lock.json'),
        tuning_trials_sha256=digest(OUT/'tuning_trials.csv'),
        source_sha256={f:digest(ROOT/f) for f in ('src/expr01.py','src/rumex_cv.py','src/lettuce_cv.py')}))
    print(json.dumps(dict(tuning_images=len(rows),configs=len(configs),winner=index,objective=scores[index],config=configs[index])))


def evaluate():
    verify_selection_lock(); guard_new([OUT/'predictions',OUT/'metrics.csv',OUT/'summary.json',OUT/'group_metrics.csv'])
    selected=read_csv(OUT/'selected_images.csv'); assert_groups(selected)
    lock=json.loads((OUT/'locked_config.json').read_text()); metrics=[]
    if digest(OUT/'selection_lock.json')!=lock['selection_lock_sha256'] or digest(OUT/'tuning_trials.csv')!=lock['tuning_trials_sha256']:
        raise ValueError('Tuning lock drift')
    for name,sha in lock['source_sha256'].items():
        if digest(ROOT/name)!=sha: raise ValueError('Locked inference source drift')
    with zipfile.ZipFile(ARCHIVE) as z:
        ann=annotations(z)
        for row in selected:
            rgb,gt=rgb_and_gt(z,ann,row); pred=segment_features(color_features(rgb),lock['config'])
            Image.fromarray(pred.astype(np.uint8)*255).save(OUT/'predictions'/f"{row['review_id']}.png")
            metrics.append(dict(review_id=row['review_id'],archive_entry=row['archive_entry'],subset=row['subset'],
                split=row['split'],group=row['group'],sequence=row['sequence'],**pixel_metrics(gt,pred)))
    save_csv(OUT/'metrics.csv',metrics)
    summary={split:summarize([r for r in metrics if r['split']==split]) for split in ('tuning','evaluation')}
    save_json(OUT/'summary.json',summary)
    grouped=[]
    for split in ('tuning','evaluation'):
        rr=[r for r in metrics if r['split']==split]
        for field in ('group','subset','sequence'):
            for value in sorted({r[field] for r in rr}):
                grouped.append(dict(split=split,grouping=field,value=value,**summarize([r for r in rr if r[field]==value])))
    save_csv(OUT/'group_metrics.csv',grouped)
    print(json.dumps(summary))


def cases():
    guard_new([OUT/'failure_cases',OUT/'success_cases',OUT/'label_quality_cases'])
    rows=read_csv(OUT/'metrics.csv'); evaluation=[r for r in rows if r['split']=='evaluation']
    queue={r['review_id']:r for r in read_csv(OUT/'review_queue.csv')}
    bad=sorted(evaluation,key=lambda r:float(r['percentage_area_error']),reverse=True)[:8]
    low_iou=sorted(evaluation,key=lambda r:float(r['iou']))[:4]
    good=sorted(evaluation,key=lambda r:float(r['iou']),reverse=True)[:4]
    with zipfile.ZipFile(ARCHIVE) as z:
        ann=annotations(z)
        for category,items in [('failure_cases',list({r['review_id']:r for r in bad+low_iou}.values())),('success_cases',good)]:
            for row in items:
                rgb,gt=rgb_and_gt(z,ann,queue[row['review_id']]); pred=np.asarray(Image.open(OUT/'predictions'/f"{row['review_id']}.png"))>0
                label=f"DEVELOPMENT {row['review_id']} | IoU {float(row['iou']):.3f} | signed area {float(row['signed_percentage_area_error']):+.1f}% | RGB / GT / green TP, red FP, blue FN"
                panel(rgb,gt,pred,size=(1800,850),label=label).save(OUT/category/f"{row['review_id']}.jpg",quality=95)
        decisions=json.loads((OUT/'screen_decisions.json').read_text())
        for code in ('missing_visible_blades','hidden_tissue','bad_polygon','ambiguous_target'):
            ids=[key for key,d in decisions.items() if not d['include'] and code in d['reason']][:3]
            for key in ids:
                rgb,gt=rgb_and_gt(z,ann,queue[key])
                panel(rgb,gt,size=(1500,850),label=f"EXCLUDED {key}: {decisions[key]['reason']} | RGB / supplied GT").save(OUT/'label_quality_cases'/f'{code}_{key}.jpg',quality=95)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['prepare','freeze','tune','evaluate','cases'])
    globals()[parser.parse_args().command]()
