"""Post-hoc RGB/GT diagnostic analysis; never changes predictions/settings."""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image
from .exp001 import ROOT, OUT as BASE, read_csv, write_csv, write_json, load_rgb, gt_mask, sha
from .exp002 import OUT, read_lock, predictions, comparison_panel
from .lettuce_cv import color_features
from .verify_exp002 import direct_summary


def diagnose(destination):
    if (destination/'color_error_audit.csv').exists():raise FileExistsError('Diagnostics already exist')
    cfg,_=read_lock(destination)
    selected=read_csv(BASE/'selected_images.csv');lookup={r['filename']:r for r in selected}
    metrics=read_csv(destination/'metrics.csv')
    rows=[]
    for r in selected:
        if r['split']!='evaluation':continue
        rgb=load_rgb(r);gt=gt_mask(r);h,s,v,exg=color_features(rgb)
        a=np.asarray(Image.open(destination/'masks/A'/r['filename']))>0
        c=np.asarray(Image.open(destination/'masks/C'/r['filename']))>0
        fp=c & ~gt;new_fp=fp & ~a
        rows.append(dict(filename=r['filename'],tag=r['tag'],stage=r['stage'],
            C_false_positive_pixels=int(fp.sum()),C_new_false_positive_pixels_vs_A=int(new_fp.sum()),
            new_FP_hue_below_baseline_min25=int((new_fp & (h<25)).sum()),
            new_FP_saturation_below60=int((new_fp & (s<60)).sum()),
            new_FP_raw_ExG_at_most40=int((new_fp & (exg<=40)).sum()),
            new_FP_bright_V_at_least220=int((new_fp & (v>=220)).sum())))
    write_csv(destination/'color_error_audit.csv',rows)
    totals=[]
    for stage in ['all','early','mid','late']:
        rr=[r for r in rows if stage=='all' or r['stage']==stage]
        totals.append(dict(stage=stage,images=len(rr),**{k:sum(r[k] for r in rr) for k in rows[0] if k not in ['filename','tag','stage']}))
    write_csv(destination/'color_error_stage_totals.csv',totals)
    categories=[
        ('small_canopy_yellow_substrate_grid','oak_rgb_50GA2_2024-08-31T17_43_26Z.png','C includes yellowish growing medium around the small canopy; green grid is also present. H>=10 admits yellower pixels than A H>=25.'),
        ('small_canopy_grid_substrate_second_plant','oak_rgb_75GA4_2024-09-3T18_5_10Z.png','Second plant example of new substrate/background overestimation under C.'),
        ('bright_tissue_wrong_ROI','oak_rgb_75GB5_2024-09-9T17_52_23Z.png','Bright lettuce on normal dark grid; RGB dark detector locks onto exposed pot crescent and clips most canopy.'),
        ('shadow_and_bright_canopy_wrong_ROI','oak_rgb_75RB4_2024-09-13T8_54_21Z.png','Mixed shaded upper leaves and bright lower canopy; dark-object ROI misses much of the canopy.'),
        ('normal_grid_bright_tissue_color_tradeoff','oak_rgb_50GA2_2024-09-7T8_30_32Z.png','Bright/yellowish tissue on dark grid; compare local recovery with extra substrate foreground.'),
    ]
    records=[]
    for category,name,note in categories:
        r=lookup[name];masks,roi,diag,support=predictions(load_rgb(r),cfg)
        path=destination/'lighting_and_background_cases'/(category+'.jpg')
        comparison_panel(r,masks,roi,support,path)
        records.append(dict(category=category,filename=name,tag=r['tag'],stage=r['stage'],note=note,panel=str(path.relative_to(destination)),sha256=sha(path)))
    write_csv(destination/'lighting_and_background_cases'/'case_categories.csv',records)
    original_tail={r['filename'] for r in metrics if r['method']=='A' and r['split']=='evaluation' and float(r['signed_percentage_area_error'])>100}
    write_json(destination/'non_turf_diagnostic.json',{m:direct_summary([r for r in metrics if r['method']==m and r['split']=='evaluation' and r['filename'] not in original_tail]) for m in ['A','B','C']})
    # This is a diagnostic stratification, never an alternate headline benchmark.
    print('Saved post-hoc color breakdown and five explicitly categorized RGB/GT/A/B/C panels')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT);args=ap.parse_args()
    d=args.output.resolve()
    if not d.is_relative_to(ROOT.resolve()):raise ValueError('Outputs must stay inside project root')
    diagnose(d)


if __name__=='__main__':main()
