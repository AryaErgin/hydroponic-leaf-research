"""Frozen EXP-002 comparison. python -m src.exp002 {tune,evaluate,verify}."""
import argparse
import itertools
import json
import platform
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, __version__ as pillow_version
from .exp001 import ROOT, RAW, OUT as BASE, read_csv, write_csv, write_json, sha, load_rgb, gt_mask, label_path, assert_subset_integrity
from .lettuce_cv import segment, pixel_metrics
from .roi_cv import infer_roi, adaptive_segment

OUT = ROOT/'experiments/EXP-002'
SOURCES = [ROOT/'src/exp002.py', ROOT/'src/roi_cv.py', ROOT/'src/exp001.py', ROOT/'src/lettuce_cv.py']


def numeric(rows):
    keys = list(pixel_metrics(np.ones((1,1),bool),np.ones((1,1),bool)))
    return [dict(r, **{k:float(r[k]) for k in keys}) for r in rows]


def summarize(rows):
    if not rows:
        raise ValueError('Empty summary')
    area = np.array([r['percentage_area_error'] for r in rows],float)
    signed = np.array([r['signed_percentage_area_error'] for r in rows],float)
    return dict(images=len(rows), plants=len({r['tag'] for r in rows}),
        mean_iou=float(np.mean([r['iou'] for r in rows])),
        median_iou=float(np.median([r['iou'] for r in rows])),
        mean_dice=float(np.mean([r['dice'] for r in rows])),
        mean_gt_pixel_area=float(np.mean([r['gt_pixel_area'] for r in rows])),
        mean_predicted_pixel_area=float(np.mean([r['predicted_pixel_area'] for r in rows])),
        mean_absolute_pixel_area_error=float(np.mean([r['absolute_pixel_area_error'] for r in rows])),
        median_absolute_pixel_area_error=float(np.median([r['absolute_pixel_area_error'] for r in rows])),
        mean_area_error=float(area.mean()), median_area_error=float(np.median(area)),
        p90_area_error=float(np.percentile(area,90)), p95_area_error=float(np.percentile(area,95)),
        mean_signed_area_error=float(signed.mean()), overestimated_images=int((signed>0).sum()),
        underestimated_images=int((signed<0).sum()), exact_area_images=int((signed==0).sum()),
        mean_overestimation=float(signed[signed>0].mean()) if (signed>0).any() else 0.,
        mean_underestimation=float(-signed[signed<0].mean()) if (signed<0).any() else 0.,
        catastrophic_false_positives=int((signed>100).sum()),
        catastrophic_underestimation=int((signed < -50).sum()),
        total_false_positive_pixels=int(sum(r['false_positive_pixels'] for r in rows)),
        total_false_negative_pixels=int(sum(r['false_negative_pixels'] for r in rows)))


def choose_trial(rows, configs):
    if not rows or any(r['split']!='tuning' for r in rows):
        raise ValueError('Optimization requires tuning images only')
    expected = {(r['filename'],r['tag']) for r in rows if int(r['config_index'])==0}
    scores=[]
    for i,cfg in enumerate(configs):
        rr=[r for r in rows if int(r['config_index'])==i]
        if {(r['filename'],r['tag']) for r in rr}!=expected or len(rr)!=len(expected):
            raise ValueError('Trial images differ')
        tags=sorted({r['tag'] for r in rr})
        macro=float(np.mean([np.mean([float(r['iou']) for r in rr if r['tag']==t]) for t in tags]))
        scores.append(dict(config_index=i,plant_macro_iou=macro,**cfg))
    winner=max(range(len(configs)),key=lambda i:scores[i]['plant_macro_iou'])
    return configs[winner],scores


def save_mask(path, mask):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:
        Image.fromarray(mask.astype(np.uint8)*255).save(f,format='PNG')


def protection():
    return {str(p.relative_to(ROOT)):sha(p) for p in BASE.rglob('*') if p.is_file()}


