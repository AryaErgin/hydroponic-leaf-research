"""Reproduce EXP-005 supplemental cases; default read-only pixel verification.

--write is create-only. Cases are post-hoc diagnostics, never exclusions/QC
changes: every accepted DAT11..20 probe and first/middle all-mask-zero frame.
"""
import argparse
import csv
import io
import json
import zipfile
import numpy as np
from PIL import Image,ImageDraw
from .exp005 import OUT,ARCHIVE,font,put_csv,put_json


def generate(write=False):
    rows=json.loads((OUT/'predictions.json').read_text());inv=json.loads((OUT/'inventory.json').read_text())
    frames={i['serial']:i for i in inv};sums={i['serial']:0 for i in inv}
    for r in rows:sums[r['serial']]+=r['raw_pixel_area']
    zero=[i for i in inv if i['serial'] not in [1,727] and sums[i['serial']]==0]
    selected=[r for r in rows if 11<=r['dat']<=20 and not r['qc_flag']]
    for i in [zero[0],zero[len(zero)//2]]:
        selected.append(next(r for r in rows if r['serial']==i['serial'] and r['position']=='B34'))
    out=OUT/'diagnostic_cases'
    if write:out.mkdir()
    manifest=[]
    with zipfile.ZipFile(ARCHIVE) as z:
        for r in selected:
            i=frames[r['serial']];rgb=np.asarray(Image.open(io.BytesIO(z.read(i['member']))))
            x0,y0,x1,y1=(r[k] for k in ['x0','y0','x1','y1'])
            p=np.asarray(Image.open(OUT/'masks'/i['filename']))>0;roi=np.asarray(Image.open(OUT/'rois'/i['filename']))>0
            overlay=rgb.copy();overlay[p]=(rgb[p].astype(float)*.4+np.array([255,25,100])*.6).astype(np.uint8)
            canvas=Image.new('RGB',(1400,1050),'white');d=ImageDraw.Draw(canvas)
            d.text((10,8),f"{i['filename']} {r['position']}: {r['raw_pixel_area']} px; conf {r['confidence']}; {'WITHHELD' if r['qc_flag'] else 'QC ACCEPTED'}",font=font(21),fill='black')
            d.text((10,42),f"{r['localization_source']}; background green {r['background_green']}; RGB max-channel mean {np.max(rgb,axis=2).mean():.2f}",font=font(19),fill='black')
            for k,(im,title) in enumerate([(Image.fromarray(rgb),'Full RGB context'),(Image.fromarray(rgb[y0:y1,x0:x1]),'Native crop (display enlarged only)'),(Image.fromarray(overlay[y0:y1,x0:x1]),'Frozen prediction pink'),(Image.fromarray((roi[y0:y1,x0:x1]*255).astype('uint8')),'Frozen ROI')]):
                im=im.convert('RGB');im.thumbnail((670,430));x=10+(k%2)*700;y=115+(k//2)*465
                canvas.paste(im,(x,y));d.text((x,y-30),title,font=font(20),fill='black')
            name=f"{r['serial']:03}_{r['position']}.png"
            if write:canvas.save(out/name)
            elif not np.array_equal(np.asarray(canvas),np.asarray(Image.open(out/name))):raise AssertionError(f'Panel drift {name}')
            manifest.append(dict(serial=r['serial'],position=r['position'],filename=i['filename'],panel=f'diagnostic_cases/{name}',
                reason='all three mid-cycle QC-accepted probes' if not r['qc_flag'] else 'deterministic whole-mask-zero visibility example',
                raw_area=r['raw_pixel_area'],confidence=r['confidence'],RGB_max_channel_mean=float(np.max(rgb,axis=2).mean()),manual_review='pending'))
    value=dict(whole_frame_zero=len(zero),source_frames=[i['serial'] for i in zero],no_new_QC_rule_or_exclusion=True)
    if write:
        put_csv(OUT/'diagnostic_case_manifest.csv',manifest);put_json(OUT/'whole_frame_zero_diagnostic.json',value)
    else:
        expected=list(csv.DictReader((OUT/'diagnostic_case_manifest.csv').open(newline='',encoding='utf-8')))
        assert all({k:str(v) for k,v in a.items()}==b for a,b in zip(manifest,expected)) and len(manifest)==len(expected)
        assert json.loads((OUT/'whole_frame_zero_diagnostic.json').read_text())==value
    return dict(status='PASS',panels=len(selected),whole_frame_zero=len(zero),write=write)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--write',action='store_true');a=p.parse_args()
    print(json.dumps(generate(a.write),indent=2))
