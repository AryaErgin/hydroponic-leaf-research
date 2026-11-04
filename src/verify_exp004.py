"""Independent saved-native-mask recomputation and frozen-source replay.

No evaluation metric/aggregation functions are imported from exp004.
python -m src.verify_exp004 --reproduce
"""
import argparse
import csv
import hashlib
import importlib
import json
import sys
import types
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'experiments/EXP-004'


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))


def mask(path):
    with Image.open(path) as im: return np.asarray(im).copy()>0


def counts(gt,pred):
    g=np.count_nonzero(gt); p=np.count_nonzero(pred); inter=np.count_nonzero(np.logical_and(gt,pred)); union=np.count_nonzero(np.logical_or(gt,pred))
    return dict(iou=inter/union,dice=2*inter/(g+p),gt_pixel_area=int(g),predicted_pixel_area=int(p),
        absolute_pixel_area_error=int(abs(p-g)),percentage_area_error=100*abs(p-g)/g,signed_percentage_area_error=100*(p-g)/g,
        intersection_pixels=int(inter),union_pixels=int(union),false_positive_pixels=int(p-inter),false_negative_pixels=int(g-inter))


def summarize(rows):
    e=np.array([float(r['percentage_area_error']) for r in rows]); s=np.array([float(r['signed_percentage_area_error']) for r in rows]); q=np.array([str(r['qc_flag'])=='True' for r in rows])
    bad=(s>100)|(s<-50); tp=int((q&bad).sum()); fp=int((q&~bad).sum()); fn=int((~q&bad).sum()); tn=int((~q&~bad).sum())
    return dict(n=len(rows),mean_iou=float(np.mean([float(r['iou']) for r in rows])), mean_dice=float(np.mean([float(r['dice']) for r in rows])),
        mean_area_error_percent=float(e.mean()),median_area_error_percent=float(np.median(e)),p90_area_error_percent=float(np.percentile(e,90)),p95_area_error_percent=float(np.percentile(e,95)),
        mean_absolute_pixel_area_error=float(np.mean([float(r['absolute_pixel_area_error']) for r in rows])),mean_signed_area_error_percent=float(s.mean()),
        overestimates=int((s>0).sum()),underestimates=int((s<0).sum()),exact_area=int((s==0).sum()),catastrophic_over=int((s>100).sum()),catastrophic_under=int((s<-50).sum()),
        absolute_error_over50=int((e>50).sum()),qc_flagged=int(q.sum()),qc_coverage=float((~q).mean()),qc_tp=tp,qc_fp=fp,qc_fn=fn,qc_tn=tn,
        qc_precision=tp/(tp+fp) if tp+fp else None,qc_recall=tp/(tp+fn) if tp+fn else None)


