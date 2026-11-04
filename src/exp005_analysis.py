"""Descriptive repeated-position growth diagnostics, not segmentation validation."""
import math
import numpy as np
import csv
import io
import json
import zipfile
from PIL import Image,ImageDraw
from .exp005 import ROOT,OUT,ARCHIVE,grid,put_csv,put_json,valid_mass_join,font


def average_ranks(values):
    values=np.asarray(values,float);order=np.argsort(values,kind='stable');ranks=np.empty(len(values),float)
    i=0
    while i<len(values):
        j=i+1
        while j<len(values) and values[order[j]]==values[order[i]]:j+=1
        ranks[order[i:j]]=(i+1+j)/2;i=j
    return ranks


def correlations(x,y):
    pairs=[(float(a),float(b)) for a,b in zip(x,y) if a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b))]
    def pearson(a,b):
        a=np.asarray(a,float);b=np.asarray(b,float);a=a-a.mean();b=b-b.mean()
        denom=float(np.linalg.norm(a)*np.linalg.norm(b))
        return float(np.clip(np.dot(a,b)/denom,-1,1)) if denom else None
    if len(pairs)<2:return dict(n=len(pairs),pearson=None,spearman=None)
    a,b=zip(*pairs)
    return dict(n=len(pairs),pearson=pearson(a,b),spearman=pearson(average_ranks(a),average_ranks(b)))


def temporal_changes(rows):
    changes=[]
    for prev,cur in zip(rows,rows[1:]):
        gap=cur['dat']-prev['dat'];a,b=prev['area'],cur['area']
        valid=gap==1 and a is not None and b is not None
        percent=100*(b-a)/a if valid and a>0 else None
        changes.append(dict(previous_dat=prev['dat'],dat=cur['dat'],gap_days=gap,
            delta_pixels=b-a if valid else None,signed_change_percent=percent,
            abrupt_over_50pct=percent is not None and abs(percent)>50,
            negative_over_25pct=percent is not None and percent< -25,
            zero_to_positive=bool(valid and a==0 and b>0)))
    return changes


def quantiles(values):
    values=[v for v in values if v is not None]
    return dict(n=len(values),mean=float(np.mean(values)) if values else None,
        median=float(np.median(values)) if values else None,
        p90=float(np.percentile(values,90)) if values else None,
        p95=float(np.percentile(values,95)) if values else None)


def describe_curve(rows):
    changes=temporal_changes([dict(dat=r['dat'],area=r['raw_pixel_area'] if r['presence']=='present' else None) for r in rows])
    usable=[r for r in rows if r['presence']=='present'];deltas=[r['delta_pixels'] for r in changes if r['delta_pixels'] is not None]
    span=max(r['raw_pixel_area'] for r in usable)-min(r['raw_pixel_area'] for r in usable)
    return dict(observations=len(usable),qc_failures=sum(r['qc_flag'] for r in usable),
        raw_zero=sum(r['raw_pixel_area']==0 for r in usable),
        valid_adjacent_day_changes=len(deltas),negative_steps=sum(d<0 for d in deltas),
        abrupt_over_50pct=sum(r['abrupt_over_50pct'] for r in changes),
        negative_over_25pct=sum(r['negative_over_25pct'] for r in changes),
        zero_to_positive=sum(r['zero_to_positive'] for r in changes),
        total_variation_pixels=sum(abs(d) for d in deltas),
        normalized_total_variation=sum(abs(d) for d in deltas)/span if span else None,
        first_raw_area=usable[0]['raw_pixel_area'],last_raw_area=usable[-1]['raw_pixel_area'],
        first_to_last_ratio=usable[-1]['raw_pixel_area']/usable[0]['raw_pixel_area'] if usable[0]['raw_pixel_area'] else None,
        absolute_percent_change=quantiles([abs(r['signed_change_percent']) if r['signed_change_percent'] is not None else None for r in changes]))


