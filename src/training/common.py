"""Root-bounded, create-only experiment artifacts and deterministic identifiers."""
import hashlib
import json
from pathlib import Path
from ..hydroponic.config import ROOT


def bounded(path):
    path=Path(path).resolve()
    if ROOT not in path.parents:raise ValueError('Experiment files must remain below the project root')
    return path


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,data):
    path=bounded(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(data,stream,indent=2,allow_nan=False);stream.write('\n')


def read_manifest(path):
    with bounded(path).open(encoding='utf-8') as stream:return [json.loads(line) for line in stream if line.strip()]


def write_manifest(path,rows):
    path=bounded(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        for row in rows:stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')


def relative(path):return bounded(path).relative_to(ROOT).as_posix()
