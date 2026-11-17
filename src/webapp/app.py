from contextlib import asynccontextmanager
from datetime import date
import hashlib
import hmac
import ipaddress
import json
import mimetypes
from pathlib import Path
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt
from starlette.concurrency import run_in_threadpool

from .service import CandidateUnavailable, Conflict, Service
from .settings import Settings
from .calibration import prepare_image

STATIC=Path(__file__).parent/'static'
# Windows MIME registries may omit WebP; tutorial links must open as images.
mimetypes.add_type('image/webp','.webp')


class BodyLimitMiddleware:
    """Bound even chunked bodies before multipart spooling or JSON decoding."""
    def __init__(self,app,settings):self.app=app;self.settings=settings

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope['path'].startswith('/api/') or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        multipart=(scope['path'] in ('/api/uploads','/api/images/prepare','/api/calibrations/import','/api/calibrations/two-point') or
            scope['path'].startswith('/api/plants/') and scope['path'].endswith('/calibration/two-point'))
        limit=self.settings.max_upload_bytes+1024*1024 if multipart else self.settings.max_image_pixels*4+128*1024
        chunks=[];total=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            body=message.get('body',b'');total+=len(body)
            if total>limit:
                return await JSONResponse({'detail':'Request exceeds configured size limit'},status_code=413)(scope,receive,send)
            chunks.append(body)
            if not message.get('more_body',False):break
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if delivered:return await receive()
            delivered=True
            return {'type':'http.request','body':b''.join(chunks),'more_body':False}
        await self.app(scope,bounded_receive,send)


class CalibrationInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    calibration_id: str | None = None
    calibration_type: Literal['pixels_only','reviewed_scalar','reviewed_pot'] = 'pixels_only'
    cm_per_pixel: StrictFloat | StrictInt | None = None
    reference_size_px: list[StrictInt] | None = None
    calibration_provenance: str = ''
    calibration_reviewed: StrictBool = False


class LeafMetricsResponse(BaseModel):
    """Optional instance metrics preserve readability of historical records."""
    model_config=ConfigDict(extra='allow')
    leaf_count:int|None=None
    total_leaf_area:float|None=None
    mean_leaf_area:float|None=None
    median_leaf_area:float|None=None
    median_leaf_area_cm2:float|None=None
    median_leaf_pixel_area:float|None=None
    instance_metadata:dict|None=None
    calibration_reuse:dict|None=None
    effective_cm_per_pixel:float|None=None


class HistoryResponse(BaseModel):
    measurements:list[LeafMetricsResponse]
    growth:dict


class ProfileInput(CalibrationInput):
    experiment_id: str = Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9][A-Za-z0-9._ -]*$')
    plant_id: str = Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9][A-Za-z0-9._ -]*$')
    species: Literal['lettuce','rumex_acetosa']


class PlantIdentity(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=80,pattern=r'^[\p{L}\p{N}][\p{L}\p{N}._ -]*$')
    species:Literal['lettuce','rumex_acetosa']


class PlantCreate(PlantIdentity):
    calibration_choice:Literal['default','custom','none']='default'
    calibration_id:str|None=None
    request_id:UUID|None=None


class PlantCalibration(BaseModel):
    model_config=ConfigDict(extra='forbid')
    calibration_id:str


class CaptureDateInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    new_date:date=Field(alias='date')
    expected_date:date


