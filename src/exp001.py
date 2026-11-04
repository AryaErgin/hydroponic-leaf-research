"""Run with python -m src.exp001 inventory|sheet|freeze|run|verify.

All experiment files stay under the project root. Outputs are create-only.
Review decisions live in review_decisions.csv, independent of predictions.
"""
import argparse
import csv
import hashlib
import json
import platform
import itertools
import shutil
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version
from .lettuce_cv import rasterize_yolo, assign_split, capture_stage, segment, color_features, segment_features, pixel_metrics

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw/IoT4Lettuce/04_OAK-1'
OUT = ROOT / 'experiments/EXP-001'


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    rows = list(rows)
    with open(path, 'x', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_json(path, value):
    with open(path, 'x', encoding='utf-8') as f:
        json.dump(value, f, indent=2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label_path(row):
    return RAW / 'segmentation/labels/train' / (Path(row['filename']).stem + '.txt')


def gt_mask(row):
    return rasterize_yolo(label_path(row).read_text(), int(row['width']), int(row['height']))


def load_rgb(row):
    with Image.open(RAW / 'images' / row['filename']) as im:
        rgb = np.asarray(im.convert('RGB'))
    if rgb.shape[:2] != (int(row['height']), int(row['width'])):
        raise ValueError('Metadata canvas mismatch')
    return rgb


def inventory(destination=OUT):
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT):
        raise ValueError('Inventory outputs must be inside project root')
    destination.mkdir(parents=True,exist_ok=True)
    groups = defaultdict(list)
    for r in read_csv(RAW / 'metadata.csv'):
        r['timestamp'] = datetime.strptime(r['date'], '%d/%m/%Y').replace(hour=int(r['hour']), minute=int(r['minute']), second=int(r['second'])).isoformat()
        groups[r['tag']].append(r)
    rows = []
    for tag in sorted(groups):
        seq = sorted(groups[tag], key=lambda x: x['timestamp'])
        for i, r in enumerate(seq):
            txt = label_path(r).read_text()
            polygons = len([l for l in txt.splitlines() if l.strip()])
            mask = gt_mask(r)
            ys, xs = np.where(mask)
            area = int(mask.sum())
            # Pre-screen only annotation geometry; never baseline agreement.
            reason = ''
            if not polygons:
                reason = 'empty_annotation_unannotated_not_background'
            elif polygons != 1:
                reason = 'multiple_polygons_neighbor_or_identity_ambiguity_not_in_single_plant_scope'
            elif area < 0.01*mask.size:
                reason = 'annotation_area_below_1pct_possible_debris_or_incomplete_requires_future_audit'
            elif xs.min() == 0 or ys.min() == 0 or xs.max() == mask.shape[1]-1 or ys.max() == mask.shape[0]-1:
                reason = 'annotation_touches_frame_boundary_incomplete_canopy_risk'
            r.update(split=assign_split(tag), stage=capture_stage(i, len(seq)), polygons=polygons,
                     gt_pixel_area=area, prescreen_reason=reason, image_sha256=sha(RAW/'images'/r['filename']), label_sha256=sha(label_path(r)))
            rows.append(r)
    write_csv(destination/'inventory.csv', rows)
    candidates=[]
    for tag in sorted(groups):
        for stage in ['early', 'mid', 'late']:
            pool = [r for r in rows if r['tag']==tag and r['stage']==stage and not r['prescreen_reason']]
            # Stable random ordering within tag/time stratum; four candidates allow visual replacements.
            pool.sort(key=lambda r: hashlib.sha256(('EXP001-v1:'+r['filename']).encode()).hexdigest())
            candidates.extend(pool[:4])
    for i, r in enumerate(candidates):
        r['review_id'] = i
    write_csv(destination/'review_candidates.csv', candidates)
    strata=defaultdict(list)
    for r in candidates:
        strata[(r['tag'],r['stage'])].append(r)
    queue=[group[rank] for rank in range(4) for key,group in sorted(strata.items()) if len(group)>rank]
    write_csv(destination/'review_queue.csv',queue)
    print('Inventory', len(rows), 'prescreen eligible', sum(not r['prescreen_reason'] for r in rows), 'review candidates', len(candidates))
    print(Counter(r['prescreen_reason'] for r in rows))
    print(Counter((r['split'], r['stage']) for r in candidates))


def review_sheet(start, count, filename):
    target=(OUT/'review_sheets'/filename).resolve()
    if not target.is_relative_to(OUT/'review_sheets'):
        raise ValueError('Review-sheet output must remain in review_sheets')
    if target.exists():
        raise FileExistsError(target)
    queue = OUT/'review_queue.csv'
    rows = read_csv(queue if queue.exists() else OUT/'review_candidates.csv')[start:start+count]
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 17)
    # Six pairs / sheet, each original and GT translucent overlay at 480x360.
    sheet = Image.new('RGB', (1920, ((len(rows)+1)//2)*405), 'white')
    draw = ImageDraw.Draw(sheet)
    for j, r in enumerate(rows):
        rgb=load_rgb(r); mask=gt_mask(r)
        overlay=rgb.copy()
        overlay[mask] = (0.58*rgb[mask]+0.42*np.array([255,0,255])).astype(np.uint8)
        x=(j%2)*960; y=(j//2)*405
        title=f"{r['review_id']} {r['tag']} {r['stage']} {r['date']} {r['split']} GT={r['gt_pixel_area']}"
        draw.text((x+5,y+4),title,fill='black',font=font)
        for k, arr in enumerate([rgb, overlay]):
            im=Image.fromarray(arr); im.thumbnail((478,365))
            sheet.paste(im,(x+k*480,y+35))
    sheet.save(target)
    print(str(OUT/'review_sheets'/filename))


def record_review(inclusions, exclusions, note='Single canopy and visible tissue agree with polygon; no clear neighboring plant tissue; minor contour uncertainty retained'):
    """Append explicit visual decisions by stable candidate ID, before freezing/scoring."""
    if (OUT/'subset_freeze.json').exists():
        raise ValueError('Review is frozen')
    path=OUT/'review_decisions.csv'
    existing=read_csv(path) if path.exists() else []
    seen={r['review_id'] for r in existing}
    candidates={int(r['review_id']):r for r in read_csv(OUT/'review_candidates.csv')}
    new=[]
    for decision, entries in [('include',{i:note for i in inclusions}),('exclude',exclusions)]:
        for i, reason in entries.items():
            if str(i) in seen:
                raise ValueError(f'Already reviewed {i}')
            r=candidates[i]
            new.append(dict(review_id=i,filename=r['filename'],decision=decision,reason=reason,reviewer='Codex',reviewed_at=datetime.now().isoformat()))
            seen.add(str(i))
    if not existing:
        write_csv(path,new)
    else:
        with open(path,'a',encoding='utf-8',newline='') as f:
            csv.DictWriter(f,fieldnames=list(existing[0])).writerows(new)


def freeze():
    inventory_rows=read_csv(OUT/'inventory.csv')
    candidates={r['filename']:r for r in read_csv(OUT/'review_candidates.csv')}
    decisions=read_csv(OUT/'review_decisions.csv')
    if len({r['filename'] for r in decisions}) != len(decisions):
        raise ValueError('Duplicate review decision')
    reviewed={r['filename']:r for r in decisions}
    eligible=defaultdict(list)
    for d in decisions:
        if d['decision']=='include':
            r=candidates[d['filename']].copy(); r['review_note']=d['reason']; r['reviewer']='Codex visual screen'
            eligible[(r['tag'],r['stage'])].append(r)
        elif d['decision']!='exclude':
            raise ValueError('Unknown decision')
    selected=[]
    # One per tag/stage first. Additional balanced rounds to reach 100 evaluation / 27 tuning.
    for split, target in [('tuning',27),('evaluation',100)]:
        keys=sorted(k for k, v in eligible.items() if v[0]['split']==split)
        for rank in range(4):
            for key in keys:
                if sum(r['split']==split for r in selected)>=target:
                    break
                if len(eligible[key])>rank:
                    selected.append(eligible[key][rank])
    chosen={r['filename'] for r in selected}
    excluded=[]
    for r in inventory_rows:
        if r['filename'] in chosen:
            continue
        d=reviewed.get(r['filename'])
        reason = r['prescreen_reason'] or (('visual_reject:'+d['reason']) if d and d['decision']=='exclude' else ('reviewed_clean_not_selected_balance_cap' if d else 'not_visually_reviewed_not_claimed_bad'))
        excluded.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],stage=r['stage'],reason=reason))
    if {r['tag'] for r in selected if r['split']=='tuning'} & {r['tag'] for r in selected if r['split']=='evaluation'}:
        raise ValueError('Plant leakage')
    write_csv(OUT/'selected_images.csv', selected)
    write_csv(OUT/'excluded_images.csv', excluded)
    write_json(OUT/'subset_freeze.json', dict(created_utc=datetime.now().isoformat(), selected_sha256=sha(OUT/'selected_images.csv'), excluded_sha256=sha(OUT/'excluded_images.csv'), decisions_sha256=sha(OUT/'review_decisions.csv'), counts=dict(Counter(r['split'] for r in selected))))
    print('Frozen',Counter(r['split'] for r in selected),Counter((r['split'],r['stage']) for r in selected))


def assert_subset_integrity(subset):
    lock=json.loads((subset/'subset_freeze.json').read_text())
    for name,key in [('selected_images.csv','selected_sha256'),('excluded_images.csv','excluded_sha256'),('review_decisions.csv','decisions_sha256')]:
        if sha(subset/name)!=lock[key]:
            raise ValueError(f'Frozen subset changed: {name}')
    rows=read_csv(subset/'selected_images.csv')
    if len({r['filename'] for r in rows})!=len(rows):
        raise ValueError('Duplicate selected image')
    tuning={r['tag'] for r in rows if r['split']=='tuning'}
    evaluation={r['tag'] for r in rows if r['split']=='evaluation'}
    if tuning & evaluation or any(r['split'] not in ('tuning','evaluation') for r in rows):
        raise ValueError('Invalid or overlapping plant split')
    return rows


def select_config(samples, configs):
    """Only accepts explicitly tuning-tagged samples; stable first-in-grid tie break."""
    if not samples or any(split!='tuning' for split,_,_ in samples):
        raise ValueError('Threshold selection requires tuning data exclusively')
    scores=[]
    for index, cfg in enumerate(configs):
        metrics=[pixel_metrics(gt,segment_features(features,cfg)) for _,gt,features in samples]
        scores.append(dict(config_index=index,**cfg,tuning_mean_iou=float(np.mean([m['iou'] for m in metrics])),
                           tuning_mean_dice=float(np.mean([m['dice'] for m in metrics])),
                           tuning_mean_percentage_area_error=float(np.mean([m['percentage_area_error'] for m in metrics]))))
        if len(configs)>2 and index%9==0:
            print(f'Tuning {index+1}/{len(configs)}: mean IoU {scores[-1]["tuning_mean_iou"]:.4f}',flush=True)
    best=max(range(len(scores)),key=lambda i:scores[i]['tuning_mean_iou'])
    return dict(configs[best]),scores


def aggregate(rows):
    groups={}
    for split in ['tuning','evaluation']:
        pool=[r for r in rows if r['split']==split]
        for stage in ['all','early','mid','late']:
            rr=pool if stage=='all' else [r for r in pool if r['stage']==stage]
            if not rr:
                continue
            stats=dict(images=len(rr),plants=len({r['tag'] for r in rr}))
            for field in ['iou','dice','gt_pixel_area','predicted_pixel_area','absolute_pixel_area_error','percentage_area_error','signed_percentage_area_error']:
                arr=np.array([float(r[field]) for r in rr]); stats['mean_'+field]=float(arr.mean()); stats['median_'+field]=float(np.median(arr))
            stats['p90_percentage_area_error']=float(np.percentile([float(r['percentage_area_error']) for r in rr],90))
            stats['micro_iou']=sum(int(r['intersection_pixels']) for r in rr)/sum(int(r['union_pixels']) for r in rr)
            stats['successes_iou85_area10']=sum(float(r['iou'])>=0.85 and float(r['percentage_area_error'])<=10 for r in rr)
            groups[f'{split}_{stage}']=stats
    return groups


def verify_aggregates(stored, rows):
    """Independent scalar aggregation; never calls aggregate() or pixel_metrics()."""
    expected_groups={}
    fields=['iou','dice','gt_pixel_area','predicted_pixel_area','absolute_pixel_area_error','percentage_area_error','signed_percentage_area_error']
    for split in ('tuning','evaluation'):
        for stage in ('all','early','mid','late'):
            group=[r for r in rows if r['split']==split and (stage=='all' or r['stage']==stage)]
            if not group:
                continue
            expected=dict(images=len(group),plants=len(set(r['tag'] for r in group)))
            for field in fields:
                vals=[float(r[field]) for r in group]
                expected['mean_'+field]=statistics.mean(vals)
                expected['median_'+field]=statistics.median(vals)
            # numpy's default linear p90, independently evaluated from sorted scalar values.
            ordered=sorted(float(r['percentage_area_error']) for r in group)
            pos=(len(ordered)-1)*0.9; lower=int(pos); upper=min(lower+1,len(ordered)-1)
            expected['p90_percentage_area_error']=ordered[lower]+(ordered[upper]-ordered[lower])*(pos-lower)
            expected['micro_iou']=sum(int(r['intersection_pixels']) for r in group)/sum(int(r['union_pixels']) for r in group)
            expected['successes_iou85_area10']=sum(float(r['iou'])>=0.85 and float(r['percentage_area_error'])<=10 for r in group)
            expected_groups[split+'_'+stage]=expected
    if stored.keys()!=expected_groups.keys():
        raise AssertionError('Aggregate group mismatch')
    for key, expected in expected_groups.items():
        if stored[key].keys()!=expected.keys() or any(abs(stored[key][k]-v)>1e-8 for k,v in expected.items()):
            raise AssertionError('Aggregate statistics mismatch: '+key)
    return True


def case_image(row, gt, pred, path):
    if path.exists():
        raise FileExistsError(path)
    rgb=load_rgb(row)
    panels=[]
    for arr,color in [(gt,[255,0,255]),(pred,[0,255,255])]:
        overlay=rgb.copy(); overlay[arr]=(0.55*rgb[arr]+0.45*np.array(color)).astype(np.uint8)
        panels.append(overlay)
    error=rgb.copy()
    error[gt & ~pred]=[255,40,40]; error[pred & ~gt]=[0,180,255]; error[gt & pred]=(0.6*rgb[gt & pred]+0.4*np.array([0,255,0])).astype(np.uint8)
    panels=[rgb]+panels+[error]
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
    sheet=Image.new('RGB',(1600,470),'white'); draw=ImageDraw.Draw(sheet)
    draw.text((5,5),f"{row['filename']} | {row['stage']} | IoU={float(row['iou']):.3f} Dice={float(row['dice']):.3f} area error={float(row['percentage_area_error']):.1f}%",fill='black',font=font)
    for j,(arr,title) in enumerate(zip(panels,['Original','Ground truth (magenta)','Prediction (cyan)','TP green / FN red / FP blue'])):
        draw.text((j*400+5,40),title,fill='black',font=font)
        im=Image.fromarray(arr); im.thumbnail((398,390)); sheet.paste(im,(j*400,75))
    sheet.save(path,quality=95)


def run(destination):
    selected=assert_subset_integrity(OUT)
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT):
        raise ValueError('Outputs must be inside project root')
    for name in ['tuning_search.csv','config.json','metrics.csv','aggregate.json','run_manifest.json','code_snapshot']:
        if (destination/name).exists():
            raise FileExistsError(f'Refusing overwrite of {destination/name}; choose new --output')
    destination.mkdir(parents=True,exist_ok=True)
    for folder in ['gt_masks','predicted_masks','success_cases','failure_cases','code_snapshot']:
        (destination/folder).mkdir(exist_ok=True)
    # Save exact executable source before tuning. Both masks and metrics have input hashes.
    for p in [ROOT/'src/exp001.py', ROOT/'src/lettuce_cv.py']:
        shutil.copy2(p,destination/'code_snapshot'/p.name)
    for r in selected:
        if sha(RAW/'images'/r['filename'])!=r['image_sha256'] or sha(label_path(r))!=r['label_sha256']:
            raise ValueError('Raw input changed')
    tuning=[r for r in selected if r['split']=='tuning']
    samples=[('tuning',gt_mask(r),color_features(load_rgb(r))) for r in tuning]
    configs=[dict(h_min=h,h_max=100,s_min=s,v_min=15,exg_min=e,morphology_radius=m) for h,s,e,m in itertools.product([15,25,35],[20,35,60],[10,25,40],[0,1])]
    best,scores=select_config(samples,configs)
    write_csv(destination/'tuning_search.csv',scores)
    write_json(destination/'config.json',dict(parameters=best,objective='maximize image-macro tuning IoU',tie_break='first config in deterministic grid',tuning_filenames=[r['filename'] for r in tuning],tuning_tags=sorted({r['tag'] for r in tuning}),grid_count=len(configs),selected_manifest_sha256=sha(OUT/'selected_images.csv'),locked_before_evaluation=True))
    del samples
    print('Configuration locked before evaluation:',best,flush=True)
    rows=[]
    for i,r in enumerate(selected):
        gt=gt_mask(r); pred=segment(load_rgb(r),best)
        metric=pixel_metrics(gt,pred)
        row=dict(filename=r['filename'],tag=r['tag'],split=r['split'],stage=r['stage'],width=r['width'],height=r['height'],date=r['date'],**metric)
        rows.append(row)
        for folder,mask in [('gt_masks',gt),('predicted_masks',pred)]:
            path=destination/folder/r['filename']
            if path.exists(): raise FileExistsError(path)
            Image.fromarray(mask.astype(np.uint8)*255).save(path)
        if i%20==0: print(f'Scoring {i+1}/{len(selected)}',flush=True)
    write_csv(destination/'metrics.csv',rows)
    stats=aggregate(rows); write_json(destination/'aggregate.json',stats)
    evaluation=[r for r in rows if r['split']=='evaluation']
    successes=sorted([r for r in evaluation if r['iou']>=0.85 and r['percentage_area_error']<=10],key=lambda r:-r['iou'])[:8]
    failures=sorted(evaluation,key=lambda r:r['iou'])[:8]
    # Additional largest area errors if distinct from low-IoU examples.
    failures+= [r for r in sorted(evaluation,key=lambda r:-r['percentage_area_error'])[:4] if r not in failures]
    for folder,rr in [('success_cases',successes),('failure_cases',failures)]:
        for r in rr:
            gt=np.asarray(Image.open(destination/'gt_masks'/r['filename']))>0
            pred=np.asarray(Image.open(destination/'predicted_masks'/r['filename']))>0
            case_image(r,gt,pred,destination/folder/(Path(r['filename']).stem+'.jpg'))
        if rr: write_csv(destination/folder/'cases.csv',rr)
    write_json(destination/'run_manifest.json',dict(created_local=datetime.now().isoformat(),raw_root=str(RAW.relative_to(ROOT)),subset_root=str(OUT.relative_to(ROOT)),python=platform.python_version(),numpy=np.__version__,pillow=pillow_version,
        selected_sha256=sha(OUT/'selected_images.csv'),config_sha256=sha(destination/'config.json'),metrics_sha256=sha(destination/'metrics.csv'),tuning_search_sha256=sha(destination/'tuning_search.csv'),source_sha256={p.name:sha(p) for p in (destination/'code_snapshot').glob('*.py')},evaluation_images=len(evaluation),tuning_images=len(tuning),unit='pixels',rasterization='Pillow inclusive fill; round(x*(width-1)), round(y*(height-1)); union class-0 polygons',metric_definition='percentage_area_error=100*abs(predicted-gt)/gt; signed variant also stored'))
    print(json.dumps(stats['evaluation_all'],indent=2),flush=True)


