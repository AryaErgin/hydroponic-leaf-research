"""Pure, deterministic plan for a dedicated user-owned Google Cloud project.

This module neither imports a Google SDK nor reads credentials or environment
configuration. All resource names are derived, never accepted as free targets.
"""
from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit

PROTECTED_PROJECT = 'hydroponic-leaf-research-aryae'
PROTECTED_PROJECT_NUMBER = '242618885776'
SERVICE_NAME = 'hydroponic-leaf-research'
LABEL_KEY = 'hlr-installation'
REQUIRED_APIS = (
    'serviceusage.googleapis.com', 'cloudresourcemanager.googleapis.com',
    'iam.googleapis.com', 'run.googleapis.com', 'cloudbuild.googleapis.com',
    'artifactregistry.googleapis.com', 'firestore.googleapis.com',
    'storage.googleapis.com', 'secretmanager.googleapis.com',
    'logging.googleapis.com', 'firebase.googleapis.com',
    'apikeys.googleapis.com',
    'identitytoolkit.googleapis.com', 'securetoken.googleapis.com',
    'firebaserules.googleapis.com',
    'cloudbilling.googleapis.com','billingbudgets.googleapis.com',
)


def validate_project_id(project_id):
    if (not isinstance(project_id, str) or
        not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project_id) or
        project_id == PROTECTED_PROJECT or project_id.startswith(('demo-', 'goog'))):
        raise ValueError('Use your own dedicated real Google Cloud project ID; protected/demo targets are forbidden.')
    return project_id


def make_manifest(project_id, project_number, installation_id, institution_name, *, region='us-central1'):
    validate_project_id(project_id)
    if (not isinstance(project_number, str) or not re.fullmatch(r'[1-9][0-9]{5,19}', project_number) or
        project_number == PROTECTED_PROJECT_NUMBER):
        raise ValueError('A verified numeric Google Cloud project number is required.')
    if not isinstance(installation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', installation_id):
        raise ValueError('Installation ID must be a persisted lowercase UUID hex value.')
    if region != 'us-central1':
        raise ValueError('This reviewed installation supports the us-central1 region only.')
    if (not isinstance(institution_name, str) or not 2 <= len(institution_name.strip()) <= 120 or
        any(ord(c) < 32 for c in institution_name)):
        raise ValueError('An institution name of 2 to 120 characters is required.')
    return dict(schema_version=1, project_id=project_id, project_number=project_number,
        installation_id=installation_id, region=region, service_name=SERVICE_NAME,
        firestore_database='(default)', bucket=project_id+'-hlr-science',
        auth_prefix='hlr_'+installation_id[:16]+'_auth',
        science_prefix='hlr_'+installation_id[:16]+'_science',
        origin=f'https://{SERVICE_NAME}-{project_number}.{region}.run.app',
        firebase_web=None, institution_name=institution_name.strip())


def validate_manifest(manifest, *, require_runtime=False):
    if not isinstance(manifest, dict): raise ValueError('Installation manifest must be an object.')
    try:
        expected = make_manifest(manifest['project_id'], manifest['project_number'],
            manifest['installation_id'], manifest['institution_name'], region=manifest['region'])
    except (KeyError, TypeError) as error:
        raise ValueError('Incomplete installation manifest.') from error
    if set(manifest) != set(expected): raise ValueError('Unexpected or missing installation manifest fields.')
    for key, value in expected.items():
        if key != 'firebase_web' and (type(manifest[key]) is not type(value) or manifest[key] != value):
            raise ValueError('Manifest contains a conflicting derived resource: '+key)
    web = manifest['firebase_web']
    if web is None:
        if require_runtime: raise ValueError('Register and verify this project Firebase web application first.')
    else:
        if not isinstance(web, dict) or set(web) != {'apiKey','appId','authDomain','projectId'}:
            raise ValueError('Only the four public Firebase web configuration fields are allowed.')
        if (web['projectId'] != manifest['project_id'] or
            web['authDomain'] != manifest['project_id']+'.firebaseapp.com' or
            not isinstance(web['apiKey'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', web['apiKey']) or
            not isinstance(web['appId'],str) or
            not re.fullmatch(r'1:'+re.escape(manifest['project_number'])+r':web:[A-Za-z0-9]+', web['appId'])):
            raise ValueError('Firebase web configuration belongs to a different or invalid project.')
    if len(urlsplit(manifest['origin']).hostname.split('.')[0]) > 63:
        raise ValueError('Cloud Run deterministic hostname exceeds the DNS segment limit.')
    return copy.deepcopy(manifest)


def build_plan(manifest):
    manifest = validate_manifest(manifest)
    project = manifest['project_id']; region = manifest['region']
    short = manifest['installation_id'][:16]
    runtime = f'hlr-runtime-{short}@{project}.iam.gserviceaccount.com'
    build = f'hlr-build-{short}@{project}.iam.gserviceaccount.com'
    repository = 'hlr-images-'+short
    secret = 'hlr-runtime-'+short
    config_secret = 'hlr-config-'+short
    return dict(schema_version=1, manifest=manifest,
        labels={LABEL_KEY:manifest['installation_id']}, apis=list(REQUIRED_APIS),
        buckets=[dict(name=manifest['bucket'],purpose='scientific artifacts',location=region,
                     uniform_bucket_level_access=True,public_access_prevention='enforced'),
                 dict(name=project+'-hlr-build',purpose='private build source staging',location=region,
                     uniform_bucket_level_access=True,public_access_prevention='enforced')],
        firestore=dict(database='(default)',location=region,type='FIRESTORE_NATIVE',delete_protection=True,
                       browser_rules='deny all'),
        repository=dict(name=repository,location=region,format='DOCKER',
                        image=f'{region}-docker.pkg.dev/{project}/{repository}/application'),
        secrets=[secret,config_secret],
        iam=dict(runtime=dict(service_account=runtime,project_roles=['roles/datastore.user'],
                              auth_role='hlrAuth'+short,
                              auth_permissions=['firebaseauth.users.createSession','firebaseauth.users.get'],
                              bucket=manifest['bucket'],bucket_role='roles/storage.objectUser',
                              secrets=[secret,config_secret],secret_role='roles/secretmanager.secretAccessor'),
                 build=dict(service_account=build,project_roles=['roles/logging.logWriter'],
                            repository_role='roles/artifactregistry.writer',
                            bucket=project+'-hlr-build',bucket_role='roles/storage.objectViewer')),
        build=dict(timeout_seconds=1200,queue_ttl_seconds=300,logging='CLOUD_LOGGING_ONLY',
                   staging_bucket=project+'-hlr-build'),
        run=dict(service=SERVICE_NAME,region=region,cpu=2,memory='4Gi',min_instances=0,max_instances=1,
                 concurrency=1,timeout_seconds=360,request_based_cpu=True,public_https=True,
                 startup_cpu_boost=False,execution_environment='gen2'),
        costs=dict(budget_alert_is_not_a_cap=True,max_instances_is_not_a_billing_cap=True,
                   paid_services=['Cloud Run','Cloud Build','Artifact Registry','Firestore','Cloud Storage',
                                  'Secret Manager','Cloud Logging'],
                   firebase_auth='Standard email/password; no automatic Identity Platform upgrade',
                   exclusions='No spending guarantee; storage, builds, logs, egress and retained revisions can incur charges.'))
