"""Independent EXP-003 numerical/provenance audit, including QC and trials."""
import argparse
import csv
import hashlib
import io
import json
import re
import statistics as st
import zipfile
from pathlib import Path
import numpy as np
from PIL import Image
from .exp001 import ROOT,RAW,OUT as BASE,read_csv,sha,gt_mask,load_rgb,label_path,write_json
from .exp003 import OUT,OLD2,read_lock,infer,protected_artifacts,safe_destination
from .robust_roi import robust_segment
from .verify_exp002 import direct_metrics,direct_summary


def independent_detection(rows,clipping=False):
    truth=[float(r['gt_outside_roi_percent'])>5 if clipping else
        float(r['signed_percentage_area_error'])>100 or float(r['signed_percentage_area_error'])<-50 for r in rows]
    flags=[r['qc_flag'] is True or r['qc_flag']=='True' for r in rows]
    tp=sum(t and f for t,f in zip(truth,flags));fp=sum(not t and f for t,f in zip(truth,flags))
    fn=sum(t and not f for t,f in zip(truth,flags));tn=sum(not t and not f for t,f in zip(truth,flags))
    return dict(images=len(rows),true_positive=tp,false_positive=fp,false_negative=fn,true_negative=tn,
        precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
        f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,flagged_images=tp+fp,actual_failures=tp+fn,
        coverage=(tn+fn)/len(rows) if rows else None)


def trial_identity_matches(trials,selected):
    lookup={r['filename']:r for r in selected}
    return all(r['filename'] in lookup and r['tag']==lookup[r['filename']]['tag']
        and r['split']==lookup[r['filename']]['split']=='tuning' for r in trials)


