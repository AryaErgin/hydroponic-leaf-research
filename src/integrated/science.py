"""Bind the existing scientific implementation to a verified institution.

The caller cannot select storage through IDs or parameters. Organization identity
comes only from the current account session, checked again before mutations.
Unattributed legacy namespaces are deliberately not imported.
"""
from contextvars import ContextVar
from dataclasses import replace
import hashlib
import time
from pathlib import Path
from threading import RLock

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..webapp.service import Service

PREFIXES=('/api/config','/api/models','/api/calibrations','/api/profiles','/api/setup',
          '/api/plants','/api/images','/api/uploads','/api/analyses','/api/measurements','/api/assets')


def is_scientific_path(path):
    return any(path==prefix or path.startswith(prefix+'/') for prefix in PREFIXES)


_current=ContextVar('verified_scientific_scope')


class ScientificServices:
    def __init__(self,settings,auth):
        if settings.database_url:
            raise ValueError('Integrated SQL storage requires a reviewed institution schema; global database reuse is refused')
        if settings.organization_id:raise ValueError('Base settings cannot select an institution')
        if settings.firestore_project and (settings.firestore_project!=auth.config.firestore_project or
            settings.firestore_database!=auth.config.firestore_database):
            raise ValueError('Account and scientific transactions must use the same dedicated Firestore database')
        self.settings=settings;self.auth=auth;self.services={};self.lock=RLock()

    def for_organization(self,org):
        with self.lock:
            if org not in self.services:
                # Staging and production use different base namespaces even
                # when the same approved institution ID exists in both.
                digest=hashlib.sha256((self.settings.firestore_prefix+':'+org).encode()).hexdigest()
                scoped=replace(self.settings,data_dir=self.settings.data_dir/'organizations'/digest,
                    organization_id=org,firestore_prefix='tenant_'+digest)
                self.services[org]=Service(scoped)
                if scoped.firestore_project:
                    self.services[org].db.engine.authorization_guard=self.authorize_commit
            return self.services[org]

    def authorize_commit(self,transaction):
        """Read account grants inside the scientific write transaction itself."""
        from ..auth_preview.passwords import token_hash
        registry,token,org=_current.get()
        if registry is not self:raise HTTPException(403,'Invalid scientific request context')
        database=self.auth.db
        session=database.ref('sessions',token_hash(token)).get(transaction=transaction,timeout=20).to_dict()
        if not session or session['expires']<=time.time() or not session.get('user_id'):
            raise HTTPException(401,'Your session expired or was revoked. Sign in again.')
        if getattr(self.auth,'credential_authority',None)=='firebase-v1':
            from ..firebase_preview.identity import request_cache
            binding=database.ref('firebase_sessions',token_hash(token)).get(transaction=transaction,timeout=20).to_dict()
            identity=(database.ref('firebase_identities',binding['uid']).get(transaction=transaction,timeout=20).to_dict()
                      if binding else None)
            # A long analysis must not reuse provider proof cached before an
            # external password reset/revocation. Backend bindings/grants below
            # remain reads in this same scientific Firestore transaction.
            cache_marker=request_cache.set(None)
            try:provider=self.auth.gateway.inspect_cookie(token)
            except Exception:raise HTTPException(401,'Your identity changed. Sign in again.') from None
            finally:request_cache.reset(cache_marker)
            if (not identity or identity['user_id']!=session['user_id'] or
                provider['uid']!=binding['uid'] or provider['stamp']!=binding['security_stamp']):
                raise HTTPException(401,'Your session expired or was revoked. Sign in again.')
        actor=database.ref('users',session['user_id']).get(transaction=transaction,timeout=20).to_dict()
        institution=database.ref('organizations',org).get(transaction=transaction,timeout=20).to_dict()
        if not actor or actor['status']!='ACTIVE' or actor.get('org_id')!=org or not self.auth.verified_for_workspace(actor) or not institution or not institution['enabled']:
            raise HTTPException(403,'Workspace access changed. Reopen your account before saving.')

    def authorize(self,token,claimed=None):
        with self.auth.db.transaction() as db:
            actor=self.auth.scope(db,token,claimed)
            return dict(actor)

    def close(self):
        for service in self.services.values():service.db.engine.dispose()


class ScopedService:
    """Existing endpoint closures resolve this proxy in their request context."""
    def __getattr__(self,name):
        registry,token,org=_current.get()
        service=registry.for_organization(org)
        value=getattr(service,name)
        if not callable(value):return value
        def invoke(*args,**kwargs):
            if name=='approve' and not registry.auth.config.firestore_project:
                # Serialize local approval with suspension/session revocation.
                # Cloud transactions read the same grants atomically above.
                with registry.auth.db.transaction() as db:
                    actor=registry.auth.actor(db,token)
                    if actor['org_id']!=org:raise HTTPException(403,'Institution membership changed. Reopen your workspace.')
                    return value(*args,**kwargs)
            actor=registry.authorize(token)
            if actor['org_id']!=org:raise HTTPException(403,'Institution membership changed. Reopen your workspace.')
            return value(*args,**kwargs)
        return invoke


class ScientificScope:
    def __init__(self,app,registry,cookie):self.app=app;self.registry=registry;self.cookie=cookie

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not is_scientific_path(scope['path']):
            return await self.app(scope,receive,send)
        from starlette.requests import Request
        request=Request(scope)
        token=request.cookies.get(self.cookie,'')
        claims=request.query_params.getlist('organization_id')+request.query_params.getlist('org_id')
        for name in ('x-organization-id','x-org-id'):
            if request.headers.get(name):claims.append(request.headers[name])
        try:
            actor=await run_in_threadpool(self.registry.authorize,token)
            if any(claim!=actor['org_id'] for claim in claims):
                raise HTTPException(403,'Access denied: this institution is outside your verified membership.')
        except HTTPException as exc:
            return await JSONResponse({'detail':exc.detail},status_code=exc.status_code)(scope,receive,send)
        context=_current.set((self.registry,token,actor['org_id']))
        try:await self.app(scope,receive,send)
        finally:_current.reset(context)
