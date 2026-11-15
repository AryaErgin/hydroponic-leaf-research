"""Independent adversarial release checks. No cloud credentials or network calls."""
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest


PROJECT = 'audit-student-leaf-123'
NUMBER = '123456789012'


def manifest():
    from src.cloud_setup.plan import make_manifest
    return make_manifest(PROJECT, NUMBER, 'a' * 32, 'Audit University')


class Recorder:
    project_id = PROJECT

    def __init__(self, *, auth_subtype='FIREBASE_AUTH', secret_name=None):
        self.calls = []
        self.auth_subtype = auth_subtype
        self.secret_name = secret_name

    def api(self, method, url, body=None):
        self.calls.append((method, url, body))
        if 'identitytoolkit' in url:
            return dict(subtype=self.auth_subtype,
                authorizedDomains=[PROJECT + '.firebaseapp.com', urlsplit(manifest()['origin']).hostname],
                signIn={'email':{'enabled':True, 'passwordRequired':True}},
                emailPrivacyConfig={'enableImprovedEmailPrivacy':True})
        return {}

    def gcloud(self, args, *, json_output=False, input_bytes=None):
        self.calls.append((args, input_bytes))
        if args[:3] == ['secrets', 'versions', 'add']:
            return {'name':self.secret_name or f'projects/{PROJECT}/secrets/{args[3]}/versions/7'}
        return {}


def provisioner(client=None):
    from src.cloud_setup.provision import Provisioner
    return Provisioner(client or Recorder(), manifest())


def test_planned_runtime_manifest_roundtrip_and_readonly_public_identity(monkeypatch):
    from src.cloud_instance.config import CloudInstanceConfig
    for name in list(__import__('os').environ):
        if name.startswith(('HYDRO_', 'FIREBASE_', 'FIRESTORE_')) or name in ('K_SERVICE', 'GOOGLE_CLOUD_PROJECT', 'GCLOUD_PROJECT', 'GOOGLE_CLOUD_QUOTA_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS'):
            monkeypatch.delenv(name, raising=False)
    data = manifest()
    data['firebase_web'] = dict(projectId=PROJECT, apiKey='public-browser-key',
        authDomain=PROJECT+'.firebaseapp.com', appId='1:'+NUMBER+':web:audit123')
    config = CloudInstanceConfig.from_mapping(data)
    assert config.project_id == data['project_id']
    with pytest.raises(TypeError):
        config.firebase_web['projectId'] = 'other-project'


@pytest.mark.parametrize('resource', [
    'projects/'+PROJECT+'-other/config',
    'projects/'+PROJECT+'%2F..%2Fother/config',
    'projects/'+PROJECT+'/../other/config',
    'projects/'+PROJECT+'/config?fields=signIn.hashConfig',
])
def test_api_resource_cannot_escape_exact_project_or_inject_query(resource):
    client = Recorder()
    with pytest.raises(ValueError):
        provisioner(client)._api('GET', 'identitytoolkit', resource)
    assert client.calls == []


def test_auth_setup_refuses_identity_platform_before_any_patch():
    client = Recorder(auth_subtype='IDENTITY_PLATFORM')
    with pytest.raises(ValueError):
        provisioner(client).configure_auth()
    assert [call[0] for call in client.calls] == ['GET']


def test_both_auth_read_and_write_project_only_nonsensitive_fields():
    client = Recorder()
    provisioner(client).configure_auth()
    assert [call[0] for call in client.calls] == ['GET', 'PATCH']
    for _, url, _ in client.calls:
        query = parse_qs(urlsplit(url).query)
        fields = query['fields'][0]
        assert 'subtype' in fields and 'authorizedDomains' in fields
        assert 'signIn.email' in fields or 'signIn(email)' in fields
        assert 'hash' not in fields.lower()


@pytest.mark.parametrize('returned_name', [
    'projects/foreign-project/secrets/wrong/versions/7',
    f'projects/{PROJECT}/secrets/wrong-secret/versions/7',
    '7',
])
def test_secret_response_cannot_persist_foreign_or_ambiguous_version(returned_name):
    client = Recorder(secret_name=returned_name)
    p = provisioner(client)
    p._infrastructure_ready = True
    with pytest.raises(ValueError):
        p.put_runtime_secret(b'SYNTHETIC_TEST_INPUT_ONLY_' * 2)
    assert 'runtime_secret_version' not in p.state


