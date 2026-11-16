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


def test_only_the_seven_reviewed_tutorial_images_and_manifest_enter_cloud_context():
    for name in ['manifest.json', *(f'step-{i:02}.webp' for i in range(1,8))]:
        assert runtime_source_path('src/webapp/static/tutorial/'+name)
    for name in ['private.webp', 'step-08.webp', 'step-01.png', 'personal.json', '../private.webp']:
        assert not runtime_source_path('src/webapp/static/tutorial/'+name)
    assert not runtime_source_path('src/webapp/static/private-photo.webp')


@pytest.mark.parametrize('tracked',['src/cloud_instance/runtime.py',''])
def test_packager_rejects_uncommitted_runtime_source_instead_of_silently_shipping_old_code(tmp_path,monkeypatch,tracked):
    from src.cloud_setup import context
    calls=[]
    def git(root,*args):
        calls.append(args)
        if args==('diff','--name-only','HEAD'):return (tracked+'\n').encode()
        if args==('ls-files','--others','--exclude-standard'):return b'src/cloud_instance/uncommitted.py\n' if not tracked else b''
        raise AssertionError('Dirty runtime source must fail before reading source contents')
    monkeypatch.setattr(context,'_git',git)
    with pytest.raises(ValueError,match='Commit'):context._source(tmp_path)
