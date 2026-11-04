"""Offline candidate-store operations. Never connects to Firestore or the current app.

Requires an explicit store and secure origin. Passwords read privately via getpass.
An operator with trusted filesystem/IAM access must execute owner recovery.
"""
import argparse
from getpass import getpass
from pathlib import Path
import secrets
import time

from .config import Config
from .passwords import token_hash
from .service import Service


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['bootstrap-owner','recover-owner'])
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--origin',required=True)
    p.add_argument('--username',required=True)
    p.add_argument('--institution')
    args=p.parse_args()
    # create_app validates store mode before an operation, without seeding or dev routes.
    from .app import create_app
    app=create_app(Config(args.data_dir.resolve(),args.origin,development=False))
    service=app.state.service
    if args.action=='bootstrap-owner':
        if not args.institution:p.error('--institution is required for bootstrap')
        password=getpass('Owner passphrase (15–128 characters): ')
        if password!=getpass('Confirm passphrase: '):p.error('Passphrases do not match')
        service.bootstrap(args.username,password,args.institution)
        print('Owner created in the explicitly selected candidate store. No production migration performed.')
    else:
        with service.db.transaction() as db:
            owner=db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND role='PLATFORM_OWNER'",(args.username,)).fetchone()
            if not owner:p.error('Owner not found')
            service.revoke(db,owner['id'])
            token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO recovery VALUES(?,?,?)',(token_hash(token),owner['id'],time.time()+900))
            service.audit(db,'offline-operator','owner_recovery_issued',owner['id'])
            print('Deliver this one-use code privately to the verified owner (expires in 15 minutes):')
            print(token)


if __name__=='__main__':main()
