"""One immutable manifest binds identity, metadata, artifacts and origin."""
from dataclasses import dataclass, field, fields
import json
import os
from pathlib import Path
import re
from types import MappingProxyType

from ..auth_preview.config import Config
from ..hydroponic.config import ROOT

PROTECTED_PROJECT='hydroponic-leaf-research-aryae'
PROTECTED_NUMBER='242618885776'


@dataclass(frozen=True)
class CloudInstanceConfig:
    project_id: str
    project_number: str
    installation_id: str
    region: str
    service_name: str
    firestore_database: str
    bucket: str
    auth_prefix: str
    science_prefix: str
    origin: str
    firebase_web: dict=field(repr=False)
    institution_name: str
    schema_version: int = 1

    @classmethod
    def from_mapping(cls,data):
        if not isinstance(data,dict):raise ValueError('Cloud instance manifest must be an object')
        try:config=cls(**data)
        except TypeError:raise ValueError('Cloud instance manifest has missing or unsupported fields') from None
        config.validate()
        object.__setattr__(config,'firebase_web',MappingProxyType(dict(config.firebase_web)))
        return config

    @classmethod
    def load(cls,path):
        return cls.from_mapping(json.loads(Path(path).read_text(encoding='utf-8')))

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('Unsupported cloud manifest schema')
        if (not isinstance(self.project_id,str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]',self.project_id)
            or self.project_id==PROTECTED_PROJECT or self.project_id.startswith('demo-')
            or not isinstance(self.project_number,str) or not re.fullmatch(r'[1-9][0-9]{5,19}',self.project_number)
            or self.project_number==PROTECTED_NUMBER):raise ValueError('Use an explicitly authorized independent Google Cloud project')
        if not isinstance(self.installation_id,str) or not re.fullmatch(r'[0-9a-f]{32}',self.installation_id):raise ValueError('Invalid installation identity')
        base='hlr_'+self.installation_id[:16]
        if (self.region!='us-central1' or self.service_name!='hydroponic-leaf-research' or
            self.firestore_database!='(default)' or self.bucket!=self.project_id+'-hlr-science' or
            self.auth_prefix!=base+'_auth' or self.science_prefix!=base+'_science'):
            raise ValueError('Independent resources must match the installation manifest')
        expected_origin=f'https://{self.service_name}-{self.project_number}.{self.region}.run.app'
        if self.origin!=expected_origin:raise ValueError('Use this independent service exact HTTPS Cloud Run origin')
        if not isinstance(self.institution_name,str) or not 2<=len(self.institution_name.strip())<=120:raise ValueError('An initial institution name is required')
        web=self.firebase_web
        if (not isinstance(web,(dict,MappingProxyType)) or set(web)!={'apiKey','appId','authDomain','projectId'} or
            web['projectId']!=self.project_id or web['authDomain']!=self.project_id+'.firebaseapp.com' or
            not isinstance(web['appId'],str) or not re.fullmatch(r'1:'+self.project_number+r':web:[A-Za-z0-9]+',web['appId']) or
            not isinstance(web['apiKey'],str) or not web['apiKey'] or len(web['apiKey'])>200):
            raise ValueError('Firebase web identity must belong to the same independent project')
        # Provisioning and runtime share the exact manifest contract. Runtime
        # additionally requires completed Firebase web registration, whereas a
        # pure dry-run plan may still have firebase_web=None.
        from ..cloud_setup.plan import validate_manifest
        manifest={item.name:getattr(self,item.name) for item in fields(self)}
        manifest['firebase_web']=dict(web)
        validate_manifest(manifest,require_runtime=True)
        return self

    def guard(self):
        self.validate()
        if any(os.getenv(key) for key in ('FIREBASE_AUTH_EMULATOR_HOST','FIRESTORE_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST','STORAGE_EMULATOR_HOST','GOOGLE_APPLICATION_CREDENTIALS')):
            raise ValueError('Independent cloud cannot use emulators or local credential files')
        expected={'GOOGLE_CLOUD_PROJECT':self.project_id,'GCLOUD_PROJECT':self.project_id,
            'GOOGLE_CLOUD_QUOTA_PROJECT':self.project_id,'HYDRO_FIRESTORE_PROJECT':self.project_id,
            'HYDRO_FIRESTORE_DATABASE':self.firestore_database,'HYDRO_GCS_BUCKET':self.bucket,
            'HYDRO_FIRESTORE_PREFIX':self.science_prefix,'HYDRO_AUTH_FIRESTORE_PREFIX':self.auth_prefix,
            'HYDRO_APP_ORIGIN':self.origin,'K_SERVICE':self.service_name}
        if any(os.getenv(key) and os.environ[key]!=value for key,value in expected.items()):
            raise ValueError('Environment conflicts with independent cloud instance manifest')
        if os.getenv('HYDRO_DATABASE_URL') or os.getenv('HYDRO_LOCAL_MODE'):
            raise ValueError('Independent cloud requires its durable Firestore and GCS backends')

    @property
    def runtime_dir(self):return ROOT/'.runtime/cloud'/self.installation_id

    @property
    def organization_id(self):return 'installation-'+self.installation_id

    def auth_config(self):
        self.guard()
        return IndependentFirebaseConfig(self.runtime_dir/'firebase-auth',self.origin,integrated=True,
            require_verified_email=True,require_durable_auth=True,firestore_project=self.project_id,
            firestore_database=self.firestore_database,firestore_prefix=self.auth_prefix,instance=self)

    def scientific_settings(self,secret):
        from ..webapp.settings import Settings
        self.guard()
        return Settings(data_dir=self.runtime_dir/'science',firestore_project=self.project_id,
            firestore_database=self.firestore_database,firestore_prefix=self.science_prefix,
            gcs_bucket=self.bucket,auth_token=secret,cloud_run=True,closed_academic_research=False,
            inference_device='cpu',cloud_instance=self)

    def validate_settings(self,settings):
        import hashlib
        self.guard()
        prefix=self.science_prefix
        expected_path=(self.runtime_dir/'science').resolve()
        if settings.organization_id:
            digest=hashlib.sha256((self.science_prefix+':'+settings.organization_id).encode()).hexdigest()
            prefix='tenant_'+digest;expected_path=expected_path/'organizations'/digest
        if (settings.firestore_project!=self.project_id or settings.firestore_database!=self.firestore_database or
            settings.firestore_prefix!=prefix or settings.gcs_bucket!=self.bucket or
            settings.data_dir.resolve()!=expected_path or settings.database_url or not settings.cloud_run or
            not settings.auth_token or len(settings.auth_token)<32 or settings.inference_device!='cpu'):
            raise ValueError('Independent scientific storage must match its durable instance manifest')


@dataclass(frozen=True)
class IndependentFirebaseConfig(Config):
    instance: CloudInstanceConfig=field(default=None,repr=False)

    def guard(self):
        if not isinstance(self.instance,CloudInstanceConfig):raise ValueError('Independent instance manifest is required')
        self.instance.guard()

    def __post_init__(self):
        self.guard();instance=self.instance
        if (self.development or not self.integrated or not self.require_verified_email or not self.require_durable_auth or
            self.firestore_project!=instance.project_id or self.firestore_database!=instance.firestore_database or
            self.firestore_prefix!=instance.auth_prefix or self.origin!=instance.origin or
            self.data_dir.resolve()!=(instance.runtime_dir/'firebase-auth').resolve() or
            self.smtp or self.email_hmac_key or self.phone_review_owner_id or not 300<=self.session_seconds<=14*86400):
            raise ValueError('Independent Firebase configuration must match its manifest')
        super().__post_init__()

    def public_config(self):
        self.guard()
        return dict(self.instance.firebase_web,local_only=False,identity_mode='independent_cloud',origin=self.origin)

    @property
    def cookie(self):return '__Host-hlr_'+self.instance.installation_id[:16]


RuntimeConfig=CloudInstanceConfig
