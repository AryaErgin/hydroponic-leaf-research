"""Post-lock diagnostic measurements. Never changes thresholds, GT or inclusion."""
import json
import zipfile
import numpy as np
from src.expr01 import OUT, ARCHIVE, read_csv, save_csv, save_json, annotations, rgb_and_gt, panel
from src.lettuce_cv import color_features


def threshold_diagnostics(rgb,gt,config):
    h,s,v,exg=color_features(rgb)
    failures={'hue':(h<config['h_min']) | (h>config['h_max']),
              'saturation':s<config['s_min'],'dark':v<config['v_min'],'exg':exg<=config['exg_min']}
    values={f'gt_{k}_rejected_pct':100*int((gt & fail).sum())/int(gt.sum()) for k,fail in failures.items()}
    values['gt_any_color_rejected_pct']=100*int((gt & np.logical_or.reduce(list(failures.values()))).sum())/int(gt.sum())
    return values


def analyze():
    selected=read_csv(OUT/'selected_images.csv'); metrics={r['review_id']:r for r in read_csv(OUT/'metrics.csv')}
    config=json.loads((OUT/'locked_config.json').read_text())['config']; rows=[]
    folder=OUT/'diagnostic_cases'; folder.mkdir()
    targets={'136':'pale blades and dark upright tissue', '128':'red veins and folded blade',
        '164':'thin straw occlusion and color loss','165':'shadow and area cancellation',
        '156':'neighboring toothed weed','149':'overlap and background vegetation',
        '126':'overlap with small net area error','85':'grass contamination','148':'soil plus green weeds'}
    with zipfile.ZipFile(ARCHIVE) as z:
        ann=annotations(z)
        for row in selected:
            rgb,gt=rgb_and_gt(z,ann,row); m=metrics[row['review_id']]
            rows.append(dict(review_id=row['review_id'],split=row['split'],subset=row['subset'],
                **threshold_diagnostics(rgb,gt,config),
                false_positive_over_gt_pct=100*float(m['false_positive_pixels'])/float(m['gt_pixel_area']),
                false_negative_over_gt_pct=100*float(m['false_negative_pixels'])/float(m['gt_pixel_area'])))
            if row['review_id'] in targets:
                from PIL import Image
                pred=np.asarray(Image.open(OUT/'predictions'/f"{row['review_id']}.png"))>0
                label=f"{row['split'].upper()} {row['review_id']}: {targets[row['review_id']]} | IoU {float(m['iou']):.3f}, signed area {float(m['signed_percentage_area_error']):+.1f}%"
                panel(rgb,gt,pred,size=(1800,850),label=label).save(folder/f"{row['review_id']}.jpg",quality=95)
    save_csv(OUT/'color_diagnostics.csv',rows)
    save_json(OUT/'diagnostic_case_index.json',dict(note='Post-score visual interpretations, no label/config changes',cases=targets,
        warning='Color rejection fractions overlap; do not add them. Net area can cancel spatial errors.'))
    print(json.dumps([r for r in rows if r['review_id'] in targets]))


if __name__=='__main__': analyze()
