"""Durable owner-only batch inbox. No training, promotion or professor endpoint."""
import argparse
import copy
import hashlib
import json
import subprocess
from datetime import datetime,timezone
from pathlib import Path

from sqlalchemy import insert,select,update
from .model_registry import ModelRegistry,bounded,read_jsonl,sha256

PROJECT='hydroponic-leaf-research-aryae'
THRESHOLD=10
SPECIES=('lettuce','rumex_acetosa')

def snapshot(registry,species='lettuce',successful=None):
    if species not in SPECIES:raise ValueError('Unsupported species')
    if successful:
        if successful.get('species')!=species or successful.get('status')!='COMPLETED':raise ValueError('Wrong/unsuccessful species training snapshot')
        return successful['snapshot_id'],set(successful['covered_image_sha256'])
    manifests=[];trained=set()
    for model in (registry.list() if species=='lettuce' else []):
        if model.get('species','lettuce')!=species:continue
        ref=model['artifacts']['train_manifest'];path=bounded(registry.root/ref['path'])
        digest=sha256(path.read_bytes())
        if digest!=ref['sha256']:raise ValueError('Training snapshot hash mismatch')
        manifests.append((model['version'],digest))
        trained.update(r['image_sha256']for r in read_jsonl(path)if r.get('image_sha256'))
    return sha256(json.dumps([species,sorted(manifests)],separators=(',',':')).encode()),trained

def advance(state,row,snapshot_id,trained,species='lettuce',threshold=THRESHOLD):
    if species not in SPECIES or type(threshold)is not int or threshold<1:raise ValueError('Invalid species/threshold')
    state=copy.deepcopy(state)if state else dict(snapshot_id=snapshot_id,species=species,threshold=threshold,
        status='ACCUMULATING',audience='PROJECT_OWNER_ONLY',samples={},new_eligible_count=0,
        training_executed=False,promotion_executed=False,deployment_executed=False)
    if state['snapshot_id']!=snapshot_id:raise ValueError('Notification snapshot mismatch')
    if state.get('species',species)!=species:raise ValueError('Cross-species notification state')
    if (row.get('species')!=species or row.get('review_status')!='HUMAN_APPROVED' or
        row.get('new_real_eligible')is not True or row.get('data_origin')!='real' or
        row.get('synthetic')or row.get('test_only')or row.get('demo')or row.get('diagnostic')or row.get('historically_exposed')):return state,False
    digest=row['assets']['original']['sha256']
    mask_digest=row['assets'].get('approved_mask',{}).get('sha256')
    if digest in trained or digest in state['samples'] or (mask_digest and mask_digest in state.get('mask_hashes',[])):return state,False
    state['samples'][digest]=row['id'];state['new_eligible_count']=len(state['samples'])
    if mask_digest:state.setdefault('mask_hashes',[]).append(mask_digest)
    crossing=state['new_eligible_count']>=state['threshold'] and not state.get('notified_at')
    if crossing:
        state.update(status='OWNER_REVIEW_REQUIRED',notified_at=datetime.now(timezone.utc).isoformat(),
            message=f'Hydroponic model update ready for local training. Species: {species}. New approved masks: {state["new_eligible_count"]}. Run the local update workflow when convenient. No automatic training or promotion.')
    return state,crossing

def record_approval(db,conn,canonical,registry):
    if not canonical.get('new_real_eligible'):return None
    species=canonical['species']
    previous=conn.execute(select(db.owner_training_snapshots).where(db.owner_training_snapshots.c.id==species)).mappings().first()
    sid,trained=snapshot(registry,species,previous['payload']if previous else None);table=db.owner_notifications
    existing=conn.execute(select(table).where(table.c.id==sid)).mappings().first()
    state,event=advance(existing['payload']if existing else None,canonical,sid,trained,species=species)
    if existing:conn.execute(update(table).where(table.c.id==sid).values(payload=state))
    else:conn.execute(insert(table).values(id=sid,payload=state))
    return state if event else None

def email_log_entry(state,namespace='hydroponic'):
    return dict(severity='NOTICE',event='owner_retraining_batch_ready',registry_namespace=namespace,
        species=state['species'],batch_id=state['snapshot_id'],new_approved_masks=state['new_eligible_count'],
        threshold=state['threshold'],message=state['message'],owner_only=True,automatic_training=False,
        automatic_promotion=False,automatic_deployment=False)

def emit_email_event(state,namespace):
    # Bare JSON stdout is parsed as jsonPayload by Cloud Run; Monitoring delivers
    # OPENED-only email to the one owner channel. Never emitted before DB commit.
    print(json.dumps(email_log_entry(state,namespace),sort_keys=True),flush=True)

