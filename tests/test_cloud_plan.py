"""Offline own-project infrastructure boundaries; no Google credentials are used."""
import copy
import importlib
import unittest


def fixture():
    from src.cloud_setup.plan import make_manifest
    return make_manifest('student-leaf-12345', '123456789012', 'a' * 32, 'Student lab')


class PlanTests(unittest.TestCase):
    def test_manifest_builder_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('src.cloud_setup.plan'))

    def test_own_resources_and_origin_are_derived_together(self):
        manifest = fixture()
        self.assertEqual(manifest['bucket'], 'student-leaf-12345-hlr-science')
        self.assertEqual(manifest['auth_prefix'], 'hlr_aaaaaaaaaaaaaaaa_auth')
        self.assertEqual(manifest['origin'], 'https://hydroponic-leaf-research-123456789012.us-central1.run.app')
        self.assertEqual(manifest['firestore_database'], '(default)')

    def test_protected_or_ambiguous_project_never_produces_plan(self):
        from src.cloud_setup.plan import make_manifest
        for project in ('hydroponic-leaf-research-aryae', 'demo-test', 'bad project', 'abc;whoami', '--project=x', 'a'*31):
            with self.subTest(project=project), self.assertRaises(ValueError):
                make_manifest(project, '123456789012', 'a'*32, 'lab')
        with self.assertRaises(ValueError):
            make_manifest('my-independent-lab', '242618885776', 'a'*32, 'lab')

    def test_cross_project_and_alias_targets_are_rejected(self):
        from src.cloud_setup.plan import validate_manifest
        for key, value in [('bucket', 'other-private-data'), ('auth_prefix', 'hydroponic_auth_v1'),
                           ('origin', 'https://other.run.app'), ('service_name', 'different'),
                           ('project_number', 'not-a-number'), ('installation_id', '../oops'),
                           ('firebase_web', {'projectId':'other', 'apiKey':'x', 'appId':'y', 'authDomain':'other.firebaseapp.com'})]:
            manifest = fixture(); manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_manifest(manifest)

    def test_plan_is_deterministic_and_least_privilege(self):
        from src.cloud_setup.plan import build_plan
        manifest = fixture(); original = copy.deepcopy(manifest)
        plan = build_plan(manifest)
        self.assertEqual(manifest, original)
        self.assertEqual(plan, build_plan(manifest))
        runtime = plan['iam']['runtime']; build = plan['iam']['build']
        self.assertEqual(runtime['auth_permissions'], ['firebaseauth.users.createSession', 'firebaseauth.users.get'])
        self.assertEqual(runtime['bucket'], 'student-leaf-12345-hlr-science')
        self.assertEqual(build['bucket'], 'student-leaf-12345-hlr-build')
        self.assertNotIn('roles/editor', str(plan)); self.assertNotIn('getHashConfig', str(plan))
        self.assertEqual(plan['run']['min_instances'], 0)
        self.assertEqual(plan['run']['max_instances'], 1)
        self.assertEqual(plan['run']['concurrency'], 1)
        self.assertTrue(plan['costs']['budget_alert_is_not_a_cap'])


class RecordingClient:
    def __init__(self, project='student-leaf-12345', inventories=None, labels=None):
        self.project_id = project; self.calls = []; self.inventories = inventories or {}
        self.labels = labels or {}

    def gcloud(self, args, *, json_output=False, input_bytes=None):
        self.calls.append((args, input_bytes))
        if args[:2] == ['projects', 'describe']:
            return {'projectId': self.project_id, 'projectNumber':'123456789012', 'labels':self.labels, 'lifecycleState':'ACTIVE'}
        for prefix, value in self.inventories.items():
            if args[:len(prefix)] == list(prefix): return value
        if args[:3] == ['secrets','versions','add']:
            return {'name':'projects/student-leaf-12345/secrets/'+args[3]+'/versions/1'}
        if args[:2] == ['run','deploy']:
            return {'status':{'conditions':[{'type':'Ready','status':'True'}],
                              'latestReadyRevisionName':'hydroponic-leaf-research-00001-test'}}
        if 'list' in args: return []
        return {}

    def api(self, method, url, body=None):
        self.calls.append((method, url, body))
        if '/webApps' in url: return {'apps':[]}
        if 'firebase.googleapis.com' in url and method == 'GET':
            return {'projectId':self.project_id,'projectNumber':'123456789012'}
        return {}


