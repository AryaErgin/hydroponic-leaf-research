"""Seal supplemental EXP-002 outputs after independent pixel/color checks."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from .exp001 import ROOT, OUT as BASE, read_csv, sha, gt_mask, load_rgb, write_json
from .exp002 import OUT, read_lock


def check_artifacts(destination):
    if (destination/'final_artifact_manifest.json').exists():raise FileExistsError('Already sealed')
    cfg,prov=read_lock(destination);checks=[]
    def check(ok,message):
        if not ok:raise AssertionError(message)
        checks.append(message)
    for name in ['verification.json','independent_verification.json']:
        report=json.loads((destination/name).read_text())
        check(report['status']=='PASS',name+' passed')
    for relative,digest in json.loads((destination/'output_hashes.json').read_text()).items():
        check(sha(destination/relative)==digest,'Original run artifact unchanged '+relative)
    selected={r['filename']:r for r in read_csv(BASE/'selected_images.csv')}
    evaluation={name for name,r in selected.items() if r['split']=='evaluation'}
    colors=read_csv(destination/'color_error_audit.csv')
    check(len(colors)==100 and {r['filename'] for r in colors}==evaluation,'Complete color audit coverage')
    for r in colors:
        source=selected[r['filename']];rgb=load_rgb(source);gt=gt_mask(source)
        hsv=np.asarray(Image.fromarray(rgb).convert('HSV'))
        hue=hsv[:,:,0].astype(np.float32)*(180/256)
        sat,val=hsv[:,:,1],hsv[:,:,2]
        signed=rgb.astype(np.int16);exg=2*signed[:,:,1]-signed[:,:,0]-signed[:,:,2]
        a=np.asarray(Image.open(destination/'masks/A'/r['filename']))!=0
        c=np.asarray(Image.open(destination/'masks/C'/r['filename']))!=0
        false=c & ~gt;new=false & ~a
        expected=dict(C_false_positive_pixels=np.count_nonzero(false),C_new_false_positive_pixels_vs_A=np.count_nonzero(new),
            new_FP_hue_below_baseline_min25=np.count_nonzero(new & (hue<25)),
            new_FP_saturation_below60=np.count_nonzero(new & (sat<60)),
            new_FP_raw_ExG_at_most40=np.count_nonzero(new & (exg<=40)),
            new_FP_bright_V_at_least220=np.count_nonzero(new & (val>=220)))
        check(all(int(r[k])==v for k,v in expected.items()),'Independent post-hoc color pixels '+r['filename'])
    totals=read_csv(destination/'color_error_stage_totals.csv')
    check(len(totals)==4 and {r['stage'] for r in totals}=={'all','early','mid','late'},'Color stage totals complete')
    for r in totals:
        rr=[s for s in colors if r['stage']=='all' or s['stage']==r['stage']]
        check(int(r['images'])==len(rr) and all(int(r[k])==sum(int(s[k]) for s in rr) for k in colors[0] if k not in ['filename','tag','stage']), 'Independent color stage totals '+r['stage'])
    tail=json.loads((destination/'tail_analysis.json').read_text())['filenames']
    case_rows=read_csv(destination/'catastrophic_before_after/cases.csv')
    check(len(case_rows)==21 and {(r['filename'],r['method']) for r in case_rows}=={(name,m) for name in tail for m in ['A','B','C']},'All seven catastrophic comparisons contain three methods')
    check(len(list((destination/'catastrophic_before_after').glob('*.jpg')))==7,'Seven catastrophic panels saved')
    categories=read_csv(destination/'lighting_and_background_cases/case_categories.csv')
    check(len(categories)==5 and len({r['category'] for r in categories})==5,'Five explicit lighting/background categories')
    for r in categories:
        check(r['filename'] in evaluation and sha(destination/r['panel'])==r['sha256'],'Category panel provenance '+r['category'])
        with Image.open(destination/r['panel']) as im:
            check(im.width>900 and im.height>500,'Panel native legibility '+r['category'])
    if destination==OUT:
        check((ROOT/'docs/reports/LATEST_REPORT.md').read_bytes()==(destination/'summary.md').read_bytes(),'LATEST_REPORT equals final summary')
        check('Status: DONE (2026-10-03)' in (ROOT/'TASKS.md').read_text(encoding='utf-8'),'Task status recorded')
    records={str(p.relative_to(destination)):sha(p) for p in destination.rglob('*') if p.is_file()}
    sources={str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'src/verify_exp002.py',ROOT/'src/exp002_diagnostics.py',Path(__file__),ROOT/'tests/test_exp002.py',ROOT/'requirements-exp002.txt']}
    docs={str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'TASKS.md',ROOT/'experiments/EXPERIMENT_LOG.md',ROOT/'docs/PROJECT_STATUS.md',ROOT/'docs/reports/LATEST_REPORT.md']}
    write_json(destination/'final_artifact_manifest.json',dict(status='PASS',checks=len(checks),artifacts_sha256=records,supplemental_sources_sha256=sources,updated_documents_sha256=docs,detail=checks))
    print(f'PASS final artifact audit: {len(checks)} checks; {len(records)} files sealed; five categorized and seven catastrophic panels verified')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT);args=ap.parse_args();d=args.output.resolve()
    if not d.is_relative_to(ROOT.resolve()):raise ValueError('Outputs must stay within project root')
    check_artifacts(d)


if __name__=='__main__':main()
