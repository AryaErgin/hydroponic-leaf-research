"""Post-score explanation and representative panels, never method adaptation."""
from collections import Counter

import numpy as np
from PIL import Image, ImageDraw

from .exp004 import OUT, read_csv, write_csv, write_json, load_mask, tint, tile, verify_lock


def pixel_weighted(rows):
    """Pooled counters across unique frames, not target-pair duplicates."""
    keys=['gt_pixel_area','predicted_pixel_area','intersection_pixels','union_pixels','false_positive_pixels','false_negative_pixels','absolute_pixel_area_error']
    totals={k:sum(int(r[k]) for r in rows) for k in keys}
    g=totals['gt_pixel_area'];p=totals['predicted_pixel_area'];i=totals['intersection_pixels'];u=totals['union_pixels']
    return dict(n=len(rows), **{'total_'+k:v for k,v in totals.items()},
        pixel_weighted_iou=i/u if u else None,pixel_weighted_dice=2*i/(g+p) if g+p else None,
        total_absolute_area_error_over_total_GT_percent=100*totals['absolute_pixel_area_error']/g if g else None,
        total_signed_area_bias_percent=100*(p-g)/g if g else None)


def pooled_diagnostics():
    destination=OUT/'pixel_weighted_metrics.json'
    if destination.exists():raise FileExistsError('Existing pooled diagnostics')
    verify_lock(OUT);rows=read_csv(OUT/'metrics.csv')
    groups={'all_unique_frames':rows,'single_target_frames':[r for r in rows if r['target_count']=='1'],
        'shared_two_target_frames':[r for r in rows if r['target_count']=='2']}
    for date in sorted({r['date'] for r in rows}):groups[date]=[r for r in rows if r['date']==date]
    result={k:pixel_weighted(rr) for k,rr in groups.items()}
    write_json(destination,result);print(result['all_unique_frames'],flush=True)


def panel(row, destination):
    fid=row['frame_id']; rgb=np.asarray(Image.open(row['rgb_path']).convert('RGB'))
    gt=load_mask(OUT/'target_union_masks'/f'{fid}.png'); pred=load_mask(OUT/'masks'/f'{fid}.png'); roi=load_mask(OUT/'roi_masks'/f'{fid}.png')
    errors=rgb.copy(); errors[gt&pred]=[30,220,70]; errors[pred&~gt]=[250,40,30]; errors[gt&~pred]=[40,100,255]
    h,w=gt.shape; sheet=Image.new('RGB',(2*w,2*h+72),'white'); draw=ImageDraw.Draw(sheet)
    title=f"{fid} | IoU {float(row['iou']):.3f} Dice {float(row['dice']):.3f} signed area {float(row['signed_percentage_area_error']):+.2f}% | QC flag {row['qc_flag']} confidence {row['confidence']}"
    draw.text((8,5),title,fill='black')
    for x,y,im,label in [(0,24,Image.fromarray(rgb),'Full native RGB'),(w,24,tint(rgb,gt),'Supplied target union: magenta'),
        (0,h+48,tint(np.asarray(tint(rgb,roi,(40,120,255))),pred,(255,220,40)),'Frozen ROI blue / prediction yellow'),(w,h+48,Image.fromarray(errors),'TP green / FP red / FN blue')]:
        sheet.paste(im,(x,y));draw.text((x+8,y),label,fill='white',stroke_width=1,stroke_fill='black')
    sheet.save(destination,quality=94)


