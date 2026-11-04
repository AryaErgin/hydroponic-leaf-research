"""Create one synthetic owner in the existing loopback-only developer runtime.

Never import this as a production entry point. No credentials are printed or saved.
"""
from getpass import getpass
import os
from pathlib import Path
import sys
from types import SimpleNamespace


EMAIL = 'owner@example.test'
USERNAME = 'demo-owner'


def require_local_environment():
    if os.getenv('K_SERVICE') or os.getenv('FIREBASE_AUTH_EMULATOR_HOST') != '127.0.0.1:9099':
        raise ValueError('Requires the local Auth emulator at 127.0.0.1:9099; cloud use is forbidden.')


def require_empty_owner_slot(service):
    with service.db.transaction() as db:
        if db.execute("SELECT 1 FROM users WHERE role='PLATFORM_OWNER' OR username=? OR email=?",
                      (USERNAME, EMAIL)).fetchone():
            raise ValueError('A local owner or demo account already exists; bootstrap cannot replace it.')


def bootstrap():
    require_local_environment()
    password = os.getenv('HYDRO_DEMO_PASSWORD') or getpass('New synthetic demo-owner password (15 to 128 characters): ')
    if not 15 <= len(password) <= 128:
        raise ValueError('Use a password with 15 to 128 characters.')
    # Executing scripts/foo.py puts scripts/ rather than the repository on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import requests
    from firebase_admin import auth
    from src.firebase_preview.run import build

    app = build()
    service = app.state.service
    try:
        require_empty_owner_slot(service)
        require_local_environment()
        # This fixed reserved-domain identity is new, never an existing user promotion.
        # email_verified=True simulates proof ONLY in the guarded local emulator.
        identity = auth.create_user(email=EMAIL, password=password, email_verified=True,
                                    app=service.gateway.app)
        response = requests.post(
            'http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake-api-key',
            json={'email': EMAIL, 'password': password, 'returnSecureToken': True}, timeout=15)
        if response.status_code != 200:
            raise ValueError('Synthetic emulator sign-in failed; no provider response was printed.')
        token = response.json()['idToken']
        _, registered = service.register(SimpleNamespace(
            id_token=token, username=USERNAME, display_name='Demo owner', requested_org_id='org-bau'), '')
        require_local_environment()
        with service.db.transaction() as db:
            if db.execute("SELECT 1 FROM users WHERE role='PLATFORM_OWNER'").fetchone():
                raise ValueError('A local owner appeared during bootstrap; refusing a second owner.')
            user_id = registered['user']['id']
            binding = db.execute('SELECT user_id FROM firebase_identities WHERE uid=?', (identity.uid,)).fetchone()
            if not binding or binding['user_id'] != user_id:
                raise ValueError('Synthetic UID binding failed.')
            db.execute("UPDATE users SET status='ACTIVE',role='PLATFORM_OWNER',org_id='org-bau',version=version+1 WHERE id=? AND username=? AND email=?",
                       (user_id, USERNAME, EMAIL))
            service.revoke(db, user_id)
            service.audit(db, 'local-demo-operator', 'synthetic_owner_bootstrapped', user_id,
                          org_id='org-bau', synthetic=True)
        print('Created synthetic demo-owner. Sign in at http://localhost:8896 with owner@example.test and your chosen password.')
        print('Emulator fixture only: no email was sent and no real email verification was performed.')
    finally:
        app.state.scientific.close()
        service.gateway.close()


if __name__ == '__main__':
    try:
        bootstrap()
    except Exception as error:
        # Provider exception messages may contain supplied credentials or action data.
        print('Demo bootstrap refused or failed (' + type(error).__name__ + '). Check the documented local prerequisites; existing accounts are never replaced.', file=sys.stderr)
        raise SystemExit(1)
