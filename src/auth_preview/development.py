"""DEVELOPMENT ONLY. Explicit import and route installation; no production import.

This module and dev.js can be excluded from any future production artifact.
Never install on the existing scientific app. No default production credentials.
"""
import json
from pathlib import Path
import secrets
import sqlite3

from fastapi import Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel,ConfigDict,Field

from .passwords import hash_password,token_hash
from .service import KINDS,now_iso,public_user,fail


PERSONAS=[
    ('pending','pending_student','PENDING','PENDING','org-bau',None),
    ('bau-student','bau_student','ACTIVE','MEMBER','org-bau','org-bau'),
    ('b-student','university_b_student','ACTIVE','MEMBER','org-b','org-b'),
    ('bau-admin','bau_administrator','ACTIVE','ORG_ADMIN','org-bau','org-bau'),
    ('owner','platform_owner','ACTIVE','PLATFORM_OWNER','org-platform','org-platform'),
    ('platform-admin','platform_administrator','ACTIVE','PLATFORM_ADMIN','org-platform','org-platform'),
    ('rejected','rejected_student','REJECTED','PENDING','org-bau',None),
    ('suspended','suspended_student','SUSPENDED','MEMBER','org-bau','org-bau'),
]


def page_additions():
    return '<aside class="dev-banner"><strong>DEVELOPMENT PREVIEW</strong><span>Local synthetic data · no production connection</span><label for="dev-persona">Test persona</label><select id="dev-persona"><option>Loading…</option></select><span id="dev-active" aria-live="polite"></span><details class="dev-mail"><summary>Test email inbox · local only</summary><p>No email is sent. Verification and reset codes appear here for testing.</p><button id="dev-mail-refresh" class="secondary">Refresh test inbox</button><div id="dev-mailbox" aria-live="polite"></div></details></aside><script src="/dev.js" defer></script>'


def seed(service):
    config=service.config
    with service.db.transaction() as db:
        if db.execute("SELECT 1 FROM users").fetchone():
            # Additive policy update to this module's previously seeded local test store only.
            if db.execute("SELECT 1 FROM organizations WHERE id='org-bau'").fetchone():
                db.execute("INSERT OR IGNORE INTO public_signup VALUES(1,'org-bau')")
            for id,username,*_ in PERSONAS:
                db.execute('UPDATE users SET email=?,email_verified_at=? WHERE id=? AND email IS NULL',(username+'@example.test',now_iso(),id))
            return
        for id,name in [('org-bau','Bahçeşehir University'),('org-b','University B'),('org-platform','Platform administration')]:
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(id,name))
        db.execute("INSERT INTO public_signup VALUES(1,'org-bau')")
        # Random unknown credential; seeded personas use the explicit development session endpoint.
        encoded=hash_password(secrets.token_urlsafe(32))
        for id,username,status,role,requested,org in PERSONAS:
            db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (id,username,encoded,status,role,requested,org,now_iso()))
            db.execute('UPDATE users SET email=?,email_verified_at=? WHERE id=?',(username+'@example.test',now_iso(),id))
        for prefix,org in [('bau','org-bau'),('b','org-b')]:
            for kind in ['plants']+sorted(KINDS-{'plants'}):
                id=prefix+'-'+kind
                payload={'name':('Bahçeşehir' if prefix=='bau' else 'University B')+' test '+kind,
                         'synthetic':True,'species':'lettuce','human_approved':False,
                         'projected_pixel_area':1200 if prefix=='bau' else 980,
                         'series':[{'day':1,'pixels':800},{'day':2,'pixels':1000},{'day':3,'pixels':1200}],
                         'note':'Synthetic fixture. No real biological measurement or automatic human approval.'}
                key=None;digest=None;ctype=None
                if kind in ('photos','masks','instances','scientific_artifacts'):
                    key=f'organizations/{org}/test/{id}.svg'
                    data=f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200"><rect width="320" height="200" fill="#e9f3eb"/><ellipse cx="160" cy="90" rx="76" ry="40" fill="#38734c"/><text x="20" y="175" font-size="14">SYNTHETIC {org} {kind}</text></svg>'.encode()
                    path=config.data_dir/'private'/key;path.parent.mkdir(parents=True,exist_ok=True)
                    # Only write explicitly owned NEW preview fixtures.
                    with path.open('xb') as f:f.write(data)
                    digest=token_hash(data.hex());ctype='image/svg+xml'
                db.execute('INSERT INTO resources VALUES(?,?,?,?,?,?,?,?)',
                           (id,org,kind,None if kind=='plants' else prefix+'-plants',json.dumps(payload),key,digest,ctype))
        db.execute('INSERT INTO resources(id,org_id,kind,payload) VALUES(?,NULL,?,?)',
                   ('legacy-unassigned','measurements',json.dumps({'note':'Unassigned historical fixture: quarantine, do not auto-assign','projected_pixel_area':17})))
        service.audit(db,'development-seed','synthetic_fixtures_created','preview')


class Switch(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    user_id:str|None=Field(default=None,max_length=80)


def install(app,service):
    if not service.config.development:raise RuntimeError('Development routes are forbidden')
    seed(service)

    @app.get('/api/dev/personas')
    def personas():
        with service.db.transaction() as db:
            users=[public_user(u) for u in db.execute('SELECT * FROM users ORDER BY created_at,username')]
        return {'personas':users,'warning':'Development only: these sessions bypass password entry, never backend authorization.'}

    @app.post('/api/dev/switch')
    def switch(request:Request,data:Switch):
        with service.db.transaction() as db:
            if data.user_id and not db.execute('SELECT 1 FROM users WHERE id=?',(data.user_id,)).fetchone():
                fail(404,'Test account not found.')
            old=request.cookies.get(service.config.cookie,'')
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(old),))
            token,csrf=service.new_session(db,data.user_id)
            service.audit(db,'development-switcher','test_persona_selected',data.user_id or 'visitor')
        return app.state.set_session_cookie(JSONResponse({'csrf_token':csrf}),token)

    @app.get('/dev.js')
    def script():return Response((Path(__file__).parent/'dev.js').read_text(encoding='utf-8'),media_type='text/javascript')

    @app.get('/api/dev/mailbox')
    def mailbox():
        with service.db.transaction() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dev_mailbox'").fetchone()
            messages=[dict(m) for m in db.execute('SELECT * FROM dev_mailbox ORDER BY created DESC LIMIT 30')] if exists else []
        return {'messages':messages,'warning':'LOCAL TEST MAILBOX. No email was sent. Production has no mailbox endpoint.'}
