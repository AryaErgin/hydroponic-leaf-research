"""Explicit compatibility repository for this auth service's static statements.

This is deliberately not a SQL interpreter. Only exact registered statement shapes
are accepted. Every auth record and uniqueness reservation is a separate Firestore
document. Native transactions buffer writes until all reads finish; conflicts
return 409 rather than replaying email or other external side effects.
"""
import copy
import hashlib
import json
import sqlite3

from fastapi import HTTPException


CAP=250
TABLES=('users','organizations','public_signup','sessions','recovery','email_challenges',
        'email_notifications','limits','audit','runtime_mode','reservations','meta',
        'firebase_identities','firebase_sessions')
DEFAULTS={
    'users':dict(display_name='',institution_request='',requested_org_id=None,org_id=None,
                 version=1,email=None,email_verified_at=None,pending_email=None),
    'organizations':dict(enabled=1,approvals_enabled=1,version=1),
    'email_challenges':dict(attempts=0),
    'email_notifications':dict(status='pending',attempts=0,delivered=None,last_error=None),
}


def normalized(statement):return ' '.join(statement.split())
def opaque(value):return hashlib.sha256(str(value).encode()).hexdigest()


class Row(dict):
    def __getitem__(self,key):
        if isinstance(key,int):return list(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self,rows=(),rowcount=0):self.rows=[Row(x) for x in rows];self.rowcount=rowcount
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows
    def __iter__(self):return iter(self.rows)


class FirestoreDatabase:
    durable=True

    def __init__(self,config,credential_authority='legacy-v1'):
        from google.cloud import firestore
        if credential_authority not in ('legacy-v1','firebase-v1'):
            raise ValueError('Unsupported credential authority')
        self.client=firestore.Client(project=config.firestore_project,database=config.firestore_database)
        self.prefix=config.firestore_prefix
        self.credential_authority=credential_authority
        try:
            with self.transaction() as db:
                marker=db.raw('meta','credential-authority')
                if marker and marker.get('value')!=credential_authority:
                    raise ValueError('Cannot reuse an account namespace with another credential authority')
                if not marker:
                    # Never infer an identity migration from existing account
                    # emails, sessions, or an older runtime-mode marker.
                    if (credential_authority=='firebase-v1' and
                        any(db.scan(table,limit=1) for table in TABLES) or
                        credential_authority=='legacy-v1' and
                        any(db.scan(table,limit=1) for table in ('firebase_identities','firebase_sessions'))):
                        raise ValueError('Cannot adopt populated storage under another credential authority')
                    db.put('meta','credential-authority',{'value':credential_authority})
        except BaseException:
            self.client.close()
            raise

    def ref(self,table,key):
        if table not in TABLES:raise ValueError('Unsupported auth collection')
        return self.client.collection(self.prefix+'_'+table).document(opaque(key))

    def collections(self):return [self.client.collection(self.prefix+'_'+t) for t in TABLES]
    def close(self):self.client.close()
    def transaction(self):return Connection(self)
    def connect(self):return Connection(self)


