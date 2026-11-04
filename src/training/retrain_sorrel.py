"""Separate, local-only Rumex research preparation/execution. Never promotion."""
import argparse,json,subprocess,sys
from pathlib import Path
from .model_registry import bounded,read_jsonl,sha256,write_json
from .approved_registry import from_measurement,native_binary
from .common import ROOT
from PIL import Image

def prepare(registry,output,threshold=10,seed=6006):
    registry=bounded(registry);output=bounded(output)
    if output.exists():raise FileExistsError(output)
    unique={};masks=set()
    for r in read_jsonl(registry):
        if r.get('species')!='rumex_acetosa':raise ValueError('Wrong species in sorrel training registry')
        canonical=from_measurement(r,r['assets'])
        if not canonical['new_real_eligible']:continue
        if sha256(bounded(r['image_path']).read_bytes())!=r['image_sha256']or sha256(bounded(r['mask_path']).read_bytes())!=r['mask_sha256']:raise ValueError('Changed approved source')
        with Image.open(bounded(r['image_path']))as im:size=im.size
        mask=native_binary(bounded(r['mask_path']).read_bytes(),size)
        if int(mask.sum())!=r['pixel_area']:raise ValueError('Mask area mismatch')
        if r['image_sha256']in unique or r['mask_sha256']in masks:continue
        unique[r['image_sha256']]=r;masks.add(r['mask_sha256'])
    rows=list(unique.values());groups=sorted({r['group_id']for r in rows},key=lambda g:sha256(f'{seed}:{g}'.encode()))
    output.mkdir(parents=True)
    result=dict(species='rumex_acetosa',status='BLOCKED_INSUFFICIENT_REAL_DATA',new_real_mask_count=len(rows),
        threshold=threshold,plant_groups=len(groups),training_executed=False,promotion_executed=False,
        registry_path=str(registry),registry_sha256=sha256(registry.read_bytes()),
        validation='UNVALIDATED / REVIEW REQUIRED',reasons=[])
    if len(rows)<threshold or len(groups)<3:
        result['reasons']=['Need >=10 distinct real masks and >=3 physical plant groups for TRAIN/DEV/untouched validation']
    else:
        # Immutable plant-disjoint reservation across exports; never regenerate a
        # nicer holdout after results. Training cannot reset this lock.
        lock_dir=ROOT/'data/approved_masks/rumex_acetosa';lock_dir.mkdir(parents=True,exist_ok=True)
        lock=lock_dir/'validation_groups.json'
        if lock.exists():
            reserve=json.loads(lock.read_text());val=set(reserve['validation']);dev=set(reserve['dev'])
        else:
            val={groups[0]};dev={groups[1]};write_json(lock,dict(species='rumex_acetosa',validation=sorted(val),dev=sorted(dev)))
        training=[{**r,'split':'DEV'if r['group_id']in dev else'TRAIN'}for r in rows if r['group_id']not in val]
        validation=[{**r,'split':'real_validation'}for r in rows if r['group_id']in val]
        if not validation or not any(r['split']=='DEV'for r in training)or not any(r['split']=='TRAIN'for r in training):
            result['reasons']=['Reserved groups missing or training empty; retain holdout and collect more images']
        else:
            for name,data in [('train_manifest.jsonl',training),('validation_manifest.jsonl',validation)]:
                with (output/name).open('x',encoding='utf8')as f:f.writelines(json.dumps(r)+'\n'for r in data)
            result.update(status='READY_FOR_LOCAL_RESEARCH',train_manifest=str(output/'train_manifest.jsonl'),
                validation_manifest=str(output/'validation_manifest.jsonl'),validation_lock=str(lock),
                note='No existing validated sorrel model; new binary challenger only, owner review before any promotion')
    write_json(output/'preparation.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--config',type=Path);p.add_argument('--architecture',default='segformer_b0',choices=['segformer_b0','unet_resnet18'])
    a=p.parse_args(argv);result=prepare(a.registry,a.output)
    if a.prepare_only or result['status']!='READY_FOR_LOCAL_RESEARCH':print(json.dumps(result));return 0
    if not a.config:p.error('--config required for explicit local training')
    from .train import create_training_lock
    lock=create_training_lock(result['train_manifest'],{a.architecture:bounded(a.config)},bounded(a.output)/'training_lock.json',additional_files=[bounded(result['validation_manifest'])])
    run=bounded(a.output)/'training_run';evaluation=bounded(a.output)/'evaluation'
    subprocess.run([sys.executable,'-m','src.training.train','--species','rumex_acetosa','--manifest',result['train_manifest'],
        '--config',str(a.config),'--architecture',a.architecture,'--lock',str(lock),'--output',str(run)],check=True)
    subprocess.run([sys.executable,'-m','src.training.evaluate_sorrel','--manifest',result['validation_manifest'],
        '--checkpoint',str(run/'model.safetensors'),'--config',str(run/'config.json'),'--output',str(evaluation)],check=True)
    write_json(bounded(a.output)/'execution.json',{**result,'status':'COMPLETED','training_executed':True,
        'comparison_report':str(evaluation/'evaluation.json'),'checkpoint':str(run/'model.safetensors'),
        'checkpoint_sha256':sha256((run/'model.safetensors').read_bytes())})
    return 0

if __name__=='__main__':raise SystemExit(main())
