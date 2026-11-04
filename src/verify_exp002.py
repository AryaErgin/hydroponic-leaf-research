"""Independent EXP-002 numerical audit; no producer aggregation functions."""
import argparse
import json
import math
import statistics as st
from pathlib import Path
import numpy as np
from .exp001 import ROOT, OUT as BASE, read_csv, sha, load_rgb, gt_mask, write_json
from .exp002 import OUT, read_lock
from .lettuce_cv import segment
from .roi_cv import infer_roi, adaptive_segment


def quantile(values, q):
    a=sorted(values); index=(len(a)-1)*q; low=math.floor(index); high=math.ceil(index)
    return a[low]+(a[high]-a[low])*(index-low)


def direct_metrics(gt,pred):
    g=int(np.count_nonzero(gt));p=int(np.count_nonzero(pred));hit=int(np.count_nonzero(gt & pred))
    return dict(iou=hit/(g+p-hit),dice=2*hit/(g+p),gt_pixel_area=g,predicted_pixel_area=p,
        intersection_pixels=hit,union_pixels=g+p-hit,false_positive_pixels=p-hit,false_negative_pixels=g-hit,
        absolute_pixel_area_error=abs(p-g),percentage_area_error=abs(100*(p/g-1)),signed_percentage_area_error=100*(p/g-1))


def direct_summary(rows):
    errors=[float(r['percentage_area_error']) for r in rows]
    signed=[float(r['signed_percentage_area_error']) for r in rows]
    over=[v for v in signed if v>0];under=[-v for v in signed if v<0]
    def mean(k):return st.mean(float(r[k]) for r in rows)
    return dict(images=len(rows),plants=len({r['tag'] for r in rows}),mean_iou=mean('iou'),
        median_iou=st.median(float(r['iou']) for r in rows),mean_dice=mean('dice'),
        mean_gt_pixel_area=mean('gt_pixel_area'),mean_predicted_pixel_area=mean('predicted_pixel_area'),
        mean_absolute_pixel_area_error=mean('absolute_pixel_area_error'),
        median_absolute_pixel_area_error=st.median(float(r['absolute_pixel_area_error']) for r in rows),
        mean_area_error=st.mean(errors),median_area_error=st.median(errors),p90_area_error=quantile(errors,.90),p95_area_error=quantile(errors,.95),
        mean_signed_area_error=st.mean(signed),overestimated_images=len(over),underestimated_images=len(under),
        exact_area_images=sum(v==0 for v in signed),mean_overestimation=st.mean(over) if over else 0,
        mean_underestimation=st.mean(under) if under else 0,catastrophic_false_positives=sum(v>100 for v in signed),
        catastrophic_underestimation=sum(v < -50 for v in signed),
        total_false_positive_pixels=sum(int(float(r['false_positive_pixels'])) for r in rows),
        total_false_negative_pixels=sum(int(float(r['false_negative_pixels'])) for r in rows))


