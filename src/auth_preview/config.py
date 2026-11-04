from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import os
import re


@dataclass(frozen=True)
class Config:
    data_dir: Path
    origin: str
    development: bool = False
    session_seconds: int = 12 * 60 * 60
    smtp: object | None = None
    email_hmac_key: bytes | None = field(default=None,repr=False)
    integrated: bool = False
    require_verified_email: bool = False
    require_durable_auth: bool = False
    firestore_project: str | None = None
    firestore_database: str = '(default)'
    firestore_prefix: str = 'hydroponic_auth_v1'
    phone_review_owner_id: str | None = None

    def __post_init__(self):
        url=urlsplit(self.origin)
        if (self.require_durable_auth or (self.integrated and os.getenv('K_SERVICE'))) and not self.firestore_project:
            raise ValueError('A durable shared auth store is required; this SQLite candidate cannot run on Cloud Run')
        if self.firestore_project:
            if not self.integrated:raise ValueError('Firestore auth requires integrated mode; synthetic preview seeding is forbidden')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{2,79}',self.firestore_prefix):
                raise ValueError('Use an explicit safe Firestore auth collection prefix')
        if self.phone_review_owner_id:
            local=self.development and url.hostname in ('localhost','127.0.0.1','testserver')
            tagged=(not self.development and url.scheme=='https' and url.hostname and
                    url.hostname.startswith('phone-review---') and self.firestore_project and
                    self.firestore_prefix.startswith('hydroponic_phone_review_'))
            if not self.integrated or not (local or tagged):
                raise ValueError('Phone review owner access requires an isolated phone-review tag and auth prefix, or an integrated loopback runtime')
        if url.path or url.query or url.fragment or not url.hostname or url.username:
            raise ValueError('Origin must be an exact scheme and host, optionally with port')
        if self.development:
            if url.scheme not in ('http','https') or url.hostname not in ('localhost','127.0.0.1','testserver'):
                raise ValueError('Development preview must use a loopback host')
        elif url.scheme!='https':
            raise ValueError('HTTPS required outside development')
        self.data_dir.mkdir(parents=True,exist_ok=True)

    @property
    def database(self):
        return self.data_dir/'auth.sqlite3'

    @property
    def cookie(self):
        if self.integrated:return 'hydro_integrated_local_v1' if self.development else '__Host-hydro_integrated'
        return 'hydro_auth_preview_v1' if self.development else '__Host-hydro_auth'
