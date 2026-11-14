"""Create-only, offline installation of the authorized immutable research model.

The checked-in policy is the trust anchor. A bundle cannot add paths or change
digests by supplying its own manifest. No Torch, pickle, network or cloud client
is imported here. Only safetensors is accepted by the scientific inference code.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT/'configs/runtime/model-assets.json'
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_FILES = 24
MAX_COMPRESSION_RATIO = 100
MODEL_PREFIX = 'models/lettuce/'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value: raise ValueError('Duplicate JSON key')
            value[key]=item
        return value
    return json.loads(data,object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))


def _safe_name(name):
    if (not isinstance(name,str) or '\\' in name or ':' in name or '\x00' in name or
        name.startswith('/') or name.endswith('/') or
        any(part in ('','.','..') or part.endswith((' ','.')) for part in name.split('/')) or
        PurePosixPath(name).as_posix()!=name):
        raise ValueError('Unsafe bundle path')
    return name


def _load_policy():
    policy=_json(POLICY_PATH.read_bytes())
    if (policy.get('schema_version')!=1 or policy.get('version')!='exp006-segformer-001' or
        policy.get('architecture')!='segformer_b0' or policy.get('species')!='lettuce' or
        not 7<=len(policy.get('files',{}))<=MAX_FILES):
        raise ValueError('Unsupported model policy')
    for name,ref in policy['files'].items():
        _safe_name(name)
        if not name.startswith(MODEL_PREFIX) or set(ref)!={'sha256','bytes'}:
            raise ValueError('Invalid policy path or artifact')
        if type(ref['bytes']) is not int or not 0<ref['bytes']<=MAX_FILE_BYTES:
            raise ValueError('Invalid policy size')
        if not isinstance(ref['sha256'],str) or len(ref['sha256'])!=64:
            raise ValueError('Invalid policy digest')
    return policy


def _regular_below(root,name):
    root=Path(root).absolute()
    path=root
    # Reject symlinks and Windows junction/reparse points, including ancestors.
    for ancestor in (root,*root.parents):
        if ancestor.exists():
            info=ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Symlink/reparse root is not allowed')
    for part in PurePosixPath(_safe_name(name)).parts:
        path=path/part
        if path.exists() or path.is_symlink():
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Symlink/reparse asset is not allowed')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Asset escaped root')
    return path


def _validate_contents(contents,policy):
    if set(contents)!=set(policy['files']):
        raise ValueError('Missing or unlisted model assets')
    for name,ref in policy['files'].items():
        data=contents[name]
        if len(data)!=ref['bytes'] or _sha(data)!=ref['sha256']:
            raise ValueError('Model asset size or SHA256 mismatch')
    version=policy['version']
    prefix=MODEL_PREFIX+'versions/'+version+'/'
    pointer=_json(contents[MODEL_PREFIX+'champion.json'])
    registry=_json(contents[prefix+'registry.json'])
    config=_json(contents[prefix+'config.json'])
    evaluation=_json(contents[prefix+'evaluation.json'])
    provenance=_json(contents[prefix+'provenance.json'])
    if (pointer.get('schema_version')!=1 or pointer.get('version')!=version or
        registry.get('schema_version')!=1 or registry.get('version')!=version or
        registry.get('species')!=policy['species'] or registry.get('architecture')!=policy['architecture'] or
        config.get('architecture')!=policy['architecture'] or config.get('input_size')!=512 or
        config.get('threshold')!=.5 or config.get('normalization')!={'mean':[.485,.456,.406],'std':[.229,.224,.225]}):
        raise ValueError('Model registry/config/version/preprocessing mismatch')
    filenames={'checkpoint':'model.safetensors','config':'config.json','train_manifest':'train_manifest.jsonl',
               'provenance':'provenance.json','evaluation':'evaluation.json'}
    refs=registry.get('artifacts',{})
    if set(refs)!=set(filenames): raise ValueError('Unsupported registry artifacts')
    for key,filename in filenames.items():
        name=prefix+filename
        expected=policy['files'][name]
        if refs[key]!={'path':'versions/'+version+'/'+filename,**expected}:
            raise ValueError('Registry artifact reference mismatch')
    checkpoint=policy['checkpoint_sha256']
    if (refs['checkpoint']['sha256']!=checkpoint or
        pointer.get('report_sha256')!=refs['evaluation']['sha256'] or
        evaluation.get('checkpoint_sha256')!=checkpoint or evaluation.get('status')!='COMPLETED' or
        provenance.get('registered_version')!=version or
        provenance.get('actual_completed_training',{}).get('checkpoint_sha256')!=checkpoint):
        raise ValueError('Model checkpoint/evaluation/provenance mismatch')


def _summary(policy,status):
    return dict(status=status,schema_version=1,version=policy['version'],architecture=policy['architecture'],
                checkpoint_sha256=policy['checkpoint_sha256'],file_count=len(policy['files']),
                total_bytes=sum(ref['bytes'] for ref in policy['files'].values()),
                target_validation='UNVALIDATED / REVIEW REQUIRED',
                use_limit='Noncommercial research/evaluation only')


def _read_installed(project_root,policy,*,strict=True):
    contents={}
    for name,ref in policy['files'].items():
        path=_regular_below(project_root,name)
        if not path.is_file() or path.stat().st_size!=ref['bytes']:
            raise ValueError('Required model asset missing or wrong size')
        contents[name]=path.read_bytes()
    if strict:
        root=Path(project_root).absolute()
        actual=set()
        for path in (root/'models/lettuce').rglob('*'):
            _regular_below(root,path.relative_to(root).as_posix())
            if path.is_file(): actual.add(path.relative_to(root).as_posix())
        if actual!=set(policy['files']):
            raise ValueError('Installed model directory contains unlisted assets')
    _validate_contents(contents,policy)
    return contents


def verify_installed(project_root):
    """Verify byte-exact artifacts and scientific registry bindings, offline."""
    policy=_load_policy()
    _read_installed(project_root,policy)
    return _summary(policy,'VERIFIED')


def export_bundle(source_root,bundle_path,*,notices_root=None):
    """Export only allowlisted artifacts; no images, secrets or pretrained cache.

    Existing private source checkouts can provide notices_root pointing to the
    retained deployment notices. Installed bundles already contain those files.
    """
    policy=_load_policy()
    contents={}
    for name,ref in policy['files'].items():
        if '/notices/' in name and notices_root is not None:
            path=_regular_below(notices_root,name.split('/notices/',1)[1])
        else:
            path=_regular_below(source_root,name)
        if not path.is_file() or path.stat().st_size!=ref['bytes']:
            raise ValueError('Required export asset missing or wrong size')
        contents[name]=path.read_bytes()
    _validate_contents(contents,policy)
    manifest=dict(schema_version=1,version=policy['version'],files=policy['files'])
    destination=Path(bundle_path).absolute()
    _regular_below(destination.parent,destination.name)
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation prevents replacing previous exports.
    created=False
    try:
        with destination.open('xb') as stream:
            created=True
            with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('manifest.json',json.dumps(manifest,sort_keys=True).encode())
                for name,data in sorted(contents.items()): archive.writestr(name,data)
        result=_summary(policy,'EXPORTED')
        result['bundle_sha256']=_sha(destination.read_bytes())
        return result
    except BaseException:
        if created: destination.unlink(missing_ok=True)
        raise


def _read_bundle(bundle_path,policy):
    path=Path(bundle_path).absolute()
    _regular_below(path.parent,path.name)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_ARCHIVE_BYTES:
        raise ValueError('Invalid or oversized model bundle')
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        names=[_safe_name(info.filename) for info in infos]
        if len(infos)>MAX_FILES+1 or len(set(names))!=len(names):
            raise ValueError('Duplicate or excessive bundle entries')
        if set(names)!=set(policy['files'])|{'manifest.json'}:
            raise ValueError('Missing or unlisted bundle entries')
        total=0
        for info in infos:
            mode=info.external_attr>>16
            if (info.is_dir() or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0,stat.S_IFREG) or
                info.flag_bits&1 or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)):
                raise ValueError('Unsupported archive entry type')
            limit=MAX_MANIFEST_BYTES if info.filename=='manifest.json' else policy['files'][info.filename]['bytes']
            if (info.file_size>limit or info.file_size<=0 or
                info.file_size>max(1,info.compress_size)*MAX_COMPRESSION_RATIO):
                raise ValueError('Archive size/compression bounds exceeded')
            total+=info.file_size
        if total>MAX_ARCHIVE_BYTES: raise ValueError('Expanded bundle too large')
        manifest=_json(archive.read('manifest.json'))
        if manifest!={'schema_version':1,'version':policy['version'],'files':policy['files']}:
            raise ValueError('Bundle manifest does not match trusted policy')
        contents={name:archive.read(name) for name in policy['files']}
    _validate_contents(contents,policy)
    return contents


@contextmanager
def _install_lock(models):
    lock=models/'.local-model-install.lock'
    with lock.open('x',encoding='utf8') as stream: stream.write(str(os.getpid()))
    try: yield
    finally: lock.unlink(missing_ok=True)


def install_bundle(bundle_path,project_root):
    """Validate completely, stage, then atomically add a model directory.

    An existing identical installation is a no-op. Any other existing directory
    is left untouched. No scientific artifact is rewritten or re-registered.
    """
    policy=_load_policy()
    contents=_read_bundle(bundle_path,policy)
    root=Path(project_root).absolute()
    if not root.is_dir(): raise ValueError('Project root does not exist')
    destination=_regular_below(root,'models/lettuce')
    models=destination.parent
    models.mkdir(exist_ok=True)
    with _install_lock(models):
        if destination.exists():
            _read_installed(root,policy)
            return _summary(policy,'ALREADY_INSTALLED')
        stage=Path(tempfile.mkdtemp(prefix='.lettuce-install-',dir=models))
        try:
            for name,data in contents.items():
                path=stage/PurePosixPath(name).relative_to('models/lettuce')
                path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
            # rename is atomic on the same filesystem; Windows refuses replacement.
            # On POSIX only an absent destination is allowed by the install lock;
            # competing changes are detected before commit.
            if destination.exists(): raise ValueError('Model destination appeared during installation')
            os.rename(stage,destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    return _summary(policy,'INSTALLED')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    export=commands.add_parser('export')
    export.add_argument('--source-root',type=Path,required=True)
    export.add_argument('--bundle',type=Path,required=True)
    export.add_argument('--notices-root',type=Path)
    install=commands.add_parser('install')
    install.add_argument('--bundle',type=Path,required=True)
    install.add_argument('--project-root',type=Path,default=PROJECT_ROOT)
    verify=commands.add_parser('verify')
    verify.add_argument('--project-root',type=Path,default=PROJECT_ROOT)
    args=parser.parse_args(argv)
    try:
        if args.command=='export': result=export_bundle(args.source_root,args.bundle,notices_root=args.notices_root)
        elif args.command=='install': result=install_bundle(args.bundle,args.project_root)
        else: result=verify_installed(args.project_root)
    except (ValueError,OSError,zipfile.BadZipFile,KeyError,TypeError) as error:
        # Exception messages may contain user paths; CLI emits a bounded type only.
        print(json.dumps(dict(status='FAILED',error=type(error).__name__)))
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=='__main__': raise SystemExit(main())
