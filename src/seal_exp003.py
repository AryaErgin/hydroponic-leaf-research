"""Freeze final reports/supplemental sources; check immutable experiment evidence."""
import argparse
import json
from pathlib import Path
from .exp001 import ROOT,RAW,OUT as BASE,read_csv,label_path,sha,write_json
from .exp003 import OUT,read_lock,protected_artifacts


SNAPSHOT=[ROOT/'src'/n for n in ['exp003_diagnostics.py','exp003_report.py','verify_exp003.py','verify_exp002.py','seal_exp003.py']]+[
    ROOT/'tests'/n for n in ['test_exp001.py','test_exp002.py','test_exp003.py']]+[
    ROOT/n for n in ['requirements-exp003.txt','AGENTS.md','TASKS.md','experiments/EXPERIMENT_LOG.md',
                    'docs/PROJECT_STATUS.md','docs/reports/LATEST_REPORT.md']]


def check_core():
    _,prov=read_lock(OUT)
    assert protected_artifacts()==prov['protected_artifacts_sha256'],'Prior experiment drift'
    for r in read_csv(BASE/'inventory.csv'):
        assert sha(RAW/'images'/r['filename'])==r['image_sha256'] and sha(label_path(r))==r['label_sha256'],'Raw pair drift'
    for name,digest in json.loads((OUT/'output_hashes.json').read_text()).items():
        assert sha(OUT/name)==digest,'Frozen output drift: '+name
    verification=json.loads((OUT/'verification.json').read_text())
    assert verification['status']=='PASS' and verification['tuning_masks_recomputed']==216
    assert sha(ROOT/'src/verify_exp003.py')==verification['audit_source_sha256'],'Independent verifier source drift'
    assert (OUT/'summary.md').read_bytes()==(ROOT/'docs/reports/LATEST_REPORT.md').read_bytes(),'Current handoff drift'
    return verification


def seal():
    manifest=OUT/'final_artifact_manifest.json';snapshot=OUT/'final_source_and_handoff_snapshot'
    if manifest.exists() or snapshot.exists():raise FileExistsError('Existing final seal')
    verification=check_core();snapshot.mkdir()
    sources={}
    for p in SNAPSHOT:
        name=str(p.relative_to(ROOT));target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write(p.read_bytes())
        sources[name]=sha(p)
    files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()}
    write_json(manifest,dict(status='SEALED',benchmark='DEVELOPMENT_ONLY_NOT_UNBIASED_FINAL_TEST',
        confirmation_images=0,artifact_files_sha256=files,contemporaneous_workspace_sha256=sources,
        raw_pairs_rechecked=1522,prior_experiments_unchanged=True,independent_checks=verification['checks'],
        note='Live task/latest files may evolve later; immutable contemporaneous copies are in the final snapshot.'))
    print(f'SEALED {len(files)} artifact files; snapshots; all 1522 raw pairs and prior experiments unchanged')


def check():
    data=json.loads((OUT/'final_artifact_manifest.json').read_text());files=data['artifact_files_sha256']
    actual={str(p.relative_to(OUT)) for p in OUT.rglob('*') if p.is_file() and p.name!='final_artifact_manifest.json'}
    assert actual==set(files),'Final experiment file inventory drift'
    for name,digest in files.items():assert sha(OUT/name)==digest,'Final artifact drift: '+name
    for name,digest in data['contemporaneous_workspace_sha256'].items():
        assert sha(OUT/'final_source_and_handoff_snapshot'/name)==digest,'Final snapshot drift'
    check_core()
    print(f'PASS final seal: {len(files)} files, source/handoff snapshots, frozen outputs, prior experiments and all raw pairs')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['seal','check']);args=ap.parse_args()
    (seal if args.command=='seal' else check)()


if __name__=='__main__':main()