def verify(reproduce=False):
    checks=0
    def equal(actual,expected,context):
        nonlocal checks
        if isinstance(expected,bool): good=(str(actual)==str(expected))
        elif isinstance(expected,(float,int)): good=abs(float(actual)-expected)<1e-9
        else: good=actual==expected
        if not good: raise AssertionError((context,actual,expected))
        checks+=1
    lock=json.loads((OUT/'screen_lock.json').read_text())
    for p,expected in lock['files'].items():
        with open(p,'rb') as f: equal(hashlib.file_digest(f,'sha256').hexdigest(),expected,p)
    pairs=read(OUT/'pair_inventory.csv'); selected=read(OUT/'selected_images.csv'); rejected=read(OUT/'excluded_images.csv'); frames=read(OUT/'frame_inventory.csv')
    equal(len(pairs),128,'mask inventory'); equal(len(frames),112,'RGB inventory')
    equal(set(r['pair_id'] for r in selected+rejected),set(r['pair_id'] for r in pairs),'complete screen')
    mapping=json.loads((ROOT/'docs/dataset_inspection/new_public_datasets_audit/AgRibot_resolved_pairing.json').read_text())
    independent_pairs={(str(ROOT/'data/raw/AgRibot'/Path(rgb).relative_to('AgRibotLeafyVeg-main')),str(ROOT/'data/raw/AgRibot'/Path(m).relative_to('AgRibotLeafyVeg-main'))) for rgb,mm in mapping.items() for m in mm}
    equal({(r['rgb_path'],r['mask_path']) for r in pairs},independent_pairs,'independent DATA-002 tag map')
    grouped=defaultdict(list)
    for r in selected: grouped[r['frame_id']].append(r)
    frame_metrics=read(OUT/'metrics.csv'); pair_metrics=read(OUT/'pair_metrics.csv'); lookup={r['frame_id']:r for r in frame_metrics}; pairlookup={r['pair_id']:r for r in pair_metrics}
    equal(len(pairlookup),len(selected),'scored pairs'); equal(len(lookup),sum(rr[0]['primary_frame_eligible']=='True' for rr in grouped.values()),'complete scored frames')
    equal(len(list((OUT/'masks').glob('*.png'))),len(grouped),'one mask per unique input')
    cfg=json.loads((OUT/'config.json').read_text()); frozen=None
    if reproduce:
        pkg=types.ModuleType('_exp004_frozen');pkg.__path__=[str(OUT/'frozen_source')];sys.modules[pkg.__name__]=pkg
        frozen=importlib.import_module(pkg.__name__+'.robust_roi')
    for i,(fid,rr) in enumerate(sorted(grouped.items())):
        pred=mask(OUT/'masks'/f'{fid}.png'); roi=mask(OUT/'roi_masks'/f'{fid}.png')
        refs=[mask(Path(r['mask_path'])) for r in rr]
        for r,gt in zip(rr,refs):
            equal(pred.shape,gt.shape,fid+' canvas')
            for k,v in counts(gt,pred).items(): equal(pairlookup[r['pair_id']][k],v,r['pair_id']+k)
        if fid in lookup:
            gt=refs[0].copy()
            for gt2 in refs[1:]: gt|=gt2
            equal(np.array_equal(gt,mask(OUT/'target_union_masks'/f'{fid}.png')),True,fid+' union')
            row=lookup[fid]
            for k,v in counts(gt,pred).items(): equal(row[k],v,fid+k)
            signed=100*(int(pred.sum())-int(gt.sum()))/int(gt.sum())
            equal(row['catastrophic_over'],signed>100,fid+' over100');equal(row['catastrophic_under'],signed<-50,fid+' under50')
            equal(row['gt_outside_roi_percent'],100*int((gt&~roi).sum())/int(gt.sum()),fid+' outside ROI')
            equal(row['raw_pixel_area'],int(pred.sum()),fid+' raw area')
            equal(row['reported_pixel_area'],'' if row['qc_flag']=='True' else str(int(pred.sum())),fid+' reportability')
        if frozen:
            rgb=np.asarray(Image.open(rr[0]['rgb_path']).convert('RGB'))
            p2,r2,card,diag=frozen.robust_segment(rgb,cfg['A'],cfg['C']);q2=frozen.quality_check(p2,p2,diag,cfg['qc_threshold'])
            equal(np.array_equal(pred,p2),True,fid+' exact frozen mask');equal(np.array_equal(roi,r2),True,fid+' exact frozen ROI')
            for k,v in q2.items(): equal(pairlookup[rr[0]['pair_id']][k],v,fid+' QC '+k)
        if i%16==0: print(f'Independent native verification {i+1}/{len(grouped)}',flush=True)
    results=json.loads((OUT/'results.json').read_text())
    partitions={'primary_complete_frames':frame_metrics,'single_target_frames':[r for r in frame_metrics if r['target_count']=='1'],
        'shared_two_target_frames':[r for r in frame_metrics if r['target_count']=='2'],'paired_target_diagnostics_NOT_independent':pair_metrics,
        'QC_accepted_only':[r for r in frame_metrics if r['qc_flag']=='False']}
    for name,rr in partitions.items():
        expected=summarize(rr) if rr else dict(n=0)
        for k,v in expected.items():equal(results[name][k],v,name+k)
    for r in read(OUT/'group_metrics.csv'):
        rr=[row for row in frame_metrics if row[r['group_by']]==r['group']]
        for k,v in summarize(rr).items(): equal(None if r[k]=='' else r[k],v,'group '+r['group']+k)
    oak=[r for r in read(ROOT/'experiments/EXP-003/metrics.csv') if r['method']=='C' and r['split']=='development']
    equal(len(oak),100,'OAK exposed DEVELOPMENT size')
    for r in oak:
        gt=mask(ROOT/'experiments/EXP-001/gt_masks'/r['filename']);pred=mask(ROOT/'experiments/EXP-003/masks/C'/r['filename'])
        for k,v in counts(gt,pred).items():equal(r[k],v,'OAK '+r['filename']+k)
    comparison=dict(OAK1_exposed_development=summarize(oak),AgRibot_external_complete_frames=summarize(frame_metrics),
        AgRibot_external_single_target_frames=summarize(partitions['single_target_frames']),OAK1_distinct_development_tags=len(set(r['tag'] for r in oak)),
        warning='OAK development exposed; external different cultivar/acquisition/target layout; no controlled causal comparison; no final hydroponic confirmation')
    result=dict(status='PASS',checks=checks,native_frames=len(grouped),pair_metrics=len(pair_metrics),OAK_development_masks=100,
        frozen_native_predictions_reproduced=len(grouped) if reproduce else 0,protected_files_verified=len(lock['files']),comparison=comparison)
    output=OUT/('verification_reproduced.json' if reproduce else 'verification.json')
    if output.exists(): raise FileExistsError('Do not overwrite previous verification')
    output.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--reproduce',action='store_true');args=ap.parse_args();verify(args.reproduce)
