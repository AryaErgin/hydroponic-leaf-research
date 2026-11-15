"""Official Firebase verifier tied to one independently owned project."""
from uuid import uuid4
import firebase_admin
from firebase_admin import auth,credentials
from ..firebase_preview.identity import Gateway
from .config import IndependentFirebaseConfig


def require_runtime_adc(config):
    """Only Cloud Run metadata credentials, never a cached developer credential."""
    from google.auth.compute_engine import Credentials as MetadataCredentials
    from google.auth.transport.requests import Request
    config.guard()
    adc=credentials.ApplicationDefault();credential=adc.get_credential()
    if not isinstance(credential,MetadataCredentials) or adc.project_id!=config.firestore_project:
        raise ValueError('Independent runtime requires same-project Cloud Run metadata credentials')
    credential.refresh(Request())
    if not credential.service_account_email.endswith('@'+config.firestore_project+'.iam.gserviceaccount.com'):
        raise ValueError('Independent runtime requires its own project service account')
    return adc


class IndependentGateway(Gateway):
    def __init__(self,config):
        if not isinstance(config,IndependentFirebaseConfig):raise ValueError('Explicit independent Firebase configuration required')
        self.config=config;self.guard()
        self.app=firebase_admin.initialize_app(require_runtime_adc(config),
            {'projectId':config.firestore_project,'httpTimeout':15},name='independent-'+uuid4().hex)

    def guard(self):self.config.guard()

    def lookup_owner(self,uid,email):
        self.guard()
        user=auth.get_user(uid,app=self.app)
        return dict(uid=user.uid,email=user.email,email_verified=user.email_verified,disabled=user.disabled)