def analysis():
    rows=json.loads((OUT/'predictions.json').read_text())
    masses=json.loads((OUT/'biomass_source.json').read_text())
    screens={(int(r['serial']),r['position']):r for r in csv.DictReader((OUT/'association_screen.csv').open(encoding='utf-8',newline=''))}
    positions=[c['position'] for c in grid()]
    joined=[];excluded=[]
    for r in rows:
        if not r['biomass_flagged']:continue
        mass=valid_mass_join(r,r['position'],r['presence'],masses)
        if mass:
            s=screens[r['serial'],r['position']]
            joined.append(r | mass | dict(lower_risk_diagnostic=s['lower_risk_diagnostic']=='True',
                acquisition_warnings=s['acquisition_warnings'],matched_at='stored_day_and_documented_position_not_exact_clock'))
        else:excluded.append(dict(serial=r['serial'],date_as_stored=r['date_as_stored'],dat=r['dat'],
            position=r['position'],reason=screens[r['serial'],r['position']]['reason']))
    for m in masses:
        if m['fresh_mass_g'] is None:excluded.append(m | dict(serial=None,reason='harvest_mass_blank_no_flagged_image'))
    put_csv(OUT/'biomass_matched.csv',joined);put_json(OUT/'biomass_matched.json',joined)
    put_csv(OUT/'association_exclusions.csv',excluded)
    daily={p:sorted([r for r in rows if r['position']==p and r['biomass_flagged']],key=lambda r:r['dat']) for p in positions}
    changes=[];qc_changes=[];within={};curve=[];corr=[];capture=[]
    for p in positions:
        cr=describe_curve(daily[p]);curve.append(dict(position=p,**{k:v for k,v in cr.items() if k!='absolute_percent_change'},
            **{'abs_change_'+k:v for k,v in cr['absolute_percent_change'].items()}))
        changes.extend(dict(position=p,**c) for c in temporal_changes([dict(dat=r['dat'],area=r['raw_pixel_area'] if r['presence']=='present' else None) for r in daily[p]]))
        qc_changes.extend(dict(position=p,**c) for c in temporal_changes([dict(dat=r['dat'],area=r['raw_pixel_area'] if r['presence']=='present' and not r['qc_flag'] else None) for r in daily[p]]))
        allp=sorted([r for r in rows if r['position']==p],key=lambda r:r['serial'])
        within[p]=[]
        for dat in range(1,32):
            rs=[r for r in allp if r['dat']==dat];a=[r['raw_pixel_area'] for r in rs]
            within[p].append(dict(position=p,dat=dat,date_as_stored=rs[0]['date_as_stored'],frames=len(a),
                min_area=min(a),max_area=max(a),median_area=float(np.median(a)),qc_failures=sum(r['qc_flag'] for r in rs),
                diagnostic_presence_not_verified=True))
        for a,b in zip(allp,allp[1:]):
            delta=b['raw_pixel_area']-a['raw_pixel_area'];pct=100*delta/a['raw_pixel_area'] if a['raw_pixel_area'] else None
            capture.append(dict(position=p,previous_serial=a['serial'],serial=b['serial'],dat=b['dat'],
                same_stored_day=a['dat']==b['dat'],delta_pixels=delta,signed_change_percent=pct,
                abrupt_over_50pct=pct is not None and abs(pct)>50,negative_over_25pct=pct is not None and pct< -25,
                zero_to_positive=a['raw_pixel_area']==0 and b['raw_pixel_area']>0,
                touches_empty_control=a['serial']==1 or b['serial']==727,
                touches_known_absent=a['presence']=='absent' or b['presence']=='absent',
                diagnostic_only_presence_unknown=True))
        group=[r for r in joined if r['position']==p]
        for label,subset in [('raw_all',group),('original_QC_accepted',[r for r in group if not r['qc_flag']])]:
            corr.append(dict(position=p,analysis=label,**correlations([r['raw_pixel_area'] for r in subset],[r['fresh_mass_g'] for r in subset])))
    put_csv(OUT/'daily_trajectories.csv',[r for p in positions for r in daily[p]])
    put_csv(OUT/'daily_changes.csv',changes)
    put_csv(OUT/'daily_QC_accepted_changes.csv',qc_changes)
    put_csv(OUT/'daily_all_capture_variability.csv',[r for p in positions for r in within[p]])
    put_csv(OUT/'capture_changes.csv',capture);put_csv(OUT/'position_summary.csv',curve)
    pooled=[]
    for label,subset in [('raw_all',joined),('original_QC_accepted',[r for r in joined if not r['qc_flag']]),
                         ('preselected_lower_risk_raw',[r for r in joined if r['lower_risk_diagnostic']]),
                         ('preselected_lower_risk_QC',[r for r in joined if r['lower_risk_diagnostic'] and not r['qc_flag']])]:
        pooled.append(dict(analysis=label,**correlations([r['raw_pixel_area'] for r in subset],[r['fresh_mass_g'] for r in subset])))
    for label,subset in [('date_demeaned_raw',joined),('date_demeaned_QC',[r for r in joined if not r['qc_flag']])]:
        residual=[]
        for day in sorted({r['dat'] for r in subset}):
            dayrows=[r for r in subset if r['dat']==day]
            if len(dayrows)<2:continue
            am=np.mean([r['raw_pixel_area'] for r in dayrows]);bm=np.mean([r['fresh_mass_g'] for r in dayrows])
            residual.extend((r['raw_pixel_area']-am,r['fresh_mass_g']-bm) for r in dayrows)
        pooled.append(dict(analysis=label,**correlations([a for a,b in residual],[b for a,b in residual])))
    difference=[]
    for p in positions:
        prs=sorted([r for r in joined if r['position']==p],key=lambda r:r['dat'])
        for a,b in zip(prs,prs[1:]):
            if b['dat']!=a['dat']+1:continue
            difference.append(dict(position=p,dat=b['dat'],delta_area=b['raw_pixel_area']-a['raw_pixel_area'],
                delta_mass=b['fresh_mass_g']-a['fresh_mass_g'],both_QC_accepted=not a['qc_flag'] and not b['qc_flag']))
    put_csv(OUT/'area_mass_first_differences.csv',difference)
    for label,subset in [('first_differences_raw',difference),('first_differences_QC',[r for r in difference if r['both_QC_accepted']])]:
        pooled.append(dict(analysis=label,**correlations([r['delta_area'] for r in subset],[r['delta_mass'] for r in subset])))
    put_csv(OUT/'correlations_by_position.csv',corr);put_csv(OUT/'correlations_pooled.csv',pooled)
    from .exp005_plots import trajectory_chart,scatter_chart,capture_chart
    plots=OUT/'plots';plots.mkdir()
    trajectory_chart(plots/'area_vs_time',positions,daily,within,'EXP-005: 18 frozen-analyzer position trajectories (no segmentation GT)')
    scatter_chart(plots/'area_vs_fresh_mass',joined,'EXP-005: projected cell area vs fresh mass')
    for p in positions:
        scatter_chart(plots/f'{p}_area_vs_fresh_mass',[r for r in joined if r['position']==p],f'EXP-005: {p} cell area vs fresh mass',p)
        capture_chart(plots/f'{p}_all_capture_area',sorted([r for r in rows if r['position']==p],key=lambda r:r['serial']),p)
    summary=dict(frames=len({r['serial'] for r in rows}),positions=18,position_predictions=len(rows),
        qc_failures=sum(r['qc_flag'] for r in rows),qc_accepted=sum(not r['qc_flag'] for r in rows),
        zero_predictions=sum(r['raw_pixel_area']==0 for r in rows),
        biomass_joined=len(joined),biomass_QC_accepted=sum(not r['qc_flag'] for r in joined),
        excluded_mass_associations=len(excluded),
        matched_raw_zero=sum(r['raw_pixel_area']==0 for r in joined),
        crop_edge_contact=sum(r['prediction_touches_crop_edge'] for r in rows),
        daily_changes=dict(transitions=sum(r['delta_pixels'] is not None for r in changes),
            negative_steps=sum(r['delta_pixels'] is not None and r['delta_pixels']<0 for r in changes),
            abrupt_over_50pct=sum(r['abrupt_over_50pct'] for r in changes),negative_over_25pct=sum(r['negative_over_25pct'] for r in changes),
            zero_to_positive=sum(r['zero_to_positive'] for r in changes),
            absolute_percent_change=quantiles([abs(r['signed_change_percent']) if r['signed_change_percent'] is not None else None for r in changes])),
        daily_QC_accepted_changes=dict(transitions=sum(r['delta_pixels'] is not None for r in qc_changes),
            negative_steps=sum(r['delta_pixels'] is not None and r['delta_pixels']<0 for r in qc_changes),
            abrupt_over_50pct=sum(r['abrupt_over_50pct'] for r in qc_changes),negative_over_25pct=sum(r['negative_over_25pct'] for r in qc_changes),
            absolute_percent_change=quantiles([abs(r['signed_change_percent']) if r['signed_change_percent'] is not None else None for r in qc_changes])),
        all_capture_diagnostic=dict(transitions=len(capture),noncontrol_transitions=sum(not r['touches_empty_control'] for r in capture),
            abrupt_over_50pct=sum(r['abrupt_over_50pct'] for r in capture if not r['touches_empty_control']),
            negative_over_25pct=sum(r['negative_over_25pct'] for r in capture if not r['touches_empty_control']),
            zero_to_positive=sum(r['zero_to_positive'] for r in capture if not r['touches_empty_control'])),
        correlations_pooled=pooled,correlations_by_position=corr,
        dat_bands=[dict(band=label,n=len(rs),qc_failures=sum(r['qc_flag'] for r in rs),
            qc_accepted=sum(not r['qc_flag'] for r in rs),zero=sum(r['raw_pixel_area']==0 for r in rs),
            median_raw_area=float(np.median([r['raw_pixel_area'] for r in rs])),
            matched_n=len(ms),matched_QC_accepted=sum(not r['qc_flag'] for r in ms))
            for label,rs,ms in [(f'DAT{lo}..{hi}',[r for r in rows if lo<=r['dat']<=hi],[r for r in joined if lo<=r['dat']<=hi])
                for lo,hi in [(1,10),(11,20),(21,30),(31,31)]]],
        controls=[dict(serial=s,raw_sum=sum(r['raw_pixel_area'] for r in rows if r['serial']==s),qc_failures=sum(r['qc_flag'] for r in rows if r['serial']==s)) for s in [1,727]],
        localization_counts={s:sum(r['localization_source']==s for r in rows) for s in sorted({r['localization_source'] for r in rows})},
        date_conflict='UNRESOLVED 2022 versus 2024; no environment join',segmentation_accuracy='UNAVAILABLE_NO_MASKS',physical_scale='UNAVAILABLE_PIXELS_ONLY')
    put_json(OUT/'statistics.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ['correlations_by_position']},indent=2))


def cases():
    rows=json.loads((OUT/'predictions.json').read_text())
    inv={r['serial']:r for r in json.loads((OUT/'inventory.json').read_text())}
    changes=list(csv.DictReader((OUT/'daily_changes.csv').open(newline='',encoding='utf-8')))
    selections={1:'pre-transplant empty control',727:'post-harvest empty control',2:'early separated seedlings',26:'cyan lighting',457:'absent B44 versus neighbor intrusion'}
    for c in sorted([r for r in changes if r['signed_change_percent']],key=lambda r:abs(float(r['signed_change_percent'])),reverse=True)[:4]:
        r=next(r for r in rows if r['position']==c['position'] and r['dat']==int(c['dat']) and r['biomass_flagged'])
        a=next(r for r in rows if r['position']==c['position'] and r['dat']==int(c['previous_dat']) and r['biomass_flagged'])
        for t in [a,r]:selections[t['serial']]=f"large daily change {c['position']} DAT{c['previous_dat']}->{c['dat']} {float(c['signed_change_percent']):.1f}%"
    for dat in [10,16,23,30]:
        candidates=[r for r in rows if r['dat']==dat and r['biomass_flagged']]
        selections[candidates[0]['serial']]=f'DAT{dat} systematic growth/crowding inspection'
    for r in sorted([r for r in rows if r['biomass_flagged'] and r['dat']>=11 and not r['qc_flag'] and r['presence']=='present'],key=lambda r:r['raw_pixel_area'])[:3]:
        selections[r['serial']]=f"small late QC-accepted diagnostic {r['position']} {r['raw_pixel_area']}px confidence{r['confidence']}"
    out=OUT/'case_panels';out.mkdir();manifest=[]
    with zipfile.ZipFile(ARCHIVE) as z:
        for serial,why in sorted(selections.items()):
            image=inv[serial];rgb=np.asarray(Image.open(io.BytesIO(z.read(image['member']))))
            mask=np.asarray(Image.open(OUT/'masks'/image['filename']))>0
            roi=np.asarray(Image.open(OUT/'rois'/image['filename']))>0
            overlay=rgb.copy();overlay[mask]=(rgb[mask].astype(float)*.4+np.array([255,35,100])*.6).astype(np.uint8)
            roiv=rgb.copy();roiv[~roi]=(rgb[~roi]*.2).astype(np.uint8)
            panel=Image.new('RGB',(2560,1600),'white');d=ImageDraw.Draw(panel)
            for k,(arr,title) in enumerate([(rgb,'Native RGB / fixed position cells'),(overlay,'Frozen C foreground (pink)'),(mask.astype(np.uint8)*255,'Native binary mask'),(roiv,'Frozen C ROI (outside dimmed)')]):
                x,y=(k%2)*1280,90+(k//2)*750
                im=Image.fromarray(arr).convert('RGB');dd=ImageDraw.Draw(im)
                for cell in grid():
                    x0,y0,x1,y1=cell['box'];rr=next(r for r in rows if r['serial']==serial and r['position']==cell['position'])
                    dd.rectangle((x0,y0,x1-1,y1-1),outline='red',width=1)
                    dd.text((x0+2,y0+3),f"{cell['position']} {rr['raw_pixel_area']}px {'WITHHELD' if rr['qc_flag'] else 'QC PASS'}",font=font(15),fill='white',stroke_width=1,stroke_fill='black')
                panel.paste(im,(x,y));d.text((x+5,y-25),title,font=font(20),fill='black')
            d.text((5,5),f"{image['filename']} | {why}",font=font(23),fill='black')
            d.text((5,35),'No GT: qualitative mask/localization inspection only; pixels not cm2; QC is heuristic.',font=font(20),fill='black')
            name=f'{serial:03}_panel.png';panel.save(out/name)
            manifest.append(dict(serial=serial,filename=image['filename'],dat=image['dat'],reason=why,panel=f'case_panels/{name}',manual_review='pending'))
    put_csv(OUT/'case_manifest.csv',manifest)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['analysis','cases']);a=p.parse_args()
    {'analysis':analysis,'cases':cases}[a.action]()
