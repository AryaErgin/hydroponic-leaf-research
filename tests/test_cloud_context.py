import hashlib
import json
import pytest
from src.cloud_setup.context import runtime_source_path,verify_context

def test_build_source_allowlist_excludes_private_and_unnecessary_files():
    for path in ['.git/config','.env','data/photo.png','models/model.safetensors','.runtime/account.json',
                 'src/firebase_preview/client/node_modules/a.js','configs/private-credentials.json',
                 'docs/INCIDENT.md','src/example/__pycache__/x.pyc','src/secret.key']:
        assert not runtime_source_path(path)
    for path in ['src/cloud_instance/runtime.py','src/webapp/static/app.js','configs/lettuce.json',
                 'src/firebase_preview/client/package-lock.json','infra/cloud/Dockerfile','requirements-web.txt']:
        assert runtime_source_path(path)

def test_forged_self_hashed_receipt_cannot_authorize_any_source(tmp_path):
    payload=b'private-but-self-hashed-fixture'
    (tmp_path/'private-key.json').write_bytes(payload)
    (tmp_path/'context-manifest.json').write_text(json.dumps({'files':{'private-key.json':{'sha256':hashlib.sha256(payload).hexdigest()}}}))
    with pytest.raises(ValueError):verify_context(tmp_path)
