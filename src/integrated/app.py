"""Single FastAPI/session router, reusing the accepted scientific endpoints."""
from contextlib import asynccontextmanager
import re

from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..auth_preview.app import create_app as account_app
from ..webapp.app import create_app as scientific_app,STATIC,BodyLimitMiddleware
from ..webapp.settings import Settings
from .science import ScientificServices,ScopedService,ScientificScope,is_scientific_path


def create_app(config,settings=None,*,account_service=None):
    if not config.integrated or not config.require_verified_email:
        raise ValueError('The integrated application requires verified-email institutional sessions')
    settings=(settings or Settings()).validate()
    app=account_app(config,service_override=account_service)
    app.title='Hydroponic Leaf Research'
    registry=ScientificServices(settings,app.state.service)
    app.state.scientific=registry
    # Reuse endpoint handlers and exception contracts, not a second mounted app
    # or a second implementation of the scientific workflow.
    scientific=scientific_app(settings,service_override=ScopedService())
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path','') not in ('/analyze','/health')
        and not getattr(r,'path','').startswith(('/api/workspace','/api/artifacts'))]
    app.router.routes.extend(r for r in scientific.router.routes if is_scientific_path(getattr(r,'path','')))
    for kind,handler in scientific.exception_handlers.items():
        if kind not in app.exception_handlers:app.exception_handlers[kind]=handler
    # Scientific ValueError is deliberately actionable; auth validation excludes
    # credential payloads, so its sanitized RequestValidationError stays intact.
    app.add_middleware(BodyLimitMiddleware,settings=settings)
    app.add_middleware(ScientificScope,registry=registry,cookie=config.cookie)
    app.mount('/science-static',StaticFiles(directory=STATIC),name='science-static')

    @app.get('/help',response_class=HTMLResponse)
    def user_guide():
        # Public, static instructions only. No account or institution data read.
        return HTMLResponse((STATIC/'user-guide.html').read_text(encoding='utf-8'))

    @app.get('/health')
    def health():
        with app.state.service.db.transaction() as db:db.execute('SELECT 1')
        return dict(status='ok',application='integrated',scientific_storage='organization_scoped',
                    email_configured=bool(config.smtp),production_ready=False)

    @app.get('/analyze')
    def analyze_page(request:Request):
        from fastapi import HTTPException
        try:registry.authorize(request.cookies.get(config.cookie,''))
        except HTTPException as exc:
            if exc.status_code==401:return RedirectResponse('/signin',status_code=303)
            return RedirectResponse('/profile' if 'email' in str(exc.detail).lower() else '/pending',status_code=303)
        page=(STATIC/'index.html').read_text(encoding='utf-8').replace('/static/','/science-static/')
        page=page.replace('<body>','<body data-integrated="true">')
        page=re.sub(r'<section id="login-panel".*?</section>','',page,flags=re.S)
        if config.phone_review_owner_id:
            page=page.replace('<body data-integrated="true">','<body data-integrated="true"><aside class="card" role="status">Phone review workspace. Email delivery is unfinished. Existing production records remain in the original site; this workspace saves separately.</aside>')
        return HTMLResponse(page)

    @asynccontextmanager
    async def lifespan(_):
        try:yield
        finally:registry.close()
    app.router.lifespan_context=lifespan
    return app
