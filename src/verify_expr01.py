"""Independent EXP-R01 numerical/provenance verification, no experiment edits."""
import csv
import hashlib
import io
import json
import math
from collections import defaultdict, Counter
from pathlib import Path, PurePosixPath
import statistics
import xml.etree.ElementTree as ET
import zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'experiments/EXP-R01'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def csvrows(name):
    with (OUT/name).open(encoding='utf-8',newline='') as f: return list(csv.DictReader(f))


def independent_metrics(gt,pred):
    gt=np.asarray(gt,dtype=bool); pred=np.asarray(pred,dtype=bool)
    assert gt.shape==pred.shape
    tp=int(np.count_nonzero(gt & pred)); fn=int(np.count_nonzero(gt & ~pred)); fp=int(np.count_nonzero(~gt & pred))
    g=tp+fn; p=tp+fp; assert g>0
    return dict(iou=tp/(tp+fp+fn),dice=2*tp/(2*tp+fp+fn),gt_pixel_area=g,predicted_pixel_area=p,
        absolute_pixel_area_error=abs(fp-fn),percentage_area_error=100*abs(fp-fn)/g,
        signed_percentage_area_error=100*(fp-fn)/g,intersection_pixels=tp,union_pixels=tp+fp+fn,
        false_positive_pixels=fp,false_negative_pixels=fn)


def percentile(values,q):
    values=sorted(values); x=(len(values)-1)*q; lo=math.floor(x); hi=math.ceil(x)
    return values[lo]+(values[hi]-values[lo])*(x-lo)


def independent_summary(rows):
    def values(key): return [float(r[key]) for r in rows]
    error=values('percentage_area_error'); signed=values('signed_percentage_area_error')
    return dict(n=len(rows),mean_iou=statistics.mean(values('iou')),mean_dice=statistics.mean(values('dice')),
        mean_area_error_pct=statistics.mean(error),median_area_error_pct=statistics.median(error),
        p90_area_error_pct=percentile(error,.9),p95_area_error_pct=percentile(error,.95),
        mean_absolute_pixel_area_error=statistics.mean(values('absolute_pixel_area_error')),
        mean_signed_area_error_pct=statistics.mean(signed),overestimates=sum(v>0 for v in signed),
        underestimates=sum(v<0 for v in signed),exact_area=sum(v==0 for v in signed),
        catastrophic_over=sum(v>50 for v in signed),catastrophic_under=sum(v< -50 for v in signed))


def reference_mask(im):
    w,h=int(im.get('width')),int(im.get('height')); image=Image.new('1',(w,h)); draw=ImageDraw.Draw(image)
    for polygon in im.findall('polygon'):
        if polygon.get('label')!='leaf_blade': continue
        pts=[(min(w-1,max(0,round(float(x)))),min(h-1,max(0,round(float(y)))))
             for x,y in (v.split(',') for v in polygon.get('points').split(';'))]
        draw.polygon(pts,fill=1)
    return np.asarray(image,dtype=bool)


def independent_predict(rgb,config):
    hsv=np.asarray(Image.fromarray(rgb).convert('HSV'))
    hue=hsv[:,:,0].astype(float)*(180/256)
    rr=rgb[:,:,0].astype(np.int32); gg=rgb[:,:,1].astype(np.int32); bb=rgb[:,:,2].astype(np.int32)
    pred=(hue>=config['h_min']) & (hue<=config['h_max']) & (hsv[:,:,1]>=config['s_min']) & (hsv[:,:,2]>=config['v_min']) & (2*gg-rr-bb>config['exg_min'])
    if config['morphology_radius']:
        k=2*config['morphology_radius']+1; im=Image.fromarray(pred.astype(np.uint8)*255)
        for filt in [ImageFilter.MinFilter(k),ImageFilter.MaxFilter(k),ImageFilter.MaxFilter(k),ImageFilter.MinFilter(k)]: im=im.filter(filt)
        pred=np.asarray(im)>0
    return pred


