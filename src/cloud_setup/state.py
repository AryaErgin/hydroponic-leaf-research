"""Atomic local resumable deployment metadata, never credential storage."""
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def save_state(path,state):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            if os.name!='nt':os.chmod(temporary,0o600)
            stream.write(json.dumps(state,sort_keys=True,indent=2).encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def load_state(path):
    value=json.loads(Path(path).read_text(encoding='utf8'))
    if not isinstance(value,dict):raise ValueError('Invalid local deployment state; preserve the file and ask the maintainer.')
    return value


def consent_digest(plan):
    value=copy.deepcopy(plan)
    # Public provider-issued client IDs arrive after project setup; they do not
    # change the approved resources/cost envelope. Their project binding is strict.
    if isinstance(value.get('manifest'),dict):value['manifest'].pop('firebase_web',None)
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def require_consent(state,plan):
    if state.get('approved_plan_sha256')!=consent_digest(plan):
        raise ValueError('This resource plan has not been explicitly approved; no cloud write is permitted.')
