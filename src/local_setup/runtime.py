"""Foreground, loopback-only process supervisor with private durable local state."""
import argparse
from contextlib import contextmanager, closing
from getpass import getpass
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.runtime/local'
PROJECT = 'demo-hydroponic-leaf-research'
ORIGIN = 'http://localhost:8896'
AUTH = 'http://127.0.0.1:9099'
_SNAPSHOT_LOCK = threading.RLock()


def guard_environment():
    blocked = ('K_SERVICE', 'HYDRO_FIRESTORE_PROJECT', 'HYDRO_GCS_BUCKET',
        'HYDRO_DATABASE_URL', 'GOOGLE_APPLICATION_CREDENTIALS', 'FIRESTORE_EMULATOR_HOST',
        'STORAGE_EMULATOR_HOST', 'FIREBASE_CONFIG')
    if any(os.environ.get(key) for key in blocked):
        raise ValueError('Cloud/storage credentials or configuration are set. Open a clean local shell without them; no values were read or copied.')
    host = os.getenv('FIREBASE_AUTH_EMULATOR_HOST')
    if host and host != '127.0.0.1:9099':
        raise ValueError('FIREBASE_AUTH_EMULATOR_HOST must be unset or exactly 127.0.0.1:9099.')


def atomic_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            if os.name != 'nt': os.chmod(temporary, 0o600)
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def claim_storage(path=STATE):
    path = Path(path); marker = path/'owner.json'
    if marker.exists():
        data = json.loads(marker.read_text(encoding='utf-8'))
        if data.get('application') != 'hydroponic-local-v1' or not data.get('installation_id'):
            raise ValueError('Unrecognized local storage owner marker; refusing to adopt existing data.')
        return data
    if path.exists() and any(path.iterdir()):
        raise ValueError('Refusing unrecognized existing local data. Keep it and choose a separate clean clone.')
    path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt': os.chmod(path, 0o700)
    data = dict(application='hydroponic-local-v1', installation_id=uuid4().hex, version=1)
    atomic_json(marker, data)
    return data


class RuntimeLock:
    """OS-owned lock releases even after a killed supervisor; no stale PID killing."""
    def __init__(self, path): self.path=Path(path); self.stream=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=self.path.open('a+b'); self.stream.seek(0)
        if self.path.stat().st_size==0: self.stream.write(b'0'); self.stream.flush()
        try:
            if os.name=='nt':
                import msvcrt
                self.stream.seek(0); msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close(); self.stream=None
            raise ValueError('A launcher is already running for this clone; return to its terminal.') from None
        return self
    def __exit__(self,*args):
        if self.stream:
            if os.name=='nt':
                import msvcrt
                self.stream.seek(0); msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
            self.stream.close()


def available_ports(ports=(9099,8896)):
    for port in ports:
        with socket.socket() as probe:
            if os.name=='nt': probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try: probe.bind(('127.0.0.1',port))
            except OSError:
                raise ValueError(f'Port {port} is already in use. Close the other application yourself or its original local-preview terminal, then retry. No process was stopped.') from None


def load_profile(path):
    profile=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if profile.get('version')!=1 or profile.get('backend') not in ('windows','wsl') or profile.get('mode') not in ('FULL','DEMO'):
        raise ValueError('Runtime profile is invalid. Run setup.ps1 again.')
    for key in ('python','node','firebase_tools_root'):
        if not Path(profile.get(key,'')).is_absolute() or not Path(profile[key]).exists():
            raise ValueError(f'Runtime profile {key} is unavailable. Run setup.ps1 for this clone again.')
    return profile


def require_installation(profile_path,profile):
    import hashlib
    profile_path=Path(profile_path)
    marker=profile_path.parent/'installation.json'
    if not marker.is_file():
        raise ValueError('Local setup has not completed successfully. Run setup.ps1 and resolve its diagnostics before starting.')
    completed=json.loads(marker.read_text(encoding='utf-8-sig'))
    digest=hashlib.sha256(profile_path.read_bytes()).hexdigest()
    if (completed.get('version')!=1 or completed.get('profile_sha256')!=digest or
        completed.get('mode')!=profile['mode'] or completed.get('device')!=profile.get('device','cpu')):
        raise ValueError('Runtime profile differs from the completed setup. Run setup.ps1 again before starting.')


def require_password(password):
    if not 15 <= len(password) <= 128: raise ValueError('Choose a password with 15 to 128 characters.')
    return password


