"""Install the isolated local application; no cloud credentials or services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parents[2]
TOOLS_VERSION='15.22.4'


def validate_prerequisites(python_version,node_version):
    if tuple(python_version[:2])!=(3,11):
        raise ValueError('Install 64-bit Python 3.11; other Python versions are not qualified.')
    major=int(node_version.strip().lstrip('v').split('.')[0])
    if major not in (22,24): raise ValueError('Install Node.js 22 or 24 with npm, then reopen the terminal.')


def choose_device(request,driver):
    # CUDA 12.8 Windows binary minimum driver. Actual Torch probe follows install.
    compatible=bool(driver and tuple(map(int,driver.split('.')[:2]))>=(570,65))
    if request=='cuda' and not compatible:
        raise ValueError('CUDA 12.8 needs a compatible NVIDIA driver (570.65+). Use -Device cpu; no driver was changed.')
    return 'cuda' if request=='cuda' or(request=='auto' and compatible) else 'cpu'


def resolve_bundle(root,specified,demo):
    if demo: return None
    path=Path(specified).expanduser().resolve() if specified else Path(root)/'.model-bundles/research-model-exp006.zip'
    if not path.is_file():
        raise ValueError('FULL mode requires the authorized research model bundle. Obtain research-model-exp006.zip from an authorized maintainer, place it in .model-bundles/, then run setup.ps1 again; or explicitly run setup.ps1 -Demo. No weights were downloaded.')
    return path


def write_profile(path,*,python,node,tools,mode,device):
    from .runtime import atomic_json
    profile=dict(version=1,backend='windows' if os.name=='nt' else 'wsl',python=str(python),
        node=str(node),firebase_tools_root=str(tools),mode=mode,device=device)
    if os.getenv('WSL_DISTRO_NAME'):profile['wsl_distribution']=os.environ['WSL_DISTRO_NAME']
    atomic_json(path,profile)
    return profile


def execute(args,*,cwd=ROOT,capture=False):
    result=subprocess.run([str(arg) for arg in args],cwd=cwd,text=True,
        stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else subprocess.STDOUT)
    if result.returncode:
        raise ValueError('Installation step failed: '+Path(str(args[0])).name+'. Your existing data was preserved; correct the error above and retry.')
    return result.stdout.strip() if capture else None


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-bundle');parser.add_argument('--demo',action='store_true')
    parser.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    args=parser.parse_args(argv)
    try:
        from .runtime import guard_environment, available_ports
        guard_environment()
        available_ports()
        node=shutil.which('node');npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        if not node or not npm:raise ValueError('Install Node.js 22 or 24 with npm; then reopen the terminal.')
        validate_prerequisites(sys.version_info,execute([node,'--version'],capture=True))
        bundle=resolve_bundle(ROOT,args.model_bundle,args.demo)
        driver=None
        smi=shutil.which('nvidia-smi')
        if smi:
            probe=subprocess.run([smi,'--query-gpu=driver_version','--format=csv,noheader'],capture_output=True,text=True)
            if probe.returncode==0:driver=probe.stdout.strip().splitlines()[0]
        device=choose_device(args.device,driver) if not args.demo else 'cpu'
        environment=ROOT/'.venv'
        python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.exists():venv.EnvBuilder(with_pip=True).create(environment)
        validate_prerequisites(json.loads(execute([python,'-c','import json,sys; print(json.dumps(list(sys.version_info[:3])))'],capture=True)),execute([node,'--version'],capture=True))
        print('Installing pinned application dependencies...',flush=True)
        execute([python,'-m','pip','install','--disable-pip-version-check','-r','requirements-local.txt'])
        if bundle:
            execute([python,'-m','pip','install','--disable-pip-version-check','-r',f'requirements-model-{device}.txt'])
            execute([python,'-m','pip','install','--disable-pip-version-check','-r','requirements-model-common.txt'])
            execute([python,'-m','src.local_setup.model_assets','install','--bundle',bundle])
            execute([python,'-c',f'import torch; x=torch.ones(2,2,device={device!r}); assert float((x@x).sum())==8; print("PyTorch: OK ({device})")'])
        print('Installing pinned local Auth emulator and building browser SDK...',flush=True)
        tools=ROOT/'.runtime/firebase-tools'
        tools.mkdir(parents=True,exist_ok=True)
        for filename in ('package.json','package-lock.json'):
            shutil.copyfile(ROOT/'configs/runtime/firebase-tools'/filename,tools/filename)
        execute([npm,'--prefix',tools,'ci','--no-audit','--no-fund'])
        client=ROOT/'src/firebase_preview/client'
        execute([npm,'ci','--no-audit','--no-fund'],cwd=client)
        esbuild=client/'node_modules/.bin'/('esbuild.cmd' if os.name=='nt' else 'esbuild')
        execute([esbuild,'sdk-entry.js','--bundle','--minify','--format=esm','--platform=browser','--outfile=../static/firebase-sdk.js'],cwd=client)
        execute([python,'scripts/build_user_guide.py','--check'])
        profile=ROOT/'.runtime/runtime-profile.json'
        write_profile(profile,python=python,node=node,tools=ROOT/'.runtime/firebase-tools/node_modules/firebase-tools',mode='DEMO' if args.demo else 'FULL',device=device)
        execute([python,'-m','src.local_setup.runtime','initialize','--profile',profile])
        execute([python,'-m','src.local_setup.diagnostics','--profile',profile,'--installation'])
        from .runtime import atomic_json
        atomic_json(ROOT/'.runtime/installation.json',dict(version=1,mode='DEMO' if args.demo else 'FULL',
            device=device,profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest()))
        print('READY: '+('DEMO only; learned segmentation was not verified.' if args.demo else 'FULL model installation verified.')+' Run .\\start.ps1.',flush=True)
        return 0
    except (ValueError,OSError) as error:
        print('ERROR: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