def verify():
    checks=0
    def check(condition,label):
        nonlocal checks
        if not condition: raise AssertionError(label)
        checks+=1
    def numbers(expected,actual,label):
        for k,v in expected.items(): check(math.isclose(v,float(actual[k]),rel_tol=1e-10,abs_tol=1e-8),label+':'+k)
    selected=csvrows('selected_images.csv'); excluded=csvrows('excluded_images.csv'); inventory=csvrows('inventory.csv'); queue=csvrows('review_queue.csv')
    decisions=json.loads((OUT/'screen_decisions.json').read_text()); before=json.loads((OUT/'protected_before.json').read_text())
    s={r['archive_entry'] for r in selected}; e={r['archive_entry'] for r in excluded}; all_entries={r['archive_entry'] for r in inventory}
    check(len(selected)==len(s) and len(excluded)==len(e),'No duplicate manifest records')
    check(not s&e and s|e==all_entries and len(all_entries)==809,'Full inventory reconciliation')
    q={r['archive_entry']:r for r in queue}
    check(len(q)==179 and len(decisions)==179,'Complete bounded review')
    for r in selected+excluded:
        d=decisions[q[r['archive_entry']]['review_id']] if r['archive_entry'] in q else dict(include=False,reason='not_screened_budget')
        check(d['include']==(r['archive_entry'] in s) and d['reason']==r['screen_reason'],'Screen decision agreement')
    groups=defaultdict(set)
    for r in inventory: groups[r['group']].add(r['split'])
    check(all(len(v)==1 for v in groups.values()),'Group partition across all inventory')
    check({r['group'] for r in selected if r['split']=='tuning'}.isdisjoint({r['group'] for r in selected if r['split']=='evaluation'}),'Selected group separation')
    lock=json.loads((OUT/'locked_config.json').read_text()); selection=json.loads((OUT/'selection_lock.json').read_text())
    for key,name in [('selected','selected_images.csv'),('excluded','excluded_images.csv'),('decisions','screen_decisions.json'),('protocol','protocol.md')]:
        check(sha(OUT/name)==selection[key+'_sha256'],'Selection lock '+key)
    check(sha(OUT/'selection_lock.json')==lock['selection_lock_sha256'],'Config links selection')
    check(sha(OUT/'tuning_trials.csv')==lock['tuning_trials_sha256'],'Config links tuning trials')
    for name,h in lock['source_sha256'].items(): check(sha(ROOT/name)==h,'Locked source '+name)
    trials=csvrows('tuning_trials.csv'); configs=json.loads((OUT/'config_grid.json').read_text()); bytrial={(r['review_id'],int(r['config_index'])):r for r in trials}
    tuning=[r for r in selected if r['split']=='tuning']; expected={(r['review_id'],i) for r in tuning for i in range(96)}
    check(len(configs)==96 and len(trials)==len(expected) and set(bytrial)==expected,'Every tuning image/config exactly once')
    for r in trials:
        owner=next(x for x in tuning if x['review_id']==r['review_id'])
        check(r['split']=='tuning' and r['group']==owner['group'],'Trial tuning-only identity')
    objectives={}
    for i in range(96):
        gg=defaultdict(list)
        for r in trials:
            if int(r['config_index'])==i: gg[r['group']].append(float(r['iou']))
        objectives[i]=statistics.mean(statistics.mean(v) for v in gg.values())
        check(math.isclose(objectives[i],lock['all_objectives'][str(i)],abs_tol=1e-12),'Every tuning group-macro objective')
    winner=min(objectives,key=lambda i:(-objectives[i],i))
    check(winner==lock['config_index'] and configs[winner]==lock['config'],'Deterministic tuning winner')
    metrics=csvrows('metrics.csv'); byid={r['review_id']:r for r in metrics}
    check(len(metrics)==len(selected) and set(byid)=={r['review_id'] for r in selected},'All selected metrics exactly once')
    check({p.stem for p in (OUT/'predictions').glob('*.png')}==set(byid),'Exactly selected predicted masks')
    check({p.stem for p in (OUT/'gt_masks').glob('*.png')}==set(byid),'Exactly selected reference masks')
    recomputed=[]
    with zipfile.ZipFile(ROOT/'RumexLeaves.zip') as z:
        ann={im.get('name'):im for name in z.namelist() if name.endswith('/annotations.xml') for im in ET.fromstring(z.read(name)).findall('image')}
        for row in selected:
            key=row['review_id']; gt=reference_mask(ann[PurePosixPath(row['archive_entry']).name]); raw=z.read(row['archive_entry'])
            check(hashlib.sha256(raw).hexdigest()==row['image_sha256'],'Source RGB hash')
            rgb=np.array(Image.open(io.BytesIO(raw)).convert('RGB')); saved_gt=np.asarray(Image.open(OUT/'gt_masks'/f'{key}.png'))
            pred=np.asarray(Image.open(OUT/'predictions'/f'{key}.png'))
            check(set(np.unique(saved_gt))<={0,255} and np.array_equal(saved_gt>0,gt),'Independent GT union')
            check(set(np.unique(pred))<={0,255} and np.array_equal(pred>0,independent_predict(rgb,lock['config'])),'Independent native inference')
            m=independent_metrics(gt,pred>0); numbers(m,byid[key],'Native metrics '+key)
            check(all(byid[key][k]==row[k] for k in ('archive_entry','subset','split','group','sequence')),'Metric identity')
            recomputed.append(dict(m,split=row['split'],group=row['group'],subset=row['subset'],sequence=row['sequence']))
            if row['split']=='tuning':
                for i,c in enumerate(configs): numbers(independent_metrics(gt,independent_predict(rgb,c)),bytrial[key,i],'Tuning native trial')
    summary=json.loads((OUT/'summary.json').read_text())
    for split in ('tuning','evaluation'): numbers(independent_summary([r for r in recomputed if r['split']==split]),summary[split],'Summary '+split)
    group_rows=csvrows('group_metrics.csv'); expected_groups={(sp,field,value) for sp in ('tuning','evaluation') for field in ('group','subset','sequence') for value in {r[field] for r in recomputed if r['split']==sp}}
    check(len(group_rows)==len(expected_groups) and {(r['split'],r['grouping'],r['value']) for r in group_rows}==expected_groups,'Complete group tables')
    for r in group_rows: numbers(independent_summary([x for x in recomputed if x['split']==r['split'] and x[r['grouping']]==r['value']]),r,'Group summary')
    for name,h in before['prior_artifacts'].items(): check(sha(ROOT/name)==h,'Prior experiment unchanged '+name)
    check(sha(ROOT/'RumexLeaves.zip')==before['raw_sha256'] and (ROOT/'RumexLeaves.zip').stat().st_size==before['raw_size'],'Raw ZIP unchanged')
    archive=OUT/'EXP-003_LATEST_REPORT.md'
    check(sha(archive if archive.exists() else ROOT/'docs/reports/LATEST_REPORT.md')==before['prior_latest_sha256'],'Prior report preserved')
    result=dict(status='PASS',checks=checks,selected=len(selected),evaluation=len([r for r in selected if r['split']=='evaluation']),
        tuning=len(tuning),native_predictions_reproduced=len(selected),native_tuning_trials_reproduced=len(trials),
        prior_artifacts_unchanged=len(before['prior_artifacts']),raw_sha256=before['raw_sha256'],
        numpy_version=np.__version__,pillow_version=Image.__version__,physical_plant_independence='UNKNOWN')
    with (OUT/'verification.json').open('x',encoding='utf-8') as f: json.dump(result,f,indent=2)
    print(json.dumps(result))


if __name__=='__main__': verify()