def auth_request(path, data=None, method=None):
    body=None if data is None else json.dumps(data).encode()
    req=Request(AUTH+path,data=body,headers={'Content-Type':'application/json','Authorization':'Bearer owner'},method=method)
    with urlopen(req,timeout=15) as response: return json.load(response)


def snapshot_auth(path=STATE):
    with _SNAPSHOT_LOCK: return _snapshot_auth(path)


def local_database_paths(path=STATE):
    """Known SQLite locations only: never traverse uploads or media hashes.

    Media uses extended Windows paths; recursive scandir of those directories
    through an ordinary Path can fail at MAX_PATH and is unnecessary for backup.
    """
    path=Path(path)
    found=list((path/'auth').glob('*.sqlite3'))
    found.extend((path/'science').glob('*.sqlite3'))
    found.extend((path/'science/organizations').glob('*/hydroponic.sqlite3'))
    return sorted(found)


def _snapshot_auth(path=STATE):
    """Use the same account/config export REST API as pinned firebase-tools HubExport.

    One atomic file contains the identity export and a SQLite backup payload. The live
    SQLite remains authoritative and is never replaced or rolled back automatically.
    """
    import base64
    path=Path(path)
    backups={}; bound=set()
    for dbpath in local_database_paths(path):
        if '.snapshot-' in dbpath.name: continue
        temp=path/('.snapshot-'+uuid4().hex)
        try:
            with closing(sqlite3.connect(dbpath)) as source, closing(sqlite3.connect(temp)) as target: source.backup(target)
            backups[dbpath.relative_to(path).as_posix()]=base64.b64encode(temp.read_bytes()).decode('ascii')
            if dbpath.relative_to(path).as_posix()=='auth/auth.sqlite3':
                with closing(sqlite3.connect(temp)) as captured:
                    tables={row[0] for row in captured.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'firebase_identities' in tables:
                        bound={row[0] for row in captured.execute('SELECT uid FROM firebase_identities')}
        finally: temp.unlink(missing_ok=True)
    # Registration creates its emulator identity before committing its SQLite binding.
    # Back up SQLite first, then export Auth, so concurrent registration cannot add
    # a captured binding whose identity was absent from an older export.
    accounts=auth_request(f'/identitytoolkit.googleapis.com/v1/projects/{PROJECT}/accounts:batchGet?maxResults=-1')
    config=auth_request(f'/emulator/v1/projects/{PROJECT}/config')
    exported={user['localId'] for user in accounts.get('users',[])}
    if not bound.issubset(exported):
        raise ValueError('Identity snapshot changed during export; existing atomic snapshot was preserved.')
    atomic_json(path/'identity-state.json',dict(version=1,accounts=accounts,config=config,sqlite_backups=backups))


def import_auth(path=STATE):
    statefile=Path(path)/'identity-state.json'
    if not statefile.exists():
        database=Path(path)/'auth/auth.sqlite3'
        if database.exists():
            with closing(sqlite3.connect(database)) as db:
                if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                    raise ValueError('Existing accounts have no identity export. Refusing to create replacement identities; restore the matching local backup.')
        return
    export=json.loads(statefile.read_text(encoding='utf-8'))
    if export.get('version')!=1: raise ValueError('Unsupported local identity export.')
    auth_request(f'/emulator/v1/projects/{PROJECT}/config',export['config'],'PATCH')
    if export['accounts'].get('users'):
        result=auth_request(f'/identitytoolkit.googleapis.com/v1/projects/{PROJECT}/accounts:batchCreate',export['accounts'])
        if result.get('error'): raise ValueError('Local identity import rejected an account; storage was preserved.')
        # firebase-tools15.22.4 batchCreate resets validSince to import time.
        # Privileged emulator accounts:update accepts the exported original stamp;
        # preserve it so valid backend sessions remain valid across local restarts.
        for user in export['accounts']['users']:
            if user.get('validSince') is not None:
                auth_request('/identitytoolkit.googleapis.com/v1/accounts:update?key=fake-api-key',
                    dict(localId=user['localId'],validSince=user['validSince']))
    # UIDs and bindings must agree; never silently mint replacements for missing IDs.
    database=Path(path)/'auth/auth.sqlite3'
    if database.exists():
        exported={u['localId'] for u in export['accounts'].get('users',[])}
        with closing(sqlite3.connect(database)) as db:
            try: bound={r[0] for r in db.execute('SELECT uid FROM firebase_identities')}
            except sqlite3.OperationalError: bound=set()
        if not bound.issubset(exported):
            raise ValueError('Local identity export and SQLite bindings disagree. Restore a matching backup; existing data was not overwritten.')


def bootstrap_owner(app):
    service=app.state.service
    with service.db.transaction() as db:
        owner=db.execute("SELECT id FROM users WHERE role='PLATFORM_OWNER'").fetchone()
        if owner: return False
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('Existing local accounts have no owner. Automatic privilege changes are refused.')
    email=os.environ.pop('HYDRO_LOCAL_OWNER_EMAIL',None) or input('Local owner email (reserved .test address, e.g. owner@example.test): ').strip()
    if '@' not in email or not email.lower().endswith('.test'):
        raise ValueError('Use a reserved .test email address for this isolated local identity. No email will be sent.')
    password=os.environ.pop('HYDRO_LOCAL_OWNER_PASSWORD',None)
    if password is None:
        password=getpass('Choose local owner password (15 to 128 characters): ')
        if password != getpass('Repeat local owner password: '): raise ValueError('Passwords did not match.')
    require_password(password)
    from firebase_admin import auth
    from ..auth_preview.service import now_iso
    recovered_orphan=False
    try:
        identity=auth.get_user_by_email(email,app=service.gateway.app)
        recovered_orphan=True
    except auth.UserNotFoundError:
        identity=auth.create_user(email=email,password=password,email_verified=True,app=service.gateway.app)
    # Recovery never resets an existing password, changes provider proof, or
    # promotes an existing backend user. The operator must prove possession of
    # this exact selected reserved-domain identity with its unchanged password.
    if identity.disabled or not identity.email_verified or identity.email.casefold()!=email.casefold():
        raise ValueError('The selected local identity is not an eligible verified bootstrap identity. No account was changed.')
    try:
        signed=auth_request('/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake-api-key',
            dict(email=email,password=password,returnSecureToken=True))
    except Exception:
        raise ValueError('Could not prove the selected local identity with that password. Its password and access were unchanged.') from None
    finally: password=None
    if signed.get('localId')!=identity.uid:
        raise ValueError('Local bootstrap identity proof did not match; no access was changed.')
    # One SQLite commit makes owner, UID binding and audit inseparable. A failure
    # leaves zero backend users, so the same operator can safely retry the orphan.
    with service.db.transaction() as db:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('Local accounts appeared during bootstrap. Automatic privilege changes are refused.')
        org=db.execute('SELECT org_id FROM public_signup WHERE singleton=1').fetchone()[0]
        user_id=str(uuid4());created=now_iso()
        db.execute('INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (user_id,'local-owner','Local owner','FIREBASE_MANAGED','ACTIVE','PLATFORM_OWNER',org,org,created,identity.email,created))
        db.execute('INSERT INTO firebase_identities(uid,user_id) VALUES(?,?)',(identity.uid,user_id))
        service.audit(db,'local-operator','local_owner_bootstrapped',user_id,org_id=org,
            synthetic_identity=True,recovered_orphan=recovered_orphan)
    snapshot_auth()
    print('Local owner created. Sign in using your chosen .test email and password. No email was sent.',flush=True)
    return True


def sanitized_environment():
    guard_environment(); env=os.environ.copy()
    for key in ('HYDRO_LOCAL_OWNER_EMAIL','HYDRO_LOCAL_OWNER_PASSWORD','GOOGLE_CLOUD_PROJECT','GCLOUD_PROJECT','CLOUDSDK_CORE_PROJECT'):
        env.pop(key,None)
    env.update(FIREBASE_AUTH_EMULATOR_HOST='127.0.0.1:9099',FIREBASE_CLI_DISABLE_TELEMETRY='1',CI='true')
    return env


def wait_auth(process,timeout=40):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if process.poll() is not None: raise ValueError('Local Auth emulator stopped. Run setup.ps1 to repair its pinned tools.')
        try:
            auth_request(f'/emulator/v1/projects/{PROJECT}/config'); return
        except Exception: time.sleep(.15)
    raise ValueError('Local Auth emulator did not become ready; no browser was opened.')


def run(profile,initialize=False,no_browser=False):
    guard_environment()
    if not STATE.resolve().is_relative_to(ROOT.resolve()): raise ValueError('Local storage must remain inside this clone; directory links outside it are refused.')
    claim_storage()
    with RuntimeLock(STATE/'launcher.lock'):
        available_ports((9099,) if initialize else (9099,8896))
        env=sanitized_environment(); env['HYDRO_FIREBASE_TOOLS_ROOT']=profile['firebase_tools_root']
        process=subprocess.Popen([profile['node'],str(ROOT/'scripts/local_auth_emulator.cjs')],cwd=ROOT,env=env,
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
        stop=threading.Event(); server=None
        previous={}
        def request_stop(*_):
            stop.set()
            if server: server.should_exit=True
            else: raise KeyboardInterrupt
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous[sig]=signal.signal(sig,request_stop)
        try:
            wait_auth(process); import_auth()
            os.environ['FIREBASE_AUTH_EMULATOR_HOST']='127.0.0.1:9099'
            os.environ['HYDRO_LOCAL_MODE']=profile['mode']
            os.environ['HF_HUB_OFFLINE']='1'
            os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
            from .app import build
            app=build(mode=profile['mode'],device=profile.get('device','cpu')); bootstrap_owner(app)
            snapshot_auth()
            if initialize:
                app.state.scientific.close(); app.state.service.gateway.close()
                print('READY: Local identity and storage initialized; run start.ps1.',flush=True); return
            import uvicorn
            # Warm and verify the actual promoted model before opening a browser.
            if profile['mode']=='FULL':
                from .model_assets import verify_installed
                verify_installed(ROOT)
                from ..training.inference import predict_version
                from ..training.model_registry import ModelRegistry
                import numpy as np
                registry=ModelRegistry(); champion=registry.champion()
                if not champion: raise ValueError('FULL mode requires the installed promoted model; run setup.ps1 with its authorized bundle.')
                device=profile.get('device','cpu')
                predict_version(np.zeros((64,64,3),dtype=np.uint8),version=champion['version'],registry=registry,device=device)
                app.state.local_model_status=dict(ready=True,loading=False,device=device,model_version=champion['version'],mode='FULL')
            server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8896,log_level='error',access_log=False,log_config=None))
            thread=threading.Thread(target=server.run,daemon=True); thread.start()
            until=time.monotonic()+90
            while not server.started and thread.is_alive() and time.monotonic()<until: time.sleep(.1)
            if not server.started: raise ValueError('The local app failed to start; browser was not opened.')
            atomic_json(STATE/'running.json',dict(pid=os.getpid(),auth_pid=process.pid,origin=ORIGIN,mode=profile['mode'],owned_by='hydroponic-local-v1'))
            print(f'READY: {ORIGIN} | {profile["mode"]} | local isolated storage | Ctrl+C stops only these services.',flush=True)
            if not no_browser:
                import webbrowser
                if os.getenv('WSL_DISTRO_NAME'):
                    subprocess.Popen(['powershell.exe','-NoProfile','-Command','Start-Process http://localhost:8896'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else: webbrowser.open(ORIGIN)
            while thread.is_alive() and not stop.wait(1):
                if process.poll() is not None: raise ValueError('The owned Auth emulator stopped; shutting down this local app.')
                snapshot_auth()
            server.should_exit=True; thread.join(timeout=30)
        finally:
            if server: server.should_exit=True
            try:
                if process.poll() is None: snapshot_auth()
            except Exception: print('ERROR: Final identity snapshot failed. Existing export and SQLite data were preserved.',flush=True)
            if process.poll() is None:
                try: process.stdin.write('stop\n'); process.stdin.flush(); process.wait(timeout=15)
                except Exception: process.terminate(); process.wait(timeout=10)
            (STATE/'running.json').unlink(missing_ok=True)
            for sig,handler in previous.items(): signal.signal(sig,handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','initialize'))
    parser.add_argument('--profile',default=str(ROOT/'.runtime/runtime-profile.json'))
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    try:
        profile=load_profile(args.profile)
        if args.action=='start': require_installation(args.profile,profile)
        run(profile,args.action=='initialize',args.no_browser)
    except KeyboardInterrupt: return 0
    except ValueError as error: print('ERROR: '+str(error),file=sys.stderr); return 1
    except Exception as error:
        # Provider exception messages can contain identity data. Never print them.
        print('ERROR: Local startup failed ('+type(error).__name__+'). Existing data was preserved; run check.ps1.',file=sys.stderr); return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
