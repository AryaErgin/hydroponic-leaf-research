"""EXP-003 development benchmark; no unbiased confirmation claim."""
import argparse
import itertools
import json
import platform
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,__version__ as pillow_version
from .exp001 import ROOT,RAW,OUT as BASE,read_csv,write_csv,write_json,sha,load_rgb,gt_mask,label_path,assert_subset_integrity
from .exp002 import choose_trial,summarize,save_mask
from .lettuce_cv import segment,pixel_metrics
from .roi_cv import infer_roi
from .robust_roi import robust_segment,quality_check,measurement

OLD2=ROOT/'experiments/EXP-002'
OUT=ROOT/'experiments/EXP-003'
SOURCES=[ROOT/'src'/n for n in ['exp003.py','robust_roi.py','confirmation_audit.py','lettuce_cv.py','roi_cv.py','exp001.py','exp002.py']]


def failure_detection(rows,truth_key=None):
    tp=fp=fn=tn=0
    for r in rows:
        if truth_key:
            value=r[truth_key];failure=value if isinstance(value,bool) else str(value)=='True'
        else:failure=float(r['signed_percentage_area_error'])>100 or float(r['signed_percentage_area_error'])<-50
        flag=r['qc_flag'] if isinstance(r['qc_flag'],bool) else str(r['qc_flag'])=='True'
        tp+=int(flag and failure);fp+=int(flag and not failure)
        fn+=int(not flag and failure);tn+=int(not flag and not failure)
    precision=tp/(tp+fp) if tp+fp else None
    recall=tp/(tp+fn) if tp+fn else None
    return dict(images=len(rows),true_positive=tp,false_positive=fp,false_negative=fn,true_negative=tn,
                precision=precision,recall=recall,f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                flagged_images=tp+fp,actual_failures=tp+fn,coverage=(tn+fn)/len(rows) if rows else None)


def protected_artifacts():
    return {str(p.relative_to(ROOT)):sha(p) for folder in [BASE,OLD2] for p in folder.rglob('*') if p.is_file()}


