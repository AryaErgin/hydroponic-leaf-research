"""Strictly local entry point; existing production factories remain unchanged."""
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
from uuid import uuid4

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, Response

from ..auth_preview.config import Config
from ..auth_preview.service import now_iso, fail
from ..firebase_preview.identity import FirebaseConfig, Gateway, local_guard
from ..firebase_preview.service import FirebaseService
from .runtime import ROOT, STATE, ORIGIN, guard_environment, claim_storage, snapshot_auth


@dataclass(frozen=True)
class LocalConfig(FirebaseConfig):
    def __post_init__(self):
        guard_environment(); local_guard()
        if (self.data_dir.resolve()!= (STATE/'auth').resolve() or self.origin!=ORIGIN or
            not self.development or not self.integrated or not self.require_verified_email or
            self.firestore_project or self.smtp or self.email_hmac_key or self.phone_review_owner_id):
            raise ValueError('Local configuration must use its isolated local SQLite store and exact localhost origin.')
        Config.__post_init__(self)

    @property
    def cookie(self): return 'hydro_local_workspace_v1'


class LocalService(FirebaseService):
    """Additional local organizations retain the existing approval and role checks."""
    def signup_institutions(self):
        with self.db.transaction() as db:
            return [dict(id=o['id'],name=o['name']) for o in db.execute('SELECT * FROM organizations WHERE enabled=1 ORDER BY name')]

    def register(self,data,old_token):
        identity=self._identity(data.id_token)
        with self.db.transaction() as db:
            binding=db.execute('SELECT user_id FROM firebase_identities WHERE uid=?',(identity['uid'],)).fetchone()
            if binding:
                user=db.execute('SELECT * FROM users WHERE id=?',(binding['user_id'],)).fetchone()
                return self._issue(db,data.id_token,identity,user,old_token)
            org=db.execute('SELECT * FROM organizations WHERE id=? AND enabled=1',(data.requested_org_id,)).fetchone()
            if not org: fail(400,'Select a local institution currently accepting registration.')
            user_id=str(uuid4())
            try:
                db.execute('INSERT INTO users(id,username,display_name,password_hash,status,role,requested_org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (user_id,data.username,data.display_name,'FIREBASE_MANAGED','PENDING','PENDING',data.requested_org_id,now_iso(),identity['email'],now_iso() if identity['email_verified'] else None))
                db.execute('INSERT INTO firebase_identities VALUES(?,?)',(identity['uid'],user_id))
            except sqlite3.IntegrityError: fail(409,'Account information is unavailable. Contact the local owner.')
            self.audit(db,user_id,'firebase_registration',user_id,requested_org_id=data.requested_org_id,local_only=True)
            user=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            return self._issue(db,data.id_token,identity,user,old_token)


LOCAL_SCRIPT = r'''
(() => {
 const api=async(path,data)=>{const state=await(await fetch('/api/session')).json();const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf_token},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw Error(result.detail||'Local action failed');return result;};
 async function install(){
  const state=await(await fetch('/api/session')).json();
  if(state.user?.role!=='PLATFORM_OWNER')return;
  const main=document.querySelector('main');if(!main||document.querySelector('#local-tools'))return;
  const tools=document.createElement('section');tools.id='local-tools';tools.className='card';
  if(location.pathname==='/organizations'){
   tools.innerHTML='<h2>Add local organization</h2><p>Stored only in this clone. Members still require owner or institution approval.</p><form id="local-org"><label>Organization name<input name="name" minlength="2" maxlength="120" required></label><button type="submit">Add organization</button></form><p role="status" id="local-action-status"></p>';
   tools.querySelector('form').onsubmit=async e=>{e.preventDefault();try{await api('/api/admin/organizations',{name:e.target.elements.name.value});location.reload();}catch(error){tools.querySelector('#local-action-status').textContent=error.message;}};
  }else if(location.pathname==='/users'){
   tools.innerHTML='<h2>Local email proof simulation</h2><p>No email is sent by this emulator. Verify the selected synthetic identity here, then use the normal approval and role controls above. This does not approve access.</p><form id="local-proof"><label>Pending local username<input name="username" required></label><button type="submit">Simulate local email verification</button></form><p role="status" id="local-action-status"></p>';
   tools.querySelector('form').onsubmit=async e=>{e.preventDefault();try{await api('/api/local/verify-member',{username:e.target.elements.username.value});tools.querySelector('#local-action-status').textContent='Local proof simulated. Member must sign in again, then request normal approval.';}catch(error){tools.querySelector('#local-action-status').textContent=error.message;}};
  }else return;
  main.append(tools);
 }
 window.addEventListener('auth-ready',()=>setTimeout(install,100));window.addEventListener('load',()=>setTimeout(install,300));
})();
'''


def build(mode=None,device='cpu'):
    guard_environment(); claim_storage(); local_guard()
    mode=mode or os.getenv('HYDRO_LOCAL_MODE','FULL')
    if mode not in ('FULL','DEMO'): raise ValueError('Select FULL or DEMO local mode.')
    if device not in ('cpu','cuda'): raise ValueError('Local device must be cpu or cuda.')
    from ..firebase_preview.app import create_app
    config=LocalConfig(STATE/'auth',ORIGIN,development=True,integrated=True,require_verified_email=True)
    gateway=Gateway()
    app=create_app(config,gateway,STATE/'science')
    from dataclasses import replace
    app.state.scientific.settings=replace(app.state.scientific.settings,inference_device=device)
    # Existing route closures retain this same service object and all authorization.
    app.state.service.__class__=LocalService
    service=app.state.service
    with service.db.transaction() as db:
        existing=db.execute('SELECT org_id FROM public_signup WHERE singleton=1').fetchone()
        if not existing:
            org_id=str(uuid4())
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(org_id,'Local research workspace'))
            db.execute('INSERT INTO public_signup VALUES(1,?)',(org_id,))
    app.state.local_model_status={'ready':False,'loading':mode=='FULL','device':device,'mode':mode}

    @app.get('/health/local')
    def local_health():
        return dict(status='ok',mode=mode,local_only=True,emails_sent=False,
            identity='firebase_auth_emulator',storage='local_sqlite',
            model=app.state.local_model_status)

    @app.get('/local-account.js')
    def local_account_script():
        from ..auth_preview.app import STATIC
        script=(STATIC/'app.js').read_text(encoding='utf-8')
        script=script.replace('Bah\u00e7e\u015fehir is the only signup institution. Additional institution onboarding is reserved for a later release.',
            'Local organizations have independent membership and approval. The local owner can add an institution below.')
        return Response(script,media_type='text/javascript')

    @app.get('/local-tools.js')
    def local_script(): return Response(LOCAL_SCRIPT,media_type='text/javascript')

    from pydantic import BaseModel, Field
    class VerifyMember(BaseModel): username:str=Field(min_length=3,max_length=40)
    @app.post('/api/local/verify-member')
    def verify_member(request:Request,data:VerifyMember):
        from firebase_admin import auth
        with service.db.transaction() as db:
            actor=service.admin(db,request.cookies.get(config.cookie,''),owner=True)
            target=db.execute('SELECT u.*,f.uid FROM users u JOIN firebase_identities f ON f.user_id=u.id WHERE u.username=?',(data.username,)).fetchone()
            if not target: raise HTTPException(404,'Local member not found.')
            auth.update_user(target['uid'],email_verified=True,app=gateway.app)
            service.audit(db,actor['id'],'local_email_proof_simulated',target['id'],local_only=True)
        return {'simulated':True,'approval_changed':False,'sign_in_again':True}

    @app.middleware('http')
    async def local_presentation(request,call_next):
        response=await call_next(request)
        if response.headers.get('content-type','').startswith('text/html'):
            body=b''.join([chunk async for chunk in response.body_iterator])
            label=('FULL scientific mode: real promoted SegFormer enabled; human review required.' if mode=='FULL' else
                'DEMO mode: scientific-model readiness has not been verified. Complete FULL setup before new model analyses.')
            banner=('<aside role="status" style="padding:12px;background:#fff2be;color:#302900;border:2px solid #b08100">LOCAL / '+mode+'  |  '+label+' Isolated local accounts and data. No email is sent.</aside>').encode()
            closing=body.find(b'>',body.find(b'<body'))
            if closing>=0: body=body[:closing+1]+banner+body[closing+1:]
            body=body.replace(b'/static/app.js',b'/local-account.js')
            body=body.replace(b'Synthetic scientific data only.',b'Isolated local research workspace.')
            body=body.replace(b'</body>',b'<script src="/local-tools.js" defer></script></body>')
            headers=dict(response.headers); headers.pop('content-length',None)
            response=Response(body,status_code=response.status_code,headers=headers,media_type=None)
        if request.method in ('POST','PUT','PATCH','DELETE') and response.status_code<400:
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(snapshot_auth)
        return response
    return app