def create_app(settings=None,*,service_override=None):
    settings=(settings or Settings.from_env()).validate()
    service=service_override if service_override is not None else Service(settings)
    @asynccontextmanager
    async def lifespan(app):
        yield
        service.db.engine.dispose()
    app=FastAPI(title='Hydroponic projected area',version='1.0.0',lifespan=lifespan,
                docs_url=None,redoc_url=None,openapi_url=None)
    app.state.service=service
    app.add_middleware(BodyLimitMiddleware,settings=settings)
    session=hmac.new((settings.auth_token or '').encode(),b'hydroponic-web-session',hashlib.sha256).hexdigest()

    @app.middleware('http')
    async def boundaries(request,call_next):
        api=request.url.path.startswith('/api/')
        if api:
            origin=request.headers.get('origin')
            expected=f'{request.url.scheme}://{request.url.netloc}'
            if origin and origin!=expected:return JSONResponse({'detail':'Same-origin request required'},status_code=403)
            if not settings.auth_token and request.url.hostname not in ('127.0.0.1','localhost','testserver'):
                return JSONResponse({'detail':'Configure private access before exposing this application'},status_code=403)
            if not settings.auth_token:
                try:local_peer=ipaddress.ip_address(request.client.host).is_loopback
                except (ValueError,AttributeError):local_peer=False
                if not local_peer:return JSONResponse({'detail':'Private access is required for remote clients'},status_code=403)
            if settings.auth_token and request.url.path not in ('/api/login','/api/session'):
                bearer=request.headers.get('authorization','')
                authorized=secrets.compare_digest(request.cookies.get('hydro_session',''),session) or secrets.compare_digest(bearer,'Bearer '+settings.auth_token)
                if not authorized:return JSONResponse({'detail':'Sign in to access this experiment'},status_code=401)
            length=request.headers.get('content-length')
            try:oversized=length is not None and int(length)>max(settings.max_upload_bytes+1024*1024,settings.max_image_pixels*2+8192)
            except ValueError:return JSONResponse({'detail':'Invalid content length'},status_code=400)
            if oversized:return JSONResponse({'detail':'Request exceeds configured size limit'},status_code=413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(Conflict)
    async def conflict(request,error):return JSONResponse({'detail':str(error)},status_code=409)
    @app.exception_handler(CandidateUnavailable)
    async def candidate_unavailable(request,error):return JSONResponse({'detail':str(error)},status_code=503)
    @app.exception_handler(ValueError)
    async def invalid(request,error):return JSONResponse({'detail':str(error)},status_code=400)
    @app.exception_handler(KeyError)
    async def missing(request,error):return JSONResponse({'detail':'Record or asset not found'},status_code=404)
    @app.exception_handler(OSError)
    async def storage_error(request,error):return JSONResponse({'detail':'Storage unavailable; no final measurement committed. Retry when storage is restored.'},status_code=503)

    async def bounded(file,limit):
        data=await file.read(limit+1)
        if len(data)>limit:raise HTTPException(413,'File exceeds configured size limit')
        return data

    @app.get('/health')
    def health():
        with service.db.engine.connect() as conn:conn.exec_driver_sql('SELECT 1')
        return dict(status='ok',version='1.0.0')

    @app.get('/')
    def index():return FileResponse(STATIC/'index.html')

    @app.get('/help')
    def user_guide():
        from fastapi.responses import HTMLResponse
        page=(STATIC/'user-guide.html').read_text(encoding='utf-8')
        return HTMLResponse(page.replace('/science-static/','/static/').replace('href="/analyze"','href="/"'))

    @app.get('/api/session')
    def session_state(request:Request):
        logged_in=not settings.auth_token or secrets.compare_digest(request.cookies.get('hydro_session',''),session)
        return dict(auth_required=bool(settings.auth_token),authenticated=logged_in)

    @app.post('/api/login')
    async def login(request:Request):
        payload=await request.json()
        if not settings.auth_token or not isinstance(payload,dict) or not secrets.compare_digest(str(payload.get('token','')),settings.auth_token):
            raise HTTPException(401,'Invalid access key')
        response=JSONResponse({'authenticated':True})
        response.set_cookie('hydro_session',session,httponly=True,secure=settings.cloud_run,samesite='strict',max_age=12*3600)
        return response

    @app.post('/api/logout')
    def logout():
        response=JSONResponse({'authenticated':False});response.delete_cookie('hydro_session');return response

    @app.get('/api/config')
    def config():
        saved=service.existing_calibration()
        return dict(max_upload_bytes=settings.max_upload_bytes,max_image_pixels=settings.max_image_pixels,
            saved_pot_available=bool(saved),saved_pot_label=saved['label'] if saved else None,
            saved_pot_diameter_cm=saved['pot_diameter_cm'] if saved else None)

    @app.get('/api/models')
    def models():
        if settings.cloud_run:raise HTTPException(404,'Research tooling is local only')
        return service.model_options()

    @app.get('/api/calibrations')
    def calibrations():return [r['payload'] for r in service.db.rows(service.db.calibrations)]

    @app.post('/api/calibrations/existing',status_code=201)
    def existing():
        saved=service.existing_calibration(import_it=True)
        if saved is None:raise HTTPException(404,'No saved reviewed pot calibration is bundled; import JSON and its reference image')
        return saved

    @app.post('/api/calibrations/import',status_code=201)
    async def import_reviewed(calibration_file:UploadFile=File(...),reference_image:UploadFile=File(...)):
        if not (calibration_file.filename or '').lower().endswith('.json'):raise ValueError('Calibration file must be JSON')
        raw=await bounded(calibration_file,65536);reference=await bounded(reference_image,settings.max_upload_bytes)
        return await run_in_threadpool(service.import_calibration,raw,reference,reference_image.filename,reference_image.content_type)

    @app.get('/api/profiles')
    def profiles():return service.profiles()

    @app.get('/api/setup')
    def setup():return service.default_setup()

    @app.post('/api/plants',status_code=201)
    def add_plant(payload:PlantCreate):return service.create_plant(payload.model_dump(mode='json'))

    @app.put('/api/plants/{id}')
    def edit_plant(id:str,payload:PlantIdentity):return service.edit_plant(id,payload.model_dump())

    @app.put('/api/plants/{id}/calibration')
    def plant_calibration(id:str,payload:PlantCalibration):return service.update_calibration(id,payload.model_dump())

    @app.post('/api/calibrations/two-point',status_code=201)
    async def two_point(diameter_cm:float=Form(...),points:str=Form(...),reviewed:bool=Form(False),image:UploadFile=File(...)):
        try:endpoints=json.loads(points)
        except ValueError:raise ValueError('Exactly two native outer-rim points are required')
        data=await bounded(image,settings.max_upload_bytes)
        return await run_in_threadpool(service.reviewed_pot,data,image.filename,image.content_type,diameter_cm,endpoints,reviewed)

    @app.post('/api/images/prepare')
    async def prepare(request:Request,image:UploadFile=File(...)):
        request_id=str(uuid4());data=b''
        diagnostic=dict(stage='prepare',request_id=request_id,revision=__import__('os').environ.get('K_REVISION','local'))
        try:
            data=await bounded(image,settings.max_upload_bytes)
            diagnostic['received_bytes']=len(data)
            prepared,mime,metadata=await run_in_threadpool(prepare_image,data,image.filename,image.content_type,settings)
        except (ValueError,HTTPException) as error:
            diagnostic.update(received_bytes=None if isinstance(error,HTTPException) and error.status_code==413 else len(data),pixel_limit=settings.max_image_pixels,
                decoder_error_type=type(error.__cause__).__name__ if error.__cause__ else type(error).__name__)
            safe=getattr(error,'diagnostic',{})
            for key in ('detected_format','width','height','frames'):
                if key in safe:diagnostic[key]=safe[key]
            code=getattr(error,'code','image.too_large' if isinstance(error,HTTPException) and error.status_code==413 else 'image.prepare_failed')
            detail=str(error) if getattr(error,'code',None) else ('Photo exceeds the configured upload size limit. Choose a smaller still photo.' if isinstance(error,HTTPException) and error.status_code==413 else 'The image decoder could not prepare this photo. Copy the error details, then select a still JPEG, PNG or HEIC image.')
            __import__('logging').getLogger('hydroponic.image_prepare').warning(json.dumps({'event':'photo_prepare_failed','code':code,**diagnostic},separators=(',',':')))
            return JSONResponse({'detail':detail,'code':code,'diagnostic':diagnostic},status_code=413 if isinstance(error,HTTPException) else 400,headers={'X-Photo-Request-ID':request_id})
        return Response(prepared,media_type=mime,headers={'X-Image-Provenance':json.dumps(metadata,separators=(',',':')),
            'X-Photo-Request-ID':request_id,'X-Image-Sha256':metadata['normalized_sha256'],'X-Image-Width':str(metadata['normalized_size_px'][0]),
            'X-Image-Height':str(metadata['normalized_size_px'][1])})

    @app.post('/api/plants/{id}/calibration/two-point',status_code=201)
    async def calibrate_photo(id:str,diameter_cm:float=Form(...),points:str=Form(...),reviewed:bool=Form(False),
                              image:UploadFile=File(...),analysis_id:str|None=Form(None),image_sha256:str|None=Form(None),
                              image_provenance:str|None=Form(None)):
        try:endpoints=json.loads(points)
        except ValueError:raise ValueError('Mark two opposite outer-rim points on this photo, then save.')
        data=await bounded(image,settings.max_upload_bytes)
        return await run_in_threadpool(service.calibrate_plant_photo,id,data,image.filename,image.content_type,
            diameter_cm,endpoints,reviewed,analysis_id,image_sha256,image_provenance)

    @app.post('/api/profiles',status_code=201)
    def create_profile(payload:ProfileInput):return service.create_profile(payload.model_dump())

    @app.put('/api/profiles/{id}/calibration')
    def update_calibration(id:str,payload:CalibrationInput):return service.update_calibration(id,payload.model_dump())

    @app.post('/api/uploads',status_code=201)
    async def upload(profile_id:str=Form(...),date:date=Form(...),geometry_unchanged:bool=Form(False),
                     source_research_permission:bool=Form(False),image:UploadFile=File(...),image_provenance:str|None=Form(None),
                     request_id:UUID|None=Form(None)):
        data=await bounded(image,settings.max_upload_bytes)
        permission=settings.closed_academic_research or source_research_permission
        basis='closed_academic_research_policy' if settings.closed_academic_research else 'source_research_permission'
        return await run_in_threadpool(service.upload,profile_id,date,geometry_unchanged,data,image.filename,image.content_type,permission,basis,image_provenance,
            str(request_id) if request_id else None)

    @app.post('/api/analyses/{id}/analyze')
    def analyze(id:str,engine:str='auto'):
        if settings.cloud_run and engine!='auto':raise HTTPException(400,'Production uses the promoted model automatically')
        return service.analyze(id,engine)

    @app.get('/api/analyses/{id}',response_model=LeafMetricsResponse)
    def analysis(id:str):return service.analysis(id)

    @app.get('/api/profiles/{id}/analyses')
    def pending(id:str,active_id:str|None=None):return service.pending_review(id,active_id)

    @app.post('/api/analyses/{id}/draft')
    async def draft(id:str,request:Request):return await run_in_threadpool(service.draft,id,await request.json())

    @app.post('/api/analyses/{id}/capture-date',response_model=LeafMetricsResponse)
    async def capture_date(id:str,payload:CaptureDateInput):
        return await run_in_threadpool(service.capture_date,id,payload.new_date,payload.expected_date)

    @app.post('/api/analyses/{id}/split-leaf')
    async def split_leaf(id:str,request:Request):return await run_in_threadpool(service.split_leaf,id,await request.json())

    @app.post('/api/analyses/{id}/confirm-calibration')
    async def confirm_calibration(id:str,request:Request):return await run_in_threadpool(service.confirm_calibration,id,await request.json())

    @app.post('/api/analyses/{id}/calibration')
    async def recalibrate_review(id:str,request:Request):return await run_in_threadpool(service.recalibrate_review,id,await request.json())

    @app.post('/api/analyses/{id}/geometry-changed')
    def mark_geometry_changed(id:str):return service.mark_geometry_changed(id)

    @app.post('/api/analyses/{id}/approve',status_code=201,response_model=LeafMetricsResponse)
    async def approve(id:str,request:Request):return await run_in_threadpool(service.approve,id,await request.json())

    @app.get('/api/measurements/{id}',response_model=LeafMetricsResponse)
    def measurement(id:str):return service.measurement(id)

    @app.get('/api/profiles/{id}/history',response_model=HistoryResponse)
    def history(id:str):return service.history(id)

    @app.get('/api/profiles/{id}/export.csv')
    def export(id:str):return Response(service.export(id),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="projected-area.csv"'})

    @app.get('/api/assets/{kind}/{id}/{name}')
    def asset(kind:str,id:str,name:str):
        data,content_type=service.asset(kind,id,name);return Response(data,media_type=content_type)

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app