def tune(destination):
    # Preflight prevents a partial overwrite even when an earlier run exists.
    if any((destination/n).exists() for n in ['config.json','tuning_metrics.csv','provenance.json','selected_images.csv']):
        raise FileExistsError('Existing EXP-002 run; choose a new output directory')
    destination.mkdir(parents=True,exist_ok=True)
    selected=assert_subset_integrity(BASE)
    tuning=[r for r in selected if r['split']=='tuning']
    if len(tuning)!=27 or len(selected)!=127:
        raise ValueError('Unexpected frozen subset')
    protected=protection()
    for name in ['selected_images.csv','excluded_images.csv']:
        with (destination/name).open('xb') as f: f.write((BASE/name).read_bytes())
    with (destination/'EXP-001_LATEST_REPORT.md').open('xb') as f:
        f.write((ROOT/'docs/reports/LATEST_REPORT.md').read_bytes())
    for p in SOURCES:
        d=destination/'code_snapshot'/p.name; d.parent.mkdir(exist_ok=True)
        with d.open('xb') as f:f.write(p.read_bytes())
    base=json.loads((BASE/'config.json').read_text())['parameters']
    bconfigs=[dict(expansion=e,fallback_radius=f) for e,f in itertools.product([1.25,1.75,2.25],[.40,.48])]
    cconfigs=[dict(h_max=h,weak_exg=e,bright_radius=r) for h,e,r in itertools.product([70,85],[.025,.06],[0,4])]
    btrials=[]; cache=[]
    for r in tuning:
        rgb,gt=load_rgb(r),gt_mask(r)
        if sha(RAW/'images'/r['filename'])!=r['image_sha256'] or sha(label_path(r))!=r['label_sha256']:
            raise ValueError('Input drift')
        baseline=segment(rgb,base)
        cache.append((r,rgb,gt,baseline))
        for i,cfg in enumerate(bconfigs):
            roi,diag,support=infer_roi(rgb,cfg)
            btrials.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],config_index=i,**pixel_metrics(gt,baseline & roi)))
    b,sb=choose_trial(btrials,bconfigs)
    ctrials=[]
    for j,(r,rgb,gt,_) in enumerate(cache):
        roi,diag,support=infer_roi(rgb,b)
        for i,cfg in enumerate(cconfigs):
            ctrials.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],config_index=i,**pixel_metrics(gt,adaptive_segment(rgb,roi,support,diag,cfg))))
        print(f'Tuning adaptive method {j+1}/{len(cache)}',flush=True)
    c,sc=choose_trial(ctrials,cconfigs)
    write_csv(destination/'B_tuning_trials.csv',btrials)
    write_csv(destination/'C_tuning_trials.csv',ctrials)
    score_keys=['config_index','plant_macro_iou','expansion','fallback_radius','h_max','weak_exg','bright_radius']
    write_csv(destination/'tuning_scores.csv',[dict(method=m,**{k:s.get(k,'') for k in score_keys}) for m,ss in [('B',sb),('C',sc)] for s in ss])
    cfg=dict(A=base,B=b,C=c,objective='maximum plant-macro tuning IoU; first-grid exact tie',
        tuning_filenames=[r['filename'] for r in tuning],tuning_tags=sorted({r['tag'] for r in tuning}),
        selected_sha256=sha(BASE/'selected_images.csv'),locked_before_evaluation=True,
        grid_B=bconfigs,grid_C=cconfigs,created_local=datetime.now().isoformat())
    write_json(destination/'config.json',cfg)
    write_json(destination/'provenance.json',dict(config_sha256=sha(destination/'config.json'),
        protected_EXP001_files=protected,source_sha256={p.name:sha(p) for p in SOURCES},
        selected_sha256=sha(BASE/'selected_images.csv'),python=platform.python_version(),numpy=np.__version__,pillow=pillow_version,
        protocol_sha256=sha(OUT/'plan.md'),unit='pixels',known_evaluation='previously exposed regression benchmark'))
    print('Locked before evaluation:',json.dumps(dict(B=b,C=c)),flush=True)


def read_lock(destination):
    prov=json.loads((destination/'provenance.json').read_text())
    cfg=json.loads((destination/'config.json').read_text())
    if sha(destination/'config.json')!=prov['config_sha256']:
        raise ValueError('Configuration changed')
    if any(sha(p)!=prov['source_sha256'][p.name] for p in SOURCES):
        raise ValueError('Inference code changed since tuning; use a new run directory')
    if sha(BASE/'selected_images.csv')!=prov['selected_sha256']:
        raise ValueError('Selection changed')
    if sha(OUT/'plan.md')!=prov['protocol_sha256']:
        raise ValueError('Protocol changed')
    return cfg,prov


