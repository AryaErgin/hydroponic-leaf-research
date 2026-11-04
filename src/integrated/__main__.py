"""Explicit local or Cloud Run entrypoint; never seeds a user or synthetic inbox."""
import argparse
import hashlib
import os
from pathlib import Path

import uvicorn

from ..auth_preview.config import Config
from ..auth_preview.mail import SMTP
from ..auth_preview.providers import Resend
from ..hydroponic.config import ROOT
from ..webapp.settings import Settings
from .app import create_app


def configured_mail():
    provider=os.getenv('HYDRO_EMAIL_PROVIDER','disabled')
    if provider=='disabled':return None
    sender=os.environ['HYDRO_EMAIL_SENDER']
    verified=os.getenv('HYDRO_EMAIL_SENDER_VERIFIED')=='1'
    if not verified:raise ValueError('Verify the sending identity before enabling email')
    if provider=='resend':return Resend(os.environ['HYDRO_EMAIL_API_KEY'],sender,verified_sender=verified)
    if provider=='smtp':
        return SMTP(os.environ['HYDRO_SMTP_HOST'],os.environ['HYDRO_SMTP_USER'],os.environ['HYDRO_SMTP_PASSWORD'],
                    sender,port=int(os.getenv('HYDRO_SMTP_PORT','587')))
    raise ValueError('Unknown email provider; choose disabled, smtp or resend')


def configuration(*,local=False,port=8895):
    if local:
        if os.getenv('K_SERVICE'):raise ValueError('Local mode is forbidden on Cloud Run')
        base=ROOT/'.runtime/site'
        science=Settings(data_dir=base/'science',closed_academic_research=False,firestore_prefix='integrated_local_v1')
        origin='http://localhost:'+str(port)
        project=None
    else:
        science=Settings.from_env()
        if not science.cloud_run:raise ValueError('Use --local for loopback testing')
        project=science.firestore_project
        if project!='hydroponic-leaf-research-aryae':raise ValueError('Only the dedicated Hydroponic project is permitted')
        if os.getenv('FIRESTORE_EMULATOR_HOST'):raise ValueError('Cloud Run cannot use emulator accounts')
        origin=os.environ['HYDRO_APP_ORIGIN']
        base=science.data_dir
    key=os.getenv('HYDRO_EMAIL_HMAC_KEY')
    # The existing Secret Manager mount can supply the key-derivation root. It
    # grants no HTTP access: the integrated router has no access-key login route.
    if not key:key=os.getenv('HYDRO_AUTH_TOKEN') if not local else None
    hmac_key=hashlib.sha256(b'hydroponic-email-challenge-v1\0'+key.encode()).digest() if key else None
    config=Config(base/'auth',origin,development=local,integrated=True,require_verified_email=True,
        require_durable_auth=not local,firestore_project=project,firestore_database=science.firestore_database,
        firestore_prefix=os.getenv('HYDRO_AUTH_FIRESTORE_PREFIX','hydroponic_auth_v1'),
        phone_review_owner_id=os.getenv('HYDRO_PHONE_REVIEW_OWNER_ID'),
        smtp=configured_mail(),email_hmac_key=hmac_key)
    return config,science


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',action='store_true')
    parser.add_argument('--port',type=int,default=8895)
    args=parser.parse_args()
    config,science=configuration(local=args.local,port=args.port)
    uvicorn.run(create_app(config,science),host='127.0.0.1' if args.local else '0.0.0.0',
        port=args.port if args.local else int(os.getenv('PORT','8080')),access_log=False,
        proxy_headers=not args.local,forwarded_allow_ips='*' if not args.local else None)


if __name__=='__main__':main()