def safe_destination(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('Outputs must stay within project root')
    return path


def tune(destination):
    destination=safe_destination(destination)
    if any((destination/n).exists() for n in ['config.json','provenance.json','selected_images.csv','tuning_trials.csv','code_snapshot']):
        raise FileExistsError('Existing run; choose a new directory')
    destination.mkdir(parents=True,exist_ok=True)
    selected=assert_subset_integrity(BASE)
    source_audit=OUT/'source_audit/source_overlap.json'
    if not source_audit.exists():raise ValueError('Complete source identity audit first')
    with (destination/'EXP-002_LATEST_REPORT.md').open('xb') as f:f.write((ROOT/'docs/reports/LATEST_REPORT.md').read_bytes())
    with (destination/'inherited_EXP001_selection.csv').open('xb') as f:f.write((BASE/'selected_images.csv').read_bytes())
    with (destination/'excluded_images.csv').open('xb') as f:f.write((BASE/'excluded_images.csv').read_bytes())
    selected=[dict(r,source_split=r['split'],split='development' if r['split']=='evaluation' else 'tuning',
        benchmark_role='exposed_development' if r['split']=='evaluation' else 'parameter_tuning') for r in selected]
    write_csv(destination/'selected_images.csv',selected)
    sources=destination/'code_snapshot';sources.mkdir()
    for p in SOURCES:
        with (sources/p.name).open('xb') as f:f.write(p.read_bytes())
    baseline=json.loads((BASE/'config.json').read_text())['parameters']
    old_roi=json.loads((OLD2/'config.json').read_text())['B']
    grid=[dict(background_quantile=q,contrast_margin=c,envelope_margin=m) for q,c,m in itertools.product([.90,.95],[.03,.06],[.02,.04])]
    trials=[]
    tuning=[r for r in selected if r['split']=='tuning']
    for j,r in enumerate(tuning):
        rgb,gt=load_rgb(r),gt_mask(r)
        for i,cfg in enumerate(grid):
            pred,roi,support,diag=robust_segment(rgb,baseline,cfg)
            metric=pixel_metrics(gt,pred)
            trials.append(dict(filename=r['filename'],tag=r['tag'],split='tuning',config_index=i,
                gt_outside_roi_percent=100*int((gt & ~roi).sum())/int(gt.sum()),**metric))
        print(f'C tuning {j+1}/{len(tuning)}',flush=True)
    winner,scores=choose_trial(trials,grid)
    write_csv(destination/'tuning_trials.csv',trials);write_csv(destination/'tuning_scores.csv',scores)
    config=dict(A=baseline,B=old_roi,C=winner,grid=grid,qc_threshold=.70,
        objective='maximum tuning plant-macro IoU; first grid exact tie',locked_before_development=True,
        tuning_filenames=[r['filename'] for r in tuning],tuning_tags=sorted({r['tag'] for r in tuning}),
        benchmark_status='DEVELOPMENT_ONLY_NOT_UNBIASED_FINAL_TEST',independent_confirmation_images=0)
    write_json(destination/'config.json',config)
    write_json(destination/'provenance.json',dict(config_sha256=sha(destination/'config.json'),
        source_sha256={p.name:sha(p) for p in SOURCES},protocol_sha256=sha(OUT/'plan.md'),
        original_selection_sha256=sha(BASE/'selected_images.csv'),selected_sha256=sha(destination/'selected_images.csv'),
        source_audit_sha256=sha(source_audit),protected_artifacts_sha256=protected_artifacts(),
        python=platform.python_version(),numpy=np.__version__,pillow=pillow_version,unit='pixels',
        benchmark_status='DEVELOPMENT_ONLY',confirmation_status='NO_ADEQUATE_INDEPENDENT_HOLDOUT'))
    print('Configuration locked before development:',winner,flush=True)


def read_lock(destination):
    cfg=json.loads((destination/'config.json').read_text());prov=json.loads((destination/'provenance.json').read_text())
    if sha(destination/'config.json')!=prov['config_sha256']:raise ValueError('Configuration drift')
    if any(sha(p)!=prov['source_sha256'][p.name] for p in SOURCES):raise ValueError('Source drift after tuning')
    if sha(destination/'selected_images.csv')!=prov['selected_sha256'] or sha(BASE/'selected_images.csv')!=prov['original_selection_sha256']:
        raise ValueError('Selection drift')
    if sha(OUT/'plan.md')!=prov['protocol_sha256']:raise ValueError('Protocol drift')
    if sha(OUT/'source_audit/source_overlap.json')!=prov['source_audit_sha256']:raise ValueError('Source identity audit drift')
    return cfg,prov


def infer(rgb,cfg):
    a=segment(rgb,cfg['A']);b_roi,b_diag,_=infer_roi(rgb,cfg['B'])
    c,c_roi,card,c_diag=robust_segment(rgb,cfg['A'],cfg['C'])
    masks=dict(A=a,B=a & b_roi,C=c)
    qc={m:quality_check(mask,c,c_diag,cfg['qc_threshold']) for m,mask in masks.items()}
    return masks,dict(A=np.ones(a.shape,bool),B=b_roi,C=c_roi),card,c_diag,qc


def group_summaries(rows,key=None,accepted=False):
    groups=[]
    for split in ['tuning','development']:
        for method in ['A','B','C']:
            base=[r for r in rows if r['split']==split and r['method']==method]
            for value in sorted({r[key] for r in base}) if key else ['all']:
                rr=[r for r in base if not key or r[key]==value]
                full_count=len(rr)
                if accepted:rr=[r for r in rr if not r['qc_flag']]
                if not rr:continue
                groups.append(dict(split=split,method=method,**({key:value} if key else {}),
                    score_scope='QC_ACCEPTED_ONLY' if accepted else 'ALL_RAW_PREDICTIONS',
                    full_group_images=full_count,**summarize(rr)))
    return groups


def outcome(rows,diagnostics):
    dev=[r for r in rows if r['split']=='development'];by={m:[r for r in dev if r['method']==m] for m in ['A','B','C']}
    totals={m:summarize(rr) for m,rr in by.items()}
    b_old_under={r['filename'] for r in by['B'] if r['signed_percentage_area_error']<-50}
    a_tail={r['filename'] for r in by['A'] if r['signed_percentage_area_error']>100}
    c_old=[r for r in by['C'] if r['filename'] in b_old_under]
    a_fp=sum(r['false_positive_pixels'] for r in by['A'] if r['filename'] in a_tail)
    c_fp=sum(r['false_positive_pixels'] for r in by['C'] if r['filename'] in a_tail)
    plants=group_summaries(rows,'tag')
    paired=[]
    for method in ['B','C']:
        for tag in sorted({r['tag'] for r in dev}):
            a=next(r for r in plants if r['split']=='development' and r['method']=='A' and r['tag']==tag)
            p=next(r for r in plants if r['split']=='development' and r['method']==method and r['tag']==tag)
            paired.append(dict(method=method,tag=tag,images=a['images'],delta_iou_vs_A=p['mean_iou']-a['mean_iou'],
                delta_area_error_vs_A=p['mean_area_error']-a['mean_area_error']))
    bplants=[r for r in plants if r['split']=='development' and r['method']=='B']
    cplants=[r for r in plants if r['split']=='development' and r['method']=='C']
    c_iou_improved=sum(c['mean_iou']>next(b['mean_iou'] for b in bplants if b['tag']==c['tag']) for c in cplants)
    maxclip=max(d['C_gt_outside_roi_percent'] for d in diagnostics if d['split']=='development')
    checks=dict(original_six_underestimates_resolved=len(b_old_under)==6 and all(r['signed_percentage_area_error']>=-50 for r in c_old),
        no_new_catastrophic_underestimates=totals['C']['catastrophic_underestimation']==0,
        catastrophic_overestimates_at_most_B=totals['C']['catastrophic_false_positives']<=2,
        original_turf_FP_reduction=1-c_fp/a_fp>=.80,
        median_preserved=totals['C']['median_area_error']<=totals['B']['median_area_error']+2,
        mean_iou_preserved=totals['C']['mean_iou']>=totals['B']['mean_iou']-.02,
        plant_macro_area_improved=np.mean([r['mean_area_error'] for r in cplants])<np.mean([r['mean_area_error'] for r in bplants]),
        plant_iou_broad_improvement=c_iou_improved/len(cplants)>=.60,
        no_material_geometric_clipping=maxclip<=5)
    gate=dict(status='PASS_DEVELOPMENT_CRITERIA' if all(checks.values()) else 'FAIL_DEVELOPMENT_CRITERIA',checks={k:bool(v) for k,v in checks.items()},
        max_gt_outside_C_roi_percent=maxclip,original_turf_FP_reduction=1-c_fp/a_fp,plants_iou_improved_vs_B=c_iou_improved,
        B_plant_macro_area_error=float(np.mean([r['mean_area_error'] for r in bplants])),C_plant_macro_area_error=float(np.mean([r['mean_area_error'] for r in cplants])),
        independent_confirmation='UNAVAILABLE',benchmark='DEVELOPMENT_NOT_UNBIASED_FINAL_TEST')
    return totals,plants,paired,gate


def panel(row,masks,rois,card,qc,path):
    rgb=load_rgb(row);gt=gt_mask(row);h,w=gt.shape;scale=420/max(h,w);size=(round(w*scale),round(h*scale))
    thumb=Image.fromarray(rgb).resize(size);canvas=Image.new('RGB',(size[0]*3,size[1]*2+135),'white');draw=ImageDraw.Draw(canvas)
    layers=[None,gt,rois['C'],masks['A'],masks['B'],masks['C']]
    titles=['RGB','GT: diagnostic reference','C canopy ROI / RGB card','A EXP-001','B EXP-002 ROI','C robust ROI / overhang']
    for i,mask in enumerate(layers):
        x=(i%3)*size[0];y=23+(i//3)*(size[1]+59);im=thumb.copy()
        if mask is not None:
            ss=np.asarray(Image.fromarray(mask.astype(np.uint8)*255).resize(size,Image.Resampling.NEAREST))>0
            pixels=np.asarray(im).copy().astype(float);pixels[ss]=.5*pixels[ss]+.5*np.array([255,70,180])
            if i==2 and card is not None:
                support=np.asarray(Image.fromarray(card.astype(np.uint8)*255).resize(size,Image.Resampling.NEAREST))>0
                pixels[support]=.5*pixels[support]+.5*np.array([40,170,255])
            im=Image.fromarray(pixels.astype(np.uint8))
        canvas.paste(im,(x,y));draw.text((x+4,y-18),titles[i],fill='black')
        if i>=3:
            m='ABC'[i-3];metric=pixel_metrics(gt,mask)
            draw.text((x+4,y+size[1]+3),f"IoU {metric['iou']:.3f}; signed area {metric['signed_percentage_area_error']:+.1f}%",fill='black')
            draw.text((x+4,y+size[1]+18),f"QC {'WITHHOLD' if qc[m]['qc_flag'] else 'accept'}; score {qc[m]['confidence']:.2f}",fill='black')
    draw.text((4,canvas.height-18),row['filename']+'; DEVELOPMENT',fill='black')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:canvas.save(f,format='JPEG',quality=90)


def evaluate(destination):
    destination=safe_destination(destination)
    if (destination/'metrics.csv').exists() or (destination/'masks').exists():raise FileExistsError('Existing development run')
    cfg,prov=read_lock(destination);selected=read_csv(destination/'selected_images.csv');rows=[];diagnostics=[]
    old1={r['filename']:r for r in read_csv(BASE/'metrics.csv')}
    old2={r['filename']:r for r in read_csv(OLD2/'metrics.csv') if r['method']=='B'}
    for i,r in enumerate(selected):
        masks,rois,card,diag,qc=infer(load_rgb(r),cfg);gt=gt_mask(r)
        for m,pred in masks.items():
            metrics=pixel_metrics(gt,pred)
            if m in ['A','B']:
                old=old1[r['filename']] if m=='A' else old2[r['filename']]
                if not all(abs(float(old[k])-v)<1e-10 for k,v in metrics.items()):raise AssertionError('Baseline metrics drift '+m)
                prior=BASE/'predicted_masks'/r['filename'] if m=='A' else OLD2/'masks/B'/r['filename']
                if not np.array_equal(np.asarray(Image.open(prior))>0,pred):raise AssertionError('Baseline mask drift '+m)
            clipping=100*int((gt & ~rois[m]).sum())/int(gt.sum())
            rows.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],source_split=r['source_split'],stage=r['stage'],method=m,
                **metrics,**qc[m],**measurement(pred,qc[m]),gt_outside_roi_percent=clipping,material_roi_clipping=clipping>5,
                benchmark_role=r['benchmark_role']))
            save_mask(destination/'masks'/m/r['filename'],pred)
            save_mask(destination/'roi_masks'/m/r['filename'],rois[m])
        if card is not None:save_mask(destination/'card_masks'/r['filename'],card)
        diagnostics.append(dict(filename=r['filename'],tag=r['tag'],split=r['split'],stage=r['stage'],**diag,
            B_gt_outside_roi_percent=100*int((gt & ~rois['B']).sum())/int(gt.sum()),
            C_gt_outside_roi_percent=100*int((gt & ~rois['C']).sum())/int(gt.sum())))
        if i%10==0:print(f'Development/tuning scored {i+1}/{len(selected)}',flush=True)
    write_csv(destination/'metrics.csv',rows)
    diagkeys=list(dict.fromkeys(k for r in diagnostics for k in r))
    write_csv(destination/'roi_diagnostics.csv',[{k:r.get(k,'') for k in diagkeys} for r in diagnostics])
    write_csv(destination/'global_metrics.csv',group_summaries(rows))
    write_csv(destination/'stage_metrics.csv',group_summaries(rows,'stage'))
    accepted=group_summaries(rows,accepted=True)
    if accepted:write_csv(destination/'QC_accepted_only_metrics.csv',accepted)
    stats,plants,paired,gate=outcome(rows,diagnostics)
    write_csv(destination/'plant_metrics.csv',plants);write_csv(destination/'paired_plant_deltas.csv',paired)
    write_json(destination/'development_summary.json',stats);write_json(destination/'development_outcome.json',gate)
    qcresults={split:{m:dict(area_failure=failure_detection([r for r in rows if r['split']==split and r['method']==m]),
        ROI_clipping=failure_detection([r for r in rows if r['split']==split and r['method']==m],'material_roi_clipping')) for m in ['A','B','C']} for split in ['tuning','development']}
    write_json(destination/'QC_failure_detection.json',qcresults)
    dev=[r for r in rows if r['split']=='development'];lookup={r['filename']:r for r in selected}
    original_under={r['filename'] for r in dev if r['method']=='B' and r['signed_percentage_area_error']<-50}
    original_over={r['filename'] for r in dev if r['method']=='B' and r['signed_percentage_area_error']>100}
    original_A_tail={r['filename'] for r in dev if r['method']=='A' and r['signed_percentage_area_error']>100}
    known=original_under | original_over | original_A_tail
    write_csv(destination/'known_failure_investigation.csv',[dict(r,original_case='B_under50' if r['filename'] in original_under else 'B_over100' if r['filename'] in original_over else 'A_turf_tail') for r in dev if r['filename'] in known])
    remaining={r['filename'] for r in dev if r['method']=='C' and (r['signed_percentage_area_error']>100 or r['signed_percentage_area_error']<-50 or r['material_roi_clipping'])}
    worst={r['filename'] for r in sorted([r for r in dev if r['method']=='C'],key=lambda r:r['iou'])[:6]}
    for folder,names in [('known_failure_cases',known),('remaining_failures',remaining-known),('additional_diagnostics',worst-known)]:
        (destination/folder).mkdir(parents=True,exist_ok=True)
        for name in sorted(names):
            r=lookup[name];masks,rois,card,diag,qc=infer(load_rgb(r),cfg)
            panel(r,masks,rois,card,qc,destination/folder/(Path(name).stem+'.jpg'))
        if names:write_csv(destination/folder/'cases.csv',[r for r in dev if r['filename'] in names])
    write_json(destination/'output_hashes.json',{str(p.relative_to(destination)):sha(p) for p in destination.rglob('*') if p.is_file()})
    print(json.dumps(dict(summary=stats,outcome=gate,QC=qcresults['development']),indent=2),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['tune','evaluate']);ap.add_argument('--output',type=Path,default=OUT)
    args=ap.parse_args();d=safe_destination(args.output)
    if args.command=='tune':tune(d)
    else:evaluate(d)


if __name__=='__main__':main()
