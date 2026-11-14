"""Safe local diagnostics; real learned inference is never mocked."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen

from . import runtime


def leaf_smoke():
    import numpy as np
    from ..webapp import leaf_instances
    mask=np.zeros((64,96),bool);mask[8:56,8:88]=True
    labels=np.where(mask,1,0).astype(np.uint16)
    split=leaf_instances.split_instance(labels,1,[[48,0],[48,63]])
    assert len(np.unique(split[split>0]))==2
    assert np.array_equal(split>0,mask)
    summary=leaf_instances.summary(split,mask,.01,reviewed=True)
    assert sum(summary['per_leaf_pixel_area'])==int(mask.sum())
    return {'status':'PASSED','scope':'proposal/split/area invariants; browser acceptance is separate'}


def model_smoke(root,device):
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    import numpy as np
    from .model_assets import verify_installed
    from ..training.inference import predict_version
    from ..training.model_registry import ModelRegistry
    verified=verify_installed(root)
    import torch
    if device=='cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA is unavailable. Retry setup.ps1 -Device cpu, or use a compatible NVIDIA driver; no fallback was attempted.')
    # Procedural fixture, original code, no research photograph or claimed accuracy.
    y,x=np.indices((160,192));rgb=np.full((160,192,3),45,np.uint8)
    rgb[((x-96)/64)**2+((y-80)/55)**2<1]=[75,154,48]
    started=time.monotonic();result=predict_version(rgb,registry=ModelRegistry(),device=device)
    assert result['mask'].shape==rgb.shape[:2]
    assert result['metadata']['architecture']=='segformer_b0'
    return dict(status='PASSED',device=device,torch_version=torch.__version__,seconds=round(time.monotonic()-started,3),
        predicted_pixels=int(result['mask'].sum()),model=verified,synthetic_fixture=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default=str(runtime.ROOT/'.runtime/runtime-profile.json'))
    parser.add_argument('--installation',action='store_true')
    args=parser.parse_args(argv)
    results={};failed=False
    def check(name,action):
        nonlocal failed
        try:
            results[name]=action()
            print(name+': '+('PASSED' if results[name].get('status')=='PASSED' else 'OK'),flush=True)
        except Exception as error:
            failed=True;results[name]={'status':'FAILED','type':type(error).__name__}
            print(name+': ERROR. Run setup.ps1 again; existing data is preserved. ('+type(error).__name__+')',flush=True)
    try:
        runtime.guard_environment();profile=runtime.load_profile(args.profile)
    except (ValueError,OSError) as error:
        print('ERROR: '+str(error),file=sys.stderr);return 1
    def python_check():
        assert sys.version_info[:2]==(3,11)
        result=subprocess.run([sys.executable,'-m','pip','check'],capture_output=True,text=True)
        assert result.returncode==0
        return {'version':sys.version.split()[0],'dependencies':'compatible'}
    def node_check():
        from .install import validate_prerequisites
        version=subprocess.check_output([profile['node'],'--version'],text=True).strip()
        validate_prerequisites(sys.version_info,version)
        return {'version':version}
    def emulator_check():
        tools=Path(profile['firebase_tools_root'])
        assert json.loads((tools/'package.json').read_text())['version']=='15.22.4'
        try:
            with urlopen(runtime.AUTH+'/emulator/v1/projects/'+runtime.PROJECT+'/config',timeout=2) as response:
                assert response.status==200
            return {'installed':True,'running':True}
        except OSError:
            # Probe only our own child when there is no listener. Never stop/adopt another process.
            runtime.available_ports((9099,))
            env=runtime.sanitized_environment();env['HYDRO_FIREBASE_TOOLS_ROOT']=str(tools)
            process=subprocess.Popen([profile['node'],str(runtime.ROOT/'scripts/local_auth_emulator.cjs')],env=env,
                cwd=runtime.ROOT,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
            try:runtime.wait_auth(process)
            finally:
                if process.poll() is None:
                    process.stdin.write('stop\n');process.stdin.flush();process.wait(timeout=15)
            return {'installed':True,'real_start_probe':'PASSED','running':False}
    def database_check():
        runtime.claim_storage()
        databases=runtime.local_database_paths()
        assert databases,'Run setup.ps1 to initialize the local owner.'
        for path in databases:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        export=json.loads((runtime.STATE/'identity-state.json').read_text())
        assert export['accounts'].get('users')
        exported={user['localId'] for user in export['accounts']['users']}
        auth_db=runtime.STATE/'auth/auth.sqlite3'
        with closing(sqlite3.connect(auth_db.as_uri()+'?mode=ro',uri=True)) as db:
            bound={row[0] for row in db.execute('SELECT uid FROM firebase_identities')}
            assert bound and bound.issubset(exported),'Restore the matching local identity backup.'
            assert db.execute("SELECT count(*) FROM users WHERE role='PLATFORM_OWNER' AND status='ACTIVE'").fetchone()[0]==1
        return {'sqlite_databases':len(databases),'identity_export':True}
    def frontend_check():
        sdk=runtime.ROOT/'src/firebase_preview/static/firebase-sdk.js'
        assert sdk.stat().st_size>10000
        subprocess.run([sys.executable,'scripts/build_user_guide.py','--check'],check=True,capture_output=True)
        return {'built_sdk':True,'guide_consistent':True}
    check('Python',python_check);check('Node',node_check)
    check('Firebase emulator',emulator_check);check('Database',database_check);check('Frontend',frontend_check)
    if profile['mode']=='FULL':
        check('Real inference smoke test',lambda:model_smoke(runtime.ROOT,profile.get('device','cpu')))
        if results.get('Real inference smoke test',{}).get('status')=='PASSED':
            print('SegFormer checkpoint: OK\nModel SHA-256: VERIFIED\nPyTorch: OK\nGPU/CPU inference: OK ('+profile.get('device','cpu')+')',flush=True)
    else:print('SegFormer checkpoint: UNAVAILABLE — DEMO MODE; no learned inference was tested.',flush=True)
    check('Leaf-instance workflow',leaf_smoke)
    runtime.atomic_json(runtime.ROOT/'.runtime/check-results.json',dict(mode=profile['mode'],passed=not failed,checks=results))
    print(('ERROR: Follow the indicated repair steps.' if failed else 'READY: '+profile['mode']+' local checks passed.'),flush=True)
    return int(failed)


if __name__=='__main__':raise SystemExit(main())