class MissingResource(ValueError):
    status_code = 404


class FreshProjectClient(RecordingClient):
    """Deterministic external API fixture; no live cloud operation is possible."""
    def __init__(self):
        super().__init__(); self.firebase = False

    def api(self, method, url, body=None):
        self.calls.append((method,url,body))
        if 'identitytoolkit.googleapis.com' in url:
            if method == 'GET':
                return {'subtype':'FIREBASE_AUTH','authorizedDomains':[], 'signIn':{'email':{'enabled':False}}}
            return dict(body,subtype='FIREBASE_AUTH')
        if 'firebaserules.googleapis.com' in url:
            if method == 'POST' and '/rulesets' in url:
                return {'name':'projects/'+self.project_id+'/rulesets/own-rule-id'}
            if method == 'GET': raise MissingResource()
            return body
        if ':addFirebase' in url:
            self.firebase = True
            return {'done':True,'response':{'projectId':self.project_id,'projectNumber':'123456789012'}}
        if '/webApps' in url:
            if method == 'POST': return {'done':True,'response':{'appId':'1:123456789012:web:abcdef'}}
            if '/config' in url:
                return {'apiKey':'public-api-key','appId':'1:123456789012:web:abcdef',
                        'projectId':self.project_id,'authDomain':self.project_id+'.firebaseapp.com'}
            return {'apps':[]}
        if not self.firebase: raise MissingResource()
        return {'projectId':self.project_id,'projectNumber':'123456789012'}


