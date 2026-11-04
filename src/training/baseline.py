"""Evaluate the unchanged full-image classical candidate; never tune thresholds."""
import argparse
from collections import defaultdict
import json
import time
from pathlib import Path
import numpy as np
from PIL import Image
from ..hydroponic.config import ROOT,load_profiles
from ..quick_test import process_single_plant_rgb
from .common import bounded,relative,sha,read_manifest,write_manifest,write_json
from .datasets import load_sample
from .metrics import binary_metrics,aggregate


def evaluate(manifest,output):
    output=bounded(output);output.mkdir(parents=True,exist_ok=False)
    species,acquisition=load_profiles('lettuce');rows=read_manifest(manifest);scores=[]
    for i,row in enumerate(rows):
        rgb,reference=load_sample(row)
        start=time.perf_counter()
        result=process_single_plant_rgb(rgb,species,acquisition,image_sha256=row['image_sha256'])
        elapsed=time.perf_counter()-start
        mask=result['mask']
        if mask is None:raise ValueError('Frozen lettuce candidate unexpectedly unavailable')
        score=dict(id=row['id'],dataset=row['dataset'],domain=row['domain'],split=row['split'],
            image_sha256=row['image_sha256'],mask_sha256=row['mask_sha256'],
            group_id=row['group_id'],inference_seconds=elapsed,qc_status=result['metadata']['qc_status'],
            **binary_metrics(mask,reference))
        scores.append(score)
        if row.get('diagnostic'):
            Image.fromarray(mask.astype(np.uint8)*255).save(output/'lettuce_test_classical_mask.png')
            overlay=rgb.copy();overlay[mask]=(.55*rgb[mask]+.45*np.array([0,220,240])).astype(np.uint8)
            Image.fromarray(overlay).save(output/'lettuce_test_classical_overlay.png')
            errors=rgb.copy();errors[mask&~reference]=[255,70,70];errors[reference&~mask]=[80,120,255]
            Image.fromarray(errors).save(output/'lettuce_test_classical_errors.png')
            calibration=json.loads((ROOT/'results/manual_ground_truth/20261008T182536_343358Z/lettuce_test/measurement.json').read_text())
            score.update(scope='DEVELOPMENT / DIAGNOSTIC',cm_per_pixel=calibration['cm_per_pixel'],
                predicted_projected_area_cm2=score['predicted_pixel_area']*calibration['cm_per_pixel']**2,
                true_projected_area_cm2=calibration['projected_area_cm2'],
                failure_description='Green distractor/moss false positives plus reference lettuce omissions, including the pale upper blade that the final human-approved mask includes')
        if (i+1)%25==0:print(f'Frozen baseline {i+1}/{len(rows)}',flush=True)
    grouped=defaultdict(list)
    for score in scores:grouped[(score['dataset'],score['split'])].append(score)
    domains=[dict(dataset=dataset,split=split,**aggregate(data)) for (dataset,split),data in sorted(grouped.items())]
    sources={**{p.replace('\\','/'):digest for p,digest in result['metadata']['source_sha256'].items()},
        **{p:sha(ROOT/p) for p in ['configs/species/lettuce.json','configs/acquisition/exp003_oak1.json']}}
    report=dict(status='COMPLETED',model='frozen_classical_single_plant',manifest=relative(manifest),manifest_sha256=sha(manifest),
        measurement_target='Visible projected lettuce reference pixels; no true 3D area',
        comparison_scope='OAK labels previously exposed to classical development; AgRibot oracle target crops are conditional reference comparisons, not localization validation',
        inference_timing='CPU wall time full frozen single-plant pipeline, including QC; no learned runtime measured',
        sources=sources,
        domains=domains,diagnostic=next(s for s in scores if s.get('scope')))
    write_manifest(output/'per_sample_metrics.jsonl',scores)
    report['per_sample_metrics_sha256']=sha(output/'per_sample_metrics.jsonl')
    write_json(output/'baseline_report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/processed/exp006/v1/manifest.jsonl')
    parser.add_argument('--output',required=True);args=parser.parse_args()
    print(json.dumps(evaluate(args.manifest,args.output),indent=2))


if __name__=='__main__':main()
