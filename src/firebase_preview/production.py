"""Future explicit Cloud Run factory. Not selected by existing deployments.

No user provisioning, data migration, service activation or credential fallback.
Production rollout remains blocked until the incident and release gates close.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import firebase_admin
from firebase_admin import credentials

from .identity import Gateway
from .modes import ProductionFirebaseConfig,LIVE_PROJECT


class ProductionGateway(Gateway):
    def __init__(self,config):
        if not isinstance(config,ProductionFirebaseConfig):raise ValueError('Explicit production Firebase configuration required')
        self.config=config;self.guard()
        self.app=firebase_admin.initialize_app(credentials.ApplicationDefault(),
            {'projectId':LIVE_PROJECT,'httpTimeout':15},name='durable-'+uuid4().hex)

    def guard(self):self.config.guard()


def build():
    from .app import create_app
    from ..webapp.settings import Settings
    if not os.getenv('K_SERVICE'):raise ValueError('This entry point requires an explicitly configured Cloud Run runtime')
    settings=Settings.from_env()
    # There is no default namespace: existing scientific data cannot be silently
    # adopted by an authentication-only revision.
    science_prefix=os.environ['HYDRO_FIRESTORE_PREFIX']
    if not science_prefix.startswith('hydroponic_firebase_science_'):
        raise ValueError('Use a separately approved Firebase scientific namespace')
    base=settings.data_dir
    web=json.loads(Path(os.environ['HYDRO_FIREBASE_WEB_CONFIG_FILE']).read_text(encoding='utf-8'))
    config=ProductionFirebaseConfig(base/'firebase-auth',os.environ['HYDRO_APP_ORIGIN'],
        integrated=True,require_verified_email=True,require_durable_auth=True,
        firestore_project=settings.firestore_project,firestore_database=settings.firestore_database,
        firestore_prefix=os.environ['HYDRO_AUTH_FIRESTORE_PREFIX'],web_config=web)
    settings=replace(settings,data_dir=base/'science')
    return create_app(config,ProductionGateway(config),settings.data_dir,scientific_settings=settings)
