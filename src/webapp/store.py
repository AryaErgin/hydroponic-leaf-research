import hashlib
import os
from pathlib import Path, PurePosixPath

from sqlalchemy import (Column, Date, ForeignKey, JSON, MetaData, String, Table,
                        UniqueConstraint, create_engine, event, select)


class Database:
    def __init__(self,settings):
        url=settings.database_url or 'sqlite:///'+(settings.data_dir/'hydroponic.sqlite3').as_posix()
        options={'connect_args':{'check_same_thread':False,'timeout':30}} if url.startswith('sqlite:') else {'pool_size':2,'max_overflow':2}
        if settings.firestore_project:
            from .firestore_store import FirestoreEngine
            self.engine=FirestoreEngine(settings)
        else:self.engine=create_engine(url,pool_pre_ping=True,**options)
        if not settings.firestore_project and url.startswith('sqlite:'):
            @event.listens_for(self.engine,'connect')
            def setup(connection,record):
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA journal_mode=WAL')
        schema=MetaData()
        self.calibrations=Table('calibrations',schema,Column('id',String(64),primary_key=True),Column('payload',JSON,nullable=False))
        self.profiles=Table('profiles',schema,Column('id',String(36),primary_key=True),
            Column('experiment_id',String(80),nullable=False),Column('plant_id',String(80),nullable=False),
            Column('species',String(32),nullable=False),Column('calibration_id',String(64),ForeignKey('calibrations.id')),
            Column('payload',JSON,nullable=False),UniqueConstraint('experiment_id','plant_id'))
        self.analyses=Table('analyses',schema,Column('id',String(36),primary_key=True),
            Column('profile_id',String(36),ForeignKey('profiles.id'),nullable=False),Column('date',Date,nullable=False),
            Column('status',String(20),nullable=False),Column('measurement_id',String(36)),Column('payload',JSON,nullable=False))
        # One resumable workflow per plant; analysis provenance is separate.
        self.review_sessions=Table('review_sessions',schema,Column('id',String(36),primary_key=True),
            Column('profile_id',String(36),ForeignKey('profiles.id'),nullable=False),
            Column('analysis_id',String(36),ForeignKey('analyses.id'),nullable=False),Column('payload',JSON,nullable=False))
        self.measurements=Table('measurements',schema,Column('id',String(36),primary_key=True),
            Column('analysis_id',String(36),ForeignKey('analyses.id'),unique=True,nullable=False),
            Column('profile_id',String(36),ForeignKey('profiles.id'),nullable=False),Column('date',Date,nullable=False),
            Column('payload',JSON,nullable=False),UniqueConstraint('profile_id','date'))
        self.approved_masks=Table('approved_masks',schema,Column('id',String(36),primary_key=True),
            Column('measurement_id',String(36),ForeignKey('measurements.id'),unique=True,nullable=False),
            Column('profile_id',String(36),ForeignKey('profiles.id'),nullable=False),Column('payload',JSON,nullable=False))
        # No professor API: owner IAM/local tooling reads this durable inbox.
        self.owner_notifications=Table('owner_notifications',schema,Column('id',String(64),primary_key=True),Column('payload',JSON,nullable=False))
        self.owner_training_snapshots=Table('owner_training_snapshots',schema,Column('id',String(32),primary_key=True),Column('payload',JSON,nullable=False))
        self.approved_sorrel_masks=Table('approved_sorrel_masks',schema,Column('id',String(36),primary_key=True),
            Column('measurement_id',String(36),ForeignKey('measurements.id'),unique=True,nullable=False),
            Column('profile_id',String(36),ForeignKey('profiles.id'),nullable=False),Column('payload',JSON,nullable=False))
        if settings.firestore_project:self.engine.tables=schema.tables
        else:schema.create_all(self.engine)

    def one(self,table,id,connection=None):
        if connection is None:
            with self.engine.connect() as conn:return self.one(table,id,conn)
        row=connection.execute(select(table).where(table.c.id==id)).mappings().first()
        if row is None:raise KeyError('Record not found')
        return dict(row)

    def rows(self,table,condition=None):
        statement=select(table)
        if condition is not None:statement=statement.where(condition)
        with self.engine.connect() as conn:return [dict(r) for r in conn.execute(statement).mappings()]


class MediaStore:
    def __init__(self,settings):
        self.root=settings.data_dir/'media'
        self.root.mkdir(exist_ok=True)
        self.bucket=None
        self.prefix='hydroponic/'
        if settings.organization_id:
            self.prefix+='organizations/'+settings.firestore_prefix+'/'
        if settings.gcs_bucket:
            from google.cloud import storage
            self.bucket=storage.Client(project=settings.firestore_project).bucket(settings.gcs_bucket)

    def _path(self,key):
        parsed=PurePosixPath(key)
        if parsed.is_absolute() or '..' in parsed.parts or not parsed.parts:
            raise ValueError('Invalid media key')
        path=self.root.joinpath(*parsed.parts)
        # Institution isolation adds a directory level. Preserve full IDs without
        # hitting legacy Win32 MAX_PATH during local calibration/artifact writes.
        if os.name=='nt' and not str(path).startswith('\\\\?\\'):
            path=Path('\\\\?\\'+str(path.resolve()))
        return path

    def put(self,key,data,content_type='application/octet-stream'):
        path=self._path(key)
        digest=hashlib.sha256(data).hexdigest()
        if self.bucket:
            blob=self.bucket.blob(self.prefix+key)
            blob.metadata={'sha256':digest}
            blob.upload_from_string(data,content_type=content_type,if_generation_match=0,timeout=60)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        return dict(key=key,sha256=digest,content_type=content_type,size_bytes=len(data))

    def read(self,reference):
        key=reference['key'];path=self._path(key)
        data=self.bucket.blob(self.prefix+key).download_as_bytes(timeout=60) if self.bucket else path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=reference['sha256']:
            raise ValueError('Stored media hash mismatch; content changed')
        return data
