"""Explicit durable Firebase modes; evaluation guards remain unchanged."""
from dataclasses import dataclass,field
import os
import re
from urllib.parse import urlsplit

from ..auth_preview.config import Config
from ..hydroponic.config import ROOT
from .identity import local_guard,PROJECT

LIVE_PROJECT='hydroponic-leaf-research-aryae'
WEB_APP='1:242618885776:web:cd700b2282539597081a15'
PRODUCTION_ORIGIN='https://hydroponic-research-242618885776.us-central1.run.app'
ACCEPTANCE_ORIGIN='https://firebase-acceptance---hydroponic-research-jnruunuv5a-uc.a.run.app'


def durable_test_guard():
    local_guard()
    if os.getenv('FIRESTORE_EMULATOR_HOST')!='127.0.0.1:8791':
        raise ValueError('Durable Firebase evaluation requires the loopback Firestore emulator')


@dataclass(frozen=True)
class DurableEmulatorConfig(Config):
    def guard(self):durable_test_guard()

    def __post_init__(self):
        self.guard()
        if (not self.data_dir.resolve().is_relative_to((ROOT/'.runtime/firebase').resolve()) or
            not self.development or not self.integrated or not self.require_verified_email or
            self.firestore_project!=LIVE_PROJECT or not self.firestore_prefix.startswith('firebase_test_') or
            self.smtp or self.email_hmac_key or self.phone_review_owner_id or
            not 300<=self.session_seconds<=14*86400):
            raise ValueError('Use a new isolated Firebase/Firestore emulator fixture namespace')
        super().__post_init__()

    def public_config(self):
        return dict(projectId=PROJECT,apiKey='fake-api-key',authDomain=PROJECT+'.firebaseapp.com',
            emulator='http://127.0.0.1:9099',local_only=True,identity_mode='emulator')

    @property
    def cookie(self):return 'hydro_firebase_durable_eval_v1'


@dataclass(frozen=True)
class ProductionFirebaseConfig(Config):
    web_config:dict=field(default_factory=dict,repr=False)

    def guard(self):
        if os.getenv('FIREBASE_AUTH_EMULATOR_HOST') or os.getenv('FIRESTORE_EMULATOR_HOST'):
            raise ValueError('Production Firebase cannot use emulator identities or metadata')

    def __post_init__(self):
        self.guard()
        if (self.development or not self.integrated or not self.require_verified_email or
            not self.require_durable_auth or self.firestore_project!=LIVE_PROJECT or
            self.firestore_database!='(default)' or
            not re.fullmatch(r'hydroponic_firebase_auth_[A-Za-z0-9_]{1,48}',self.firestore_prefix) or
            self.smtp or self.email_hmac_key or self.phone_review_owner_id or
            not 300<=self.session_seconds<=14*86400 or
            self.origin not in (PRODUCTION_ORIGIN,ACCEPTANCE_ORIGIN) or
            not self.data_dir.resolve().is_relative_to(ROOT.resolve())):
            raise ValueError('Explicit isolated durable Firebase production configuration is required')
        expected=dict(projectId=LIVE_PROJECT,appId=WEB_APP,authDomain=LIVE_PROJECT+'.firebaseapp.com')
        if any(self.web_config.get(key)!=value for key,value in expected.items()) or not self.web_config.get('apiKey'):
            raise ValueError('Use the existing dedicated Firebase web application configuration')
        super().__post_init__()

    def public_config(self):
        self.guard()
        result={key:self.web_config[key] for key in ('apiKey','appId','authDomain','projectId')}
        return dict(result,local_only=False,identity_mode='firebase_production',origin=self.origin)

    @property
    def cookie(self):return '__Host-hydro_firebase'