def successful_training_snapshot(report_path,species):
    report_path=bounded(report_path);report=json.loads(report_path.read_text())
    if report.get('species','lettuce')!=species or not report.get('training_executed'):
        raise ValueError('Completed training for the matching species required')
    # Lettuce execution inherits READY_FOR_TRAINING; its independently completed
    # training/evaluation artifacts are authoritative, not that preparation label.
    run=report_path.parent/'training_run';status=json.loads((run/'status.json').read_text())
    evaluation_path=bounded(report['comparison_report']);evaluation=json.loads(evaluation_path.read_text())
    checkpoint=bounded(report.get('checkpoint',run/'model.safetensors'));digest=sha256(checkpoint.read_bytes())
    if status.get('status')!='COMPLETED'or status.get('species','lettuce')!=species or evaluation.get('status')!='COMPLETED'or evaluation.get('species','lettuce')!=species or evaluation.get('checkpoint_sha256')!=digest:
        raise ValueError('Successful species-bound training/checkpoint/evaluation required')
    registry=bounded(report.get('registry_path',report.get('approved_registry')))
    if sha256(registry.read_bytes())!=report.get('registry_sha256',report.get('approved_registry_sha256')):
        raise ValueError('Training approval snapshot changed')
    from .approved_registry import from_measurement
    covered=[]
    for row in read_jsonl(registry):
        if row['species']!=species:raise ValueError('Cross-species training snapshot')
        canonical=from_measurement(row,row['assets'])
        if canonical['new_real_eligible']:covered.append(canonical['assets']['original']['sha256'])
    return dict(status='COMPLETED',species=species,snapshot_id=sha256((species+digest+sha256(registry.read_bytes())).encode()),
        covered_image_sha256=sorted(set(covered)),checkpoint_sha256=digest,
        report_sha256=sha256(report_path.read_bytes()),recorded_at=datetime.now(timezone.utc).isoformat())

def cloud_credentials():
    def cli(args):
        # Authorized cloud tooling must be installed on this machine's PATH.
        name='gcloud.cmd' if __import__('os').name=='nt' else 'gcloud'
        command=[name,*args]
        return subprocess.run(command,capture_output=True,text=True,check=True).stdout.strip()
    if cli(['config','get-value','project','--configuration=hydroponic-research'])!=PROJECT:
        raise ValueError('Dedicated hydroponic project configuration required')
    if cli(['config','configurations','list','--filter=is_active:true','--format=value(name)'])!='hydroponic-research':
        raise ValueError('Active configuration must be hydroponic-research')
    from google.oauth2.credentials import Credentials
    return Credentials(cli(['auth','print-access-token','--project='+PROJECT,'--configuration=hydroponic-research']),quota_project_id=PROJECT)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cloud',action='store_true',required=True)
    p.add_argument('--sync',type=Path,help='Owner-only read-only cloud export for LOCAL retraining')
    p.add_argument('--species',choices=SPECIES,default='lettuce')
    p.add_argument('--record-successful-training',type=Path,help='Owner-only record verified completed LOCAL training/evaluation snapshot; no promotion')
    a=p.parse_args(argv)
    from google.cloud import firestore,storage
    cred=cloud_credentials();db=firestore.Client(project=PROJECT,credentials=cred)
    if a.record_successful_training:
        record=successful_training_snapshot(a.record_successful_training,a.species)
        cloud_credentials() # dedicated-project recheck before the owner mutation
        db.collection('hydroponic_owner_training_snapshots').document(a.species).set(dict(id=a.species,payload_json=json.dumps(record)))
    rows_by_species={species:[json.loads(d.to_dict()['payload_json'])for d in db.collection('hydroponic_'+('approved_masks'if species=='lettuce'else'approved_sorrel_masks')).stream()]for species in SPECIES}
    counters={}
    from .approved_registry import from_measurement
    for species,rows in rows_by_species.items():
        saved=db.collection('hydroponic_owner_training_snapshots').document(species).get()
        successful=json.loads(saved.to_dict()['payload_json'])if saved.exists else None
        sid,trained=snapshot(ModelRegistry(),species,successful);state=None
        for row in rows:
            canonical=from_measurement(row,row['assets']);state,_=advance(state,canonical,sid,trained,species=species)
        if state is None:state,_=advance(None,{},sid,trained,species=species)
        counters[species]=state
    inbox=[json.loads(d.to_dict()['payload_json'])for d in db.collection('hydroponic_owner_notifications').stream()]
    result=dict(project=PROJECT,current_snapshot=counters[a.species],counters=counters,owner_inbox=inbox,
        notification_mechanism='Google Cloud Monitoring OPENED-only email to configured owner; durable species inbox/log. No professor notification.',
        training_executed=False,promotion_executed=False)
    if a.sync:
        # Use the existing native/hash/provenance-verified exporter unchanged.
        from .approved_registry import export_web
        from types import SimpleNamespace
        bucket=storage.Client(project=PROJECT,credentials=cred).bucket(PROJECT+'-media')
        class CloudMedia:
            def read(self,ref):
                data=bucket.blob('hydroponic/'+ref['key']).download_as_bytes(timeout=60)
                if sha256(data)!=ref['sha256']:raise ValueError('Cloud sync asset hash mismatch')
                return data
        rows=rows_by_species[a.species]
        adapter=SimpleNamespace(db=SimpleNamespace(approved_masks=True,approved_sorrel_masks=True,rows=lambda table:[dict(payload=r)for r in rows]),media=CloudMedia())
        result['sync']=export_web(adapter,bounded(a.sync),species=a.species)
    print(json.dumps(result,indent=2,allow_nan=False));return 0

if __name__=='__main__':raise SystemExit(main())
