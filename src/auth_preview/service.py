from datetime import datetime,timezone
import csv
import io
import json
from pathlib import PurePosixPath
import secrets
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException

from .database import create_database
from .passwords import hash_password,verify_password,token_hash
from .mail import MailCodes


KINDS=frozenset(('plants','photos','calibrations','analyses','reviews','measurements','masks','instances','growth','scientific_artifacts'))
ADMINS=('PLATFORM_OWNER','PLATFORM_ADMIN','ORG_ADMIN')


def fail(code,message):
    raise HTTPException(code,message)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def public_user(user):
    return {k:user[k] for k in ('id','username','display_name','status','role','requested_org_id',
                              'institution_request','org_id','created_at','version','email','email_verified_at','pending_email')}


class Service:
    session_capacity=1000
    anonymous_session_seconds=15*60

    def __init__(self,config):
        self.config=config
        self.db=create_database(config)
        self.mail=MailCodes(config)
        # Equal-cost unknown-user verification, process-specific, no usable account.
        self.dummy_hash=hash_password(secrets.token_urlsafe(32))

    def audit(self,db,actor,action,target,**details):
        db.execute('INSERT INTO audit(at,actor_id,action,target_id,details) VALUES(?,?,?,?,?)',
                   (now_iso(),actor,action,target,json.dumps(details,ensure_ascii=False)))

    def rate_limit(self,key,maximum,seconds):
        # Own transaction so a failed credential/action cannot roll back the limit.
        with self.db.transaction() as db:
            now=time.time()
            row=db.execute('SELECT * FROM limits WHERE key=?',(key,)).fetchone()
            if row and row['resets']>now:
                if row['count']>=maximum:
                    fail(429,'Too many attempts. Try again after the temporary lockout (up to 15 minutes for sign in, one hour for signup).')
                db.execute('UPDATE limits SET count=count+1 WHERE key=?',(key,))
            else:
                db.execute('INSERT OR REPLACE INTO limits VALUES(?,?,?)',(key,1,now+seconds))

    def reserve_session(self,db,now):
        """Keep the bounded pool usable without evicting signed-in people.

        Anonymous CSRF bootstraps are expendable and short lived. A cookie-less
        visitor must not fill the shared pool and block everyone's next login.
        Selection/deletion/insertion share the caller's database transaction.
        """
        db.execute('DELETE FROM sessions WHERE expires<?',(now,))
        if db.execute('SELECT count(*) FROM sessions').fetchone()[0]>=self.session_capacity:
            anonymous=db.execute('SELECT token_hash FROM sessions WHERE user_id IS NULL LIMIT 1').fetchone()
            if anonymous:db.execute('DELETE FROM sessions WHERE token_hash=?',(anonymous['token_hash'],))
        if db.execute('SELECT count(*) FROM sessions').fetchone()[0]>=self.session_capacity:
            fail(429,'Session capacity reached. Try again later.')

    def new_session(self,db,user_id=None):
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32); now=time.time()
        self.reserve_session(db,now)
        seconds=self.config.session_seconds if user_id else min(self.config.session_seconds,self.anonymous_session_seconds)
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(token_hash(token),user_id,csrf,now+seconds,now))
        return token,csrf

    def session(self,db,token):
        return db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',
                          (token_hash(token or ''),time.time())).fetchone()

    def actor(self,db,token,active=True):
        session=self.session(db,token)
        user=db.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone() if session else None
        if not user:
            fail(401,'Sign in to continue. Your previous session may have been revoked.')
        if active and user['status']!='ACTIVE':
            fail(403,'Your account is '+user['status']+'. Administrator approval is required for workspace access.')
        if active and (self.config.integrated or self.config.require_verified_email) and not self.verified_for_workspace(user):
            fail(403,'Verify your email before opening the scientific workspace.')
        if active:
            org=db.execute('SELECT * FROM organizations WHERE id=?',(user['org_id'],)).fetchone()
            if not org or not org['enabled']:
                fail(403,'This institution is disabled. Contact the platform owner.')
        return user

    def verified_for_workspace(self,user):
        """Email proof, or one explicitly isolated phone-review owner identity.

        The staging exception never writes email_verified_at and never applies to
        public registration, member approval or administrator promotion targets.
        ACTIVE account and enabled institution are independently required by actor.
        """
        return bool(user['email_verified_at'] or (self.config.phone_review_owner_id and
                    user['id']==self.config.phone_review_owner_id and user['role']=='PLATFORM_OWNER'))

    def admin(self,db,token,platform=False,owner=False):
        user=self.actor(db,token)
        roles=('PLATFORM_OWNER',) if owner else ('PLATFORM_OWNER','PLATFORM_ADMIN') if platform else ADMINS
        if user['role'] not in roles:
            fail(403,'This action requires an authorized administrator.')
        return user

    def signup(self,data,old_token):
        password_hash=hash_password(data.password)
        with self.db.transaction() as db:
            public_org=db.execute('SELECT org_id FROM public_signup WHERE singleton=1').fetchone()
            if not public_org or data.requested_org_id!=public_org['org_id'] or data.institution_request:
                fail(400,'Registration is currently available for Bahçeşehir University only. New institution onboarding is not available yet.')
            if data.requested_org_id:
                org=db.execute('SELECT * FROM organizations WHERE id=? AND enabled=1',(data.requested_org_id,)).fetchone()
                if not org: fail(400,'Select a registered institution or request a new one.')
            elif not data.institution_request.strip():
                fail(400,'Enter the name of the institution you would like registered.')
            id=str(uuid4())
            try:
                db.execute('INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,institution_request,created_at,email) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (id,data.username,data.display_name,password_hash,'PENDING','PENDING',data.requested_org_id,data.institution_request.strip(),now_iso(),data.email))
            except sqlite3.IntegrityError:
                fail(409,'That username or email is unavailable. Sign in or use password recovery if you already have an account.')
            self.mail.issue(db,{'id':id},data.email,'verify')
            self.audit(db,id,'registration',id,requested_org_id=data.requested_org_id,institution_request=data.institution_request)
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(old_token or ''),))
            token,csrf=self.new_session(db,id)
            user=db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone()
            return token,{'user':public_user(user),'csrf_token':csrf,'message':'Account created. An administrator must approve your access. We sent an email verification code for password recovery.'}

    def login(self,data,old_token):
        with self.db.transaction() as db:
            user=db.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(data.username,)).fetchone()
            if not verify_password(data.password,user['password_hash'] if user else self.dummy_hash):
                fail(401,'Username or password is incorrect.')
            if user['status'] in ('REJECTED','SUSPENDED'):
                fail(403,'This account is '+user['status']+'. Contact an administrator.')
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(old_token or ''),))
            token,csrf=self.new_session(db,user['id'])
            self.audit(db,user['id'],'login',user['id'])
            return token,{'user':public_user(user),'csrf_token':csrf}

    def revoke(self,db,id):
        db.execute('DELETE FROM sessions WHERE user_id=?',(id,))
        db.execute('DELETE FROM recovery WHERE user_id=?',(id,))
        db.execute('DELETE FROM email_challenges WHERE user_id=?',(id,))

    def users(self,token):
        with self.db.transaction() as db:
            actor=self.admin(db,token)
            rows=db.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
            if actor['role']=='ORG_ADMIN':
                rows=[r for r in rows if (r['org_id'] or r['requested_org_id'])==actor['org_id'] and r['role'] in ('MEMBER','PENDING','ORG_ADMIN')]
            return [public_user(r) for r in rows]

    def target(self,db,actor,id,version):
        target=db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone()
        if not target: fail(404,'Account not found.')
        if id==actor['id'] or target['role']=='PLATFORM_OWNER':
            fail(403,'Self-administration and changes to platform owners are prohibited. The owner cannot be removed by this interface.')
        if actor['role']!='PLATFORM_OWNER' and target['role'] not in ('PENDING','MEMBER'):
            fail(403,'Only the platform owner can manage administrators.')
        if actor['role']=='ORG_ADMIN':
            if (target['org_id'] or target['requested_org_id'])!=actor['org_id']:
                fail(403,'You can manage members of your own institution only.')
            org=db.execute('SELECT * FROM organizations WHERE id=?',(actor['org_id'],)).fetchone()
            if not org['approvals_enabled']: fail(403,'Institutional approval delegation is disabled.')
        if target['version']!=version: fail(409,'This account changed. Reload before making another decision.')
        return target

    def decide(self,token,id,data):
        with self.db.transaction() as db:
            actor=self.admin(db,token); target=self.target(db,actor,id,data.version)
            transitions={'approve':('PENDING','ACTIVE'),'reject':('PENDING','REJECTED'),
                         'suspend':('ACTIVE','SUSPENDED'),'reactivate':('SUSPENDED','ACTIVE')}
            source,status=transitions[data.action]
            if target['status']!=source: fail(409,'This decision is no longer valid for the current account state.')
            if status=='ACTIVE' and (self.config.integrated or self.config.require_verified_email) and not target['email_verified_at']:
                fail(409,'The user must verify their email before access can be approved or reactivated.')
            org_id=target['org_id']; role=target['role']
            if data.action=='approve':
                org_id=data.verified_org_id or target['requested_org_id']
                org=db.execute('SELECT * FROM organizations WHERE id=? AND enabled=1',(org_id,)).fetchone()
                if not org: fail(400,'An administrator must verify and assign a registered institution first.')
                if actor['role']=='ORG_ADMIN' and org_id!=actor['org_id']:
                    fail(403,'Institution assignment is outside your authority.')
                role='MEMBER'
            db.execute('UPDATE users SET status=?,role=?,org_id=?,version=version+1 WHERE id=? AND version=?',
                       (status,role,org_id,id,data.version))
            self.revoke(db,id)
            self.audit(db,actor['id'],'account_'+{'approve':'approved','reject':'rejected','suspend':'suspended','reactivate':'reactivated'}[data.action],id,previous_status=source,status=status,org_id=org_id)
            if org_id!=target['org_id']:
                self.audit(db,actor['id'],'organization_assigned',id,previous_org_id=target['org_id'],org_id=org_id)
            return public_user(db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone())

    def role(self,token,id,data):
        with self.db.transaction() as db:
            actor=self.admin(db,token,owner=True); target=self.target(db,actor,id,data.version)
            if target['status']!='ACTIVE': fail(409,'Approve or reactivate this account before changing its role.')
            if target['role']==data.role: fail(409,'The account already has this role.')
            rank={'MEMBER':0,'ORG_ADMIN':1,'PLATFORM_ADMIN':2}
            promotion=rank.get(data.role,-1)>rank.get(target['role'],-1)
            if promotion and (self.config.integrated or self.config.require_verified_email) and not target['email_verified_at']:
                fail(409,'This account must verify its email before receiving additional administrator permissions.')
            db.execute('UPDATE users SET role=?,version=version+1 WHERE id=?',(data.role,id))
            self.revoke(db,id)
            self.audit(db,actor['id'],'role_changed',id,previous_role=target['role'],role=data.role)
            return public_user(db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone())

    def organizations(self,token=None):
        with self.db.transaction() as db:
            actor=None
            if token:
                session=self.session(db,token)
                if session and session['user_id']:
                    actor=db.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
            platform=actor and actor['status']=='ACTIVE' and actor['role'] in ('PLATFORM_OWNER','PLATFORM_ADMIN')
            if platform and (self.config.integrated or self.config.require_verified_email) and not self.verified_for_workspace(actor):
                platform=False
            if platform:return [dict(x) for x in db.execute('SELECT * FROM organizations ORDER BY name')]
            membership=(actor['org_id'] or actor['requested_org_id']) if actor else None
            if not membership:
                public=db.execute('SELECT org_id FROM public_signup WHERE singleton=1').fetchone()
                membership=public['org_id'] if public else None
            return [dict(x) for x in db.execute('SELECT * FROM organizations WHERE id=? AND enabled=1',(membership,))]

    def signup_institutions(self):
        with self.db.transaction() as db:
            return [dict(o) for o in db.execute('SELECT o.id,o.name FROM organizations o JOIN public_signup p ON p.org_id=o.id WHERE o.enabled=1')]

    def organization(self,token,data,id=None):
        with self.db.transaction() as db:
            actor=self.admin(db,token,platform=True)
            if id:
                current=db.execute('SELECT * FROM organizations WHERE id=?',(id,)).fetchone()
                if not current: fail(404,'Institution not found.')
                if current['version']!=data.version: fail(409,'Institution changed. Reload and retry.')
                # Disabling an organization containing the owner could lock out its only administrator.
                if not data.enabled and db.execute("SELECT 1 FROM users WHERE org_id=? AND role='PLATFORM_OWNER'",(id,)).fetchone():
                    fail(403,'The owner institution cannot be disabled. Establish a reviewed ownership-transfer procedure first.')
                if actor['role']!='PLATFORM_OWNER' and (data.enabled!=bool(current['enabled']) or data.approvals_enabled!=bool(current['approvals_enabled'])):
                    fail(403,'Only the owner may change institution access or approval delegation.')
                try:
                    db.execute('UPDATE organizations SET name=?,enabled=?,approvals_enabled=?,version=version+1 WHERE id=?',
                               (data.name,data.enabled,data.approvals_enabled,id))
                except sqlite3.IntegrityError: fail(409,'An institution with that name already exists.')
                action='organization_updated'
            else:
                id=str(uuid4())
                try: db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(id,data.name))
                except sqlite3.IntegrityError: fail(409,'An institution with that name already exists.')
                action='organization_created'
            self.audit(db,actor['id'],action,id,name=data.name)
            return dict(db.execute('SELECT * FROM organizations WHERE id=?',(id,)).fetchone())

    def scope(self,db,token,organization_id=None):
        actor=self.actor(db,token)
        if organization_id and organization_id!=actor['org_id']:
            fail(403,'Access denied: this institution is outside your verified membership.')
        return actor

    def resource(self,db,actor,kind,id):
        row=db.execute('SELECT * FROM resources WHERE id=? AND org_id=? AND kind=?',
                       (id,actor['org_id'],kind)).fetchone()
        if not row: fail(404,'Record not found in your institution.')
        return row

    @staticmethod
    def resource_json(row):
        return {'id':row['id'],'org_id':row['org_id'],'kind':row['kind'],'plant_id':row['plant_id'],
                'data':json.loads(row['payload']),'has_artifact':bool(row['artifact_key'])}

    def resources(self,token,kind,id=None,organization_id=None):
        if kind not in KINDS: fail(404,'Unknown scientific record type.')
        with self.db.transaction() as db:
            actor=self.scope(db,token,organization_id)
            if id:return self.resource_json(self.resource(db,actor,kind,id))
            rows=db.execute('SELECT * FROM resources WHERE org_id=? AND kind=? ORDER BY id',(actor['org_id'],kind))
            return [self.resource_json(r) for r in rows]

    def create_resource(self,token,kind,data):
        if kind not in ('plants','photos'): fail(405,'This preview supports test plant/photo metadata creation only. Scientific processing uses the unchanged application.')
        with self.db.transaction() as db:
            actor=self.scope(db,token)
            if kind!='plants' and not data.plant_id: fail(400,'Select a plant in your institution.')
            if data.plant_id:self.resource(db,actor,'plants',data.plant_id)
            id=str(uuid4()); payload={'name':data.name,'species':data.species,'synthetic':True}
            db.execute('INSERT INTO resources(id,org_id,kind,plant_id,payload) VALUES(?,?,?,?,?)',
                       (id,actor['org_id'],kind,data.plant_id,json.dumps(payload)))
            self.audit(db,actor['id'],'test_record_created',id,org_id=actor['org_id'],kind=kind)
            return self.resource_json(self.resource(db,actor,kind,id))

    def export(self,token,organization_id):
        rows=self.resources(token,'measurements',organization_id=organization_id)
        out=io.StringIO(newline=''); writer=csv.writer(out)
        writer.writerow(['measurement_id','org_id','plant_id','projected_pixel_area','human_approved','synthetic'])
        for row in rows:
            data=row['data']
            writer.writerow([row['id'],row['org_id'],row['plant_id'],data.get('projected_pixel_area'),data.get('human_approved'),True])
        return out.getvalue()

    def artifact(self,token,id):
        with self.db.transaction() as db:
            actor=self.scope(db,token)
            row=db.execute('SELECT * FROM resources WHERE id=? AND org_id=?',(id,actor['org_id'])).fetchone()
            if not row or not row['artifact_key']: fail(404,'Artifact not found in your institution.')
            key=PurePosixPath(row['artifact_key'])
            prefix=('organizations',actor['org_id'])
            if key.is_absolute() or '..' in key.parts or key.parts[:2]!=prefix:
                fail(403,'Artifact ownership metadata is inconsistent. Access blocked.')
            root=(self.config.data_dir/'private').resolve(); path=root.joinpath(*key.parts).resolve()
            if not path.is_relative_to(root): fail(403,'Invalid artifact path.')
            try: contents=path.read_bytes()
            except FileNotFoundError: fail(404,'Artifact is unavailable.')
            if token_hash(contents.hex())!=row['artifact_hash']:
                fail(409,'Artifact integrity verification failed.')
            return contents,row['artifact_type']

    def change_password(self,token,data):
        new_hash=hash_password(data.new_password)
        with self.db.transaction() as db:
            actor=self.actor(db,token,active=False)
            if not verify_password(data.current_password,actor['password_hash']): fail(401,'Current password is incorrect.')
            db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?',(new_hash,actor['id']))
            self.revoke(db,actor['id'])
            self.audit(db,actor['id'],'password_changed',actor['id'])
            self.queue_notification(db,actor,'password_changed')
            new=self.new_session(db,actor['id'])[0]
        self.attempt_notifications()
        return new

    def attempt_notifications(self):
        # Account/security changes already committed. A transient outbox-store
        # failure must not hide the newly rotated session or suggest rollback.
        try:self.retry_notifications()
        except HTTPException:pass

    def queue_notification(self,db,user,event,email=None):
        # Contains event metadata only: never password, code, session or provider key.
        recipient=email or (user['email'] if user['email_verified_at'] else None)
        if recipient:
            db.execute('INSERT INTO email_notifications(id,user_id,email,event,created) VALUES(?,?,?,?,?)',
                       (str(uuid4()),user['id'],recipient,event,time.time()))

    def retry_notifications(self,token=None):
        """Operator-retryable local outbox; failures cannot undo account security changes.

        Runs under the SQLite write lock to avoid concurrent duplicate sends. A
        crash after provider acceptance may still cause a duplicate notification.
        """
        sent=0
        with self.db.transaction() as db:
            if token is not None:
                actor=self.actor(db,token,active=False)
                rows=db.execute("SELECT * FROM email_notifications WHERE status='pending' AND user_id=? ORDER BY created LIMIT 20",(actor['id'],)).fetchall()
            else:
                rows=db.execute("SELECT * FROM email_notifications WHERE status='pending' ORDER BY created LIMIT 20").fetchall()
            for row in rows:
                try:
                    self.mail.ready()
                    if self.config.development and not self.config.integrated:
                        pass  # No real delivery in legacy preview.
                    else:
                        self.config.smtp.notify(row['email'],row['event'])
                except (OSError,ValueError,HTTPException,AttributeError):
                    db.execute('UPDATE email_notifications SET attempts=attempts+1,last_error=? WHERE id=?',
                               ('Delivery unavailable; check configured sender, provider credentials, quota and connectivity.',row['id']))
                else:
                    db.execute("UPDATE email_notifications SET status='sent',attempts=attempts+1,delivered=?,last_error=NULL WHERE id=?",(time.time(),row['id']))
                    sent+=1
        return sent

    def email_delivery_status(self,token):
        with self.db.transaction() as db:
            actor=self.actor(db,token,active=False)
            pending=db.execute("SELECT count(*) FROM email_notifications WHERE user_id=? AND status='pending'",(actor['id'],)).fetchone()[0]
            return {'pending_security_notifications':pending,
                    'message':'Security notification delivery is pending. Retry delivery or contact the operator.' if pending else None}

    def email_change(self,token,data):
        with self.db.transaction() as db:
            actor=self.actor(db,token,active=False)
            if not verify_password(data.current_password,actor['password_hash']):fail(401,'Current password is incorrect.')
            if self.config.integrated:
                if data.email==actor['email']:fail(409,'This is already your account email.')
                if db.execute('SELECT 1 FROM users WHERE email=? AND id!=?',(data.email,actor['id'])).fetchone():
                    fail(409,'That email cannot be used. Check the address or use account recovery.')
                self.mail.issue(db,actor,data.email,'change')
                db.execute('UPDATE users SET pending_email=?,version=version+1 WHERE id=?',(data.email,actor['id']))
                # Verification of an old signup challenge must not override this operation.
                db.execute("DELETE FROM email_challenges WHERE user_id=? AND purpose='verify'",(actor['id'],))
                self.audit(db,actor['id'],'email_change_requested',actor['id'])
                return token
            try:
                db.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?',(data.email,actor['id']))
            except sqlite3.IntegrityError:fail(409,'That email cannot be used. Check the address or use account recovery.')
            self.revoke(db,actor['id'])
            self.mail.issue(db,actor,data.email,'verify')
            self.audit(db,actor['id'],'email_changed',actor['id'])
            return self.new_session(db,actor['id'])[0]

    def email_resend(self,token):
        with self.db.transaction() as db:
            actor=self.actor(db,token,active=False)
            if self.config.integrated and actor['pending_email']:
                self.mail.issue(db,actor,actor['pending_email'],'change')
                return
            if not actor['email']:fail(400,'Add an email address first.')
            if actor['email_verified_at']:fail(409,'This email is already verified.')
            self.mail.issue(db,actor,actor['email'],'verify')

    def email_verify(self,token,code):
        new=None
        with self.db.transaction() as db:
            actor=self.actor(db,token,active=False)
            changing=self.config.integrated and bool(actor['pending_email'])
            email=actor['pending_email'] if changing else actor['email']
            valid=self.mail.consume(db,actor,email,'change' if changing else 'verify',code)
            if valid:
                try:
                    if changing:
                        db.execute('UPDATE users SET email=?,pending_email=NULL,email_verified_at=?,version=version+1 WHERE id=?',(email,now_iso(),actor['id']))
                        self.queue_notification(db,actor,'email_changed')
                        self.revoke(db,actor['id'])
                        new=self.new_session(db,actor['id'])[0]
                        self.audit(db,actor['id'],'email_changed',actor['id'])
                    else:
                        db.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?',(now_iso(),actor['id']))
                except sqlite3.IntegrityError:
                    fail(409,'That email is no longer available. Request a different address.')
                self.audit(db,actor['id'],'email_verified',actor['id'])
        # Invalid attempt counters must commit, not roll back with the HTTP error.
        if not valid:fail(400,'Code is invalid, expired, or already used. Request another email if needed.')
        if changing:self.attempt_notifications()
        return new

    def request_reset(self,email):
        self.mail.ready()
        with self.db.transaction() as db:
            user=db.execute('SELECT * FROM users WHERE email=? AND email_verified_at IS NOT NULL',(email,)).fetchone()
            if user:
                try:self.mail.issue(db,user,email,'reset')
                except HTTPException:
                    # Same public response for registered and unknown addresses even
                    # if the provider fails. No raw provider exception in audit.
                    db.execute("DELETE FROM email_challenges WHERE user_id=? AND purpose='reset'",(user['id'],))
                    self.audit(db,user['id'],'password_reset_delivery_failed',user['id'])
                else:self.audit(db,user['id'],'password_reset_requested',user['id'])

    def complete_reset(self,data,old_token=''):
        # Equal hashing work before checking whether an email/code matches.
        encoded=hash_password(data.new_password)
        with self.db.transaction() as db:
            user=db.execute('SELECT * FROM users WHERE email=? AND email_verified_at IS NOT NULL',(data.email,)).fetchone()
            valid=bool(user and self.mail.consume(db,user,data.email,'reset',data.code))
            if valid:
                db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?',(encoded,user['id']))
                self.revoke(db,user['id'])
                self.audit(db,user['id'],'password_recovered',user['id'])
                self.queue_notification(db,user,'password_reset')
                db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(old_token),))
                new_session=self.new_session(db,user['id'])[0] if user['status'] in ('ACTIVE','PENDING') else None
                if new_session:self.audit(db,user['id'],'recovery_session_started',user['id'])
                status=user['status']
        if not valid:fail(400,'Code is invalid, expired, or already used. Request a new reset email.')
        self.attempt_notifications()
        return new_session,status

    def issue_recovery(self,token,id):
        with self.db.transaction() as db:
            actor=self.admin(db,token,owner=True)
            user=db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone()
            if not user:fail(404,'Account not found.')
            if user['role']=='PLATFORM_OWNER':fail(403,'Owner recovery requires the offline operator command.')
            db.execute('DELETE FROM recovery WHERE user_id=?',(id,))
            raw=secrets.token_urlsafe(32)
            db.execute('INSERT INTO recovery VALUES(?,?,?)',(token_hash(raw),id,time.time()+900))
            self.audit(db,actor['id'],'recovery_issued',id)
            return raw

    def recover(self,data):
        new_hash=hash_password(data.new_password)
        with self.db.transaction() as db:
            row=db.execute('SELECT * FROM recovery WHERE token_hash=? AND expires>?',(token_hash(data.token),time.time())).fetchone()
            if not row:fail(400,'Recovery code is invalid, expired, or already used.')
            db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?',(new_hash,row['user_id']))
            self.revoke(db,row['user_id'])
            self.audit(db,row['user_id'],'password_recovered',row['user_id'])
            user=db.execute('SELECT * FROM users WHERE id=?',(row['user_id'],)).fetchone()
            self.queue_notification(db,user,'password_reset')
        self.attempt_notifications()

    def bootstrap(self,username,password,institution):
        """Offline, explicit operator action. No default credentials; never changes existing accounts."""
        if institution!='Bahçeşehir University':
            raise ValueError('Only Bahçeşehir University is available for the initial release')
        encoded=hash_password(password)
        with self.db.transaction() as db:
            if db.execute("SELECT 1 FROM users WHERE role='PLATFORM_OWNER'").fetchone():
                raise ValueError('An owner already exists. Bootstrap cannot replace it.')
            org_id=str(uuid4());id=str(uuid4())
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(org_id,institution))
            db.execute('INSERT INTO public_signup VALUES(1,?)',(org_id,))
            db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (id,username,encoded,'ACTIVE','PLATFORM_OWNER',org_id,org_id,now_iso()))
            self.audit(db,'offline-operator','owner_bootstrapped',id,org_id=org_id)