class ProvisionTests(unittest.TestCase):
    def test_resume_accepts_only_own_documented_firebase_sdk_account_with_creation_evidence(self):
        from src.cloud_setup.provision import Provisioner
        email='firebase-adminsdk-a1b2c@student-leaf-12345.iam.gserviceaccount.com'
        account={'email':email,'projectId':'student-leaf-12345',
                 'name':'projects/student-leaf-12345/serviceAccounts/'+email}
        client=RecordingClient(labels={'hlr-installation':'a'*32},
            inventories={('iam','service-accounts','list'):[account]})
        p=Provisioner(client,fixture(),state={'intents':['firebase-project'],'completed':[]})
        inventory=p._inventory(p.preflight())
        self.assertTrue(inventory['firebase_exists'])
        self.assertEqual(inventory['accounts'],[account])
        self.assertFalse(any(isinstance(c[0],list) and 'create' in c[0] for c in client.calls))

    def test_firebase_sdk_name_does_not_bypass_missing_project_ownership_or_intent(self):
        from src.cloud_setup.provision import Provisioner
        email='firebase-adminsdk-a1b2c@student-leaf-12345.iam.gserviceaccount.com'
        account={'email':email,'projectId':'student-leaf-12345',
                 'name':'projects/student-leaf-12345/serviceAccounts/'+email}
        for labels,intents in [({},['firebase-project']),({'hlr-installation':'a'*32},[])]:
            client=RecordingClient(labels=labels,inventories={('iam','service-accounts','list'):[account]})
            p=Provisioner(client,fixture(),state={'intents':intents,'completed':[]})
            with self.subTest(labels=labels,intents=intents), self.assertRaises(ValueError):
                p._inventory(p.preflight())

    def test_firebase_sdk_account_requires_exact_own_metadata_and_firebase_project_identity(self):
        from src.cloud_setup.provision import Provisioner
        email='firebase-adminsdk-a1b2c@student-leaf-12345.iam.gserviceaccount.com'
        own={'email':email,'projectId':'student-leaf-12345',
             'name':'projects/student-leaf-12345/serviceAccounts/'+email}
        for change in [{'projectId':'foreign-lab-123'},
                       {'name':'projects/foreign-lab-123/serviceAccounts/'+email},
                       {'email':'firebase-adminsdk-a1b2c@foreign-lab-123.iam.gserviceaccount.com'},
                       {'email':'firebase-adminsdk-arbitrary-name@student-leaf-12345.iam.gserviceaccount.com'}]:
            client=RecordingClient(labels={'hlr-installation':'a'*32},
                inventories={('iam','service-accounts','list'):[dict(own,**change)]})
            p=Provisioner(client,fixture(),state={'intents':['firebase-project'],'completed':[]})
            with self.subTest(change=change), self.assertRaises(ValueError):p._inventory(p.preflight())
        class WrongFirebaseIdentity(RecordingClient):
            def api(self,method,url,body=None):
                result=super().api(method,url,body)
                if 'firebase.googleapis.com' in url and '/webApps' not in url:
                    result['projectNumber']='999999999999'
                return result
        client=WrongFirebaseIdentity(labels={'hlr-installation':'a'*32},
            inventories={('iam','service-accounts','list'):[own]})
        p=Provisioner(client,fixture(),state={'intents':['firebase-project'],'completed':[]})
        with self.assertRaises(ValueError):p._inventory(p.preflight())

    def test_fresh_project_flow_records_intents_and_uses_narrow_iam(self):
        from src.cloud_setup.provision import Provisioner
        client = FreshProjectClient(); state = {}; snapshots=[]
        p = Provisioner(client,fixture(),state=state,save_state=lambda:snapshots.append(copy.deepcopy(state)))
        manifest = p.apply_infrastructure()
        self.assertEqual(manifest['firebase_web']['appId'],'1:123456789012:web:abcdef')
        self.assertIn('database',state['intents']); self.assertIn('database',state['completed'])
        before_create = next(s for s in snapshots if 'database' in s['intents'])
        self.assertNotIn('database',before_create['completed'])
        commands = [call[0] for call in client.calls if isinstance(call[0],list)]
        for args in commands:
            self.assertIn('--project=student-leaf-12345',args)
        grants = [a for a in commands if 'add-iam-policy-binding' in a]
        for grant in grants:
            self.assertFalse(any('roles/editor' in a or 'roles/owner' in a for a in grant))
        build_members = [g for g in grants if any('--member=serviceAccount:hlr-build-' in a for a in g)]
        self.assertFalse(any('gs://student-leaf-12345-hlr-science' in g for g in build_members))
        p.configure_auth()
        self.assertIn('auth-config',state['completed'])
        self.assertEqual(p.put_runtime_secret(b'a'*64),'1')
        self.assertEqual(p.put_instance_config(),'1')
        self.assertNotIn('a'*64,str(state))

    def test_fresh_flow_emits_deny_rules_not_public_browser_access(self):
        from src.cloud_setup.provision import Provisioner
        client = FreshProjectClient(); Provisioner(client,fixture()).apply_infrastructure()
        rule_write = next(c for c in client.calls if c[0]=='POST' and '/rulesets' in c[1])
        self.assertIn('allow read, write: if false;',rule_write[2]['source']['files'][0]['content'])

    def test_deploy_uses_own_immutable_image_and_pinned_secrets_with_cost_limits(self):
        from src.cloud_setup.provision import Provisioner
        client=FreshProjectClient(); p=Provisioner(client,fixture())
        p.apply_infrastructure(); p.configure_auth(); p.put_runtime_secret(b'a'*64); p.put_instance_config()
        image='us-central1-docker.pkg.dev/student-leaf-12345/hlr-images-aaaaaaaaaaaaaaaa/application@sha256:'+'f'*64
        result=p.deploy(image)
        self.assertEqual(result['status'],'READY_REQUIRES_ACCEPTANCE')
        args=client.calls[-1][0]
        for option in ('--cpu=2','--memory=4Gi','--min=0','--max=1','--concurrency=1','--timeout=360s','--cpu-throttling','--no-cpu-boost'):
            self.assertIn(option,args)
        secrets=next(a for a in args if a.startswith('--set-secrets='))
        self.assertIn('/secrets/instance/config.json=hlr-config-aaaaaaaaaaaaaaaa:1',secrets)
        self.assertIn('HYDRO_AUTH_TOKEN=hlr-runtime-aaaaaaaaaaaaaaaa:1',secrets)
        self.assertNotIn('latest',secrets)

    def test_owned_marker_does_not_adopt_unrecorded_database(self):
        from src.cloud_setup.provision import Provisioner
        db = {'name':'projects/student-leaf-12345/databases/(default)','locationId':'us-central1',
              'type':'FIRESTORE_NATIVE','deleteProtectionState':'DELETE_PROTECTION_ENABLED'}
        client = RecordingClient(labels={'hlr-installation':'a'*32},inventories={('firestore','databases','list'):[db]})
        with self.assertRaises(ValueError): Provisioner(client,fixture()).apply_infrastructure()
        self.assertFalse(any(isinstance(c[0],list) and 'create' in c[0] for c in client.calls))

    def test_interrupted_create_saves_intent_before_attempt(self):
        from src.cloud_setup.provision import Provisioner
        class FailingCreate(FreshProjectClient):
            def gcloud(self,args,**kwargs):
                if args[:3]==['storage','buckets','create']:
                    self.calls.append((args,None)); raise ValueError('synthetic cloud failure')
                return super().gcloud(args,**kwargs)
        state={}; client=FailingCreate()
        with self.assertRaises(ValueError): Provisioner(client,fixture(),state=state).apply_infrastructure()
        self.assertIn('bucket:student-leaf-12345-hlr-science',state['intents'])
        self.assertNotIn('bucket:student-leaf-12345-hlr-science',state['completed'])

    def test_existing_firebase_requires_remote_marker_and_local_intent(self):
        from src.cloud_setup.provision import Provisioner
        client=RecordingClient(labels={'hlr-installation':'a'*32})
        with self.assertRaises(ValueError): Provisioner(client,fixture()).apply_infrastructure()
        self.assertFalse(any(isinstance(c[0],list) and 'create' in c[0] for c in client.calls))

    def test_auth_not_initialized_returns_manual_standard_auth_step(self):
        from src.cloud_setup.provision import ManualStepRequired, Provisioner
        class Uninitialized(FreshProjectClient):
            def api(self,method,url,body=None):
                if 'identitytoolkit' in url: raise MissingResource()
                return super().api(method,url,body)
        with self.assertRaises(ManualStepRequired): Provisioner(Uninitialized(),fixture()).configure_auth()

    def test_client_project_mismatch_stops_before_any_call(self):
        from src.cloud_setup.provision import Provisioner
        client = RecordingClient('wrong-project-123')
        with self.assertRaises(ValueError): Provisioner(client, fixture())
        self.assertEqual(client.calls, [])

    def test_read_only_preflight_never_mutates_or_switches_active_config(self):
        from src.cloud_setup.provision import Provisioner
        client = RecordingClient(); Provisioner(client, fixture()).preflight()
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0][:3], ['projects', 'describe', 'student-leaf-12345'])

    def test_unknown_scientific_bucket_fails_before_iam_or_resource_creation(self):
        from src.cloud_setup.provision import Provisioner
        client = RecordingClient(inventories={('storage','buckets','list'):[{'name':'some-existing-bucket','projectNumber':'123456789012'}]})
        with self.assertRaises(ValueError): Provisioner(client, fixture()).apply_infrastructure()
        serialized = str(client.calls)
        self.assertNotIn('add-iam-policy-binding', serialized)
        self.assertNotIn("'create'", serialized)
        self.assertNotIn('update', serialized)

    def test_secret_content_is_stdin_and_project_scope_is_explicit(self):
        from src.cloud_setup.provision import Provisioner
        client = RecordingClient(labels={'hlr-installation':'a'*32})
        provisioner = Provisioner(client, fixture())
        provisioner._infrastructure_ready = True
        provisioner.put_runtime_secret(b'x'*64)
        args, data = client.calls[-1]
        self.assertEqual(data, b'x'*64)
        self.assertIn('--data-file=-', args)
        self.assertIn('--project=student-leaf-12345', args)
        self.assertNotIn('x'*64, str(args))

    def test_deploy_refuses_cross_project_image_before_call(self):
        from src.cloud_setup.provision import Provisioner
        client = RecordingClient(); provisioner = Provisioner(client, fixture())
        with self.assertRaises(ValueError):
            provisioner.deploy('us-central1-docker.pkg.dev/other-project/repo/image@sha256:'+'f'*64)
        self.assertEqual(client.calls, [])

    def test_auth_config_reads_have_allowlisted_partial_response(self):
        from src.cloud_setup.provision import Provisioner
        client = RecordingClient(); provisioner = Provisioner(client, fixture())
        provisioner.read_auth_config()
        method, url, body = client.calls[-1]
        self.assertEqual(method, 'GET'); self.assertIsNone(body)
        self.assertIn('fields=authorizedDomains%2CsignIn.email%2CemailPrivacyConfig%2Csubtype', url)
        self.assertNotIn('hash', url.lower())


if __name__ == '__main__': unittest.main()
