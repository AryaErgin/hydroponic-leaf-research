"""Independent auth candidate; deliberately does not mount the current scientific app."""
import json
from pathlib import Path
import secrets
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Config
from .passwords import token_hash
from .service import Service,public_user,fail
from .mail import normalize_email

STATIC=Path(__file__).parent/'static'


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,str_strip_whitespace=False)


class Signup(Input):
    username:str=Field(min_length=3,max_length=40,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]+$')
    password:str=Field(min_length=15,max_length=128)
    confirm_password:str=Field(min_length=15,max_length=128)
    display_name:str=Field(default='',max_length=80)
    requested_org_id:str|None=Field(default=None,max_length=80)
    institution_request:str=Field(default='',max_length=120)
    email:str=Field(min_length=3,max_length=254)

    @field_validator('email')
    @classmethod
    def email_check(cls,value):return normalize_email(value)

    @model_validator(mode='after')
    def confirmed(self):
        if self.password!=self.confirm_password:raise ValueError('Passwords do not match')
        if self.requested_org_id and self.institution_request:raise ValueError('Select an institution or request a new one')
        return self


class Login(Input):
    username:str=Field(min_length=1,max_length=40)
    password:str=Field(min_length=1,max_length=128)


class Decision(Input):
    action:Literal['approve','reject','suspend','reactivate']
    version:int=Field(ge=1)
    verified_org_id:str|None=Field(default=None,max_length=80)


class Role(Input):
    role:Literal['MEMBER','ORG_ADMIN','PLATFORM_ADMIN']
    version:int=Field(ge=1)


class OrganizationCreate(Input):
    name:str=Field(min_length=2,max_length=120)

    @field_validator('name')
    @classmethod
    def name_check(cls,name):
        if len(name.strip())<2:raise ValueError('Enter an institution name')
        return name.strip()


class OrganizationEdit(OrganizationCreate):
    enabled:bool
    approvals_enabled:bool
    version:int=Field(ge=1)


class Resource(Input):
    name:str=Field(min_length=1,max_length=120)
    plant_id:str|None=Field(default=None,max_length=80)
    species:Literal['lettuce','rumex_acetosa']='lettuce'


class PasswordChange(Input):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=15,max_length=128)


class Recover(Input):
    token:str=Field(min_length=20,max_length=100)
    new_password:str=Field(min_length=15,max_length=128)


class Empty(Input):
    pass


class EmailInput(Input):
    email:str=Field(min_length=3,max_length=254)

    @field_validator('email')
    @classmethod
    def email_check(cls,value):return normalize_email(value)


class EmailChange(EmailInput):
    current_password:str=Field(min_length=1,max_length=128)


class EmailCode(Input):
    code:str=Field(min_length=8,max_length=8)


class ResetComplete(EmailInput):
    code:str=Field(min_length=8,max_length=8)
    new_password:str=Field(min_length=15,max_length=128)


class BodyLimit:
    def __init__(self,app,scientific=False):self.app=app;self.scientific=scientific

    async def __call__(self,scope,receive,send):
        if self.scientific:
            from ..integrated.science import is_scientific_path
            if is_scientific_path(scope.get('path','')):
                # The outer scientific limiter handles multipart/native masks.
                return await self.app(scope,receive,send)
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        data=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            data.extend(message.get('body',b''))
            if len(data)>16384:
                return await JSONResponse({'detail':'Request too large'},status_code=413)(scope,receive,send)
            if not message.get('more_body'):break
        delivered=False
        async def buffered():
            nonlocal delivered
            if delivered:return await receive()
            delivered=True
            return {'type':'http.request','body':bytes(data),'more_body':False}
        await self.app(scope,buffered,send)


