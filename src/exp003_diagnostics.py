"""Post-hoc failure investigation; no changes to frozen inference/config."""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from .exp001 import ROOT,read_csv,write_csv,write_json,load_rgb,gt_mask
from .exp003 import OUT,read_lock,infer,panel,safe_destination
from .lettuce_cv import color_features,pixel_metrics
from .verify_exp002 import direct_summary


def diagnose(destination):
    if (destination/'QC_flagged_tuning_cases/cases.csv').exists():raise FileExistsError('Existing diagnostics')
    cfg,_=read_lock(destination);selected={r['filename']:r for r in read_csv(destination/'selected_images.csv')}
    rows=read_csv(destination/'metrics.csv')
    names={r['filename'] for r in rows if r['split']=='tuning' and r['method']=='C' and r['qc_flag']=='True'}
    folder=destination/'QC_flagged_tuning_cases';folder.mkdir(parents=True,exist_ok=True)
    for name in sorted(names):
        r=selected[name];masks,rois,card,diag,qc=infer(load_rgb(r),cfg)
        panel(r,masks,rois,card,qc,folder/(Path(name).stem+'.jpg'))
    if names:write_csv(folder/'cases.csv',[r for r in rows if r['filename'] in names])
    # Same original tail stratum for all methods, with no headline exclusion.
    tail={r['filename'] for r in rows if r['split']=='development' and r['method']=='A' and float(r['signed_percentage_area_error'])>100}
    write_json(destination/'non_turf_diagnostic.json',{m:direct_summary([r for r in rows if r['split']=='development' and r['method']==m and r['filename'] not in tail]) for m in ['A','B','C']})
    print('Saved two QC-flagged tuning comparisons and unchanged non-turf diagnostic stratum')


def role_correct_panel(row,masks,rois,card,qc,path):
    """Supplemental renderer; retain the frozen renderer and original outputs."""
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
            method='ABC'[i-3];metric=pixel_metrics(gt,mask)
            draw.text((x+4,y+size[1]+3),f"IoU {metric['iou']:.3f}; signed area {metric['signed_percentage_area_error']:+.1f}%",fill='black')
            draw.text((x+4,y+size[1]+18),f"QC {'WITHHOLD' if qc[method]['qc_flag'] else 'accept'}; score {qc[method]['confidence']:.2f}",fill='black')
    draw.text((4,canvas.height-18),row['filename']+'; '+row['split'].upper(),fill='black')
    with path.open('xb') as f:canvas.save(f,format='JPEG',quality=90)


def supplement(destination):
    folder=destination/'QC_flagged_tuning_cases_corrected'
    if folder.exists():raise FileExistsError('Existing supplemental diagnostics')
    folder.mkdir()
    cfg,_=read_lock(destination);selected={r['filename']:r for r in read_csv(destination/'selected_images.csv')}
    metrics=read_csv(destination/'metrics.csv')
    names={r['filename'] for r in metrics if r['split']=='tuning' and r['method']=='C' and r['qc_flag']=='True'}
    details=[]
    for name in sorted(names):
        row=selected[name];rgb=load_rgb(row);masks,rois,card,diag,qc=infer(rgb,cfg)
        role_correct_panel(row,masks,rois,card,qc,folder/(Path(name).stem+'.jpg'))
        hue,sat,val,exg=color_features(rgb);norm=exg.astype(float)/np.maximum(rgb.astype(float).sum(axis=2),1)
        h,w=val.shape;band=max(1,round(.08*min(h,w)));perimeter=np.zeros((h,w),bool)
        perimeter[:band]=True;perimeter[-band:]=True;perimeter[:,:band]=True;perimeter[:,-band:]=True
        green=(hue>=10)&(hue<=85)&(sat>=30)&(norm>.08)&perimeter
        details.append(dict(filename=name,split=row['split'],localization_source=diag['localization_source'],
            green_periphery_fraction=diag['green_periphery_fraction'],green_periphery_pixels=int(green.sum()),
            green_periphery_V_below_15_fraction=float((val[green]<15).mean()),
            green_periphery_V_below_30_fraction=float((val[green]<30).mean()),
            green_periphery_normalized_exg_p95=float(np.quantile(norm[green],.95)),
            green_periphery_normalized_exg_max=float(norm[green].max()),normalized_exg_mathematical_upper_bound=2,
            contrast_cutoff=diag.get('background_normalized_exg_cutoff',''),
            qc_confidence=qc['C']['confidence'],reported_pixel_area='' if qc['C']['qc_flag'] else int(masks['C'].sum())))
    write_csv(folder/'cases.csv',[r for r in metrics if r['filename'] in names])
    write_csv(destination/'tuning_failure_diagnostics.csv',details)
    print('Correctly labeled tuning panels and post-hoc dark-periphery evidence saved; inference unchanged')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT);ap.add_argument('--supplement',action='store_true');args=ap.parse_args()
    (supplement if args.supplement else diagnose)(safe_destination(args.output))


if __name__=='__main__':main()
