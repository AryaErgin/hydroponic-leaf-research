"""Independent arithmetic/count/join verification and archived C replay.

No production analysis/correlation/ranking/parser functions are imported.
Run before and after report finalization. A seal optionally covers every output.
"""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import zipfile

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'experiments/EXP-005'


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def table(name):
    with (OUT/name).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))


def corr(xs,ys):
    n=len(xs)
    if n<2:return None,None
    def pc(a,b):
        am=math.fsum(a)/n;bm=math.fsum(b)/n
        av=math.fsum((v-am)**2 for v in a);bv=math.fsum((v-bm)**2 for v in b)
        return math.fsum((x-am)*(y-bm) for x,y in zip(a,b))/math.sqrt(av*bv) if av*bv else None
    def ranks(a):return [1+sum(v<x for v in a)+(sum(v==x for v in a)-1)/2 for x in a]
    return pc(xs,ys),pc(ranks(xs),ranks(ys))


def verify(replay=True):
    count=0
    def check(ok,message):
        nonlocal count
        count+=1
        if not ok:raise AssertionError(message)
    def near(a,b,message):check(a is None and b is None or a is not None and b is not None and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8),message)
    lock=json.loads((OUT/'freeze.json').read_text())
    protected=json.loads((OUT/'protected_before.json').read_text())
    for path,h in protected.items():check(digest(ROOT/path)==h,f'protected changed {path}')
    for path,h in lock['files'].items():check(digest(OUT/path)==h,f'freeze changed {path}')
    check((OUT/'config.json').read_bytes()==(ROOT/'experiments/EXP-003/config.json').read_bytes(),'original config')
    inventory=json.loads((OUT/'inventory.json').read_text());grid=json.loads((OUT/'grid.json').read_text())
    rows=json.loads((OUT/'predictions.json').read_text());stats=json.loads((OUT/'statistics.json').read_text())
    bykey={(r['serial'],r['position']):r for r in rows}
    csvrows=table('predictions.csv')
    check(len(csvrows)==len(rows),'CSV/JSON prediction coverage')
    for a,b in zip(rows,csvrows):
        check(all(b[k]==('' if a.get(k) is None else str(a[k])) for k in b),'CSV/JSON prediction field parity')
    check(len(rows)==13086 and len(bykey)==13086,'complete unique 727x18 predictions')
    check(len(inventory)==727 and all(r['usable'] for r in inventory),'all 727 decode/canvas usable')
    check(len(list((OUT/'masks').glob('*.png')))==727,'727 mosaics')
    check(len(list((OUT/'rois').glob('*.png')))==727,'727 ROI mosaics')
    coverage=np.zeros((720,1280),int)
    for c in grid:
        x0,y0,x1,y1=c['box'];coverage[y0:y1,x0:x1]+=1
    check(np.all(coverage==1),'grid disjoint complete')
    check(grid[0]['position']=='B42' and grid[-1]['position']=='B27','position orientation')
    for image in inventory:
        p=np.asarray(Image.open(OUT/'masks'/image['filename']));roi=np.asarray(Image.open(OUT/'rois'/image['filename']))
        check(p.shape==roi.shape==(720,1280),'native mosaic shapes')
        check(set(np.unique(p))<={0,255} and set(np.unique(roi))<={0,255},'binary mosaic values')
        check(not np.any((p>0)&(roi==0)),'prediction outside ROI')
        for c in grid:
            x0,y0,x1,y1=c['box'];r=bykey[image['serial'],c['position']];pm=p[y0:y1,x0:x1]>0;rm=roi[y0:y1,x0:x1]>0
            area=int(pm.sum());geom=r['localization_confidence'];confidence=geom if area else 0
            check(area==r['raw_pixel_area'],'saved native area')
            check(int(rm.sum())==r['roi_pixel_area'],'saved native ROI area')
            near(confidence,r['confidence'],'original self-reference QC confidence')
            check(r['qc_flag']==(confidence<.7),'unchanged QC threshold')
            check(r['reported_pixel_area']==(None if confidence<.7 else area),'reportability')
            check([r[k] for k in ['x0','y0','x1','y1']]==c['box'],'native cell bounds')
            edge=np.zeros_like(pm);edge[:3]=1;edge[-3:]=1;edge[:,:3]=1;edge[:,-3:]=1
            check(int((pm&edge).sum())==r['crop_boundary_foreground_pixels'],'crop contact pixels')
    check(stats['qc_failures']==sum(r['qc_flag'] for r in rows),'all-position QC count')
    check(stats['zero_predictions']==sum(r['raw_pixel_area']==0 for r in rows),'zero count')
    # Source-only prior audit is a separate independently parsed biomass table.
    with (ROOT/'docs/dataset_inspection/new_public_datasets_audit/Aalto_biomass_long_inspection.csv').open(newline='',encoding='utf-8') as f:
        native={(r['date_as_stored'],r['plant_id']):float(r['fresh_biomass_g']) for r in csv.DictReader(f)}
    check(len(native)==540,'numeric source biomass count')
    joined=json.loads((OUT/'biomass_matched.json').read_text());screens=table('association_screen.csv')
    screen={(int(r['serial']),r['position']):r for r in screens}
    check(len(joined)==539 and len({(r['date_as_stored'],r['position']) for r in joined})==539,'539 unique valid mass matches')
    check(len(screens)==540 and sum(r['presence']=='absent' for r in screens)==1,'screen exclusions')
    check(screen[457,'B44']['presence']=='absent','B44 correctly mapped absent')
    for r in joined:
        check(r['biomass_flagged'] and r['presence']=='present','flagged present join')
        near(r['fresh_mass_g'],native[r['date_as_stored'],r['position']],'native biomass unchanged')
        check(r['raw_pixel_area']==bykey[r['serial'],r['position']]['raw_pixel_area'],'join did not alter area')
    exclusions=table('association_exclusions.csv')
    check(len(exclusions)==19 and sum(r['reason']=='harvest_mass_blank_no_flagged_image' for r in exclusions)==18,'missingness not zeros')
    check(stats['biomass_QC_accepted']==sum(not r['qc_flag'] for r in joined),'matched QC count')
    # Independently reconstruct every daily change, leaving absence gaps explicit.
    observed=table('daily_changes.csv');observed={(r['position'],int(r['dat'])):r for r in observed}
    eligible=negative=abrupt=negative25=zero=0;abschanges=[];diff=[]
    for c in grid:
        p=c['position'];seq=sorted([r for r in rows if r['position']==p and r['biomass_flagged']],key=lambda r:r['dat'])
        check(len(seq)==30,'30 daily flagged frames per position')
        for a,b in zip(seq,seq[1:]):
            o=observed[p,b['dat']];valid=a['presence']==b['presence']=='present' and b['dat']==a['dat']+1
            pc=100*(b['raw_pixel_area']-a['raw_pixel_area'])/a['raw_pixel_area'] if valid and a['raw_pixel_area']>0 else None
            near(pc,float(o['signed_change_percent']) if o['signed_change_percent'] else None,'daily relative difference')
            delta=b['raw_pixel_area']-a['raw_pixel_area'] if valid else None
            near(delta,float(o['delta_pixels']) if o['delta_pixels'] else None,'daily absolute difference')
            eligible+=valid;negative+=valid and delta<0;abrupt+=pc is not None and abs(pc)>50;negative25+=pc is not None and pc< -25
            zero+=valid and a['raw_pixel_area']==0 and b['raw_pixel_area']>0
            if pc is not None:abschanges.append(abs(pc))
        prs=sorted([r for r in joined if r['position']==p],key=lambda r:r['dat'])
        for a,b in zip(prs,prs[1:]):
            if b['dat']==a['dat']+1:diff.append(dict(delta_area=b['raw_pixel_area']-a['raw_pixel_area'],delta_mass=b['fresh_mass_g']-a['fresh_mass_g'],both_QC_accepted=not a['qc_flag'] and not b['qc_flag']))
    for k,v in [('transitions',eligible),('negative_steps',negative),('abrupt_over_50pct',abrupt),('negative_over_25pct',negative25),('zero_to_positive',zero)]:
        check(stats['daily_changes'][k]==v,f'daily aggregate {k}')
    sortedabs=sorted(abschanges)
    for k,q in [('median',.5),('p90',.9),('p95',.95)]:
        index=q*(len(sortedabs)-1);lo=math.floor(index);hi=math.ceil(index);v=sortedabs[lo]*(hi-index)+sortedabs[hi]*(index-lo) if hi!=lo else sortedabs[lo]
        near(stats['daily_changes']['absolute_percent_change'][k],v,f'independent quantile {k}')
    near(stats['daily_changes']['absolute_percent_change']['mean'],math.fsum(abschanges)/len(abschanges),'mean abs change')
    # Independent Pearson with fsum and Spearman ranks by counting ties.
    for r in stats['correlations_by_position']:
        sub=[v for v in joined if v['position']==r['position'] and (r['analysis']=='raw_all' or not v['qc_flag'])]
        a,b=corr([v['raw_pixel_area'] for v in sub],[v['fresh_mass_g'] for v in sub])
        check(r['n']==len(sub),'position correlation n');near(r['pearson'],a,'position Pearson');near(r['spearman'],b,'position Spearman')
    for r in stats['correlations_pooled']:
        label=r['analysis']
        if label.startswith('first_differences'):
            sub=[v for v in diff if label.endswith('raw') or v['both_QC_accepted']];xs=[v['delta_area'] for v in sub];ys=[v['delta_mass'] for v in sub]
        else:
            sub=[v for v in joined if ('QC' not in label and label!='original_QC_accepted' or not v['qc_flag']) and ('lower_risk' not in label or v['lower_risk_diagnostic'])]
            if label.startswith('date_demeaned'):
                residual=[]
                for d in sorted({v['dat'] for v in sub}):
                    day=[v for v in sub if v['dat']==d]
                    if len(day)<2:continue
                    # Reproduce the specified float64 centering transform exactly.
                    # Changing summation order shifts near-tied residual ranks;
                    # correlation/rank arithmetic below remains independent.
                    am=float(np.mean([v['raw_pixel_area'] for v in day]));bm=float(np.mean([v['fresh_mass_g'] for v in day]))
                    residual.extend((v['raw_pixel_area']-am,v['fresh_mass_g']-bm) for v in day)
                xs=[v[0] for v in residual];ys=[v[1] for v in residual]
            else:xs=[v['raw_pixel_area'] for v in sub];ys=[v['fresh_mass_g'] for v in sub]
        a,b=corr(xs,ys);check(r['n']==len(xs),f'pooled correlation n {label}');near(r['pearson'],a,f'pooled Pearson {label}');near(r['spearman'],b,f'pooled Spearman {label}')
    # Replay fixed inputs across early/middle/late/controls and known absence.
    replayed=0
    if replay:
        snapshot=OUT/'code_snapshot';name='_exp005_archived'
        spec=importlib.util.spec_from_file_location(name,snapshot/'__init__.py',submodule_search_locations=[str(snapshot)])
        package=importlib.util.module_from_spec(spec);sys.modules[name]=package;spec.loader.exec_module(package)
        frozen=importlib.import_module(name+'.robust_roi');cfg=json.loads((OUT/'config.json').read_text())
        serials=[1,2,26,219,363,457,527,693,727]
        with zipfile.ZipFile(ROOT/'Lettuce_growth.zip') as z:
            for serial in serials:
                im=next(v for v in inventory if v['serial']==serial);blob=z.read(im['member'])
                check(hashlib.sha256(blob).hexdigest()==im['sha256'],'replay RGB source bytes')
                rgb=np.asarray(Image.open(io.BytesIO(blob)));saved=np.asarray(Image.open(OUT/'masks'/im['filename']))>0;sroi=np.asarray(Image.open(OUT/'rois'/im['filename']))>0
                for c in grid:
                    x0,y0,x1,y1=c['box'];r=bykey[serial,c['position']]
                    p,roi,_,d=frozen.robust_segment(rgb[y0:y1,x0:x1],cfg['A'],cfg['C']);q=frozen.quality_check(p,p,d,cfg['qc_threshold'])
                    check(np.array_equal(p,saved[y0:y1,x0:x1]),'archived C prediction replay')
                    check(np.array_equal(roi,sroi[y0:y1,x0:x1]),'archived C ROI replay')
                    for k,v in (d|q|frozen.measurement(p,q)).items():check(v==r[k],f'archived C diagnostic replay {k}')
                    replayed+=1
    if (OUT/'artifact_manifest.json').exists():
        for path,h in json.loads((OUT/'artifact_manifest.json').read_text()).items():check(digest(ROOT/path)==h,f'sealed output changed {path}')
    return dict(status='PASS',checks=count,replayed_crops=replayed,frames=727,position_predictions=13086,
        biomass_matches=539,protected_files=len(protected),freeze_files=len(lock['files']),
        date_conflict_preserved=True,no_segmentation_accuracy_claim=True,physical_units='pixels only')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--no-replay',action='store_true');p.add_argument('--output');a=p.parse_args()
    r=verify(not a.no_replay);print(json.dumps(r,indent=2))
    if a.output:
        with (OUT/a.output).open('x',encoding='utf-8') as f:json.dump(r,f,indent=2);f.write('\n')