@pytest.mark.parametrize('relative', ['.git/config', '.runtime/accounts.json', 'photos/private.jpg', 'private-key.json'])
def test_self_hashed_receipt_does_not_authorize_private_build_context(tmp_path, relative):
    context = tmp_path / 'forged-context'
    path = context / relative
    path.parent.mkdir(parents=True)
    payload = b'NOT_A_SECRET_SYNTHETIC_ONLY'
    path.write_bytes(payload)
    config = context / 'infra/cloud/cloudbuild.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('steps: []\n', encoding='utf-8')
    files = {relative:hashlib.sha256(payload).hexdigest(),
        'infra/cloud/cloudbuild.yaml':hashlib.sha256(config.read_bytes()).hexdigest()}
    (context/'context-manifest.json').write_text(json.dumps({'files':files}), encoding='utf-8')
    client = Recorder()
    p = provisioner(client)
    p._infrastructure_ready = True
    with pytest.raises(ValueError):
        p.submit_build(context)
    assert client.calls == []


def test_transport_ignores_ambient_endpoint_and_credential_authority(monkeypatch):
    from src.cloud_setup.transport import safe_environment
    names = ['CLOUDSDK_API_ENDPOINT_OVERRIDES_FIRESTORE', 'CLOUDSDK_API_ENDPOINT_OVERRIDES_RUN',
        'CLOUDSDK_CORE_ACCOUNT', 'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT',
        'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE']
    for name in names:
        monkeypatch.setenv(name, 'untrusted-ambient-value')
    env = safe_environment()
    assert not set(names).intersection(env)


@pytest.mark.parametrize('args', [
    ['storage', 'buckets', 'describe', 'gs://hydroponic-leaf-research-aryae-science'],
    ['storage', 'buckets', 'describe', 'gs://foreign-project-private'],
    ['secrets', 'describe', 'projects/foreign-project/secrets/runtime'],
    ['iam', 'service-accounts', 'describe', 'runtime@foreign-project.iam.gserviceaccount.com'],
])
def test_transport_refuses_explicit_foreign_resource_selectors_before_process(args):
    from src.cloud_setup.transport import CloudClient
    calls = []
    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('Unapproved foreign resource must never reach a subprocess')
    client = CloudClient(PROJECT, 'hlr-own-audit', executable='gcloud', runner=runner)
    with pytest.raises(ValueError):
        client.gcloud(args, json_output=True)
    assert calls == []


def test_context_policy_trust_anchor_must_equal_committed_source(monkeypatch, tmp_path):
    from src.cloud_setup import context
    original = context.model_assets.POLICY_PATH.read_bytes()
    policy = json.loads(original)
    policy['checkpoint_sha256'] = 'f' * 64
    altered = tmp_path/'changed-policy.json'
    altered.write_text(json.dumps(policy), encoding='utf-8')
    monkeypatch.setattr(context.model_assets, 'POLICY_PATH', altered)
    names = sorted(context.REQUIREMENTS | context.INFRA | {'src/cloud_instance/runtime.py','configs/runtime/model-assets.json'})
    def fake_git(root, *args):
        if args in (('diff','--name-only','HEAD'),('ls-files','--others','--exclude-standard')):
            return b''
        if args == ('rev-parse', 'HEAD'):
            return ('a'*40).encode()
        if args == ('ls-tree', '-r', '--name-only', 'HEAD'):
            return '\n'.join(names).encode()
        if args[0] == 'show':
            return original if args[1] == 'HEAD:configs/runtime/model-assets.json' else b'committed-placeholder'
        raise AssertionError('Unexpected git read')
    monkeypatch.setattr(context, '_git', fake_git)
    with pytest.raises(ValueError):
        context._source(tmp_path)


def test_api_transport_disables_credential_forwarding_redirects(monkeypatch):
    from urllib.request import Request
    from src.cloud_setup import transport
    observed = []
    class Opener:
        def open(self, request, timeout):
            observed.append((request, timeout))
            return 'safe-stub'
    def build(handler):
        request = Request('https://firebase.googleapis.com/v1beta1/projects/'+PROJECT,
            headers={'Authorization':'Bearer synthetic-token-only'})
        assert handler.redirect_request(request, None, 302, 'redirect', {}, 'https://evil.example.test/') is None
        return Opener()
    monkeypatch.setattr(transport, 'build_opener', build)
    request = Request('https://firebase.googleapis.com/v1beta1/projects/'+PROJECT)
    assert transport.open_google(request, 10) == 'safe-stub'
    assert observed == [(request, 10)]