def predictions(rgb,cfg):
    # Keep this path GT-free; score and diagnostics audit occur afterwards.
    a=segment(rgb,cfg['A'])
    roi,diag,support=infer_roi(rgb,cfg['B'])
    c=adaptive_segment(rgb,roi,support,diag,cfg['C'])
    return dict(A=a,B=a & roi,C=c),roi,diag,support


def grouped(rows, key):
    return [dict(split=split,method=method,**({key:value} if key else {}),**summarize(rr))
        for split in ['tuning','evaluation'] for method in ['A','B','C']
        for value in (sorted({r[key] for r in rows if r['split']==split}) if key else ['all'])
        if (rr:=[r for r in rows if r['split']==split and r['method']==method and (not key or r[key]==value)])]


def analysis(rows):
    ev=[r for r in rows if r['split']=='evaluation']
    by={m:[r for r in ev if r['method']==m] for m in ['A','B','C']}
    stats={m:summarize(rr) for m,rr in by.items()}
    worst={r['filename'] for r in by['A'] if r['signed_percentage_area_error']>100}
    tail=[r for r in by['A'] if r['filename'] in worst]
    tail_sum=sum(r['percentage_area_error'] for r in tail)
    tail_info=dict(baseline_catastrophic_images=len(tail),tail_mean_area_error=float(np.mean([r['percentage_area_error'] for r in tail])),
        share_of_total_percentage_error=tail_sum/sum(r['percentage_area_error'] for r in by['A']),
        tail_contribution_to_mean=tail_sum/len(by['A']),remaining_93_summary=summarize([r for r in by['A'] if r['filename'] not in worst]),
        filenames=sorted(worst))
    plants=grouped(rows,'tag')
    paired=[]; gates={}
    amap={r['tag']:r for r in plants if r['method']=='A' and r['split']=='evaluation'}
    for method in ['B','C']:
        pmap={r['tag']:r for r in plants if r['method']==method and r['split']=='evaluation'}
        for tag,a in amap.items():
            p=pmap[tag]
            paired.append(dict(method=method,tag=tag,images=a['images'],delta_iou=p['mean_iou']-a['mean_iou'],
                delta_mean_area_error=p['mean_area_error']-a['mean_area_error'],baseline_iou=a['mean_iou'],candidate_iou=p['mean_iou'],
                baseline_mean_area_error=a['mean_area_error'],candidate_mean_area_error=p['mean_area_error']))
        pp=[r for r in paired if r['method']==method]
        a_area=float(np.mean([r['mean_area_error'] for r in amap.values()]))
        p_area=float(np.mean([r['mean_area_error'] for r in pmap.values()]))
        a_fp=sum(r['false_positive_pixels'] for r in tail)
        p_fp=sum(r['false_positive_pixels'] for r in by[method] if r['filename'] in worst)
        measures=dict(catastrophic_count=stats[method]['catastrophic_false_positives'],
            catastrophic_count_reduction=1-stats[method]['catastrophic_false_positives']/len(worst),
            baseline_tail_false_positive_reduction=1-p_fp/a_fp,
            median_area_error_delta=stats[method]['median_area_error']-stats['A']['median_area_error'],
            mean_iou_delta=stats[method]['mean_iou']-stats['A']['mean_iou'],
            baseline_plant_macro_area_error=a_area,candidate_plant_macro_area_error=p_area,
            plant_macro_area_error_reduction=1-p_area/a_area,
            plants_iou_improved=sum(r['delta_iou']>0 for r in pp),plants_iou_degraded=sum(r['delta_iou']<0 for r in pp),
            plants_area_improved=sum(r['delta_mean_area_error']<0 for r in pp),plants_area_degraded=sum(r['delta_mean_area_error']>0 for r in pp))
        checks=dict(catastrophic_count=measures['catastrophic_count_reduction']>=.70,
            tail_false_positives=measures['baseline_tail_false_positive_reduction']>=.80,
            median_area_error=measures['median_area_error_delta']<=2,
            mean_iou=measures['mean_iou_delta']>=-.02,
            plant_macro_area=measures['plant_macro_area_error_reduction']>=.20,
            plants_iou=measures['plants_iou_improved']/len(amap)>=.60)
        gates[method]=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,measures=measures)
    return stats,tail_info,plants,paired,gates


