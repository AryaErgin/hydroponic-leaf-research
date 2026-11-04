"""Prepare plant-disjoint retraining evidence; never approve or promote models."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image

from ..hydroponic.config import ROOT
from .approved_registry import DIAGNOSTIC_IMAGE_SHA256, native_binary, from_measurement
from .model_registry import ModelRegistry, bounded, mutation_lock, read_jsonl, sha256, stamp, write_json

DEFAULT_VALIDATION_LOCK_DIR = ROOT/'data/approved_masks'


def write_manifest(path, rows):
    with Path(path).open('x',encoding='utf8') as stream:
        stream.writelines(json.dumps(r,sort_keys=True,allow_nan=False)+'\n' for r in rows)


def verified_rows(registry):
    rows = []
    if not registry.exists():
        return rows
    for row in read_jsonl(registry):
        if row.get('species') != 'lettuce':raise ValueError('Wrong species in lettuce training registry')
        if row.get('review_status') != 'HUMAN_APPROVED':
            continue
        canonical = from_measurement(row,row['assets'])
        if canonical['group_id'] != row.get('group_id'):
            raise ValueError('Plant group differs from canonical approval identity')
        image = bounded(row['image_path']);mask = bounded(row['mask_path'])
        image_bytes=image.read_bytes();mask_bytes=mask.read_bytes()
        if sha256(image_bytes) != row['image_sha256'] or sha256(mask_bytes) != row['mask_sha256']:
            raise ValueError('Approved registry sample hash mismatch')
        if row['assets']['original']['sha256'] != row['image_sha256'] or row['assets']['approved_mask']['sha256'] != row['mask_sha256']:
            raise ValueError('Export hash differs from approved media reference')
        with Image.open(image) as source:
            source.load();size=source.size
        binary=native_binary(mask_bytes,size)
        if not binary.any() or int(binary.sum()) != row['pixel_area']:
            raise ValueError('Approved mask is empty or pixel area differs')
        if row.get('group_id') is None:
            raise ValueError('Stable physical plant grouping required')
        # Export flags are cached presentation values. Recompute permission,
        # exposure and eligibility from the original approval provenance.
        rows.append({**row, **canonical})
    return rows


def prepare(registry, output, minimum_new_real=10, *, historical_manifests=(),
            public_manifest=None, model_registry=ROOT/'models/lettuce',validation_lock_dir=None):
    """Create immutable manifests, reusing a persistent validation reservation."""
    registry = bounded(registry);output=bounded(output)
    if type(minimum_new_real) is not int or minimum_new_real < 5:
        raise ValueError('At least five new real masks are required')
    if output.exists():
        raise FileExistsError('Retraining preparation output already exists')
    rows=verified_rows(registry)
    by_digest={}
    duplicate_groups={}
    for row in rows:
        digest=row['image_sha256']
        duplicate_groups.setdefault(digest,set()).add(row['group_id'])
        previous=by_digest.get(digest)
        if previous is not None and previous['mask_sha256'] != row['mask_sha256']:
            raise ValueError('Conflicting approved masks for the same original image')
        by_digest.setdefault(digest,row)
    unique=[];mask_hashes=set()
    for row in by_digest.values():
        if row['mask_sha256']in mask_hashes:continue
        mask_hashes.add(row['mask_sha256']);unique.append(row)
    new=[r for r in unique if r.get('new_real_eligible') is True and r.get('data_origin') == 'real' and
         r.get('dataset') == 'real_approved' and r.get('synthetic') is not True and
         r.get('diagnostic') is not True and r.get('historically_exposed') is not True and
         r['image_sha256'] != DIAGNOSTIC_IMAGE_SHA256]
    groups={r['group_id'] for r in new}
    historic_rows=[]
    for path in historical_manifests:
        historic_rows += read_jsonl(bounded(path))
    lock_dir=bounded(validation_lock_dir) if validation_lock_dir is not None else bounded(DEFAULT_VALIDATION_LOCK_DIR)
    exposure_dir=lock_dir/'training_exposure'
    reserved_exposure=[]
    if exposure_dir.exists():
        for path in sorted(exposure_dir.glob('*.json')):
            reserved_exposure += json.loads(path.read_text(encoding='utf8'))['rows']
    for model in ModelRegistry(model_registry).list():
        historic_rows += read_jsonl(bounded(Path(model_registry)/model['artifacts']['train_manifest']['path']))
    historic_groups={r.get('group_id') for r in historic_rows};historic_images={r.get('image_sha256') for r in historic_rows}
    if public_manifest is not None:
        public_manifest=bounded(public_manifest)
        public=[r for r in read_jsonl(public_manifest) if r.get('split') in ('TRAIN','DEV')
                and r.get('license_permits_research') is True and not r.get('diagnostic') and not r.get('synthetic')
                and r.get('species','lettuce')=='lettuce']
        # Existing training lineage is exposed and cannot become real validation.
        historic_groups |= {r.get('group_id') for r in public}
        historic_images |= {r.get('image_sha256') for r in public}
    else:
        public=[]
    new=[r for r in new if r['image_sha256'] not in historic_images]
    groups={r['group_id'] for r in new}
    count=len(new)
    report=dict(schema_version=1,prepared_at=stamp(),status='BLOCKED_INSUFFICIENT_REAL_DATA',
        minimum_new_real=minimum_new_real,minimum_plant_groups=2,new_real_mask_count=count,plant_group_count=len(groups),
        excluded_development_or_synthetic_count=len(unique)-count,duplicate_original_count=len(rows)-len(unique),
        approved_registry=str(registry),approved_registry_sha256=sha256(registry.read_bytes()) if registry.exists() else None,
        public_manifest_sha256=sha256(public_manifest.read_bytes()) if public_manifest is not None else None,
        training_executed=False,comparison_executed=False,promotion_executed=False,reasons=[])
    output.mkdir(parents=True,exist_ok=False)
    if count < minimum_new_real or len(groups) < 2:
        report['reasons'].append(f'Need >= {minimum_new_real} unique new real approved masks across >= 2 physical plant groups')
        write_json(output/'preparation.json',report)
        return report
    if not any(r.get('split')=='DEV' for r in public):
        report['status']='BLOCKED_MISSING_CHECKPOINT_DEV'
        report['reasons'].append('A licensed, non-diagnostic, disjoint DEV set is required for checkpoint selection')
        write_json(output/'preparation.json',report);return report
    # This lock lives beside the durable approved registry, outside disposable runs.
    lock_dir.mkdir(parents=True,exist_ok=True)
    lock_path=bounded(lock_dir/'real_validation_lock.json')
    lock_digest_path=bounded(lock_dir/'real_validation_lock.sha256')
    with mutation_lock(lock_dir):
        if lock_path.exists():
            if not lock_digest_path.is_file() or lock_digest_path.read_text(encoding='utf8').strip() != sha256(lock_path.read_bytes()):
                raise ValueError('Persistent real validation lock changed or digest missing')
            lock=json.loads(lock_path.read_text(encoding='utf8'))
            locked=lock['rows'];reserved=set(lock['reserved_groups'])
            # Validate immutable held-out pixels even if later export omits them.
            for row in locked:
                if (sha256(bounded(row['image_path']).read_bytes()) != row['image_sha256'] or
                    sha256(bounded(row['mask_path']).read_bytes()) != row['mask_sha256']):
                    raise ValueError('Reserved real validation source changed')
            if reserved & historic_groups or {r['image_sha256'] for r in locked} & historic_images:
                report['status']='BLOCKED_VALIDATION_CONTAMINATED'
                report['reasons'].append('Persistent real validation has entered historical training')
                write_json(output/'preparation.json',report);return report
        else:
            ambiguous={g for digest,gs in duplicate_groups.items() if len(gs)>1 for g in gs}
            candidates=groups-historic_groups-ambiguous-{r.get('group_id') for r in reserved_exposure}
            candidates={g for g in candidates if not any(r['group_id']==g and r['image_sha256'] in historic_images for r in new)}
            if not candidates:
                report['status']='BLOCKED_NO_UNTOUCHED_REAL_PLANT'
                report['reasons'].append('All available real plants are historical training or identity-ambiguous')
                write_json(output/'preparation.json',report);return report
            ordered=sorted(candidates,key=lambda g:sha256(('6006:'+g).encode()))
            reserve_count=min(len(groups)-1,max(1,round(len(groups)*.2)),len(ordered))
            reserved=set(ordered[:reserve_count])
            locked=[{**r,'split':'real_validation'} for r in new if r['group_id'] in reserved]
            lock=dict(schema_version=1,created_at=stamp(),reserved_groups=sorted(reserved),rows=locked,
                      policy='Persistent untouched real physical-plant groups; never used to train or select checkpoints')
            write_json(lock_path,lock)
            with lock_digest_path.open('x',encoding='utf8') as stream:
                stream.write(sha256(lock_path.read_bytes())+'\n')
        reserved_images={r['image_sha256'] for r in locked}
        train=[{**r,'split':'TRAIN_ANCHOR' if r.get('diagnostic') else 'TRAIN'} for r in unique
               if not r.get('synthetic') and r.get('license_permits_research') is True and
               r['group_id'] not in reserved and r['image_sha256'] not in reserved_images]
        # Synthetic rows cannot enter any real subset; public masks retain existing lineage.
        train += [{**r,'lineage_split':r['split']} for r in public
                  if r.get('group_id') not in reserved and r.get('image_sha256') not in reserved_images]
        train_groups={r.get('group_id') for r in train if r['split'] in ('TRAIN','TRAIN_ANCHOR')}
        train_images={r.get('image_sha256') for r in train if r['split'] in ('TRAIN','TRAIN_ANCHOR')}
        dev=[r for r in train if r['split']=='DEV']
        if any(r.get('group_id') in train_groups or r.get('image_sha256') in train_images for r in dev):
            report['status']='BLOCKED_CHECKPOINT_DEV_OVERLAP';report['reasons'].append('TRAIN/anchor and DEV images/groups overlap')
            write_json(output/'preparation.json',report);return report
        if not any(r['split']=='TRAIN' for r in train):
            report['status']='BLOCKED_EMPTY_TRAINING_SET';report['reasons'].append('No nonreserved training masks')
            write_json(output/'preparation.json',report);return report
        write_manifest(output/'train_manifest.jsonl',train)
        write_manifest(output/'real_validation_manifest.jsonl',locked)
        # Conservatively reserve prepared training identities even if a native
        # run crashes before registration. A new export cannot recycle these as
        # untouched validation.
        exposure_dir.mkdir(exist_ok=True)
        exposure_path=exposure_dir/(sha256(str(output).encode())+'.json')
        write_json(exposure_path,dict(prepared_at=stamp(),manifest_sha256=sha256((output/'train_manifest.jsonl').read_bytes()),
            rows=[dict(group_id=r['group_id'],image_sha256=r['image_sha256']) for r in train if r['split'] in ('TRAIN','TRAIN_ANCHOR')]))
        report.update(status='READY_FOR_TRAINING',train_count=sum(r['split']=='TRAIN' for r in train),
            checkpoint_dev_count=sum(r['split']=='DEV' for r in train),real_validation_count=len(locked),
            reserved_groups=sorted(reserved),persistent_lock=str(lock_path),persistent_lock_sha256=sha256(lock_path.read_bytes()),
            withheld_additional_reserved_group_rows=sum(r['group_id'] in reserved and r['image_sha256'] not in reserved_images for r in unique),
            train_manifest=str(output/'train_manifest.jsonl'),validation_manifest=str(output/'real_validation_manifest.jsonl'),
            promotion_note='Readiness does not satisfy independent real-report promotion requirements')
        write_json(output/'preparation.json',report)
        return report


def execute_prepared(result, config, architecture, *, model_registry=ROOT/'models/lettuce'):
    """Train a fresh version, compare against the pinned champion, never promote."""
    if result.get('status') != 'READY_FOR_TRAINING':
        raise ValueError('Only ready, verified real-data preparations may execute')
    if architecture not in ('unet_resnet18','segformer_b0'):
        raise ValueError('Unsupported training architecture')
    config=bounded(config);manifest=bounded(result['train_manifest']);validation=bounded(result['validation_manifest'])
    output=manifest.parent;train_output=bounded(output/'training_run');comparison_output=bounded(output/'comparison')
    if train_output.exists() or comparison_output.exists():
        raise FileExistsError('Training/comparison run is create-only')
    registry=ModelRegistry(model_registry);previous=registry.champion()
    from .train import create_training_lock, training_config
    run_config=bounded(output/'retraining_config.json')
    settings=training_config(json.loads(config.read_text(encoding='utf8')),architecture)
    settings['training_manifest_sha256']=sha256(manifest.read_bytes())
    # The source research configuration may name an earlier augmentation file.
    # The fresh lock snapshots the current configuration for this actual run.
    settings.pop('augmentation_config_sha256',None)
    settings['source_config_sha256']=sha256(config.read_bytes())
    write_json(run_config,settings)
    training_lock=create_training_lock(manifest,{architecture:run_config},output/'training_lock.json',
                                      additional_files=(validation,config))
    command=[sys.executable,'-m','src.training.train','--manifest',str(manifest),'--config',str(run_config),
             '--lock',str(training_lock),
             '--architecture',architecture,'--output',str(train_output)]
    evaluate=[sys.executable,'-m','src.training.evaluate','--manifest',str(validation),
              '--checkpoint',str(train_output/'model.safetensors'),'--config',str(train_output/'config.json'),
              '--output',str(comparison_output),'--real-validation']
    if previous is not None:
        evaluate += ['--previous-checkpoint',str(bounded(registry.root/previous['artifacts']['checkpoint']['path'])),
                     '--previous-config',str(bounded(registry.root/previous['artifacts']['config']['path'])),
                     '--current-champion-version',previous['version']]
    write_json(output/'execution_commands.json',dict(training=command,comparison=evaluate,
        previous_champion_version=previous['version'] if previous else None,promotion_executed=False))
    subprocess.run(command,cwd=ROOT,check=True)
    subprocess.run(evaluate,cwd=ROOT,check=True)
    report_path=bounded(comparison_output/'evaluation.json');report=json.loads(report_path.read_text(encoding='utf8'))
    expected_previous=previous['version'] if previous else None
    if (report.get('status') != 'COMPLETED' or report.get('scope') != 'REAL_RETRAIN' or
        report.get('comparison',{}).get('status') != 'COMPLETED' or
        report['comparison'].get('current_champion_version') != expected_previous or
        report.get('checkpoint_sha256') != sha256(bounded(train_output/'model.safetensors').read_bytes())):
        raise ValueError('Comparison did not complete or is not bound to new weights and pinned champion')
    execution={**result,'training_executed':True,'comparison_executed':True,'promotion_executed':False,
        'comparison_report':str(report_path),'comparison_sha256':sha256(report_path.read_bytes()),
        'previous_champion_version':expected_previous,
        'next_step':'Register immutable new model and comparison artifacts; review eligibility and request separate human promotion.'}
    write_json(output/'execution.json',execution)
    return execution


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--registry',type=Path,default=ROOT/'data/approved_masks/approved_registry.jsonl')
    source.add_argument('--collect-web',action='store_true',help='Read-only collect committed human-approved lettuce masks from current configured web DB')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--minimum-new-real',type=int,default=10)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--historical-manifest',type=Path,action='append',default=[])
    parser.add_argument('--public-manifest',type=Path)
    parser.add_argument('--model-registry',type=Path,default=ROOT/'models/lettuce')
    parser.add_argument('--validation-lock-dir',type=Path,help='Durable reservation directory; default data/approved_masks for all registry locations')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--architecture',choices=('unet_resnet18','segformer_b0'),default='unet_resnet18')
    args=parser.parse_args(argv)
    registry_path=args.registry
    lock_dir=args.validation_lock_dir
    if args.collect_web:
        output=bounded(args.output)
        if output.exists():
            raise FileExistsError('Preparation output already exists')
        destination=bounded(output.with_name(output.name+'-approved'))
        if destination.exists():
            raise FileExistsError('Collected approved export already exists')
        from ..webapp.service import Service
        from ..webapp.settings import Settings
        from .approved_registry import export_web
        service=Service(Settings.from_env())
        try:
            exported=export_web(service,destination)
        finally:
            service.db.engine.dispose()
        registry_path=Path(exported['registry_path'])
        if lock_dir is None:
            lock_dir=ROOT/'data/approved_masks'
    result=prepare(registry_path,args.output,args.minimum_new_real,historical_manifests=args.historical_manifest,
                   public_manifest=args.public_manifest,model_registry=args.model_registry,validation_lock_dir=lock_dir)
    if result['status']!='READY_FOR_TRAINING' or args.prepare_only:
        print(json.dumps(result,allow_nan=False));return 0
    if args.config is None:
        parser.error('--config is required to execute an eligible prepared training run')
    execution=execute_prepared(result,args.config,args.architecture,model_registry=args.model_registry)
    print(json.dumps(execution,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
