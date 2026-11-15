import json
import subprocess
import pytest
from src.cloud_setup.transport import CloudClient, CloudError, safe_environment

PROJECT='research-install-test'

def test_protected_project_rejected_before_process():
    with pytest.raises(ValueError):CloudClient('hydroponic-leaf-research-aryae','hlr-own-test',executable='gcloud')

def test_explicit_project_and_configuration_and_no_ambient_credentials(monkeypatch):
    observed=[]
    def run(args,**kwargs):
        observed.append((args,kwargs));return subprocess.CompletedProcess(args,0,b'{}',b'')
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS','private-file')
    monkeypatch.setenv('CLOUDSDK_AUTH_ACCESS_TOKEN','private-token')
    client=CloudClient(PROJECT,'hlr-own-test',executable='gcloud',runner=run)
    assert client.gcloud(['projects','describe',PROJECT],json_output=True)=={}
    args,kwargs=observed[0]
    assert '--project='+PROJECT in args and '--configuration=hlr-own-test' in args
    assert 'GOOGLE_APPLICATION_CREDENTIALS' not in kwargs['env']
    assert 'CLOUDSDK_AUTH_ACCESS_TOKEN' not in kwargs['env']

def test_command_target_overrides_and_flags_files_rejected():
    client=CloudClient(PROJECT,'hlr-own-test',executable='gcloud')
    for args in [['run','deploy','app','--project=other-project'],['--flags-file=private.yaml'],
                 ['projects','describe','other-project'],['--configuration=hydroponic-research']]:
        with pytest.raises(ValueError):client.gcloud(args)

def test_failure_does_not_echo_provider_secret_output(capsys):
    private_value='private-fixture-value-never-echoed'
    def run(args,**kwargs):return subprocess.CompletedProcess(args,1,private_value.encode(),private_value.encode())
    client=CloudClient(PROJECT,'hlr-own-test',executable='gcloud',runner=run)
    with pytest.raises(CloudError) as error:client.gcloud(['run','services','list'])
    assert private_value not in str(error.value)
    assert private_value not in capsys.readouterr().out

def test_auth_config_requires_narrow_response_projection_before_token():
    client=CloudClient(PROJECT,'hlr-own-test',executable='gcloud')
    base='https://identitytoolkit.googleapis.com/admin/v2/projects/'+PROJECT+'/config'
    for url in [base,base+'?fields=*',base+'?fields=signIn',base+'?fields=signIn.hashConfig',
                base.replace(PROJECT,'hydroponic-leaf-research-aryae')+'?fields=authorizedDomains']:
        with pytest.raises(ValueError):client.api('GET',url)

def test_state_and_consent_are_bound_to_immutable_plan(tmp_path):
    from src.cloud_setup.state import save_state,load_state,consent_digest,require_consent
    path=tmp_path/'state.json';state={'project_id':PROJECT,'installation_id':'a'*32,'plan':{'max':1},'completed':[]}
    save_state(path,state);assert load_state(path)==state
    state['approved_plan_sha256']=consent_digest(state['plan'])
    require_consent(state,state['plan'])
    with pytest.raises(ValueError):require_consent(state,{'max':5})

def test_resource_and_operation_uris_cannot_escape_project():
    client=CloudClient(PROJECT,'hlr-own-test',executable='gcloud')
    for url in ['https://evil.test/projects/'+PROJECT,
                'https://firebase.googleapis.com/v1beta1/projects/'+PROJECT+'/../other-project',
                'https://firebase.googleapis.com/v1beta1/projects/other-project',
                'https://identitytoolkit.googleapis.com/admin/v2/projects/'+PROJECT+'/config?fields=signIn.email&fields=*']:
        with pytest.raises(ValueError):client.api('GET',url)
