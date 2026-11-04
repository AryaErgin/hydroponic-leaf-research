"""Official SDK identity verification, strictly restricted to a local demo emulator."""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import firebase_admin
from firebase_admin import auth,credentials
from google.auth.credentials import AnonymousCredentials

from ..auth_preview.config import Config
from ..hydroponic.config import ROOT

PROJECT='demo-hydroponic-leaf-research'
EMULATOR='127.0.0.1:9099'
request_cache=ContextVar('firebase_request_cache',default=None)


def local_guard():
    if os.getenv('K_SERVICE') or os.getenv('FIREBASE_AUTH_EMULATOR_HOST')!=EMULATOR:
        raise ValueError('Firebase evaluation requires the loopback demo emulator; cloud use is forbidden')


@dataclass(frozen=True)
class FirebaseConfig(Config):
    def __post_init__(self):
        # Validate before the parent creates a directory. No existing runtime can
        # accidentally become the identity-migration test store.
        local_guard()
        if not self.data_dir.resolve().is_relative_to((ROOT/'.runtime/firebase').resolve()):
            raise ValueError('Firebase evaluation requires its separate local runtime')
        if (not self.development or not self.integrated or not self.require_verified_email or
            self.firestore_project or self.smtp or self.email_hmac_key or self.phone_review_owner_id or
            not 300<=self.session_seconds<=14*86400 or
            urlsplit(self.origin).hostname not in ('127.0.0.1','localhost','testserver')):
            raise ValueError('Firebase evaluation is local only, without SMTP or owner exceptions')
        super().__post_init__()

    @property
    def cookie(self):return 'hydro_firebase_eval_v1'


class _Anonymous(credentials.Base):
    def get_credential(self):return AnonymousCredentials()


class Gateway:
    guard=staticmethod(local_guard)

    def __init__(self):
        self.guard()
        self.app=firebase_admin.initialize_app(_Anonymous(),{'projectId':PROJECT},name='local-'+uuid4().hex)

    def close(self):firebase_admin.delete_app(self.app)

    def _inspect(self,token,kind):
        self.guard()
        cache=request_cache.get()
        key=(kind,hashlib.sha256(token.encode()).hexdigest())
        if cache is not None and key in cache:return cache[key]
        if kind=='cookie':
            jwt,separator,nonce=token.partition('~')
            if not separator or len(nonce)!=32:raise ValueError('Invalid session envelope')
        else:jwt=token
        claims=(auth.verify_id_token(jwt,app=self.app,check_revoked=True) if kind=='id'
                else auth.verify_session_cookie(jwt,app=self.app,check_revoked=True))
        # The emulator skips some production verifier checks along with crypto;
        # retain expiry/future-issuance contracts in this local adapter too.
        if claims.get('exp',0)<=time.time() or claims.get('iat',0)>time.time()+10:
            raise ValueError('Session expired; sign in again')
        user=auth.get_user(claims['uid'],app=self.app)
        if (user.disabled or not user.email or claims.get('email')!=user.email or
            bool(claims.get('email_verified'))!=user.email_verified):
            raise ValueError('Identity changed; sign in again')
        if kind=='id' and not -10<=time.time()-claims.get('auth_time',0)<=300:
            raise ValueError('Recent sign in required')
        stamp=hashlib.sha256(json.dumps([user.uid,user.email,user.email_verified,user.disabled,
            user.tokens_valid_after_timestamp],separators=(',',':')).encode()).hexdigest()
        identity=dict(uid=user.uid,email=user.email,email_verified=user.email_verified,stamp=stamp)
        if cache is not None:cache[key]=identity
        return identity

    def inspect_id(self,token):return self._inspect(token,'id')
    def inspect_cookie(self,token):return self._inspect(token,'cookie')

    def cookie(self,token,seconds):
        self.guard()
        return auth.create_session_cookie(token,expires_in=timedelta(seconds=seconds),app=self.app)