class Connection:
    def __init__(self,database):
        self.database=database;self.tx=None;self.cache={};self.writes={}

    def __enter__(self):
        self.tx=self.database.client.transaction(max_attempts=1)
        try:self.tx._begin()
        except Exception as error:self.translate_conflict(error)
        return self

    def __exit__(self,kind,error,traceback):
        try:
            if kind is None:
                if len(self.writes)>450:raise HTTPException(503,'Auth transaction exceeded its safe write bound. Contact the operator.')
                for (table,key),row in self.writes.items():
                    ref=self.database.ref(table,key)
                    if row is None:self.tx.delete(ref)
                    else:self.tx.set(ref,row)
                self.tx._commit()
        except Exception as exc:
            self.translate_conflict(exc)
        finally:
            if self.tx is not None and self.tx.in_progress:
                try:self.tx._rollback()
                except Exception:pass

    @staticmethod
    def translate_conflict(error):
        from google.api_core.exceptions import Aborted,Conflict,DeadlineExceeded,ServiceUnavailable,GoogleAPICallError
        if isinstance(error,(Aborted,Conflict)):
            raise HTTPException(409,'Concurrent account update. Reload and retry the action.') from None
        if isinstance(error,(DeadlineExceeded,ServiceUnavailable)):
            raise HTTPException(503,'The account store is temporarily unavailable. Retry later.') from None
        if isinstance(error,GoogleAPICallError):
            raise HTTPException(503,'The account store request failed. Ask the operator to check service account access, database and index configuration.') from None
        raise error

    def raw(self,table,key):
        pair=(table,str(key))
        if pair not in self.cache:
            try:snap=self.database.ref(*pair).get(transaction=self.tx,timeout=20)
            except Exception as error:self.translate_conflict(error)
            self.cache[pair]=snap.to_dict() if snap.exists else None
        return copy.deepcopy(self.cache[pair])

    def put(self,table,key,row):
        pair=(table,str(key));self.cache[pair]=self.writes[pair]=copy.deepcopy(row)

    def scan(self,table,field=None,value=None,op='==',cap=CAP,order=None,descending=False,limit=None):
        from google.cloud.firestore_v1.base_query import FieldFilter
        query=self.database.client.collection(self.database.prefix+'_'+table)
        if field is not None:query=query.where(filter=FieldFilter(field,op,value))
        if order:query=query.order_by(order,direction='DESCENDING' if descending else 'ASCENDING')
        query=query.limit((limit or cap)+1)
        try:snaps=list(query.stream(transaction=self.tx,timeout=20))
        except Exception as error:self.translate_conflict(error)
        if limit is None and len(snaps)>cap:
            raise HTTPException(503,'The account query exceeded its safe capacity. Contact the operator.')
        rows={s.id:s.to_dict() for s in snaps}
        for (changed_table,key),row in self.writes.items():
            if changed_table!=table:continue
            ident=opaque(key)
            if row is None:rows.pop(ident,None)
            else:rows[ident]=copy.deepcopy(row)
        if field is not None:
            def matches(row):
                actual=row.get(field)
                return actual==value if op=='==' else actual is not None and actual<value if op=='<' else False
            rows={k:r for k,r in rows.items() if matches(r)}
        result=list(rows.values())
        if order:result.sort(key=lambda row:row[order],reverse=descending)
        if limit is not None:result=result[:limit]
        return result

    def key(self,table,row):
        return row['token_hash'] if table in ('sessions','recovery','firebase_sessions') else row['uid'] if table=='firebase_identities' else row['key'] if table=='limits' else '1' if table=='public_signup' else 'mode' if table=='runtime_mode' else row['sequence'] if table=='audit' else row['id']

    def unique_keys(self,table,row):
        if row is None:return []
        fields={'users':('username','email'),'organizations':('name',)}.get(table,())
        keys=[json.dumps([table,f,str(row[f]).casefold()],separators=(',',':')) for f in fields if row.get(f) is not None]
        if table=='email_challenges':keys.append(json.dumps([table,'user-purpose',row['user_id'],row['purpose']],separators=(',',':')))
        # Technical IDs are case-sensitive. Only human-facing names/emails use
        # the case-folding rules above.
        if table=='firebase_identities':keys.append(json.dumps([table,'user_id',row['user_id']],separators=(',',':')))
        return keys

    def save(self,table,row,insert=False):
        key=self.key(table,row);old=self.raw(table,key)
        if insert and old is not None:raise sqlite3.IntegrityError('Duplicate auth record')
        if table in ('firebase_identities','firebase_sessions'):
            if self.database.credential_authority!='firebase-v1':raise ValueError('Firebase records require Firebase credential authority')
            if old is not None and old!=row:raise sqlite3.IntegrityError('Firebase bindings are immutable')
            if table=='firebase_identities':
                if not isinstance(row.get('uid'),str) or not row['uid'] or self.raw('users',row.get('user_id')) is None:
                    raise sqlite3.IntegrityError('Missing Firebase account')
            else:
                session=self.raw('sessions',row['token_hash']);identity=self.raw('firebase_identities',row['uid'])
                if not session or not identity or session.get('user_id')!=identity['user_id']:
                    raise sqlite3.IntegrityError('Missing or inconsistent Firebase session reference')
                if not isinstance(row.get('security_stamp'),str) or not row['security_stamp']:
                    raise sqlite3.IntegrityError('Missing Firebase security stamp')
        old_keys=set(self.unique_keys(table,old));new_keys=set(self.unique_keys(table,row))
        for unique in new_keys:
            reserved=self.raw('reservations',unique)
            if reserved and reserved['record_key']!=str(key):raise sqlite3.IntegrityError('Duplicate auth reservation')
        for unique in old_keys-new_keys:self.put('reservations',unique,None)
        for unique in new_keys:self.put('reservations',unique,{'record_key':str(key),'table':table})
        if table=='users':
            for field in ('org_id','requested_org_id'):
                if row.get(field) is not None and self.raw('organizations',row[field]) is None:raise sqlite3.IntegrityError('Missing organization')
            if row['status']=='ACTIVE' and (row['role']=='PENDING' or not row['org_id']):raise sqlite3.IntegrityError('Active account requires membership')
            if row['status'] not in ('PENDING','ACTIVE','REJECTED','SUSPENDED') or row['role'] not in ('PENDING','MEMBER','ORG_ADMIN','PLATFORM_ADMIN','PLATFORM_OWNER'):
                raise sqlite3.IntegrityError('Invalid auth account state')
        if table in ('sessions','recovery','email_challenges','email_notifications') and row.get('user_id') is not None and self.raw('users',row['user_id']) is None:
            raise sqlite3.IntegrityError('Missing account')
        if table=='public_signup' and self.raw('organizations',row['org_id']) is None:
            raise sqlite3.IntegrityError('Missing public signup organization')
        if table=='sessions' and old is None:
            if row.get('user_id') is not None and len(self.scan('sessions','user_id',row['user_id']))>=50:
                raise HTTPException(429,'This account has reached its session limit. Sign out another session or reset access.')
            count=self.raw('meta','session-count') or {'value':0}
            count['value']+=1
            self.put('meta','session-count',count)
        if table=='email_notifications':row['pending_user_id']=row['user_id'] if row['status']=='pending' else None
        self.put(table,key,row)

    def remove(self,table,row):
        for unique in self.unique_keys(table,row):self.put('reservations',unique,None)
        if table=='sessions':
            binding=self.raw('firebase_sessions',row['token_hash'])
            if binding is not None:self.remove('firebase_sessions',binding)
            count=self.raw('meta','session-count')
            if not count or count['value']<=0:raise HTTPException(503,'Account session counter is inconsistent. Contact the operator.')
            count['value']-=1
            self.put('meta','session-count',count)
        self.put(table,self.key(table,row),None)

    def by_unique(self,table,field,value):
        unique=json.dumps([table,field,str(value).casefold()],separators=(',',':'))
        reservation=self.raw('reservations',unique)
        return self.raw(table,reservation['record_key']) if reservation else None

    def challenge(self,user,purpose):
        key=json.dumps(['email_challenges','user-purpose',user,purpose],separators=(',',':'))
        reservation=self.raw('reservations',key)
        return self.raw('email_challenges',reservation['record_key']) if reservation else None

    def one(self,table,key,predicate=None):
        row=self.raw(table,key)
        return Result([row] if row is not None and (predicate is None or predicate(row)) else [])

    def insert(self,table,columns,params):
        row={**DEFAULTS.get(table,{}),**dict(zip(columns,params,strict=True))}
        self.save(table,row,insert=True)
        return Result(rowcount=1)

    def update(self,table,key,fields,increments=(),expected_version=None):
        row=self.raw(table,key)
        if row is None or expected_version is not None and row['version']!=expected_version:return Result()
        row.update(fields)
        for field in increments:row[field]+=1
        self.save(table,row)
        return Result(rowcount=1)

    def delete_rows(self,table,rows):
        for row in rows:self.remove(table,row)
        return Result(rowcount=len(rows))

    def execute(self,statement,params=()):
        q=normalized(statement);p=tuple(params)
        if q=='SELECT 1':
            self.raw('meta','health')
            return Result([{'health':1}])
        if q=='CREATE TABLE IF NOT EXISTS runtime_mode (mode TEXT NOT NULL)':return Result()
        if q in (
            'CREATE TABLE IF NOT EXISTS firebase_identities(uid TEXT PRIMARY KEY,user_id TEXT NOT NULL UNIQUE REFERENCES users(id))',
            'CREATE TABLE IF NOT EXISTS firebase_sessions(token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,uid TEXT NOT NULL REFERENCES firebase_identities(uid),security_stamp TEXT NOT NULL)'):
            if self.database.credential_authority!='firebase-v1':raise ValueError('Firebase schema requires Firebase credential authority')
            return Result()
        if q=='SELECT user_id FROM firebase_identities WHERE uid=?':
            row=self.raw('firebase_identities',p[0]);return Result([{'user_id':row['user_id']}] if row else [])
        if q=='INSERT INTO firebase_identities VALUES(?,?)':return self.insert('firebase_identities',('uid','user_id'),p)
        if q=='INSERT INTO firebase_sessions VALUES(?,?,?)':return self.insert('firebase_sessions',('token_hash','uid','security_stamp'),p)
        if q=='SELECT f.*,i.user_id FROM firebase_sessions f JOIN firebase_identities i ON i.uid=f.uid WHERE f.token_hash=?':
            row=self.raw('firebase_sessions',p[0]);identity=self.raw('firebase_identities',row['uid']) if row else None
            return Result([{**row,'user_id':identity['user_id']}] if row and identity else [])
        if q=='SELECT mode FROM runtime_mode':return self.one('runtime_mode','mode')
        if q=='INSERT INTO runtime_mode VALUES(?)':
            self.save('runtime_mode',{'mode':p[0]},insert=True);return Result(rowcount=1)
        if q=='SELECT org_id FROM public_signup WHERE singleton=1':return self.one('public_signup','1')
        if q=='INSERT INTO public_signup VALUES(1,?)':
            return self.insert('public_signup',('singleton','org_id'),(1,p[0]))
        for table,keyfield in (('users','id'),('organizations','id'),('limits','key')):
            if q==f'SELECT * FROM {table} WHERE {keyfield}=?':return self.one(table,p[0])
        if q=='SELECT * FROM organizations WHERE id=? AND enabled=1':return self.one('organizations',p[0],lambda r:r['enabled'])
        if q=='SELECT * FROM users WHERE username=? COLLATE NOCASE':
            row=self.by_unique('users','username',p[0]);return Result([row] if row else [])
        if q=='SELECT * FROM users WHERE email=? AND email_verified_at IS NOT NULL':
            row=self.by_unique('users','email',p[0]);return Result([row] if row and row['email_verified_at'] else [])
        if q=='SELECT 1 FROM users WHERE email=? AND id!=?':
            row=self.by_unique('users','email',p[0]);return Result([{'exists':1}] if row and row['id']!=p[1] else [])
        if q=="SELECT 1 FROM users WHERE role='PLATFORM_OWNER'":
            return Result([{'exists':1}] if self.scan('users','role','PLATFORM_OWNER') else [])
        if q=="SELECT 1 FROM users WHERE org_id=? AND role='PLATFORM_OWNER'":
            return Result([{'exists':1}] if any(r['role']=='PLATFORM_OWNER' for r in self.scan('users','org_id',p[0])) else [])
        if q=='SELECT * FROM users ORDER BY created_at DESC':
            return Result(sorted(self.scan('users'),key=lambda r:r['created_at'],reverse=True))
        if q=='SELECT * FROM organizations ORDER BY name':return Result(sorted(self.scan('organizations'),key=lambda r:r['name'].casefold()))
        if q=='SELECT o.id,o.name FROM organizations o JOIN public_signup p ON p.org_id=o.id WHERE o.enabled=1':
            public=self.raw('public_signup','1');org=self.raw('organizations',public['org_id']) if public else None
            return Result([{'id':org['id'],'name':org['name']}] if org and org['enabled'] else [])
        if q=='SELECT * FROM audit ORDER BY sequence DESC LIMIT 300':return Result(self.scan('audit',order='sequence',descending=True,limit=300))
        if q=='INSERT INTO audit(at,actor_id,action,target_id,details) VALUES(?,?,?,?,?)':
            meta=self.raw('meta','audit-sequence') or {'value':0};meta['value']+=1
            self.put('meta','audit-sequence',meta)
            return self.insert('audit',('sequence','at','actor_id','action','target_id','details'),(meta['value'],*p))
        if q=='SELECT * FROM sessions WHERE token_hash=? AND expires>?':return self.one('sessions',p[0],lambda r:r['expires']>p[1])
        if q=='SELECT * FROM recovery WHERE token_hash=? AND expires>?':return self.one('recovery',p[0],lambda r:r['expires']>p[1])
        if q=='SELECT count(*) FROM sessions':return Result([{'count':(self.raw('meta','session-count') or {'value':0})['value']}])
        if q=='SELECT token_hash FROM sessions WHERE user_id IS NULL LIMIT 1':
            return Result([{'token_hash':row['token_hash']} for row in self.scan('sessions','user_id',None,limit=1)][:1])
        if q=='INSERT INTO sessions VALUES(?,?,?,?,?)':return self.insert('sessions',('token_hash','user_id','csrf','expires','created'),p)
        if q=='INSERT INTO recovery VALUES(?,?,?)':return self.insert('recovery',('token_hash','user_id','expires'),p)
        for table in ('sessions','recovery','email_challenges'):
            if q==f'DELETE FROM {table} WHERE user_id=?':return self.delete_rows(table,self.scan(table,'user_id',p[0]))
        if q=='DELETE FROM sessions WHERE token_hash=?':
            row=self.raw('sessions',p[0]);return self.delete_rows('sessions',[row] if row else [])
        for table in ('sessions','email_challenges'):
            # Expired rows cannot authenticate. Bounded garbage collection may
            # leave older expired rows for a later request without changing access.
            if q==f'DELETE FROM {table} WHERE expires<?':return self.delete_rows(table,self.scan(table,'expires',p[0],op='<',limit=100))
        if q=='UPDATE limits SET count=count+1 WHERE key=?':return self.update('limits',p[0],{},('count',))
        if q=='INSERT OR REPLACE INTO limits VALUES(?,?,?)':
            self.save('limits',dict(zip(('key','count','resets'),p,strict=True)));return Result(rowcount=1)
        if q=='INSERT INTO organizations(id,name) VALUES(?,?)':return self.insert('organizations',('id','name'),p)
        if q=='UPDATE organizations SET name=?,enabled=?,approvals_enabled=?,version=version+1 WHERE id=?':
            return self.update('organizations',p[3],dict(zip(('name','enabled','approvals_enabled'),p[:3])),('version',))
        if q=='INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)':
            return self.insert('users',('id','username','password_hash','status','role','requested_org_id','org_id','created_at'),p)
        if q=='INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,institution_request,created_at,email) VALUES(?,?,?,?,?,?,?,?,?,?)':
            return self.insert('users',('id','username','display_name','password_hash','status','role','requested_org_id','institution_request','created_at','email'),p)
        if q=='INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?)':
            return self.insert('users',('id','username','display_name','password_hash','status','role','requested_org_id','created_at','email','email_verified_at'),p)
        if q=='UPDATE users SET status=?,role=?,org_id=?,version=version+1 WHERE id=? AND version=?':
            return self.update('users',p[3],dict(zip(('status','role','org_id'),p[:3])),('version',),expected_version=p[4])
        for field in ('role','password_hash','pending_email','email_verified_at'):
            if q==f'UPDATE users SET {field}=?,version=version+1 WHERE id=?':return self.update('users',p[1],{field:p[0]},('version',))
        if q=='UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?':
            return self.update('users',p[1],{'email':p[0],'email_verified_at':None},('version',))
        if q=='UPDATE users SET email=?,email_verified_at=?,version=version+1 WHERE id=?':
            return self.update('users',p[2],{'email':p[0],'email_verified_at':p[1]},('version',))
        if q=='UPDATE users SET email=?,pending_email=NULL,email_verified_at=?,version=version+1 WHERE id=?':
            return self.update('users',p[2],{'email':p[0],'pending_email':None,'email_verified_at':p[1]},('version',))
        if q=='SELECT * FROM email_challenges WHERE user_id=? AND email=? AND purpose=?':
            row=self.challenge(p[0],p[2]);return Result([row] if row and row['email']==p[1] else [])
        if q=='INSERT INTO email_challenges(id,user_id,email,purpose,digest,expires) VALUES(?,?,?,?,?,?)':
            return self.insert('email_challenges',('id','user_id','email','purpose','digest','expires'),p)
        if q=='DELETE FROM email_challenges WHERE user_id=? AND purpose=?':
            row=self.challenge(p[0],p[1]);return self.delete_rows('email_challenges',[row] if row else [])
        for purpose in ('verify','reset'):
            if q==f"DELETE FROM email_challenges WHERE user_id=? AND purpose='{purpose}'":
                row=self.challenge(p[0],purpose);return self.delete_rows('email_challenges',[row] if row else [])
        if q=='DELETE FROM email_challenges WHERE id=?':
            row=self.raw('email_challenges',p[0]);return self.delete_rows('email_challenges',[row] if row else [])
        if q=='UPDATE email_challenges SET attempts=attempts+1 WHERE id=?':return self.update('email_challenges',p[0],{},('attempts',))
        if q=='INSERT INTO email_notifications(id,user_id,email,event,created) VALUES(?,?,?,?,?)':
            return self.insert('email_notifications',('id','user_id','email','event','created'),p)
        if q=="SELECT * FROM email_notifications WHERE status='pending' AND user_id=? ORDER BY created LIMIT 20":
            rows=self.scan('email_notifications','pending_user_id',p[0])
            return Result(sorted(rows,key=lambda r:r['created'])[:20])
        if q=="SELECT * FROM email_notifications WHERE status='pending' ORDER BY created LIMIT 20":
            rows=self.scan('email_notifications','status','pending')
            return Result(sorted(rows,key=lambda r:r['created'])[:20])
        if q=="SELECT count(*) FROM email_notifications WHERE user_id=? AND status='pending'":
            return Result([{'count':len(self.scan('email_notifications','pending_user_id',p[0]))}])
        if q=='UPDATE email_notifications SET attempts=attempts+1,last_error=? WHERE id=?':
            return self.update('email_notifications',p[1],{'last_error':p[0]},('attempts',))
        if q=="UPDATE email_notifications SET status='sent',attempts=attempts+1,delivered=?,last_error=NULL WHERE id=?":
            return self.update('email_notifications',p[1],{'status':'sent','delivered':p[0],'last_error':None},('attempts',))
        raise ValueError('Unsupported Firestore auth repository statement')
