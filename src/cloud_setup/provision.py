"""Own-project provisioning through an injected, secret-safe CloudClient.

Importing this module does nothing. The caller owns consent and authentication.
No subprocess, active gcloud configuration, keys, production resource names or
credential files are used here. State contains identifiers, never secret bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode, urlsplit

from .plan import LABEL_KEY, build_plan, validate_manifest, validate_project_id

AUTH_FIELDS = 'authorizedDomains,signIn.email,emailPrivacyConfig,subtype'
DENY_RULES = "rules_version = '2';\nservice cloud.firestore {\n  match /databases/{database}/documents {\n    match /{document=**} { allow read, write: if false; }\n  }\n}\n"


class ManualStepRequired(ValueError):
    """An intentional personal-console consent/setup step remains."""


class BuildRetryRequired(ValueError):
    """A verified failed own-project job needs separate approval for one retry."""
    def __init__(self,build_id,status):
        self.build_id=build_id;self.status=status
        super().__init__('Recorded Cloud Build '+build_id+' finished with '+status+'. Another build can incur charges; explicit approval is required for one retry of the same verified source receipt.')


class Provisioner:
    def __init__(self, client, manifest, *, state=None, save_state=None):
        self.manifest = validate_manifest(manifest)
        self.plan = build_plan(self.manifest)
        self.client = client
        self.state = state if state is not None else {}
        self.save_state = save_state or (lambda: None)
        self.state.setdefault('intents', [])
        self.state.setdefault('completed', [])
        self._infrastructure_ready = False
        self._firebase_operations = set()
        self._guard()

    @property
    def project(self): return self.manifest['project_id']

    def _guard(self):
        validate_manifest(self.manifest)
        validate_project_id(self.project)
        if getattr(self.client, 'project_id', None) != self.project:
            raise ValueError('Cloud client and installation target disagree; no operation was attempted.')

    def _g(self, args, *, json_output=True, input_bytes=None):
        self._guard()
        if any(arg.startswith(('--project', '--billing-project', '--impersonate-service-account', '--configuration')) for arg in args):
            raise ValueError('Resource commands cannot override the explicitly selected project or identity.')
        return self.client.gcloud([*args, '--project='+self.project, '--quiet'],
            json_output=json_output, input_bytes=input_bytes)

    def _api(self, method, service, resource, body=None, *, query=None):
        self._guard()
        if (not isinstance(resource,str) or any(c in resource for c in ('?','#','\\','%')) or
            any(part in ('','.','..') for part in resource.split('/'))):
            raise ValueError('Cloud API resource path is not canonical.')
        if service not in ('firebase','identitytoolkit','firebaserules'):
            raise ValueError('Unsupported provisioning API.')
        prefix = 'admin/v2' if service == 'identitytoolkit' else ('v1' if service == 'firebaserules' else 'v1beta1')
        target = 'projects/'+self.project
        if not (resource == target or resource.startswith(target+'/') or resource.startswith(target+':')) and not (
            service == 'firebase' and method == 'GET' and resource in self._firebase_operations):
            raise ValueError('Cloud API target is outside the selected project.')
        url = f'https://{service}.googleapis.com/{prefix}/{resource}'
        if query: url += '?' + urlencode(query)
        return self.client.api(method, url, body=body)

    def _intent(self, name):
        if name not in self.state['intents']:
            self.state['intents'].append(name); self.save_state()

    def _done(self, name):
        if name not in self.state['completed']:
            self.state['completed'].append(name); self.save_state()

    def _create(self, name, args):
        self._intent(name)
        result = self._g(args)
        self._done(name)
        return result

    def preflight(self):
        """Read only: verify immutable ID/number and refuse another installation."""
        resource = self._g(['projects','describe',self.project])
        if (not isinstance(resource,dict) or resource.get('projectId') != self.project or
            str(resource.get('projectNumber')) != self.manifest['project_number'] or
            resource.get('lifecycleState') != 'ACTIVE'):
            raise ValueError('Selected project identity/number/state could not be verified.')
        owner = resource.get('labels',{}).get(LABEL_KEY)
        if owner is not None and owner != self.manifest['installation_id']:
            raise ValueError('Project is marked for a different installation; refusing adoption.')
        return resource

    def _firebase_exists(self):
        try:
            response = self._api('GET','firebase','projects/'+self.project,
                query={'fields':'projectId,projectNumber'})
        except Exception as error:
            if getattr(error,'status_code',None) == 404: return False
            raise
        if (response.get('projectId') != self.project or
            str(response.get('projectNumber')) != self.manifest['project_number']):
            raise ValueError('Firebase project identity did not match the selected project.')
        return True

    def _list(self, args):
        result = self._g(args)
        if not isinstance(result,list): raise ValueError('Cloud inventory did not return a resource list.')
        return result

    def _inventory(self, project_resource):
        """Inventory before resource creation; unknown resources always stop setup.

        A dedicated project may contain Google-created default service accounts.
        No existing custom resources are adopted merely because names match.
        Every resumed object needs the project marker, its own marker (where
        supported), and a local persisted create intent from this installation.
        """
        owned_project = project_resource.get('labels',{}).get(LABEL_KEY) == self.manifest['installation_id']
        label = self.plan['labels']; intents = set(self.state['intents'])
        inventories = {
            'buckets': self._list(['storage','buckets','list','--raw']),
            'database': self._list(['firestore','databases','list']),
            'repository': self._list(['artifacts','repositories','list','--location=all']),
            'service': self._list(['run','services','list']),
            'accounts': self._list(['iam','service-accounts','list']),
            'roles': self._list(['iam','roles','list']),
            'secrets': self._list(['secrets','list']),
        }
        expected_buckets = {b['name'] for b in self.plan['buckets']}
        for item in inventories['buckets']:
            name = item.get('name','').removeprefix('gs://').rstrip('/')
            iam = item.get('iamConfiguration', item.get('iam_configuration',{}))
            uniform = iam.get('uniformBucketLevelAccess',iam.get('uniform_bucket_level_access',{}))
            pap = iam.get('publicAccessPrevention',iam.get('public_access_prevention'))
            if (name not in expected_buckets or not owned_project or item.get('labels') != label or
                'bucket:'+name not in intents or str(item.get('projectNumber',item.get('project_number'))) != self.manifest['project_number'] or
                item.get('location','').lower() != self.manifest['region'] or
                uniform.get('enabled') is not True or pap != 'enforced'):
                raise ValueError('Existing bucket ownership/security is unknown; no resource was adopted.')
        db_name = f'projects/{self.project}/databases/(default)'
        for item in inventories['database']:
            if (not owned_project or 'database' not in intents or item.get('name') != db_name or
                item.get('locationId') != self.manifest['region'] or item.get('type') != 'FIRESTORE_NATIVE' or
                item.get('deleteProtectionState') != 'DELETE_PROTECTION_ENABLED'):
                raise ValueError('Existing Firestore database was not created by this installation or differs from its plan.')
        repo_name = f"projects/{self.project}/locations/{self.manifest['region']}/repositories/{self.plan['repository']['name']}"
        for item in inventories['repository']:
            if (not owned_project or 'repository' not in intents or item.get('name') != repo_name or
                item.get('labels') != label or item.get('format') != 'DOCKER'):
                raise ValueError('Existing Artifact Registry repository ownership is unknown.')
        for item in inventories['service']:
            metadata = item.get('metadata',{})
            labels = metadata.get('labels',item.get('labels',{}))
            if (not owned_project or 'deploy' not in intents or
                metadata.get('name',item.get('name','').rsplit('/',1)[-1]) != self.manifest['service_name'] or
                labels.get(LABEL_KEY) != self.manifest['installation_id'] or
                labels.get('cloud.googleapis.com/location',self.manifest['region']) != self.manifest['region']):
                raise ValueError('Existing Cloud Run service ownership is unknown.')
        # Firebase creates an Admin SDK account when addFirebase succeeds. Verify
        # Firebase's immutable identity before considering that provider-created
        # account during resume; a name prefix alone is never ownership proof.
        # https://firebase.google.com/support/guides/service-accounts
        firebase_exists = self._firebase_exists()
        if firebase_exists and (not owned_project or 'firebase-project' not in intents):
            raise ValueError('An existing Firebase project must not be silently adopted; use a fresh dedicated project.')
        expected_accounts = {self.plan['iam'][key]['service_account'] for key in ('runtime','build')}
        default_accounts = {self.manifest['project_number']+'-compute@developer.gserviceaccount.com',self.project+'@appspot.gserviceaccount.com'}
        description = 'HLR installation '+self.manifest['installation_id']
        firebase_sdk_accounts = 0
        for item in inventories['accounts']:
            email = item.get('email')
            if email in default_accounts: continue
            if (owned_project and 'firebase-project' in intents and firebase_exists and
                isinstance(email,str) and re.fullmatch(r'firebase-adminsdk-[a-z0-9]{5}@'+re.escape(self.project)+r'\.iam\.gserviceaccount\.com',email) and
                item.get('projectId') == self.project and item.get('name') in {
                    'projects/'+self.project+'/serviceAccounts/'+email,
                    'projects/'+self.manifest['project_number']+'/serviceAccounts/'+email}):
                firebase_sdk_accounts += 1
                if firebase_sdk_accounts > 1:
                    raise ValueError('Unexpected multiple Firebase Admin SDK accounts; review this dedicated project before resuming.')
                continue
            if (not owned_project or email not in expected_accounts or 'account:'+email not in intents or
                item.get('description') != description):
                raise ValueError('Existing custom service account ownership is unknown.')
        role_name = f"projects/{self.project}/roles/{self.plan['iam']['runtime']['auth_role']}"
        for item in inventories['roles']:
            if (not owned_project or 'auth-role' not in intents or item.get('name') != role_name or
                item.get('description') != description or item.get('deleted') is True):
                raise ValueError('Existing custom IAM role ownership is unknown.')
            full_role = self._g(['iam','roles','describe',self.plan['iam']['runtime']['auth_role']])
            if sorted(full_role.get('includedPermissions',[])) != self.plan['iam']['runtime']['auth_permissions']:
                raise ValueError('Existing auth IAM role has unexpected permissions.')
        for item in inventories['secrets']:
            name = item.get('name','').rsplit('/',1)[-1]
            if (not owned_project or name not in self.plan['secrets'] or
                'secret:'+name not in intents or item.get('labels') != label):
                raise ValueError('Existing Secret Manager resource ownership is unknown.')
        apps = []
        if firebase_exists:
            response = self._api('GET','firebase','projects/'+self.project+'/webApps',
                query={'fields':'apps(appId,displayName),nextPageToken','pageSize':'100'})
            if response.get('nextPageToken'): raise ValueError('Unexpected Firebase app inventory pagination.')
            apps = response.get('apps',[])
            if len(apps) > 1 or any(app.get('displayName') != description or 'firebase-webapp' not in intents for app in apps):
                raise ValueError('Existing Firebase web app ownership is unknown.')
        inventories.update(firebase_exists=firebase_exists, apps=apps)
        return inventories

    def apply_infrastructure(self):
        """Called only after caller's explicit plan/cost consent gate."""
        project_resource = self.preflight()
        # APIs may need enabling to inspect dormant existing resources. This is
        # the only mutation before the full resource ownership inventory.
        self._g(['services','enable',*self.plan['apis']])
        inventory = self._inventory(project_resource)
        if project_resource.get('labels',{}).get(LABEL_KEY) != self.manifest['installation_id']:
            self._g(['projects','update',self.project,'--update-labels='+LABEL_KEY+'='+self.manifest['installation_id']])
        labels_arg = '--labels='+LABEL_KEY+'='+self.manifest['installation_id']
        description = 'HLR installation '+self.manifest['installation_id']
        existing_buckets = {b.get('name','').removeprefix('gs://').rstrip('/') for b in inventory['buckets']}
        for bucket in self.plan['buckets']:
            if bucket['name'] not in existing_buckets:
                self._create('bucket:'+bucket['name'],['storage','buckets','create','gs://'+bucket['name'],
                    '--location='+self.manifest['region'],'--uniform-bucket-level-access','--public-access-prevention',labels_arg])
        if not inventory['database']:
            self._create('database',['firestore','databases','create','--database=(default)',
                '--location='+self.manifest['region'],'--type=firestore-native','--edition=standard','--delete-protection'])
        if not inventory['repository']:
            self._create('repository',['artifacts','repositories','create',self.plan['repository']['name'],
                '--location='+self.manifest['region'],'--repository-format=docker',labels_arg])
        existing_accounts = {a.get('email') for a in inventory['accounts']}
        for kind in ('runtime','build'):
            email = self.plan['iam'][kind]['service_account']
            if email not in existing_accounts:
                self._create('account:'+email,['iam','service-accounts','create',email.split('@')[0],
                    '--display-name=Hydroponic '+kind,'--description='+description])
        if not inventory['roles']:
            self._create('auth-role',['iam','roles','create',self.plan['iam']['runtime']['auth_role'],
                '--title=Hydroponic session verification','--description='+description,'--stage=GA',
                '--permissions='+','.join(self.plan['iam']['runtime']['auth_permissions'])])
        existing_secrets = {s.get('name','').rsplit('/',1)[-1] for s in inventory['secrets']}
        for secret in self.plan['secrets']:
            if secret not in existing_secrets:
                self._create('secret:'+secret,['secrets','create',secret,'--replication-policy=automatic',labels_arg])
        self._grant_iam()
        if not inventory['firebase_exists']:
            self._intent('firebase-project')
            operation = self._api('POST','firebase','projects/'+self.project+':addFirebase',{})
            self._wait_firebase(operation)
            self._done('firebase-project')
        if inventory['apps']:
            app_id = inventory['apps'][0]['appId']
        else:
            self._intent('firebase-webapp')
            operation = self._api('POST','firebase','projects/'+self.project+'/webApps',{'displayName':description})
            result = self._wait_firebase(operation)
            app_id = result.get('appId')
            self._done('firebase-webapp')
        if not isinstance(app_id,str) or not re.fullmatch(r'1:'+self.manifest['project_number']+r':web:[A-Za-z0-9]+',app_id):
            raise ValueError('Firebase web app did not return the expected own-project application ID.')
        config = self._api('GET','firebase','projects/'+self.project+'/webApps/'+quote(app_id,safe=':')+'/config',
            query={'fields':'apiKey,appId,authDomain,projectId'})
        updated = dict(self.manifest, firebase_web=config)
        self.manifest = validate_manifest(updated,require_runtime=True)
        self.state['firebase_web'] = config; self.save_state()
        self._deny_browser_database_access()
        self._infrastructure_ready = True
        return self.manifest

    def _grant_iam(self):
        for kind in ('runtime','build'):
            identity = self.plan['iam'][kind]; member = 'serviceAccount:'+identity['service_account']
            for role in identity['project_roles']:
                self._g(['projects','add-iam-policy-binding',self.project,'--member='+member,
                    '--role='+role,'--condition=None'])
            self._g(['storage','buckets','add-iam-policy-binding','gs://'+identity['bucket'],
                '--member='+member,'--role='+identity['bucket_role']])
        runtime = 'serviceAccount:'+self.plan['iam']['runtime']['service_account']
        self._g(['projects','add-iam-policy-binding',self.project,'--member='+runtime,
            '--role=projects/'+self.project+'/roles/'+self.plan['iam']['runtime']['auth_role'],'--condition=None'])
        for secret in self.plan['secrets']:
            self._g(['secrets','add-iam-policy-binding',secret,'--member='+runtime,'--role=roles/secretmanager.secretAccessor','--condition=None'])
        self._g(['artifacts','repositories','add-iam-policy-binding',self.plan['repository']['name'],
            '--location='+self.manifest['region'],'--member=serviceAccount:'+self.plan['iam']['build']['service_account'],
            '--role=roles/artifactregistry.writer','--condition=None'])
        self._done('iam')

    def _wait_firebase(self, operation):
        initial_name = operation.get('name','')
        if not operation.get('done'):
            if not re.fullmatch(r'operations/[A-Za-z0-9_./=:-]+',initial_name):
                raise ValueError('Firebase returned an invalid operation name.')
            self._firebase_operations.add(initial_name)
        for attempt in range(120):
            if operation.get('done'):
                if operation.get('error'): raise ValueError('Firebase operation failed; inspect the selected project console and resume.')
                return operation.get('response',{})
            name = operation.get('name','')
            if name != initial_name:
                raise ValueError('Firebase returned an invalid operation name.')
            if attempt: time.sleep(2)
            operation = self._api('GET','firebase',name,query={'fields':'name,done,error,response'})
        raise ValueError('Firebase operation timed out; resources are preserved for a later resume.')

    def _deny_browser_database_access(self):
        base = 'projects/'+self.project
        if 'firestore-rules' in self.state['completed']:
            release = self._api('GET','firebaserules',base+'/releases/cloud.firestore',query={'fields':'name,rulesetName'})
            name = release.get('rulesetName','')
            if not re.fullmatch(re.escape(base)+r'/rulesets/[A-Za-z0-9-]+',name):
                raise ValueError('Existing Firestore rules are not bound to this own project.')
            rules = self._api('GET','firebaserules',name,query={'fields':'source.files(name,content)'})
            files = rules.get('source',{}).get('files',[])
            if files != [{'name':'firestore.rules','content':DENY_RULES}]:
                raise ValueError('Existing Firestore browser-deny rules changed; refusing an unsafe resume.')
            return
        if 'firestore-rules' not in self.state['completed']:
            self._intent('firestore-rules')
            result = self._api('POST','firebaserules',base+'/rulesets',
                {'source':{'files':[{'name':'firestore.rules','content':DENY_RULES}]}},query={'fields':'name'})
            name = result.get('name','')
            if not re.fullmatch(re.escape(base)+r'/rulesets/[A-Za-z0-9-]+',name):
                raise ValueError('Firebase Rules did not return an own-project ruleset.')
            release = base+'/releases/cloud.firestore'
            try:
                existing = self._api('GET','firebaserules',release,query={'fields':'name,rulesetName'})
            except Exception as error:
                if getattr(error,'status_code',None) != 404: raise
                existing = None
            if existing is None:
                self._api('POST','firebaserules',base+'/releases',{'name':release,'rulesetName':name},query={'fields':'name,rulesetName'})
            else:
                self._api('PATCH','firebaserules',release,{'release':{'name':release,'rulesetName':name},'updateMask':'rulesetName'},query={'fields':'name,rulesetName'})
            self._done('firestore-rules')

    def read_auth_config(self):
        # Never request the complete config: it can contain password-hash keys.
        return self._api('GET','identitytoolkit','projects/'+self.project+'/config',query={'fields':AUTH_FIELDS})

    def configure_auth(self, origin=None):
        if origin is not None and origin != self.manifest['origin']:
            raise ValueError('Auth redirects must use this installation Cloud Run origin.')
        try: config = self.read_auth_config()
        except Exception as error:
            if getattr(error,'status_code',None) not in (400,404): raise
            raise ManualStepRequired('In Firebase console for '+self.project+', open Authentication, choose Get started, enable Email/Password (password required), and keep standard Firebase Authentication. Do not upgrade to Identity Platform; then resume.') from None
        if not isinstance(config,dict): raise ValueError('Auth config response was not an object.')
        if config.get('subtype') != 'FIREBASE_AUTH':
            raise ValueError('Standard Firebase Authentication is required; refusing an upgraded or unknown auth subtype.')
        domain = urlsplit(self.manifest['origin']).hostname
        domains = sorted(set(config.get('authorizedDomains',[])) | {domain,self.project+'.firebaseapp.com'})
        result = self._api('PATCH','identitytoolkit','projects/'+self.project+'/config',
            {'authorizedDomains':domains,'signIn':{'email':{'enabled':True,'passwordRequired':True}},
             'emailPrivacyConfig':{'enableImprovedEmailPrivacy':True}},
            query={'updateMask':'authorizedDomains,signIn.email.enabled,signIn.email.passwordRequired,emailPrivacyConfig.enableImprovedEmailPrivacy','fields':AUTH_FIELDS})
        email = result.get('signIn',{}).get('email',{})
        if (domain not in result.get('authorizedDomains',[]) or email.get('enabled') is not True or email.get('passwordRequired') is not True or
            result.get('emailPrivacyConfig',{}).get('enableImprovedEmailPrivacy') is not True):
            raise ManualStepRequired('Confirm Email/Password and the Cloud Run authorized domain in this project Firebase console, then resume.')
        self._done('auth-config')
        return result

    def put_runtime_secret(self, value):
        if not self._infrastructure_ready: raise ValueError('Infrastructure ownership has not been verified in this process.')
        if not isinstance(value,bytes) or len(value) < 32 or len(value) > 4096:
            raise ValueError('Runtime root secret must contain 32 to 4096 bytes.')
        return self._put_secret(self.plan['secrets'][0],value,'runtime_secret_version')

    def put_instance_config(self):
        if not self._infrastructure_ready: raise ValueError('Infrastructure ownership has not been verified in this process.')
        manifest = validate_manifest(self.manifest,require_runtime=True)
        return self._put_secret(self.plan['secrets'][1],json.dumps(manifest,sort_keys=True,separators=(',',':')).encode(),'config_secret_version')

    def _put_secret(self, name, value, state_key):
        result = self._g(['secrets','versions','add',name,'--data-file=-'],input_bytes=value)
        resource = result.get('name','')
        if not re.fullmatch(r'projects/(?:'+re.escape(self.project)+'|'+self.manifest['project_number']+r')/secrets/'+re.escape(name)+r'/versions/[1-9][0-9]*',resource):
            raise ValueError('Secret Manager did not return this own-project secret concrete version.')
        version = resource.rsplit('/',1)[-1]
        self.state[state_key] = version; self.save_state()
        return version

    def submit_build(self, context_path, *, retry_failed=False):
        if not self._infrastructure_ready: raise ValueError('Verify infrastructure before submitting a build.')
        context = Path(context_path).resolve()
        # A self-consistent receipt does not authorize upload. Re-derive the
        # exact allowlist and content from trusted Git blobs + model policy.
        from .context import verify_context
        receipt = verify_context(context)
        receipt_sha = hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        image = self.plan['repository']['image']+':'+receipt_sha[:24]
        if self.state.get('build_id'):
            old_sha = self.state.get('build_receipt_sha256','')
            if not isinstance(old_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',old_sha):
                raise ManualStepRequired('The recorded Cloud Build job lacks a trusted context receipt. Review its outcome in this own project Cloud Build console; do not delete setup state or submit a blind duplicate.')
            previous = self._describe_build(self.state['build_id'],old_sha)
            if old_sha == receipt_sha:
                if retry_failed:
                    if previous['status'] not in ('FAILURE','INTERNAL_ERROR','TIMEOUT','CANCELLED','EXPIRED'):
                        raise ValueError('An explicit failed-build retry applies only to a verified terminal failed job. No duplicate was submitted.')
                else:
                    result = self._poll_build(previous,receipt_sha)
                    return self._promote_build_image(result,receipt_sha,receipt['source_commit'])
            elif retry_failed:
                raise ValueError('Failed-build retry approval cannot authorize a different source receipt.')
            if previous['status'] in ('PENDING','QUEUED','WORKING'):
                raise ManualStepRequired('A Cloud Build job for the previous source receipt is still running in '+self.project+'. Wait for it to finish or deliberately cancel that exact job in your own project console before changing source. No second job was submitted.')
            self.state.setdefault('build_history',[]).append(dict(id=previous['id'],receipt_sha256=old_sha,status=previous['status']))
            for key in ('build_id','build_receipt_sha256','build_status'):
                self.state.pop(key,None)
        elif self.state.get('build_submission') or 'build' in self.state['intents']:
            raise ManualStepRequired('A prior Cloud Build submission has no recorded job ID; its outcome is unknown. Closing the CLI does not cancel a cloud job. Inspect this own project Cloud Build console and reconcile the exact job ID with the preserved setup state before retrying; no duplicate was submitted.')
        elif retry_failed:
            raise ValueError('There is no verified failed build to retry; no submission was attempted.')
        # Persist uncertainty before the network request as well as the returned
        # ID afterwards. A lost response must never silently buy a second build.
        self.state['build_submission'] = dict(receipt_sha256=receipt_sha,image=image,region=self.manifest['region'])
        self.save_state()
        self._intent('build')
        result = self._g(['builds','submit',str(context),'--config='+str(context/'infra/cloud/cloudbuild.yaml'),
            '--region='+self.manifest['region'],
            '--gcs-source-staging-dir=gs://'+self.plan['build']['staging_bucket']+'/source',
            '--service-account=projects/'+self.project+'/serviceAccounts/'+self.plan['iam']['build']['service_account'],
            '--substitutions=_IMAGE='+image+',_CONTEXT_SHA256='+receipt_sha,
            '--timeout=1200s','--suppress-logs','--async'])
        build_id = self._build_identity(result)
        self.state.update(build_id=build_id,build_receipt_sha256=receipt_sha,build_status=result.get('status','SUBMITTED'))
        self.save_state()
        # Always fetch authoritative metadata, even if the submit response is
        # already SUCCESS. No provider response, logs or source paths are shown.
        result = self._poll_build(self._describe_build(build_id,receipt_sha),receipt_sha)
        return self._promote_build_image(result,receipt_sha,receipt['source_commit'])

    def _build_identity(self, result, expected_id=None):
        if not isinstance(result,dict): raise ValueError('Cloud Build returned no own-project job metadata.')
        build_id = result.get('id','')
        if (not isinstance(build_id,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}',build_id) or
            result.get('projectId') != self.project or expected_id is not None and build_id != expected_id):
            raise ValueError('Cloud Build job identity differs from this selected project; preserve state and inspect its console.')
        names = {f'projects/{project}/locations/{self.manifest["region"]}/builds/{build_id}'
                 for project in (self.project,self.manifest['project_number'])}
        if result.get('name') not in names:
            raise ValueError('Cloud Build returned a different project/region resource name.')
        return build_id

    def _describe_build(self, build_id, receipt_sha):
        if not isinstance(build_id,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}',build_id):
            raise ValueError('Recorded Cloud Build ID is not canonical; no lookup was attempted.')
        result = self._g(['builds','describe',build_id,'--region='+self.manifest['region']])
        self._build_identity(result,build_id)
        image = self.plan['repository']['image']+':'+receipt_sha[:24]
        service_accounts = {'projects/'+project+'/serviceAccounts/'+self.plan['iam']['build']['service_account']
                            for project in (self.project,self.manifest['project_number'])}
        if (result.get('substitutions',{}).get('_CONTEXT_SHA256') != receipt_sha or
            result.get('substitutions',{}).get('_IMAGE') != image or result.get('images') != [image] or
            result.get('serviceAccount') not in service_accounts or
            result.get('source',{}).get('storageSource',{}).get('bucket') != self.plan['build']['staging_bucket'] or
            result.get('timeout') != '1200s' or result.get('queueTtl') != '300s' or
            result.get('options',{}).get('logging') != 'CLOUD_LOGGING_ONLY'):
            raise ValueError('Cloud Build job does not match this trusted source receipt, build identity, private source bucket or bounded cost plan.')
        status = result.get('status')
        if status not in ('PENDING','QUEUED','WORKING','SUCCESS','FAILURE','INTERNAL_ERROR','TIMEOUT','CANCELLED','EXPIRED'):
            raise ValueError('Cloud Build has an unknown status; no automatic retry or deployment was attempted.')
        self.state['build_status'] = status; self.save_state()
        return result

    def _poll_build(self, result, receipt_sha):
        deadline = time.monotonic()+1550
        while result['status'] in ('PENDING','QUEUED','WORKING'):
            if time.monotonic() >= deadline:
                raise ValueError('Cloud Build is still pending beyond this local wait. Its recorded ID is preserved; rerun to inspect the same job. Closing the CLI does not cancel the cloud job.')
            time.sleep(5)
            result = self._describe_build(result['id'],receipt_sha)
        if result['status'] != 'SUCCESS':
            raise BuildRetryRequired(result['id'],result['status'])
        return result

    def _promote_build_image(self, result, receipt_sha, source_commit):
        image = self.plan['repository']['image']+':'+receipt_sha[:24]
        images = result.get('results',{}).get('images',[])
        digest = next((i.get('digest') for i in images if i.get('name') == image), None)
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):
            raise ValueError('Build did not return the promoted immutable image digest.')
        immutable = self.plan['repository']['image']+'@'+digest
        self.state.update(image=immutable,build_source_commit=source_commit); self.save_state(); self._done('build')
        return immutable

    def deploy(self, image):
        expected = re.escape(self.plan['repository']['image'])+r'@sha256:[0-9a-f]{64}'
        if not isinstance(image,str) or not re.fullmatch(expected,image):
            raise ValueError('Only an immutable image digest from this installation repository can be deployed.')
        validate_manifest(self.manifest,require_runtime=True)
        if not self._infrastructure_ready or 'auth-config' not in self.state['completed']:
            raise ValueError('Verify infrastructure and Firebase settings before deployment.')
        versions = [self.state.get('runtime_secret_version',''),self.state.get('config_secret_version','')]
        if any(not str(v).isdigit() for v in versions): raise ValueError('Create pinned runtime/config secret versions before deployment.')
        secret,config_secret = self.plan['secrets']
        self._intent('deploy')
        result = self._g(['run','deploy',self.manifest['service_name'],
            '--region='+self.manifest['region'],'--image='+image,
            '--service-account='+self.plan['iam']['runtime']['service_account'],
            '--cpu=2','--memory=4Gi','--min=0','--max=1','--min-instances=0','--max-instances=1',
            '--concurrency=1','--timeout=360s','--cpu-throttling','--no-cpu-boost',
            '--execution-environment=gen2','--ingress=all','--allow-unauthenticated',
            '--port=8080','--labels='+LABEL_KEY+'='+self.manifest['installation_id'],
            '--set-env-vars=HYDRO_CLOUD_INSTANCE_CONFIG=/secrets/instance/config.json,HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1',
            '--set-secrets=HYDRO_AUTH_TOKEN='+secret+':'+str(versions[0])+',/secrets/instance/config.json='+config_secret+':'+str(versions[1]),
            '--startup-probe=httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=0,periodSeconds=10,timeoutSeconds=5,failureThreshold=24'])
        status = result.get('status',{})
        conditions = status.get('conditions',[])
        if not any(c.get('type') == 'Ready' and c.get('status') in ('True',True) for c in conditions):
            raise ValueError('Cloud Run has not reported Ready; preserve the prior revision and inspect this project.')
        self.state['revision'] = status.get('latestReadyRevisionName'); self.save_state(); self._done('deploy')
        return {'origin':self.manifest['origin'],'revision':self.state['revision'],'status':'READY_REQUIRES_ACCEPTANCE'}