def verify(destination, report_name='verification.json'):
    if Path(report_name).name!=report_name:
        raise ValueError('Verification report must be a filename')
    if (destination/report_name).exists():
        raise FileExistsError('Choose a new --verification-name to preserve the previous report')
    selected=assert_subset_integrity(OUT)
    manifest=json.loads((destination/'run_manifest.json').read_text())
    cfg=json.loads((destination/'config.json').read_text())
    metrics=read_csv(destination/'metrics.csv')
    checks=[]
    def check(condition,message):
        if not condition: raise AssertionError(message)
        checks.append(message)
    check({r['filename'] for r in metrics}=={r['filename'] for r in selected} and len(metrics)==len(selected),'Metrics exactly match frozen selected images')
    for name,key in [('config.json','config_sha256'),('metrics.csv','metrics_sha256'),('tuning_search.csv','tuning_search_sha256')]:
        check(sha(destination/name)==manifest[key],f'{name} hash unchanged')
    check(set(cfg['tuning_filenames'])=={r['filename'] for r in selected if r['split']=='tuning'},'Threshold optimization contains tuning images exclusively')
    search=read_csv(destination/'tuning_search.csv')
    winner=max(search,key=lambda r:float(r['tuning_mean_iou']))
    check(all(int(winner[k])==v for k,v in cfg['parameters'].items()),'Frozen configuration is the measured tuning-IoU winner')
    lookup={r['filename']:r for r in selected}
    for r in metrics:
        source=lookup[r['filename']]
        check(all(r[k]==source[k] for k in ['tag','split','stage','width','height']),'Metric metadata matches selection: '+r['filename'])
        gt=np.asarray(Image.open(destination/'gt_masks'/r['filename']))>0
        pred=np.asarray(Image.open(destination/'predicted_masks'/r['filename']))>0
        check(gt.shape==(int(r['height']),int(r['width'])) and pred.shape==gt.shape,'Native mask dimensions: '+r['filename'])
        check(np.array_equal(gt,gt_mask(source)),'Stored GT equals native polygon union: '+r['filename'])
        check(np.array_equal(pred,segment(load_rgb(source),cfg['parameters'])),'Prediction reproducible without GT input: '+r['filename'])
        # Independent formulas, rather than calling pixel_metrics for verification.
        g=int(np.count_nonzero(gt)); p=int(np.count_nonzero(pred)); both=int(np.count_nonzero(gt & pred)); u=int(np.count_nonzero(gt | pred))
        expected=dict(gt_pixel_area=g,predicted_pixel_area=p,intersection_pixels=both,union_pixels=u,iou=both/u,dice=2*both/(g+p),absolute_pixel_area_error=abs(p-g),percentage_area_error=100*abs(p-g)/g,signed_percentage_area_error=100*(p-g)/g,false_positive_pixels=p-both,false_negative_pixels=g-both)
        check(all(abs(float(r[k])-v)<1e-9 for k,v in expected.items()),'Independent metric recomputation: '+r['filename'])
    inventory_rows=read_csv(OUT/'inventory.csv')
    for r in inventory_rows:
        if sha(RAW/'images'/r['filename'])!=r['image_sha256'] or sha(label_path(r))!=r['label_sha256']:
            raise AssertionError('Raw data integrity changed: '+r['filename'])
    check(True,'All 1522 raw image/label pair hashes unchanged')
    excluded=read_csv(OUT/'excluded_images.csv')
    check(len(selected)+len(excluded)==len(inventory_rows) and not ({r['filename'] for r in selected}&{r['filename'] for r in excluded}),'Exclusions and selection partition all 1522 pairs')
    check(verify_aggregates(json.loads((destination/'aggregate.json').read_text()),metrics),'All aggregate means/medians/stage counts/success counts independently recomputed')
    report=dict(status='PASS',checked_at_local=datetime.now().isoformat(),checks=len(checks),selected_images=len(selected),raw_pairs_checked=len(inventory_rows),aggregate_sha256=sha(destination/'aggregate.json'),verification_source_sha256={p.name:sha(p) for p in [ROOT/'src/exp001.py',ROOT/'src/lettuce_cv.py']},detail=checks)
    write_json(destination/report_name,report)
    print(f"PASS: {len(checks)} checks, {len(selected)} masks/metrics, all {len(inventory_rows)} raw pairs intact")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['inventory','sheet','freeze','run','verify']); ap.add_argument('--start',type=int,default=0); ap.add_argument('--count',type=int,default=6); ap.add_argument('--filename',default='sheet.jpg'); ap.add_argument('--output',type=Path,default=OUT); ap.add_argument('--verification-name',default='verification.json')
    args=ap.parse_args()
    if args.command=='inventory': inventory(args.output)
    elif args.command=='sheet': review_sheet(args.start,args.count,args.filename)
    elif args.command=='freeze': freeze()
    elif args.command=='run': run(args.output)
    elif args.command=='verify': verify(args.output,args.verification_name)


if __name__=='__main__':
    main()