def analyze():
    if (OUT/'case_manifest.csv').exists(): raise FileExistsError('Existing analysis')
    verify_lock(OUT); rows=read_csv(OUT/'metrics.csv'); d=read_csv(OUT/'roi_diagnostics.csv'); dl={r['frame_id']:r for r in d}
    choices={}
    def choose(row,reason): choices.setdefault(row['frame_id'],[]).append(reason)
    for date in sorted({r['date'] for r in rows}):
        rr=[r for r in rows if r['date']==date]
        choose(max(rr,key=lambda r:float(r['iou'])),'highest_IoU_on_date_not_necessarily_success')
        choose(min(rr,key=lambda r:float(r['iou'])),'lowest_IoU_on_date')
    for r in sorted(rows,key=lambda r:float(r['signed_percentage_area_error']),reverse=True)[:3]: choose(r,'largest_overestimate')
    for date in sorted({r['date'] for r in rows}):
        rr=[r for r in rows if r['date']==date and r['qc_flag']=='False' and float(r['signed_percentage_area_error'])<-50]
        if rr: choose(min(rr,key=lambda r:float(r['signed_percentage_area_error'])),'confident_catastrophic_underestimate_on_date')
    for r in rows:
        if dl[r['frame_id']].get('card_valid')=='True':choose(r,'card_support_accepted_in_field')
    for name in ['success_cases','failure_cases']:(OUT/name).mkdir()
    manifest=[]; lookup={r['frame_id']:r for r in rows}
    for fid,reasons in choices.items():
        r=lookup[fid]; folder='success_cases' if float(r['iou'])>=.75 and float(r['percentage_area_error'])<=25 else 'failure_cases'
        path=OUT/folder/f'{fid}.jpg';panel(r,path)
        manifest.append(dict(frame_id=fid,path=str(path),selection_reason=';'.join(reasons),iou=r['iou'],signed_percentage_area_error=r['signed_percentage_area_error'],qc_flag=r['qc_flag']))
    write_csv(OUT/'case_manifest.csv',manifest)
    # A compact overview of every prediction, preserving metrics for inspection.
    (OUT/'result_review').mkdir()
    for start in range(0,len(rows),8):
        sheet=Image.new('RGB',(1152,8*244),'white');draw=ImageDraw.Draw(sheet)
        for j,r in enumerate(rows[start:start+8]):
            y=j*244;fid=r['frame_id'];rgb=np.asarray(Image.open(r['rgb_path']).convert('RGB'))
            gt=load_mask(OUT/'target_union_masks'/f'{fid}.png');pred=load_mask(OUT/'masks'/f'{fid}.png');roi=load_mask(OUT/'roi_masks'/f'{fid}.png')
            draw.text((3,y+2),f"{fid} IoU={float(r['iou']):.3f} signed={float(r['signed_percentage_area_error']):+.1f}% QC={r['qc_flag']}",fill='black')
            errors=rgb.copy();errors[gt&pred]=[30,220,70];errors[pred&~gt]=[250,40,30];errors[gt&~pred]=[40,100,255]
            for k,im in enumerate([tint(rgb,gt),tint(np.asarray(tint(rgb,roi,(40,120,255))),pred,(255,220,40)),Image.fromarray(errors)]):sheet.paste(tile(im),(k*384,y+24))
        sheet.save(OUT/'result_review'/f'page_{start//8+1:02d}.jpg',quality=94)
    bad=[r for r in rows if float(r['signed_percentage_area_error'])<-50 or float(r['signed_percentage_area_error'])>100]
    summary=dict(localization_sources=dict(Counter(r['localization_source'] for r in d)),
        GT_outside_ROI_over5_frames=sum(float(r['gt_outside_roi_percent'])>5 for r in rows),
        catastrophic_frames_with_GT_outside_ROI_over50=sum(float(r['gt_outside_roi_percent'])>50 for r in bad),
        median_GT_rejected_by_A_percent=float(np.median([float(r['gt_rejected_by_A_percent']) for r in rows])),
        median_GT_outside_ROI_percent=float(np.median([float(r['gt_outside_roi_percent']) for r in rows])),
        frames_IoU_at_least_075=sum(float(r['iou'])>=.75 for r in rows),
        zero_overlap_frames=sum(float(r['iou'])==0 for r in rows),empty_prediction_frames=sum(int(r['predicted_pixel_area'])==0 for r in rows),
        catastrophic_accepted_confidences=dict(Counter(r['confidence'] for r in bad if r['qc_flag']=='False')),
        note='Post-hoc overlap diagnostics overlap; not independent causal ablations; no inference changes; GT not used at inference')
    write_json(OUT/'transfer_diagnostics.json',summary);print(summary,flush=True)


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--pixel-weighted',action='store_true');args=ap.parse_args()
    if args.pixel_weighted:pooled_diagnostics()
    else:analyze()
