"""Firebase is the credential authority; existing backend grants remain authoritative."""
import secrets
import sqlite3
import time
from uuid import uuid4

from ..auth_preview.database import create_database
from ..auth_preview.passwords import token_hash
from ..auth_preview.service import Service,fail,now_iso,public_user


class FirebaseService(Service):
    credential_authority='firebase-v1'

    def __init__(self,config,gateway,*,database=None):
        self.config=config;self.gateway=gateway
        if database is not None:self.db=database
        elif config.firestore_project:
            from ..auth_preview.firestore_database import FirestoreDatabase
            self.db=FirestoreDatabase(config,credential_authority=self.credential_authority)
        else:self.db=create_database(config)
        # No custom password hashes, recovery challenges or mail sender are used.
        with self.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS firebase_identities(uid TEXT PRIMARY KEY,user_id TEXT NOT NULL UNIQUE REFERENCES users(id))')
            db.execute('CREATE TABLE IF NOT EXISTS firebase_sessions(token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,uid TEXT NOT NULL REFERENCES firebase_identities(uid),security_stamp TEXT NOT NULL)')

    def new_session(self,db,user_id=None):
        if user_id is not None:fail(401,'A fresh Firebase sign in is required')
        return super().new_session(db)

    def session(self,db,token):
        row=super().session(db,token)
        if not row or not row['user_id']:return row
        binding=db.execute('SELECT f.*,i.user_id FROM firebase_sessions f JOIN firebase_identities i ON i.uid=f.uid WHERE f.token_hash=?',(row['token_hash'],)).fetchone()
        if not binding or binding['user_id']!=row['user_id']:return None
        try:identity=self.gateway.inspect_cookie(token)
        except Exception:return None  # Fail closed; never fall back to a local credential.
        if identity['uid']!=binding['uid'] or identity['stamp']!=binding['security_stamp']:return None
        return row

    def _identity(self,token):
        try:return self.gateway.inspect_id(token)
        except Exception:fail(401,'Sign in again to verify your identity.')

    def _issue(self,db,id_token,identity,user,old_token):
        if user['status'] in ('SUSPENDED','REJECTED'):fail(403,'This account is restricted. Contact an administrator.')
        # Every session issuance path, including an idempotent registration
        # retry, must refresh provider email proof. Old backend metadata must
        # never turn a newly unverified Firebase identity into workspace access.
        verified=(user['email_verified_at'] if user['email']==identity['email'] else None) if identity['email_verified'] else None
        if identity['email_verified'] and not verified:verified=now_iso()
        try:
            if user['email']!=identity['email'] or user['email_verified_at']!=verified:
                # Invalidate administrator forms prepared against old identity
                # proof; routine sign-ins with unchanged proof keep the version.
                db.execute('UPDATE users SET email=?,email_verified_at=?,version=version+1 WHERE id=?',
                    (identity['email'],verified,user['id']))
        except sqlite3.IntegrityError:fail(409,'Contact the administrator to resolve this account association.')
        user=db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        # Official JWTs can be identical when issued in the same second. The
        # per-browser nonce makes backend logout/CSRF/revocation independent;
        # Firebase still verifies the complete JWT, never an unsigned payload.
        jwt=self.gateway.cookie(id_token,self.config.session_seconds)+'~'+secrets.token_urlsafe(24)
        db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(old_token or ''),))
        now=time.time();csrf=secrets.token_urlsafe(32)
        self.reserve_session(db,now)
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(token_hash(jwt),user['id'],csrf,now+self.config.session_seconds,now))
        db.execute('INSERT INTO firebase_sessions VALUES(?,?,?)',(token_hash(jwt),identity['uid'],identity['stamp']))
        self.audit(db,user['id'],'firebase_signin',user['id'])
        return jwt,dict(user=public_user(user),csrf_token=csrf)

    def register(self,data,old_token):
        identity=self._identity(data.id_token)
        with self.db.transaction() as db:
            binding=db.execute('SELECT user_id FROM firebase_identities WHERE uid=?',(identity['uid'],)).fetchone()
            if binding:
                user=db.execute('SELECT * FROM users WHERE id=?',(binding['user_id'],)).fetchone()
                return self._issue(db,data.id_token,identity,user,old_token)
            self.registration_organization(db,data.requested_org_id)
            id=str(uuid4())
            try:
                db.execute('INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (id,data.username,data.display_name,'FIREBASE_MANAGED','PENDING','PENDING',data.requested_org_id,now_iso(),identity['email'],now_iso() if identity['email_verified'] else None))
                db.execute('INSERT INTO firebase_identities VALUES(?,?)',(identity['uid'],id))
            except sqlite3.IntegrityError:fail(409,'Account information is unavailable. Contact the administrator if you already have an account.')
            self.audit(db,id,'firebase_registration',id,requested_org_id=data.requested_org_id)
            user=db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone()
            return self._issue(db,data.id_token,identity,user,old_token)

    def registration_organization(self,db,requested_org_id):
        public=db.execute('SELECT org_id FROM public_signup WHERE singleton=1').fetchone()
        org=db.execute('SELECT * FROM organizations WHERE id=? AND enabled=1',(requested_org_id,)).fetchone()
        if not public or public['org_id']!=requested_org_id or not org:
            fail(400,'Select an institution currently accepting registration.')
        return org

    def exchange(self,id_token,old_token):
        identity=self._identity(id_token)
        with self.db.transaction() as db:
            binding=db.execute('SELECT user_id FROM firebase_identities WHERE uid=?',(identity['uid'],)).fetchone()
            if not binding:fail(403,'Complete registration or contact the administrator to link your existing account.')
            user=db.execute('SELECT * FROM users WHERE id=?',(binding['user_id'],)).fetchone()
            return self._issue(db,id_token,identity,user,old_token)

    def bind_for_local_test(self,db,user_id,uid):
        """Operator-only fixture hook: never exposed through HTTP or production."""
        from .identity import local_guard
        local_guard()
        db.execute('INSERT INTO firebase_identities VALUES(?,?)',(uid,user_id))

    def _legacy_disabled(self,*args,**kwargs):fail(405,'Use Firebase account management.')
    signup=login=change_password=recover=_legacy_disabled
