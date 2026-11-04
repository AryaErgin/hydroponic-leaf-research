"""Create-only final source/handoff snapshot and EXP-005 hash manifest."""
from pathlib import Path
from .exp005 import ROOT,OUT,sha,put_json


def seal():
    if (OUT/'artifact_manifest.json').exists():raise FileExistsError('EXP-005 already sealed')
    snapshot=OUT/'final_source_and_handoff_snapshot';snapshot.mkdir()
    sources=sorted((ROOT/'src').glob('*exp005*.py'))+[ROOT/'tests/test_exp005.py']
    handoff=[ROOT/p for p in ['AGENTS.md','TASKS.md','docs/PROJECT_STATUS.md',
        'docs/reports/LATEST_REPORT.md','experiments/EXPERIMENT_LOG.md']]
    current={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in sources+handoff}
    for p in sources+handoff:
        dest=snapshot/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('xb') as f:f.write(p.read_bytes())
    put_json(OUT/'final_source_and_handoff_hashes.json',current)
    files=[p for p in OUT.rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.name not in ['artifact_manifest.json','final_verification.json','seal_receipt.json']]
    # Mutable live status/LATEST aliases are archived, not future integrity gates.
    # Analysis/inference source and test files remain part of this frozen study.
    files+=sources
    manifest={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in sorted(set(files))}
    put_json(OUT/'artifact_manifest.json',manifest)
    print(f'Sealed {len(manifest)} static files; mutable handoff copies archived.')


if __name__=='__main__':seal()
