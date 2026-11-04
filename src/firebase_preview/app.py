"""One Firebase identity frontend around the institutional scientific app."""
from pathlib import Path
import hashlib

from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from ..auth_preview.app import Input
from ..integrated.app import create_app as integrated_app
from ..webapp.settings import Settings
from .identity import FirebaseConfig,PROJECT,local_guard,request_cache
from .service import FirebaseService

STATIC=Path(__file__).parent/'static'


class Proof(Input):
    id_token:str=Field(min_length=1,max_length=16384,repr=False)


class Registration(Proof):
    username:str=Field(min_length=3,max_length=40,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]+$')
    display_name:str=Field(default='',max_length=80)
    requested_org_id:str=Field(min_length=1,max_length=80)


class ReceivingInbox(Input):
    email:str=Field(min_length=3,max_length=254)


class IdentityContext:
    def __init__(self,app,guard=local_guard,connect="http://127.0.0.1:9099"):
        self.app=app;self.guard=guard;self.connect=connect
    async def __call__(self,scope,receive,send):
        self.guard()
        marker=request_cache.set({})
        async def safe_send(message):
            if message['type']=='http.response.start':
                headers=[(k,v) for k,v in message['headers'] if k.lower()!=b'content-security-policy']
                headers.append((b'content-security-policy',("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self' "+self.connect+"; form-action 'self'; frame-ancestors 'none'; base-uri 'none'").encode()))
                message={**message,'headers':headers}
            await send(message)
        try:await self.app(scope,receive,safe_send)
        finally:request_cache.reset(marker)


