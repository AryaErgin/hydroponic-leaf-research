"""Approved real Firebase identity tests; local scientific fixtures and inbox allowlist only.

This is deliberately not a Cloud Run or production-user authentication entry point.
"""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import Credentials

from ..auth_preview.config import Config
from ..auth_preview.mail import normalize_email
from ..hydroponic.config import ROOT
from .identity import Gateway

PROJECT='hydroponic-leaf-research-aryae'
NUMBER='242618885776'
CONFIGURATION='hydroponic-research'
LIVE_ROOT=ROOT/'.runtime/firebase/live'


def live_environment_guard():
    if any(os.getenv(key) for key in ('K_SERVICE','FIREBASE_AUTH_EMULATOR_HOST','FIRESTORE_EMULATOR_HOST')):
        raise ValueError('Live Firebase testing requires a local process with all emulator variables unset')


@dataclass(frozen=True)
class LiveTestConfig(Config):
    web_config_path:Path=field(default=LIVE_ROOT/'web-config.json',repr=False)
    inboxes_path:Path=field(default=LIVE_ROOT/'receiving-inboxes.json',repr=False)

    def __post_init__(self):
        live_environment_guard()
        for path in (self.data_dir,self.web_config_path,self.inboxes_path):
            if not Path(path).resolve().is_relative_to(LIVE_ROOT.resolve()):
                raise ValueError('Live Firebase test configuration requires its new isolated local directory')
        if (not self.development or not self.integrated or not self.require_verified_email or
            self.firestore_project or self.require_durable_auth or self.smtp or self.email_hmac_key or
            self.phone_review_owner_id or not 300<=self.session_seconds<=14*86400):
            raise ValueError('Live email test mode cannot use production stores, SMTP or owner exceptions')
        self.web_config()
        super().__post_init__()

    def guard(self):live_environment_guard()

    def web_config(self):
        value=json.loads(self.web_config_path.read_text(encoding='utf-8'))
        if (value.get('projectId')!=PROJECT or not value.get('appId','').startswith('1:'+NUMBER+':web:') or
            value.get('authDomain')!=PROJECT+'.firebaseapp.com' or not value.get('apiKey')):
            raise ValueError('Only the approved dedicated project/web application may be used')
        return value

    def receiving_inboxes(self):
        self.guard()
        if not self.inboxes_path.exists():return []
        values=json.loads(self.inboxes_path.read_text(encoding='utf-8')).get('receiving_inboxes',[])
        if not isinstance(values,list) or len(values)>2:raise ValueError('Configure at most two controlled receiving inboxes')
        return list(dict.fromkeys(normalize_email(value) for value in values))

    def require_receiving_inbox(self,email):
        email=normalize_email(email)
        if email not in self.receiving_inboxes():
            raise ValueError('Only the user-provided controlled receiving inboxes can be used for this test')
        return email

    def public_config(self):
        result={key:value for key,value in self.web_config().items() if key in ('apiKey','appId','authDomain','projectId','messagingSenderId')}
        return dict(result,local_only=True,identity_mode='google_live_test',
            test_inboxes_configured=bool(self.receiving_inboxes()))

    @property
    def cookie(self):return 'hydro_firebase_live_test_v1'


class _ScopedGcloud(Credentials):
    """Refresh operator credentials in memory using the explicit project/config/account."""
    def __init__(self,account):
        super().__init__();self.account=normalize_email(account)

    @property
    def quota_project_id(self):return PROJECT

    def refresh(self,request):
        live_environment_guard()
        result=subprocess.run(['gcloud.cmd','auth','print-access-token','--project='+PROJECT,
            '--configuration='+CONFIGURATION,'--account='+self.account],capture_output=True,text=True,timeout=30)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError('Dedicated Google operator credentials could not be refreshed')
        self.token=result.stdout.strip()
        self.expiry=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(minutes=20)


class _Operator(credentials.Base):
    def __init__(self,account):self.credential=_ScopedGcloud(account)
    def get_credential(self):return self.credential


class LiveTestGateway(Gateway):
    def __init__(self,config,operator_account):
        if not isinstance(config,LiveTestConfig):raise ValueError('Explicit live-test configuration required')
        self.config=config;self.guard()
        self.app=firebase_admin.initialize_app(_Operator(operator_account),{'projectId':PROJECT,'httpTimeout':15},name='live-test-'+uuid4().hex)

    def guard(self):self.config.guard()

    def _inspect(self,token,kind):
        identity=super()._inspect(token,kind)
        self.config.require_receiving_inbox(identity['email'])
        return identity


def build():
    from .app import create_app
    config=LiveTestConfig(LIVE_ROOT/'auth','http://localhost:8896',development=True,integrated=True,require_verified_email=True)
    operator=json.loads((LIVE_ROOT/'operator.json').read_text(encoding='utf-8'))['account']
    app=create_app(config,LiveTestGateway(config,operator),LIVE_ROOT/'science')
    with app.state.service.db.transaction() as db:
        db.execute('INSERT OR IGNORE INTO organizations(id,name) VALUES(?,?)',('org-bau','Bahçeşehir University — isolated email test'))
        db.execute('INSERT OR IGNORE INTO organizations(id,name) VALUES(?,?)',('org-b','University B — isolated email test'))
        db.execute('INSERT OR IGNORE INTO public_signup VALUES(1,?)',('org-bau',))
    return app