def verify(destination,report_name='verification.json',tuning=True):
    if Path(report_name).name!=report_name:raise ValueError('Report must be a filename')
    if (destination/report_name).exists():raise FileExistsError('Verification record exists')
    cfg,prov=read_lock(destination);checks=[]
    def check(ok,description):
        if not ok:raise AssertionError(description)
        checks.append(description)
    def close(a,b):
        if a is None or b is None:return a is None and b is None
        return abs(float(a)-float(b))<1e-8
    def compare(saved,expected,description):
        check(set(saved)==set(expected),description+' keys')
        check(all(close(saved[k],v) for k,v in expected.items()),description+' values')
    check(protected_artifacts()==prov['protected_artifacts_sha256'],'All EXP-001/002 artifacts byte-identical')
    original=read_csv(BASE/'selected_images.csv');selected=read_csv(destination/'selected_images.csv')
    old={r['filename']:r for r in original};sel={r['filename']:r for r in selected}
    check(len(selected)==127 and set(sel)==set(old),'Exact 127 inherited images; no performance exclusions')
    for r in selected:
        prior=old[r['filename']]
        check(all(r[k]==v for k,v in prior.items() if k!='split'),'Original tag/stage/review/hash metadata preserved')
        check(r['source_split']==prior['split'] and r['split']==('development' if prior['split']=='evaluation' else 'tuning'),'Legacy evaluation relabeled DEVELOPMENT')
    check({r['tag'] for r in selected if r['split']=='tuning'}.isdisjoint({r['tag'] for r in selected if r['split']=='development'}),'Plant-separated tuning/development')
    check(cfg['qc_threshold']==.70 and cfg['independent_confirmation_images']==0 and cfg['benchmark_status']=='DEVELOPMENT_ONLY_NOT_UNBIASED_FINAL_TEST','Fixed QC threshold, no fabricated confirmation')
    metrics=read_csv(destination/'metrics.csv');lookup={(r['filename'],r['method']):r for r in metrics}
    check(len(metrics)==len(lookup)==381 and set(lookup)=={(name,m) for name in sel for m in ['A','B','C']},'Complete unique 127x3 metric rows')
    saved_diag={r['filename']:r for r in read_csv(destination/'roi_diagnostics.csv')}
    for j,r in enumerate(selected):
        masks,rois,card,diag,qc=infer(load_rgb(r),cfg);gt=gt_mask(r)
        if card is not None:check(np.array_equal(card,np.asarray(Image.open(destination/'card_masks'/r['filename']))>0),'RGB card reproducible')
        for method,pred in masks.items():
            row=lookup[(r['filename'],method)];stored=np.asarray(Image.open(destination/'masks'/method/r['filename']))>0
            check(np.array_equal(stored,pred),'GT-free native prediction deterministic '+method+' '+r['filename'])
            check(np.array_equal(rois[method],np.asarray(Image.open(destination/'roi_masks'/method/r['filename']))>0),'RGB ROI deterministic '+method)
            expected=direct_metrics(gt,stored)
            check(all(close(row[k],v) for k,v in expected.items()),'Independent pixel metrics '+method)
            check(all(row[k]==r[k] for k in ['tag','stage','split','source_split','benchmark_role']),'Correct development metadata '+method)
            if method in ['A','B']:
                path=BASE/'predicted_masks'/r['filename'] if method=='A' else OLD2/'masks/B'/r['filename']
                check(np.array_equal(stored,np.asarray(Image.open(path))>0),'Exact frozen '+method+' baseline')
            reference=masks['C'];hit=int((pred & reference).sum());p=int(pred.sum());ref=int(reference.sum())
            retention=hit/ref if ref else 0.;purity=hit/p if p else 0.
            score=min(diag['localization_confidence'],retention,purity);flag=score<.70
            check(close(row['confidence'],score) and (row['qc_flag']=='True')==flag and close(row['reference_retention'],retention) and close(row['reference_purity'],purity),'Independent GT-free confidence '+method)
            check(int(row['raw_pixel_area'])==p and (row['reported_pixel_area']=='' if flag else int(row['reported_pixel_area'])==p),'QC withholds flagged area before reporting '+method)
            clip=100*int((gt & ~rois[method]).sum())/int(gt.sum())
            check(close(row['gt_outside_roi_percent'],clip) and (row['material_roi_clipping']=='True')==(clip>5),'Independent post-hoc clipping audit '+method)
        check(close(saved_diag[r['filename']]['C_gt_outside_roi_percent'],lookup[(r['filename'],'C')]['gt_outside_roi_percent']),'Diagnostic clipping agrees')
        if j%20==0:print(f'Native prediction audit {j+1}/127',flush=True)
    tables={}
    for file,key,accepted in [('global_metrics.csv',None,False),('plant_metrics.csv','tag',False),('stage_metrics.csv','stage',False),('QC_accepted_only_metrics.csv',None,True)]:
        expected={}
        for split in ['tuning','development']:
            for method in ['A','B','C']:
                rr=[r for r in metrics if r['split']==split and r['method']==method]
                for value in sorted({r[key] for r in rr}) if key else ['all']:
                    group=[r for r in rr if not key or r[key]==value];n=len(group)
                    if accepted:group=[r for r in group if r['qc_flag']=='False']
                    if group:expected[(split,method,value)]=dict(full_group_images=n,**direct_summary(group))
        saved=read_csv(destination/file) if (destination/file).exists() else []
        check(len(saved)==len(expected) and {(r['split'],r['method'],r[key] if key else 'all') for r in saved}==set(expected),'Complete summary groups '+file)
        for r in saved:
            ident=(r['split'],r['method'],r[key] if key else 'all')
            fields={k:v for k,v in r.items() if k not in ['split','method','score_scope',key]}
            compare(fields,expected[ident],'All independent summary fields '+file+' '+str(ident))
            check(r['score_scope']==('QC_ACCEPTED_ONLY' if accepted else 'ALL_RAW_PREDICTIONS'),'Accepted-only vs headline scope explicit')
        tables[file]=expected
    qcs=json.loads((destination/'QC_failure_detection.json').read_text())
    for split in ['tuning','development']:
        check(set(qcs[split])=={'A','B','C'},'QC method identities')
        for method in ['A','B','C']:
            rr=[r for r in metrics if r['split']==split and r['method']==method]
            compare(qcs[split][method]['area_failure'],independent_detection(rr),'Independent area failure detection '+split+' '+method)
            compare(qcs[split][method]['ROI_clipping'],independent_detection(rr,True),'Independent clipping detection '+split+' '+method)
    trials=read_csv(destination/'tuning_trials.csv');trial_map={(r['filename'],int(r['config_index'])):r for r in trials}
    tun=[r for r in selected if r['split']=='tuning'];tun_names={r['filename'] for r in tun}
    check(len(trials)==len(trial_map)==216 and set(trial_map)=={(name,i) for name in tun_names for i in range(8)},'All 216 tuning-only trial identities')
    check(trial_identity_matches(trials,tun),'Every tuning trial tag/split matches frozen selected metadata')
    check(set(cfg['tuning_filenames'])==tun_names and all(r['split']=='tuning' for r in trials),'No development threshold optimization')
    if tuning:
        for j,r in enumerate(tun):
            rgb,gt=load_rgb(r),gt_mask(r)
            for i,c in enumerate(cfg['grid']):
                pred,roi,card,diag=robust_segment(rgb,cfg['A'],c);expected=direct_metrics(gt,pred);saved=trial_map[(r['filename'],i)]
                check(all(close(saved[k],v) for k,v in expected.items()),'Independently recomputed tuning mask '+r['filename']+f' trial {i}')
                check(close(saved['gt_outside_roi_percent'],100*int((gt & ~roi).sum())/int(gt.sum())),'Tuning clipping audit')
            print(f'Independent tuning trial audit {j+1}/27',flush=True)
    scores=[];saved_scores=read_csv(destination/'tuning_scores.csv')
    for i,c in enumerate(cfg['grid']):
        rr=[r for r in trials if int(r['config_index'])==i]
        value=st.mean(st.mean(float(r['iou']) for r in rr if r['tag']==tag) for tag in {r['tag'] for r in rr})
        matches=[r for r in saved_scores if int(r['config_index'])==i]
        check(len(matches)==1 and close(matches[0]['plant_macro_iou'],value) and all(close(matches[0][k],v) for k,v in c.items()),'Independent plant-macro trial objective '+str(i))
        scores.append(value)
    winner=max(range(8),key=lambda i:scores[i]);check(cfg['C']==cfg['grid'][winner],'Locked C is tuning-only objective winner')
    dev=[r for r in metrics if r['split']=='development'];tags={r['tag'] for r in dev}
    by={m:[r for r in dev if r['method']==m] for m in ['A','B','C']}
    summary={m:direct_summary(rr) for m,rr in by.items()}
    saved_summary=json.loads((destination/'development_summary.json').read_text())
    for m in summary:compare(saved_summary[m],summary[m],'Development summary JSON '+m)
    paired=read_csv(destination/'paired_plant_deltas.csv')
    check(len(paired)==68 and {(r['method'],r['tag']) for r in paired}=={(m,t) for m in ['B','C'] for t in tags},'Complete paired-plant comparisons')
    plants=tables['plant_metrics.csv'];c_improved=0;b_area=[];c_area=[]
    for tag in tags:
        a=plants[('development','A',tag)];b=plants[('development','B',tag)];c=plants[('development','C',tag)]
        c_improved+=int(c['mean_iou']>b['mean_iou']);b_area.append(b['mean_area_error']);c_area.append(c['mean_area_error'])
        for m,s in [('B',b),('C',c)]:
            saved=next(r for r in paired if r['method']==m and r['tag']==tag)
            compare({k:v for k,v in saved.items() if k not in ['method','tag']},dict(images=a['images'],delta_iou_vs_A=s['mean_iou']-a['mean_iou'],delta_area_error_vs_A=s['mean_area_error']-a['mean_area_error']),'Independent paired plant '+m+' '+tag)
    old_under={r['filename'] for r in by['B'] if float(r['signed_percentage_area_error'])<-50}
    old_tail={r['filename'] for r in by['A'] if float(r['signed_percentage_area_error'])>100}
    afp=sum(int(r['false_positive_pixels']) for r in by['A'] if r['filename'] in old_tail)
    cfp=sum(int(r['false_positive_pixels']) for r in by['C'] if r['filename'] in old_tail)
    maxclip=max(float(r['gt_outside_roi_percent']) for r in by['C'])
    expected_checks=dict(original_six_underestimates_resolved=len(old_under)==6 and all(float(r['signed_percentage_area_error'])>=-50 for r in by['C'] if r['filename'] in old_under),
        no_new_catastrophic_underestimates=summary['C']['catastrophic_underestimation']==0,
        catastrophic_overestimates_at_most_B=summary['C']['catastrophic_false_positives']<=2,
        original_turf_FP_reduction=cfp<=.20*afp,median_preserved=summary['C']['median_area_error']<=summary['B']['median_area_error']+2,
        mean_iou_preserved=summary['C']['mean_iou']>=summary['B']['mean_iou']-.02,
        plant_macro_area_improved=st.mean(c_area)<st.mean(b_area),plant_iou_broad_improvement=c_improved>=.60*len(tags),no_material_geometric_clipping=maxclip<=5)
    gate=json.loads((destination/'development_outcome.json').read_text())
    check(gate['checks']==expected_checks and gate['status']==('PASS_DEVELOPMENT_CRITERIA' if all(expected_checks.values()) else 'FAIL_DEVELOPMENT_CRITERIA'),'Every development outcome independently computed')
    check(close(gate['max_gt_outside_C_roi_percent'],maxclip) and close(gate['original_turf_FP_reduction'],1-cfp/afp)
        and gate['plants_iou_improved_vs_B']==c_improved and close(gate['B_plant_macro_area_error'],st.mean(b_area)) and close(gate['C_plant_macro_area_error'],st.mean(c_area)),'Outcome quantitative evidence independent')
    for relative,digest in json.loads((destination/'output_hashes.json').read_text()).items():
        check(sha(destination/relative)==digest,'Frozen output integrity '+relative)
    inventory=read_csv(BASE/'inventory.csv')
    for r in inventory:check(sha(RAW/'images'/r['filename'])==r['image_sha256'] and sha(label_path(r))==r['label_sha256'],'Raw pair unchanged '+r['filename'])
    source=OUT/'source_audit';audit=json.loads((source/'source_overlap.json').read_text())
    check(audit['status']=='NO_ADEQUATE_INDEPENDENT_HOLDOUT' and audit['confirmation_images_scored']==0,'Independent confirmation unavailable, zero scored')
    review_names={r['filename'] for r in read_csv(BASE/'review_decisions.csv')}
    exposed_ids={r['tag'] for r in inventory if r['filename'] in review_names}
    check(audit['previously_screened_exposed_tags']==sorted(exposed_ids),'Exposed physical IDs reconstructed from prior actual review records')
    with zipfile.ZipFile(ROOT/'IoT4Lettuce.zip') as z:
        central=[dict(name=i.filename,crc=i.CRC,bytes=i.file_size,compressed_bytes=i.compress_size,offset=i.header_offset) for i in z.infolist()]
        digest=hashlib.sha256(json.dumps(central,separators=(',',':')).encode()).hexdigest()
        check(digest==audit['central_directory_sha256'],'Source archive central directory unchanged')
        for r in read_csv(source/'archive_evidence.csv')+read_csv(source/'OAKD_sidecar_hashes.csv'):
            raw=z.read(r['archive_entry']);check(hashlib.sha256(raw).hexdigest()==r['sha256'] and z.getinfo(r['archive_entry']).CRC==int(r['crc']),'Source archive metadata/image/sidecar evidence '+r['archive_entry'])
        for name,report in audit['sources'].items():
            if name=='RumexLeaves':continue
            filename='full_db.csv' if name=='13_Manual_measurements' else 'metadata.csv'
            rr=list(csv.DictReader(io.StringIO(z.read(f'IoT4Lettuce/{name}/{filename}').decode('utf-8-sig'))))
            ids=set();unknown=0
            for r in rr:
                values={r.get('tag') or r.get('sample')} if r.get('tag') or r.get('sample') else set(re.findall(r'((?:50|75)[A-Z]{2}[1-5])',r.get('folder','')))
                ids.update(values);unknown+=int(not values)
            overlap=ids & set(audit['previously_screened_exposed_tags'])
            check(report['known_ids']==sorted(ids) and report['unknown_identity_rows']==unknown and report['overlap_ids']==sorted(overlap)
                and report['overlap_count']==(None if unknown else len(overlap)),'Independent physical-ID overlap '+name)
        # Reconstruct OAK-D pairing and identity evidence, not just its JSON totals.
        infos=z.namelist()
        def capture(n):return re.sub(r'^oakd?_rgb_','rgb_',Path(n).stem)
        images=[n for n in infos if n.startswith('IoT4Lettuce/02_OAK-D_S2/images/') and n.endswith('.png')]
        labels=[n for n in infos if n.startswith('IoT4Lettuce/02_OAK-D_S2/segmentation/labels/') and n.endswith('.txt')]
        sidecars=[n for n in infos if n.startswith('IoT4Lettuce/02_OAK-D_S2/metadata/') and n.endswith('.txt')]
        metadata=list(csv.DictReader(io.StringIO(z.read('IoT4Lettuce/02_OAK-D_S2/metadata.csv').decode('utf-8-sig'))))
        pairs=read_csv(source/'OAKD_pairs.csv');pairmap={r['filename']:r for r in pairs};counts={};empty=0
        check(len(pairmap)==len(pairs)==len(metadata)==441,'All OAK-D metadata pairing rows unique and complete')
        for r in metadata:
            im=[n for n in images if capture(n)==capture(r['filename'])];lab=[n for n in labels if capture(n)==capture(r['filename'])]
            check(len(im)==len(lab)==1,'Unambiguous canonical OAK-D image/label capture')
            pair=pairmap[r['filename']];n=sum(bool(s.strip()) for s in z.read(lab[0]).decode('utf-8-sig').splitlines())
            empty+=int(n==0);counts[str(n)]=counts.get(str(n),0)+1
            check(pair['image_entry']==im[0] and pair['image_exists']=='True' and pair['label_entry']==lab[0] and int(pair['polygons'])==n
                and pair['physical_plant_ids']=='UNKNOWN' and pair['confirmation_eligible']=='False','OAK-D pairing/count/unknown-identity independently reconstructed')
        report=audit['sources']['02_OAK-D_S2']
        ids=set()
        for name in sidecars:ids.update(re.findall(r'((?:50|75)[A-Z]{2}[1-5])',z.read(name).decode('utf-8-sig')))
        check(len(images)==report['images']==441 and len(labels)==report['label_files']==441 and empty==report['empty_labels']==6
            and counts==report['polygon_counts'] and len(sidecars)==report['sidecar_files_inspected']==441 and report['sidecar_tag_ids_found']==sorted(ids),
            'OAK-D totals and all 441 sidecar identity checks independent')
    report=dict(status='PASS',checks=len(checks),native_masks_reproduced=381,tuning_masks_recomputed=216 if tuning else 0,
        raw_pairs_checked=1522,benchmark='DEVELOPMENT_ONLY',confirmation_images_scored=0,audit_source_sha256=sha(Path(__file__)),detail=checks)
    write_json(destination/report_name,report)
    print(f"PASS: {len(checks)} independent checks;381 masks;{report['tuning_masks_recomputed']} tuning trials; all raw/EXP-001/002 unchanged")


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT);ap.add_argument('--report-name',default='verification.json');ap.add_argument('--skip-tuning',action='store_true')
    args=ap.parse_args();verify(safe_destination(args.output),args.report_name,not args.skip_tuning)


if __name__=='__main__':main()