def comparison_panel(row, masks, roi, support, path):
    rgb=load_rgb(row); gt=gt_mask(row)
    w,h=int(row['width']),int(row['height'])
    scale=420/max(w,h); size=(round(w*scale),round(h*scale))
    thumb=Image.fromarray(rgb).resize(size)
    canvas=Image.new('RGB',(size[0]*3,size[1]*2+116),'white'); draw=ImageDraw.Draw(canvas)
    titles=['RGB','GT (reference only)','RGB ROI / support','A: HSV+ExG','B: ROI+HSV+ExG','C: adaptive + background']
    arrays=[None,gt,roi,masks['A'],masks['B'],masks['C']]
    for i,mask in enumerate(arrays):
        x=(i%3)*size[0]; y=(i//3)*(size[1]+48)+24
        panel=thumb.copy()
        if mask is not None:
            small=np.asarray(Image.fromarray(mask.astype(np.uint8)*255).resize(size,Image.Resampling.NEAREST))>0
            a=np.asarray(panel).copy().astype(float)
            a[small]=.50*a[small]+.50*np.array([255,70,180])
            if i==2 and support is not None:
                ss=np.asarray(Image.fromarray(support.astype(np.uint8)*255).resize(size,Image.Resampling.NEAREST))>0
                a[ss]=.5*a[ss]+.5*np.array([40,170,255])
            panel=Image.fromarray(a.astype(np.uint8))
        canvas.paste(panel,(x,y));draw.text((x+4,y-18),titles[i],fill='black')
        if i>=3:
            m=pixel_metrics(gt,mask)
            draw.text((x+4,y+size[1]+4),f"IoU {m['iou']:.3f}; signed area {m['signed_percentage_area_error']:+.1f}%",fill='black')
    draw.text((4,canvas.height-19),row['filename'],fill='black')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:canvas.save(f,format='JPEG',quality=90)


def evaluate(destination):
    if (destination/'metrics.csv').exists() or (destination/'masks').exists():
        raise FileExistsError('Evaluation already exists')
    cfg,prov=read_lock(destination)
    selected=assert_subset_integrity(BASE)
    old={(r['filename'],r['split']):r for r in numeric(read_csv(BASE/'metrics.csv'))}
    rows=[];diagnostics=[]
    for i,r in enumerate(selected):
        rgb=load_rgb(r)
        masks,roi,diag,support=predictions(rgb,cfg)
        gt=gt_mask(r)
        for method,mask in masks.items():
            metric=pixel_metrics(gt,mask)
            if method=='A':
                expected=old[(r['filename'],r['split'])]
                if any(abs(metric[k]-expected[k])>1e-10 for k in metric):
                    raise AssertionError('Baseline did not reproduce')
                if not np.array_equal(mask,np.asarray(Image.open(BASE/'predicted_masks'/r['filename']))>0):
                    raise AssertionError('Baseline mask drift')
            rows.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],stage=r['stage'],method=method,**metric))
            save_mask(destination/'masks'/method/r['filename'],mask)
        save_mask(destination/'roi_masks'/r['filename'],roi)
        if support is not None:save_mask(destination/'support_masks'/r['filename'],support)
        domain=roi if support is None or not diag['green_periphery'] else roi & support
        diagnostics.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],stage=r['stage'],**diag,
            roi_pixels=int(roi.sum()),support_pixels=int(support.sum()) if support is not None else '',
            audit_gt_pixels_outside_roi=int((gt & ~roi).sum()),
            audit_gt_pixels_outside_C_domain=int((gt & ~domain).sum())))
        if i%10==0:print(f'Scored {i+1}/{len(selected)}',flush=True)
    write_csv(destination/'metrics.csv',rows)
    write_csv(destination/'roi_diagnostics.csv',diagnostics)
    write_csv(destination/'global_metrics.csv',grouped(rows,None))
    write_csv(destination/'stage_metrics.csv',grouped(rows,'stage'))
    stats,tail,plants,paired,gates=analysis(rows)
    write_csv(destination/'plant_metrics.csv',plants)
    write_csv(destination/'paired_plant_deltas.csv',paired)
    write_json(destination/'tail_analysis.json',tail)
    write_json(destination/'success_gates.json',gates)
    write_json(destination/'evaluation_summary.json',stats)
    ev=[r for r in rows if r['split']=='evaluation']
    lookup={r['filename']:r for r in selected}
    catastrophic=set(tail['filenames'])
    # Fixed top-N diagnostic selection only, never used to optimize or exclude.
    extra={r['filename'] for r in sorted([r for r in ev if r['method']=='C'],key=lambda r:r['iou'])[:6]}
    successes={r['filename'] for r in sorted([r for r in ev if r['method']=='C' and r['filename'] not in catastrophic],key=lambda r:-r['iou'])[:4]}
    for folder,names in [('catastrophic_before_after',catastrophic),('failure_cases',extra-catastrophic),('success_cases',successes)]:
        (destination/folder).mkdir(parents=True,exist_ok=True)
        for name in sorted(names):
            r=lookup[name];masks,roi,diag,support=predictions(load_rgb(r),cfg)
            comparison_panel(r,masks,roi,support,destination/folder/(Path(name).stem+'.jpg'))
        if names:write_csv(destination/folder/'cases.csv',[r for r in ev if r['filename'] in names])
    write_json(destination/'output_hashes.json',{str(p.relative_to(destination)):sha(p) for p in destination.rglob('*') if p.is_file() and p.name!='output_hashes.json'})
    print(json.dumps(dict(summary=stats,gates=gates),indent=2),flush=True)