def create_app(config,gateway,science_dir,*,scientific_settings=None):
    from .modes import DurableEmulatorConfig,ProductionFirebaseConfig,LIVE_PROJECT
    production=isinstance(config,ProductionFirebaseConfig)
    # Production packaging excludes the local live-test entry point entirely.
    live=False
    accepted=(FirebaseConfig,DurableEmulatorConfig,ProductionFirebaseConfig)
    if not production:
        from .live_test import LiveTestConfig
        live=isinstance(config,LiveTestConfig)
        accepted=(*accepted,LiveTestConfig)
    durable=isinstance(config,(DurableEmulatorConfig,ProductionFirebaseConfig))
    if not isinstance(config,accepted):
        raise ValueError('Use the strictly isolated Firebase evaluation configuration')
    guard=config.guard if live or durable else local_guard
    guard()
    if production:
        from .production import ProductionGateway
        if not isinstance(gateway,ProductionGateway):raise ValueError('Production requires the official production Firebase gateway')
    if not Path(science_dir).resolve().is_relative_to((config.data_dir.parent).resolve()):
        raise ValueError('Scientific fixtures must share the isolated Firebase evaluation directory')
    if durable:
        if (scientific_settings is None or scientific_settings.firestore_project!=config.firestore_project or
            scientific_settings.firestore_database!=config.firestore_database or
            scientific_settings.data_dir.resolve()!=Path(science_dir).resolve() or
            not scientific_settings.gcs_bucket or
            not scientific_settings.firestore_prefix.startswith('hydroponic_firebase_science_' if production else 'firebase_test_')):
            raise ValueError('Durable Firebase requires explicit matching isolated scientific storage')
    elif scientific_settings is not None:
        raise ValueError('Existing local Firebase evaluation storage cannot be replaced')
    service=FirebaseService(config,gateway)
    settings=scientific_settings or Settings(data_dir=Path(science_dir),closed_academic_research=False,
        firestore_prefix='firebase_local_eval')
    app=integrated_app(config,settings,account_service=service)
    blocked={'/','/signin','/signup','/profile','/pending','/institution','/admin','/users','/organizations','/health','/api/login','/api/signup','/api/password',
        '/api/recover','/api/email/change','/api/email/resend','/api/email/verify','/api/email/delivery-status',
        '/api/email/retry-delivery','/api/recovery/request','/api/recovery/complete'}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path','') not in blocked
        and not getattr(r,'path','').endswith('/recovery')]
    app.mount('/firebase-static',StaticFiles(directory=STATIC),name='firebase-static')
    connect='https://identitytoolkit.googleapis.com https://securetoken.googleapis.com' if live or production else 'http://127.0.0.1:9099'
    app.add_middleware(IdentityContext,guard=guard,connect=connect)

    @app.get('/health')
    def health():
        with service.db.transaction() as db:db.execute('SELECT 1')
        if production:return dict(status='ok',identity='firebase_production',project=LIVE_PROJECT,
            auth_storage='firestore',production_ready=False)
        if live:return dict(status='ok',identity='google_live_test',project=LIVE_PROJECT,
            email_delivery='UNVERIFIED',production_ready=False)
        return dict(status='ok',identity='firebase_auth_emulator',project=PROJECT,emails_sent=False,production_ready=False)

    @app.get('/api/firebase/config')
    def public_config():
        if live or durable:return config.public_config()
        return dict(projectId=PROJECT,apiKey='fake-api-key',authDomain=PROJECT+'.firebaseapp.com',
            emulator='http://127.0.0.1:9099',local_only=True,identity_mode='emulator')

    def receiving_inbox(request:Request,data:ReceivingInbox):
        if not live:return dict(allowed=True)
        service.rate_limit('firebase-mail-intent:'+(request.client.host if request.client else 'unknown'),30,3600)
        config.require_receiving_inbox(data.email)
        return dict(allowed=True)
    if not production:
        app.add_api_route('/api/firebase/test-intent',receiving_inbox,methods=['POST'])

    @app.post('/api/firebase/register',status_code=201)
    def register(request:Request,data:Registration):
        service.rate_limit('firebase-register-attempts:'+(request.client.host if request.client else 'unknown'),120,60)
        identity=service._identity(data.id_token)
        service.rate_limit('firebase-register-uid:'+hashlib.sha256(identity['uid'].encode()).hexdigest(),5,3600)
        token,result=service.register(data,request.cookies.get(config.cookie,''))
        return app.state.set_session_cookie(JSONResponse(result,status_code=201),token)

    @app.post('/api/firebase/session')
    def exchange(request:Request,data:Proof):
        # Cloud Run's peer is a shared internal proxy with proxy-header trust off.
        # Bound raw attempts separately; normal sign-in limits require verified UID.
        service.rate_limit('firebase-exchange-attempts:'+(request.client.host if request.client else 'unknown'),240,60)
        identity=service._identity(data.id_token)
        service.rate_limit('firebase-exchange-uid:'+hashlib.sha256(identity['uid'].encode()).hexdigest(),20,900)
        token,result=service.exchange(data.id_token,request.cookies.get(config.cookie,''))
        return app.state.set_session_cookie(JSONResponse(result),token)

    @app.get('/',response_class=HTMLResponse)
    @app.get('/signin',response_class=HTMLResponse)
    @app.get('/signup',response_class=HTMLResponse)
    @app.get('/profile',response_class=HTMLResponse)
    @app.get('/pending',response_class=HTMLResponse)
    @app.get('/verify-email',response_class=HTMLResponse)
    def page():
        html=(STATIC/'index.html').read_text(encoding='utf-8')
        if production:html=html.replace('Local Firebase evaluation. Email actions use an emulator and do not send mail. No production accounts or scientific records are connected.',
            'Sign in with your verified account. Your institution must approve research access.')
        elif live:html=html.replace('Local Firebase evaluation. Email actions use an emulator and do not send mail. No production accounts or scientific records are connected.',
            'Google-managed email testing. Isolated local workspace; production is unchanged. Only configured receiving test inboxes may be used.')
        return HTMLResponse(html)

    @app.get('/institution',response_class=HTMLResponse)
    @app.get('/admin',response_class=HTMLResponse)
    @app.get('/users',response_class=HTMLResponse)
    @app.get('/organizations',response_class=HTMLResponse)
    def institutional_page(request:Request):
        from fastapi import HTTPException
        from fastapi.responses import RedirectResponse
        with service.db.transaction() as db:
            user=None
            try:
                user=service.actor(db,request.cookies.get(config.cookie,''),active=False)
                service.actor(db,request.cookies.get(config.cookie,''))
            except HTTPException as exc:
                destination=('/signin' if exc.status_code==401 else
                    '/verify-email' if user and not user['email_verified_at'] and user['status'] in ('PENDING','ACTIVE') else '/pending')
                return RedirectResponse(destination,status_code=303)
        from ..auth_preview.app import STATIC as AUTH_STATIC
        html=(AUTH_STATIC/'index.html').read_text(encoding='utf-8').replace('<body>','<body data-firebase="true">')
        note=('Verified institutional research workspace.' if production else
            'Google-managed email testing. Isolated local scientific fixtures only.' if live else
            'Local Firebase emulator evaluation. No email is sent. Synthetic scientific data only.')
        html=html.replace('<!--MODE-->','<aside class="card">'+note+'</aside>')
        return HTMLResponse(html)
    return app
