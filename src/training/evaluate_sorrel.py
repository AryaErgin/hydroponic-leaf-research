"""Native Rumex-only evaluation; no invented validated baseline or promotion."""
import argparse,json,time
from pathlib import Path
from .common import bounded,read_manifest,write_json,sha
from .datasets import load_sample
from .inference import load_predictor,predict_native
from .metrics import binary_metrics,aggregate

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['manifest','checkpoint','config','output']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(argv);rows=read_manifest(bounded(a.manifest))
    if not rows or any(r.get('species')!='rumex_acetosa'or r.get('split')!='real_validation'or r.get('synthetic')or r.get('diagnostic')for r in rows):raise ValueError('Untouched real sorrel validation only')
    settings=json.loads(bounded(a.config).read_text());predictor=load_predictor(a.checkpoint,a.config,device='cuda'if __import__('torch').cuda.is_available()else'cpu')
    scores=[]
    for r in rows:
        rgb,gt=load_sample(r);start=time.perf_counter();mask=predict_native(rgb,predictor,settings.get('input_size',512),settings.get('threshold',.5))['mask']
        scores.append(dict(id=r['id'],group_id=r['group_id'],inference_seconds=time.perf_counter()-start,**binary_metrics(mask,gt)))
    out=bounded(a.output);out.mkdir(parents=True,exist_ok=False)
    write_json(out/'evaluation.json',dict(status='COMPLETED',species='rumex_acetosa',target_validation='UNVALIDATED / REVIEW REQUIRED',
        checkpoint_sha256=sha(a.checkpoint),metrics=aggregate(scores),rows=scores,automatic_promotion=False,
        baseline='No validated existing sorrel champion; explicit owner review required'))
    return 0

if __name__=='__main__':raise SystemExit(main())