def verify(destination,report_name):
    if Path(report_name).name!=report_name:raise ValueError('Report must be a filename')
    if (destination/report_name).exists():raise FileExistsError('Verification output exists')
    cfg,prov=read_lock(destination);selected=assert_subset_integrity(BASE)
    checks=[]
    def check(ok,message):
        if not ok:raise AssertionError(message)
        checks.append(message)
    check(protection()==prov['protected_EXP001_files'],'All EXP-001 artifacts byte-identical')
    check((destination/'selected_images.csv').read_bytes()==(BASE/'selected_images.csv').read_bytes(),'Exact inherited trusted selection')
    check((destination/'excluded_images.csv').read_bytes()==(BASE/'excluded_images.csv').read_bytes(),'Exact inherited exclusions')
    tun={r['filename'] for r in selected if r['split']=='tuning'}
    check(set(cfg['tuning_filenames'])==tun,'Tuning inputs only; no evaluation threshold fitting')
    for method in ['B','C']:
        trials=read_csv(destination/f'{method}_tuning_trials.csv')
        check({r['filename'] for r in trials}==tun and all(r['split']=='tuning' for r in trials),method+' tuning trials contain exactly tuning images')
        winner,scores=choose_trial(trials,cfg['grid_'+method])
        check(winner==cfg[method],method+' winner independently reselected from trial IoUs')
    rows=numeric(read_csv(destination/'metrics.csv'))
    lookup={(r['filename'],r['method']):r for r in rows}
    check(len(lookup)==len(rows)==381 and {r['filename'] for r in rows}=={r['filename'] for r in selected},'Exactly 127 images x three methods')
    dd={r['filename']:r for r in read_csv(destination/'roi_diagnostics.csv')}
    for source in selected:
        rgb=load_rgb(source);gt=gt_mask(source)
        masks,roi,diag,support=predictions(rgb,cfg)
        check(np.array_equal(roi,np.asarray(Image.open(destination/'roi_masks'/source['filename']))>0),'RGB ROI reproducible: '+source['filename'])
        if support is not None:
            check(np.array_equal(support,np.asarray(Image.open(destination/'support_masks'/source['filename']))>0),'RGB support reproducible: '+source['filename'])
        domain=roi if support is None or not diag['green_periphery'] else roi & support
        d=dd[source['filename']]
        check(int(d['audit_gt_pixels_outside_roi'])==int((gt&~roi).sum()) and int(d['audit_gt_pixels_outside_C_domain'])==int((gt&~domain).sum()),'GT-clipping audit independently recomputed')
        for method,pred in masks.items():
            stored=np.asarray(Image.open(destination/'masks'/method/source['filename']))>0
            check(np.array_equal(stored,pred),'Deterministic GT-free prediction '+method+': '+source['filename'])
            r=lookup[(source['filename'],method)]
            check(all(r[k]==source[k] for k in ['tag','split','stage']),'Metric metadata matches selection')
            g=int(gt.sum());p=int(pred.sum());both=int((gt&pred).sum());union=int((gt|pred).sum())
            independent=dict(gt_pixel_area=g,predicted_pixel_area=p,intersection_pixels=both,union_pixels=union,
                iou=both/union,dice=2*both/(g+p),absolute_pixel_area_error=abs(p-g),percentage_area_error=100*abs(p-g)/g,
                signed_percentage_area_error=100*(p-g)/g,false_positive_pixels=p-both,false_negative_pixels=g-both)
            check(all(abs(r[k]-v)<1e-9 for k,v in independent.items()),'Independent pixel metric formulas')
            if method=='A':check(np.array_equal(stored,np.asarray(Image.open(BASE/'predicted_masks'/source['filename']))>0),'Exact EXP-001 baseline mask')
    # Independently verify exported aggregate values via direct numpy formulas.
    for name,key in [('global_metrics.csv',None),('plant_metrics.csv','tag'),('stage_metrics.csv','stage')]:
        for r in read_csv(destination/name):
            rr=[s for s in rows if s['split']==r['split'] and s['method']==r['method'] and (not key or s[key]==r[key])]
            errors=np.array([s['percentage_area_error'] for s in rr])
            expected=dict(mean_area_error=errors.mean(),median_area_error=np.median(errors),p90_area_error=np.percentile(errors,90),p95_area_error=np.percentile(errors,95),
                mean_iou=np.mean([s['iou'] for s in rr]),mean_dice=np.mean([s['dice'] for s in rr]),
                mean_absolute_pixel_area_error=np.mean([s['absolute_pixel_area_error'] for s in rr]),
                overestimated_images=sum(s['signed_percentage_area_error']>0 for s in rr),underestimated_images=sum(s['signed_percentage_area_error']<0 for s in rr))
            check(all(abs(float(r[k])-float(v))<1e-9 for k,v in expected.items()),'Independent summary '+name+': '+r['method'])
    stats,tail,plants,paired,gates=analysis(rows)
    check(gates==json.loads((destination/'success_gates.json').read_text()),'Success gates recomputed')
    check(tail==json.loads((destination/'tail_analysis.json').read_text()),'Tail contribution recomputed')
    check(stats==json.loads((destination/'evaluation_summary.json').read_text()),'Evaluation summary recomputed')
    for path,hashvalue in json.loads((destination/'output_hashes.json').read_text()).items():
        check(sha(destination/path)==hashvalue,'Output integrity: '+path)
    inventory=read_csv(BASE/'inventory.csv')
    for r in inventory:
        check(sha(RAW/'images'/r['filename'])==r['image_sha256'] and sha(label_path(r))==r['label_sha256'],'Raw pair intact: '+r['filename'])
    write_json(destination/report_name,dict(status='PASS',checks=len(checks),raw_pairs_checked=len(inventory),selected_images=len(selected),
        masks_reproduced=381,created_local=datetime.now().isoformat(),source_sha256=prov['source_sha256'],detail=checks))
    print(f'PASS {len(checks)} checks; 381 masks reproduced; all 1522 raw pairs and EXP-001 unchanged')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['tune','evaluate','verify'])
    ap.add_argument('--output',type=Path,default=OUT);ap.add_argument('--verification-name',default='verification.json')
    args=ap.parse_args()
    args.output=args.output.resolve()
    if not args.output.is_relative_to(ROOT.resolve()):
        raise ValueError('Experiment outputs must stay within project root')
    if args.command=='tune':tune(args.output)
    elif args.command=='evaluate':evaluate(args.output)
    else:verify(args.output,args.verification_name)


if __name__=='__main__':main()
