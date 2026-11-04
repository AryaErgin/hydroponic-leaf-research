"""Run the explicitly local/demo-only candidate without altering accepted runtimes."""
import os

from ..hydroponic.config import ROOT
from .app import create_app
from .identity import FirebaseConfig,Gateway


def build():
    # Selecting this local entry point cannot enable a cloud Firebase service.
    os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST','127.0.0.1:9099')
    config=FirebaseConfig(ROOT/'.runtime/firebase/manual/auth','http://localhost:8896',
        development=True,integrated=True,require_verified_email=True)
    app=create_app(config,Gateway(),ROOT/'.runtime/firebase/manual/science')
    with app.state.service.db.transaction() as db:
        db.execute('INSERT OR IGNORE INTO organizations(id,name) VALUES(?,?)',('org-bau','Bahçeşehir University — local test'))
        db.execute('INSERT OR IGNORE INTO organizations(id,name) VALUES(?,?)',('org-b','University B — local test'))
        db.execute('INSERT OR IGNORE INTO public_signup VALUES(1,?)',('org-bau',))
    return app
