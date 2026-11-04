from dataclasses import dataclass
import os
from pathlib import Path

from ..hydroponic.config import ROOT


@dataclass
class Settings:
    data_dir: Path = ROOT/'data/webapp'
    database_url: str | None = None
    gcs_bucket: str | None = None
    auth_token: str | None = None
    cloud_run: bool = False
    max_upload_bytes: int = 10*1024*1024
    max_image_pixels: int = 8_000_000
    # Closed professor research captures are authorized for later batch training.
    # Isolated demo/test instances turn this off; never change historical records.
    closed_academic_research: bool = True
    firestore_project: str | None = None
    firestore_database: str = '(default)'
    firestore_prefix: str = 'hydroponic'
    # Institutional deployments use independent metadata and object namespaces.
    # Legacy records remain in their original namespace pending explicit mapping.
    organization_id: str | None = None

    @classmethod
    def from_env(cls):
        return cls(data_dir=Path(os.getenv('HYDRO_DATA_DIR',str(ROOT/'data/webapp'))),
            database_url=os.getenv('HYDRO_DATABASE_URL'),gcs_bucket=os.getenv('HYDRO_GCS_BUCKET'),
            auth_token=os.getenv('HYDRO_AUTH_TOKEN'),cloud_run=bool(os.getenv('K_SERVICE')),
            max_upload_bytes=int(os.getenv('HYDRO_MAX_UPLOAD_MB','10'))*1024*1024,
            max_image_pixels=int(os.getenv('HYDRO_MAX_IMAGE_PIXELS','8000000')),
            closed_academic_research=os.getenv('HYDRO_CLOSED_ACADEMIC_RESEARCH','1').lower() in ('1','true'),
            firestore_project=os.getenv('HYDRO_FIRESTORE_PROJECT'),firestore_database=os.getenv('HYDRO_FIRESTORE_DATABASE','(default)'),
            firestore_prefix=os.getenv('HYDRO_FIRESTORE_PREFIX','hydroponic'))

    def validate(self):
        self.data_dir=Path(self.data_dir).resolve()
        if ROOT not in self.data_dir.parents:
            raise ValueError('HYDRO_DATA_DIR must be below the project root')
        if self.max_upload_bytes<1 or not 1<=self.max_image_pixels<=20_000_000:
            raise ValueError('Invalid upload/image limits')
        if self.auth_token is not None and len(self.auth_token)<32:
            raise ValueError('HYDRO_AUTH_TOKEN must have at least 32 characters')
        if self.firestore_project and self.firestore_project!='hydroponic-leaf-research-aryae':raise ValueError('Firestore must use the dedicated Hydroponic project')
        import re
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',self.firestore_prefix):raise ValueError('Invalid Firestore collection prefix')
        if self.firestore_project and self.database_url:raise ValueError('Choose one metadata backend')
        if self.cloud_run and (not (self.firestore_project or(self.database_url and self.database_url.startswith('postgresql+psycopg://'))) or not self.gcs_bucket or not self.auth_token):
            raise ValueError('Cloud Run requires durable metadata, GCS media and HYDRO_AUTH_TOKEN; ephemeral SQLite is refused')
        if self.cloud_run and os.getenv('FIRESTORE_EMULATOR_HOST'):raise ValueError('Cloud Run cannot use a local Firestore emulator')
        self.data_dir.mkdir(parents=True,exist_ok=True)
        return self
