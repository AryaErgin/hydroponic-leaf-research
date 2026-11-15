"""Build only committed application source and the trusted authorized checkpoint."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import tempfile

from ..local_setup import model_assets

ROOT=Path(__file__).resolve().parents[2]
REQUIREMENTS={'requirements-web.txt','requirements-firebase-preview.txt','requirements-local-constraints.txt',
    'requirements-model-cpu.txt','requirements-model-common.txt'}
INFRA={'infra/cloud/Dockerfile','infra/cloud/cloudbuild.yaml','infra/cloud/.dockerignore','infra/cloud/firestore.rules'}
GCLOUDIGNORE=b'# Strict context already selected from committed source and model allowlist.\n.git\n**/__pycache__/\n*.pyc\n'


def runtime_source_path(name):
    path=PurePosixPath(name)
    if (not isinstance(name,str) or '\\' in name or path.is_absolute() or
        any(part in ('','.','..','node_modules','__pycache__') for part in path.parts)):
        return False
    if name in REQUIREMENTS or name in INFRA:return True
    if name.startswith('configs/') and path.suffix=='.json':
        return not any(word in name.lower() for word in ('private','credential','secret','firebase-web')) and not name.startswith('configs/runtime/firebase-tools/')
    if not name.startswith('src/'):return False
    if path.suffix=='.py':return True
    if '/static/' in name and path.suffix in ('.js','.css','.html') and path.name!='firebase-sdk.js':return True
    return name in {'src/firebase_preview/client/package.json','src/firebase_preview/client/package-lock.json','src/firebase_preview/client/sdk-entry.js'}


def _git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode:raise ValueError('Build packaging requires a valid committed repository checkout.')
    return result.stdout


def _source(root):
    changed=_git(root,'diff','--name-only','HEAD').decode().splitlines()
    untracked=_git(root,'ls-files','--others','--exclude-standard').decode().splitlines()
    if any(runtime_source_path(name) for name in changed+untracked):
        raise ValueError('Commit reviewed runtime changes before packaging; uncommitted code will not silently be replaced by an older release.')
    commit=_git(root,'rev-parse','HEAD').decode().strip()
    names=[name for name in _git(root,'ls-tree','-r','--name-only','HEAD').decode().splitlines() if runtime_source_path(name)]
    required=REQUIREMENTS|INFRA|{'src/cloud_instance/runtime.py','configs/runtime/model-assets.json'}
    if not required.issubset(set(names)):
        raise ValueError('Commit the complete independent-cloud runtime and container files before packaging.')
    files={name:_git(root,'show','HEAD:'+name) for name in names}
    # The asset verifier's trust anchor must be the committed policy that will
    # actually reach the image, never a modified local policy beside a receipt.
    if model_assets.POLICY_PATH.read_bytes().replace(b'\r\n',b'\n')!=files['configs/runtime/model-assets.json'].replace(b'\r\n',b'\n'):
        raise ValueError('Model policy differs from the committed trusted source; commit reviewed changes before packaging.')
    files['.dockerignore']=files['infra/cloud/.dockerignore']
    files['.gcloudignore']=GCLOUDIGNORE
    return commit,files


def _regular(path,root):
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Build context contains an unsafe path.')
    for ancestor in (path,*path.parents):
        if ancestor==root.parent:break
        if ancestor.is_symlink() or getattr(ancestor.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('Build context cannot contain symlinks or reparse points.')
    return path.read_bytes()


def verify_context(context_path,*,source_root=ROOT):
    root=Path(source_root).resolve();context=Path(context_path).resolve()
    if not context.is_relative_to(root) or context==root:raise ValueError('Build context must be an isolated child of this repository.')
    commit,expected=_source(root)
    try:receipt=json.loads(_regular(context/'context-manifest.json',context))
    except (OSError,ValueError):raise ValueError('A trusted committed-source packaging receipt is required.') from None
    if set(receipt)!={'schema_version','source_commit','files'} or receipt['schema_version']!=1 or receipt['source_commit']!=commit:
        raise ValueError('Build context is not bound to the current trusted source commit.')
    policy=model_assets._load_policy()
    model_assets.verify_installed(context)
    for name in policy['files']:expected[name]=_regular(context/name,context)
    expected_receipt={name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)} for name,data in expected.items()}
    if receipt['files']!=expected_receipt:raise ValueError('Build receipt differs from trusted source/model allowlists.')
    actual={path.relative_to(context).as_posix() for path in context.rglob('*') if path.is_file()}
    if actual!=set(expected)|{'context-manifest.json'}:raise ValueError('Unexpected private or generated file in build context.')
    for name,data in expected.items():
        if _regular(context/name,context)!=data:raise ValueError('Build source changed after verified packaging.')
    return receipt


def create_context(bundle_path,context_path,*,source_root=ROOT):
    root=Path(source_root).resolve();context=Path(context_path).resolve()
    if not context.is_relative_to(root) or context==root:raise ValueError('Keep the private build context below this repository.')
    if context.exists():return verify_context(context,source_root=root)
    commit,files=_source(root)
    context.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.cloud-context-',dir=context.parent)).resolve()
    try:
        for name,data in files.items():
            destination=stage/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
        model_assets.install_bundle(bundle_path,stage)
        for name in model_assets._load_policy()['files']:files[name]=(stage/name).read_bytes()
        receipt=dict(schema_version=1,source_commit=commit,files={name:dict(sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)) for name,data in files.items()})
        (stage/'context-manifest.json').write_text(json.dumps(receipt,sort_keys=True,indent=2),encoding='utf8')
        os.rename(stage,context)
    finally:
        if stage.exists():
            if not stage.is_relative_to(root) or not stage.name.startswith('.cloud-context-'):raise ValueError('Refusing unsafe temporary context cleanup.')
            shutil.rmtree(stage)
    return verify_context(context,source_root=root)