def audit(destination, report_name='independent_verification.json'):
    if Path(report_name).name!=report_name:raise ValueError('Report must be a filename')
    if (destination/report_name).exists():raise FileExistsError('Audit output already exists')
    cfg,prov=read_lock(destination)
    checks=[]
    def check(ok,description):
        if not ok:raise AssertionError(description)
        checks.append(description)
    def close(a,b):return abs(float(a)-float(b))<1e-8
    def compare(saved,expected,description):
        check(set(saved)==set(expected),description+' keys')
        check(all(close(saved[k],v) for k,v in expected.items()),description+' values')
    selected=read_csv(BASE/'selected_images.csv')
    tuning=[r for r in selected if r['split']=='tuning']
    eval_tags={r['tag'] for r in selected if r['split']=='evaluation'}
    check(not eval_tags & {r['tag'] for r in tuning},'Plant tag separation independent check')
    score_rows=read_csv(destination/'tuning_scores.csv')
    trial_maps={m:{(r['filename'],int(r['config_index'])):r for r in read_csv(destination/f'{m}_tuning_trials.csv')} for m in ['B','C']}
    expected_trial_count=sum(len(tuning)*len(cfg['grid_'+m]) for m in ['B','C'])
    recomputed={m:[] for m in ['B','C']}
    for m in ['B','C']:
        expected={(r['filename'],i) for r in tuning for i in range(len(cfg['grid_'+m]))}
        check(set(trial_maps[m])==expected, m+' complete tuning trial identities')
        check(len(read_csv(destination/f'{m}_tuning_trials.csv'))==len(expected),m+' no duplicate trial rows')
    for j,r in enumerate(tuning):
        rgb,gt=load_rgb(r),gt_mask(r);a=segment(rgb,cfg['A'])
        for i,b in enumerate(cfg['grid_B']):
            roi,_,_=infer_roi(rgb,b)
            metric=direct_metrics(gt,a & roi)
            saved=trial_maps['B'][(r['filename'],i)]
            check(all(close(saved[k],v) for k,v in metric.items()),'Independent B tuning pixels '+r['filename']+f' config {i}')
            check(saved['tag']==r['tag'] and saved['split']=='tuning','B tuning metadata')
            recomputed['B'].append(dict(tag=r['tag'],index=i,iou=metric['iou']))
        roi,diag,support=infer_roi(rgb,cfg['B'])
        for i,c in enumerate(cfg['grid_C']):
            metric=direct_metrics(gt,adaptive_segment(rgb,roi,support,diag,c))
            saved=trial_maps['C'][(r['filename'],i)]
            check(all(close(saved[k],v) for k,v in metric.items()),'Independent C tuning pixels '+r['filename']+f' config {i}')
            check(saved['tag']==r['tag'] and saved['split']=='tuning','C tuning metadata')
            recomputed['C'].append(dict(tag=r['tag'],index=i,iou=metric['iou']))
        print(f'Independent tuning audit {j+1}/{len(tuning)}',flush=True)
    for m in ['B','C']:
        scores=[]
        for i,c in enumerate(cfg['grid_'+m]):
            rr=[r for r in recomputed[m] if r['index']==i]
            value=st.mean(st.mean(r['iou'] for r in rr if r['tag']==tag) for tag in {r['tag'] for r in rr})
            saved=[r for r in score_rows if r['method']==m and int(r['config_index'])==i]
            check(len(saved)==1 and close(saved[0]['plant_macro_iou'],value),m+f' independently calculated plant-macro trial {i}')
            check(all(close(saved[0][k],v) for k,v in c.items()),m+' tuning score parameters match declared grid')
            scores.append(value)
        winner=max(range(len(scores)),key=lambda i:scores[i])
        check(cfg[m]==cfg['grid_'+m][winner],m+' locked configuration equals independently measured winner')
    rows=read_csv(destination/'metrics.csv')
    lookup={(r['filename'],r['method']):r for r in rows}
    check(len(rows)==len(lookup)==381 and set(lookup)=={(r['filename'],m) for r in selected for m in ['A','B','C']},'Complete unique per-image metric coverage')
    for r in selected:
        check(all(all(lookup[(r['filename'],m)][k]==r[k] for k in ['tag','stage','split']) for m in ['A','B','C']),'Per-image plant/stage/split metadata')
    tables={}
    for name,key in [('global_metrics.csv',None),('plant_metrics.csv','tag'),('stage_metrics.csv','stage')]:
        saved=read_csv(destination/name)
        actual={}
        for split in ['tuning','evaluation']:
            for m in ['A','B','C']:
                rr=[r for r in rows if r['split']==split and r['method']==m]
                for value in ({r[key] for r in rr} if key else ['all']):
                    group=[r for r in rr if not key or r[key]==value]
                    actual[(split,m,value)]=direct_summary(group)
        check(len(saved)==len(actual) and {(r['split'],r['method'],r[key] if key else 'all') for r in saved}==set(actual),'Complete group coverage '+name)
        for r in saved:
            ident=(r['split'],r['method'],r[key] if key else 'all')
            values={k:v for k,v in r.items() if k not in ['split','method',key]}
            compare(values,actual[ident],'Independent full summary '+str(ident))
        tables[name]=actual
    global_stats={m:tables['global_metrics.csv'][('evaluation',m,'all')] for m in ['A','B','C']}
    saved_stats=json.loads((destination/'evaluation_summary.json').read_text())
    check(set(saved_stats)==set(global_stats),'Evaluation summary method identities')
    for m in global_stats:compare(saved_stats[m],global_stats[m],'Evaluation JSON '+m)
    tail=[r for r in rows if r['split']=='evaluation' and r['method']=='A' and float(r['signed_percentage_area_error'])>100]
    names={r['filename'] for r in tail}
    remaining=[r for r in rows if r['split']=='evaluation' and r['method']=='A' and r['filename'] not in names]
    saved_tail=json.loads((destination/'tail_analysis.json').read_text())
    check(saved_tail['filenames']==sorted(names) and saved_tail['baseline_catastrophic_images']==len(tail),'Baseline catastrophic case identities')
    total=st.fsum(float(r['percentage_area_error']) for r in rows if r['split']=='evaluation' and r['method']=='A')
    tail_sum=st.fsum(float(r['percentage_area_error']) for r in tail)
    check(close(saved_tail['tail_mean_area_error'],tail_sum/len(tail)) and close(saved_tail['tail_contribution_to_mean'],tail_sum/100)
        and close(saved_tail['share_of_total_percentage_error'],tail_sum/total),'Independent tail decomposition')
    compare(saved_tail['remaining_93_summary'],direct_summary(remaining),'Independent non-tail summary')
    paired=read_csv(destination/'paired_plant_deltas.csv')
    check(len(paired)==68 and {(r['method'],r['tag']) for r in paired}=={(m,t) for m in ['B','C'] for t in eval_tags},'Complete unique paired plant coverage')
    gates=json.loads((destination/'success_gates.json').read_text())
    check(set(gates)=={'B','C'},'Gate method identities')
    for m in ['B','C']:
        deltas=[];a_areas=[];p_areas=[]
        for tag in eval_tags:
            a=tables['plant_metrics.csv'][('evaluation','A',tag)];p=tables['plant_metrics.csv'][('evaluation',m,tag)]
            expected=dict(images=a['images'],delta_iou=p['mean_iou']-a['mean_iou'],delta_mean_area_error=p['mean_area_error']-a['mean_area_error'],
                baseline_iou=a['mean_iou'],candidate_iou=p['mean_iou'],baseline_mean_area_error=a['mean_area_error'],candidate_mean_area_error=p['mean_area_error'])
            saved=next(r for r in paired if r['method']==m and r['tag']==tag)
            compare({k:v for k,v in saved.items() if k not in ['method','tag']},expected,'Independent paired plant '+m+' '+tag)
            deltas.append(expected);a_areas.append(a['mean_area_error']);p_areas.append(p['mean_area_error'])
        a_fp=sum(int(r['false_positive_pixels']) for r in tail)
        p_fp=sum(int(r['false_positive_pixels']) for r in rows if r['split']=='evaluation' and r['method']==m and r['filename'] in names)
        a=global_stats['A'];p=global_stats[m]
        measures=dict(catastrophic_count=p['catastrophic_false_positives'],catastrophic_count_reduction=1-p['catastrophic_false_positives']/len(tail),
            baseline_tail_false_positive_reduction=1-p_fp/a_fp,median_area_error_delta=p['median_area_error']-a['median_area_error'],mean_iou_delta=p['mean_iou']-a['mean_iou'],
            baseline_plant_macro_area_error=st.mean(a_areas),candidate_plant_macro_area_error=st.mean(p_areas),plant_macro_area_error_reduction=1-st.mean(p_areas)/st.mean(a_areas),
            plants_iou_improved=sum(r['delta_iou']>0 for r in deltas),plants_iou_degraded=sum(r['delta_iou']<0 for r in deltas),
            plants_area_improved=sum(r['delta_mean_area_error']<0 for r in deltas),plants_area_degraded=sum(r['delta_mean_area_error']>0 for r in deltas))
        compare(gates[m]['measures'],measures,'Independent gate measures '+m)
        expected_checks=dict(catastrophic_count=p['catastrophic_false_positives']<=.30*len(tail),tail_false_positives=p_fp<=.20*a_fp,
            median_area_error=p['median_area_error']<=a['median_area_error']+2,mean_iou=p['mean_iou']>=a['mean_iou']-.02,
            plant_macro_area=st.mean(p_areas)<=.80*st.mean(a_areas),plants_iou=measures['plants_iou_improved']>=.60*len(eval_tags))
        check(gates[m]['checks']==expected_checks and gates[m]['status']==('PASS' if all(expected_checks.values()) else 'FAIL'),'Independent all-required success verdict '+m)
    write_json(destination/report_name,dict(status='PASS',checks=len(checks),recomputed_tuning_masks=expected_trial_count,
        audited_metric_rows=len(rows),paired_plant_rows=len(paired),audit_source_sha256=sha(Path(__file__)),detail=checks))
    print(f'PASS independent audit: {len(checks)} checks, {expected_trial_count} tuning masks, all summaries/plant deltas/gates')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT);ap.add_argument('--report-name',default='independent_verification.json')
    args=ap.parse_args();destination=args.output.resolve()
    if not destination.is_relative_to(ROOT.resolve()):raise ValueError('Outputs must stay inside project root')
    audit(destination,args.report_name)


if __name__=='__main__':main()