def create_app(config:Config,*,service_override=None):
    service=service_override if service_override is not None else Service(config)
    # A seeded preview database must never be repurposed as a production store.
    with service.db.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS runtime_mode (mode TEXT NOT NULL)')
        previous=db.execute('SELECT mode FROM runtime_mode').fetchone()
        mode='integrated-local' if config.integrated and config.development else 'development' if config.development else 'production-candidate'
        if previous and previous['mode']!=mode:raise ValueError('Cannot reuse preview storage in another runtime mode')
        if not previous:db.execute('INSERT INTO runtime_mode VALUES(?)',(mode,))
    app=FastAPI(title='Hydroponic accounts candidate',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.config=config;app.state.service=service

    def token(request):return request.cookies.get(config.cookie,'')

    def cookie(response,value):
        response.set_cookie(config.cookie,value,max_age=config.session_seconds,httponly=True,
                            secure=not config.development,samesite='strict',path='/')
        return response

    app.state.set_session_cookie=cookie

    @app.middleware('http')
    async def security(request,call_next):
        if (not config.development or config.integrated) and (request.url.path.startswith('/api/dev/') or request.url.path=='/static/dev.js'):
            return JSONResponse({'detail':'Not found'},status_code=404)
        if request.method in ('POST','PUT','PATCH','DELETE'):
            origin=request.headers.get('origin')
            if origin and origin!=config.origin:
                return JSONResponse({'detail':'Same-origin request required'},status_code=403)
            if request.headers.get('sec-fetch-site') in ('cross-site','same-site'):
                return JSONResponse({'detail':'Same-origin request required'},status_code=403)
            with service.db.transaction() as db:
                session=service.session(db,token(request))
                csrf=request.headers.get('x-csrf-token','')
                if not session or not secrets.compare_digest(csrf,session['csrf']):
                    return JSONResponse({'detail':'CSRF verification failed. Reload this page and try again.'},status_code=403)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            'Permissions-Policy':'geolocation=(), camera=(), microphone=()'})
        if config.integrated:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        if not config.development:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    app.add_middleware(BodyLimit,scientific=config.integrated)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[urlsplit(config.origin).hostname])

    @app.exception_handler(ValueError)
    async def bad_value(request,error):return JSONResponse({'detail':str(error)},status_code=400)

    @app.exception_handler(404)
    async def not_found(request,error):return JSONResponse({'detail':'Not found'},status_code=404)

    @app.exception_handler(__import__('fastapi').exceptions.RequestValidationError)
    async def invalid(request,error):
        # Never echo submitted passwords/tokens from Pydantic's `input` field.
        errors=[{'field':'.'.join(map(str,e['loc'][1:])),'message':e['msg']} for e in error.errors()]
        return JSONResponse({'detail':'Check your form fields.','errors':errors},status_code=422)

    @app.get('/health')
    def health():return {'status':'ok','mode':'development-preview' if config.development else 'candidate-not-integrated'}

    @app.get('/',response_class=HTMLResponse)
    @app.get('/signin',response_class=HTMLResponse)
    @app.get('/signup',response_class=HTMLResponse)
    @app.get('/pending',response_class=HTMLResponse)
    @app.get('/institution',response_class=HTMLResponse)
    @app.get('/analyze',response_class=HTMLResponse)
    @app.get('/profile',response_class=HTMLResponse)
    @app.get('/admin',response_class=HTMLResponse)
    @app.get('/users',response_class=HTMLResponse)
    @app.get('/organizations',response_class=HTMLResponse)
    def index():
        html=(STATIC/'index.html').read_text(encoding='utf-8')
        if config.development and not config.integrated:
            from .development import page_additions
            html=html.replace('<!--MODE-->',page_additions())
        return HTMLResponse(html)

    @app.get('/api/session')
    def session(request:Request):
        with service.db.transaction() as db:
            existing=service.session(db,token(request))
        if not existing:service.rate_limit('session:ip:'+peer(request),120,900)
        with service.db.transaction() as db:
            session=service.session(db,token(request));new_token=None
            if not session:
                new_token,csrf=service.new_session(db)
                session=service.session(db,new_token)
            user=db.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone() if session['user_id'] else None
            org=db.execute('SELECT * FROM organizations WHERE id=?',(user['org_id'] or user['requested_org_id'],)).fetchone() if user else None
            payload={'user':public_user(user) if user else None,'organization':dict(org) if org else None,
                     'csrf_token':session['csrf'],'development':config.development and not config.integrated}
            if config.integrated:
                payload.update(integrated=True,auth_required=True,authenticated=bool(user))
                payload['email_verification_deferred']=bool(user and not user['email_verified_at'] and service.verified_for_workspace(user))
                pending=db.execute("SELECT count(*) FROM email_notifications WHERE user_id=? AND status='pending'",(user['id'],)).fetchone()[0] if user else 0
                payload['email_delivery']={'pending_security_notifications':pending,
                    'message':'Security notification delivery is pending. Retry delivery or contact the operator.' if pending else None}
            from ..cloud_instance.config import IndependentFirebaseConfig
            if isinstance(config,IndependentFirebaseConfig):payload['independent_cloud']=True
            result=JSONResponse(payload)
            return cookie(result,new_token) if new_token else result

    @app.get('/api/organizations')
    def organizations(request:Request):return {'organizations':service.organizations(token(request))}

    @app.get('/api/signup-institutions')
    def signup_institutions():return {'organizations':service.signup_institutions()}

    def peer(request):return request.client.host if request.client else 'unknown'

    @app.post('/api/signup',status_code=201)
    def signup(request:Request,data:Signup):
        service.rate_limit('signup:ip:'+peer(request),5,3600)
        new,result=service.signup(data,token(request))
        return cookie(JSONResponse(result,status_code=201),new)

    @app.post('/api/login')
    def login(request:Request,data:Login):
        service.rate_limit('login:ip:'+peer(request),20,900)
        service.rate_limit('login:user:'+data.username.lower(),8,900)
        new,result=service.login(data,token(request))
        return cookie(JSONResponse(result),new)

    @app.post('/api/logout')
    def logout(request:Request,data:Empty):
        with service.db.transaction() as db:
            session=service.session(db,token(request))
            if session and session['user_id']:service.audit(db,session['user_id'],'logout',session['user_id'])
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token(request)),))
            new,csrf=service.new_session(db)
        return cookie(JSONResponse({'message':'Signed out. This session is now invalid.','csrf_token':csrf}),new)

    @app.post('/api/password')
    def password(request:Request,data:PasswordChange):
        service.rate_limit('password:ip:'+peer(request),10,900)
        new=service.change_password(token(request),data)
        return cookie(JSONResponse({'message':'Password changed. All previous sessions were revoked.'}),new)

    @app.post('/api/recover')
    def recover(request:Request,data:Recover):
        service.rate_limit('recovery:ip:'+peer(request),8,900)
        service.recover(data)
        return {'message':'Password reset. Previous sessions were revoked. Sign in with your new password; account approval status is unchanged.'}

    @app.get('/api/admin/users')
    def users(request:Request):return {'users':service.users(token(request))}

    @app.post('/api/admin/users/{id}/decision')
    def decision(id:str,request:Request,data:Decision):return {'user':service.decide(token(request),id,data)}

    @app.post('/api/admin/users/{id}/role')
    def role(id:str,request:Request,data:Role):return {'user':service.role(token(request),id,data)}

    @app.post('/api/email/change')
    def email_change(request:Request,data:EmailChange):
        service.rate_limit('email:ip:'+peer(request),8,900)
        new=service.email_change(token(request),data)
        message=('Verify the code sent to your new address. Your current email stays active until confirmation.' if config.integrated else
                 'Email updated. Verify the code we sent before using it for recovery. Previous sessions were revoked.')
        return cookie(JSONResponse({'message':message}),new)

    @app.post('/api/email/resend')
    def email_resend(request:Request,data:Empty):
        service.rate_limit('email:ip:'+peer(request),8,900)
        service.email_resend(token(request))
        return {'message':'Email verification code sent. Previous codes are invalid.'}

    @app.post('/api/email/verify')
    def email_verify(request:Request,data:EmailCode):
        service.rate_limit('email-verify:ip:'+peer(request),20,900)
        new=service.email_verify(token(request),data.code)
        result=JSONResponse({'message':'Email address verified. '+('Previous sessions were revoked.' if new else 'Administrator approval is also required before workspace access.')})
        return cookie(result,new) if new else result

    @app.get('/api/email/delivery-status')
    def delivery_status(request:Request):return service.email_delivery_status(token(request))

    @app.post('/api/email/retry-delivery')
    def retry_delivery(request:Request,data:Empty):
        service.rate_limit('delivery-retry:ip:'+peer(request),8,900)
        service.retry_notifications(token(request))
        return service.email_delivery_status(token(request))

    @app.post('/api/recovery/request')
    def request_reset(request:Request,data:EmailInput):
        service.rate_limit('reset-request:ip:'+peer(request),10,900)
        service.rate_limit('reset-request:email:'+token_hash(data.email),3,900)
        service.request_reset(data.email)
        return {'message':'If this address belongs to an account with verified email, we will attempt to send a reset code. Check your inbox; codes expire in 10 minutes. If no message arrives, retry or contact support.'}

    @app.post('/api/recovery/complete')
    def complete_reset(request:Request,data:ResetComplete):
        service.rate_limit('reset-complete:ip:'+peer(request),15,900)
        new,status=service.complete_reset(data,token(request))
        result=JSONResponse({'message':'Password updated. Previous sessions were revoked.', 'account_status':status})
        return cookie(result,new) if new else result

    @app.post('/api/admin/organizations',status_code=201)
    def create_org(request:Request,data:OrganizationCreate):return {'organization':service.organization(token(request),data)}

    @app.post('/api/admin/organizations/{id}')
    def edit_org(id:str,request:Request,data:OrganizationEdit):return {'organization':service.organization(token(request),data,id)}

    @app.get('/api/admin/audit')
    def audit(request:Request):
        with service.db.transaction() as db:
            service.admin(db,token(request),platform=True)
            return {'events':[dict(row) for row in db.execute('SELECT * FROM audit ORDER BY sequence DESC LIMIT 300')]}

    @app.get('/api/workspace/export.csv')
    def export(request:Request,organization_id:str|None=None):
        return Response(service.export(token(request),organization_id),media_type='text/csv',
                        headers={'Content-Disposition':'attachment; filename="institution-test-measurements.csv"'})

    @app.get('/api/workspace/{kind}')
    def resources(kind:str,request:Request,organization_id:str|None=None):
        return {'items':service.resources(token(request),kind,organization_id=organization_id)}

    @app.get('/api/workspace/{kind}/{id}')
    def resource(kind:str,id:str,request:Request,organization_id:str|None=None):
        return {'item':service.resources(token(request),kind,id,organization_id)}

    @app.post('/api/workspace/{kind}',status_code=201)
    def create_resource(kind:str,request:Request,data:Resource):
        return {'item':service.create_resource(token(request),kind,data)}

    @app.get('/api/artifacts/{id}')
    def artifact(id:str,request:Request):
        data,content_type=service.artifact(token(request),id)
        return Response(data,media_type=content_type,headers={'Content-Disposition':'attachment; filename="test-artifact.svg"'})

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    if config.development and not config.integrated:
        from .development import install
        install(app,service)
    return app
