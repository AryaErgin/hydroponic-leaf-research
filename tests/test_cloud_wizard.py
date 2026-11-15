import json
import pytest
from src.cloud_setup import wizard


def test_plan_only_is_offline_and_writes_no_credentials(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(wizard,'ROOT',tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('Offline plan cannot construct an authenticated cloud client')
    monkeypatch.setattr(wizard,'CloudClient',forbidden)
    assert wizard.main(['--plan-only','--project-id','research-own-test','--project-number','123456789012',
        '--institution','Example University'])==0
    text=capsys.readouterr().out
    assert 'OFFLINE' in text and 'not a hard spending cap' in text
    plan=json.loads((tmp_path/'.cloud/dry-run-plan.json').read_text())
    assert plan['run']['min_instances']==0 and plan['run']['max_instances']==1
    assert plan['manifest']['firebase_web'] is None


def test_protected_dry_plan_rejected_before_any_client(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(wizard,'ROOT',tmp_path)
    assert wizard.main(['--plan-only','--project-id','hydroponic-leaf-research-aryae',
        '--project-number','123456789012','--institution','Example University'])==1
    assert not (tmp_path/'.cloud/dry-run-plan.json').exists()


def test_consent_is_explicit_and_never_treated_as_timeout_or_default():
    plan={'manifest':{'project_id':'research-own-test'},'run':{'max_instances':1}}
    state={}
    with pytest.raises(ValueError):wizard.approve_plan(state,plan,answer='')
    with pytest.raises(ValueError):wizard.approve_plan(state,plan,answer='yes')
    wizard.approve_plan(state,plan,answer='DEPLOY research-own-test')
    assert len(state['approved_plan_sha256'])==64


def test_failed_new_revision_rolls_back_only_recorded_own_revision():
    observed=[]
    class Client:
        def gcloud(self,args,**kwargs):observed.append(args);return {}
    manifest={'service_name':'hydroponic-leaf-research','region':'us-central1'}
    wizard.rollback(Client(),manifest,'hydroponic-leaf-research-00001-safe')
    assert observed[0][:3]==['run','services','update-traffic']
    with pytest.raises(ValueError):wizard.rollback(Client(),manifest,'foreign-service-00001-x')


@pytest.fixture
def simulated_apply(tmp_path,monkeypatch):
    from src.cloud_setup import context,provision,key_security
    from src.cloud_setup.plan import make_manifest
    from src.cloud_setup.state import save_state
    from src.local_setup import model_assets
    project='research-own-test';head='a'*40;events=[]
    manifest=make_manifest(project,'123456789012','a'*32,'Example University')
    path=tmp_path/'.cloud/instances'/project/'state.json'
    save_state(path,{'schema_version':1,'project_id':project,'installation_id':'a'*32,'manifest':manifest,
        'accepted_revision':'hydroponic-leaf-research-00001-safe',
        'provision':{'revision':'hydroponic-leaf-research-00001-safe','runtime_secret_version':'1',
            'image':'own-image-fixture','build_source_commit':head}})
    monkeypatch.setattr(wizard,'ROOT',tmp_path)
    monkeypatch.setattr(model_assets,'_read_bundle',lambda *args:{})
    monkeypatch.setattr(wizard.subprocess,'check_output',lambda *args,**kwargs:head)
    monkeypatch.setattr(context,'create_context',lambda *args,**kwargs:events.append('package') or {'source_commit':head})
    monkeypatch.setattr(wizard,'_owner_tools',lambda:events.append('operator-tools'))
    monkeypatch.setattr(wizard,'_identity_session',lambda client:events.append('login'))
    def billing(client,state):state.update(billing_account='ABCDEF-123456-ABCDEF',budget_amount='5')
    monkeypatch.setattr(wizard,'_billing',billing)
    monkeypatch.setattr(wizard,'_budget',lambda *args:None)
    monkeypatch.setattr(key_security,'restrict_firebase_key',lambda *args:{})
    class Client:
        def __init__(self,*args,**kwargs):self.project_id=project
        def approve(self,value):events.append('consent')
        def gcloud(self,args,**kwargs):
            events.append(args)
            if args[:3]==['billing','projects','describe']:return {'billingEnabled':True,'billingAccountName':'billingAccounts/ABCDEF-123456-ABCDEF'}
            return {}
    monkeypatch.setattr(wizard,'CloudClient',Client)
    class Provision:
        def __init__(self,client,manifest,state,save_state):self.manifest=manifest;self.state=state
        def preflight(self):events.append('ownership-check')
        def apply_infrastructure(self):events.append('provision');return self.manifest
        def configure_auth(self):pass
        def put_instance_config(self):self.state['config_secret_version']='1'
        def deploy(self,image):self.state['revision']='hydroponic-leaf-research-00002-bad';return {'origin':self.manifest['origin']}
    monkeypatch.setattr(provision,'Provisioner',Provision)
    return project,path,events


def test_cancelled_apply_performs_no_cloud_write_and_validates_inputs_first(simulated_apply,monkeypatch):
    project,path,events=simulated_apply
    answers=iter(['yes',''])
    monkeypatch.setattr('builtins.input',lambda *args:next(answers))
    assert wizard.main(['--project-id',project,'--model-bundle','fixture.zip'])==1
    assert events==['package','operator-tools','login','ownership-check']


def test_unhealthy_candidate_restores_traffic_and_persisted_rollback_revision(simulated_apply,monkeypatch):
    project,path,events=simulated_apply
    answers=iter(['yes','DEPLOY '+project])
    monkeypatch.setattr('builtins.input',lambda *args:next(answers))
    def unhealthy(*args):raise ValueError('Unhealthy fixture only')
    monkeypatch.setattr(wizard,'_health',unhealthy)
    assert wizard.main(['--project-id',project,'--model-bundle','fixture.zip'])==1
    restored=json.loads(path.read_text())
    assert restored['provision']['revision']=='hydroponic-leaf-research-00001-safe'
    assert any(isinstance(event,list) and event[:3]==['run','services','update-traffic'] for event in events)


def test_first_failed_revision_is_never_saved_as_future_healthy_rollback(simulated_apply,monkeypatch):
    project,path,events=simulated_apply
    state=json.loads(path.read_text());state.pop('accepted_revision');path.write_text(json.dumps(state))
    answers=iter(['yes','DEPLOY '+project])
    monkeypatch.setattr('builtins.input',lambda *args:next(answers))
    def unhealthy(*args):raise ValueError('Unhealthy fixture only')
    monkeypatch.setattr(wizard,'_health',unhealthy)
    assert wizard.main(['--project-id',project,'--model-bundle','fixture.zip'])==1
    restored=json.loads(path.read_text())
    assert 'revision' not in restored['provision'] and not restored.get('accepted_revision')
    assert restored['last_failed_revision']=='hydroponic-leaf-research-00002-bad'


def test_existing_budget_amount_must_match_the_plan():
    from src.cloud_setup.plan import make_manifest
    manifest=make_manifest('research-own-test','123456789012','a'*32,'Example University')
    state={'manifest':manifest,'billing_account':'ABCDEF-123456-ABCDEF','budget_amount':'5'}
    class Client:
        def gcloud(self,*args,**kwargs):return [{'displayName':'Hydroponic '+('a'*32),
            'budgetFilter':{'projects':['projects/123456789012']},
            'amount':{'specifiedAmount':{'units':'100','currencyCode':'USD'}},
            'thresholdRules':[{'thresholdPercent':v} for v in (.5,.9,1)]}]
    with pytest.raises(ValueError):wizard._budget(Client(),state,lambda:None)


def test_existing_unbilled_project_is_never_billing_linked_automatically():
    events=[]
    class Client:
        def gcloud(self,args,**kwargs):events.append(args);return {'billingEnabled':False}
    state={'project_id':'research-own-test','billing_account':'ABCDEF-123456-ABCDEF'}
    with pytest.raises(ValueError):wizard.ensure_billing_target(Client(),state)
    assert events==[['billing','projects','describe','research-own-test']]


def test_new_wizard_created_project_requires_only_its_selected_billing_link():
    events=[]
    class Client:
        def gcloud(self,args,**kwargs):events.append(args);return {'billingEnabled':False}
    state={'project_id':'research-own-test','project_created':True,'billing_account':'ABCDEF-123456-ABCDEF'}
    wizard.ensure_billing_target(Client(),state)
    assert events[-1]==['billing','projects','link','research-own-test','--billing-account=ABCDEF-123456-ABCDEF']
