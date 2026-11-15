"""Billable Cloud Build jobs survive local interruption without blind retries."""
import copy
import hashlib
import json

import pytest

from src.cloud_setup.plan import make_manifest
from src.cloud_setup.provision import Provisioner,ManualStepRequired


PROJECT='independent-build-lab'
NUMBER='123456789012'
BUILD_ID='11111111-1111-4111-8111-111111111111'
REPOSITORY='us-central1-docker.pkg.dev/'+PROJECT+'/hlr-images-aaaaaaaaaaaaaaaa/application'
RECEIPT=dict(schema_version=1,source_commit='b'*40,files={'src/example.py':{'sha256':'c'*64,'bytes':12}})


def sha(receipt):
    return hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def metadata(receipt,status='WORKING'):
    digest=sha(receipt);image=REPOSITORY+':'+digest[:24]
    return dict(id=BUILD_ID,projectId=PROJECT,
        name='projects/'+PROJECT+'/locations/us-central1/builds/'+BUILD_ID,
        status=status,images=[image],substitutions={'_IMAGE':image,'_CONTEXT_SHA256':digest},
        serviceAccount='projects/'+PROJECT+'/serviceAccounts/hlr-build-aaaaaaaaaaaaaaaa@'+PROJECT+'.iam.gserviceaccount.com',
        source={'storageSource':{'bucket':PROJECT+'-hlr-build','object':'source/owned-context.tgz'}},
        timeout='1200s',queueTtl='300s',options={'logging':'CLOUD_LOGGING_ONLY'},
        results={'images':[{'name':image,'digest':'sha256:'+'f'*64}]})


class BuildClient:
    project_id=PROJECT
    def __init__(self,receipt=RECEIPT,descriptions=None,submit_error=None):
        self.receipt=receipt;self.calls=[];self.descriptions=list(descriptions or [metadata(receipt,'SUCCESS')]);self.submit_error=submit_error
    def gcloud(self,args,*,json_output=False,input_bytes=None):
        self.calls.append(list(args))
        if args[:2]==['builds','submit']:
            if self.submit_error:raise self.submit_error
            return metadata(self.receipt,'QUEUED')
        assert args[:2]==['builds','describe']
        result=self.descriptions.pop(0) if len(self.descriptions)>1 else self.descriptions[0]
        if isinstance(result,BaseException):raise result
        return copy.deepcopy(result)


def provision(client,state=None,save_state=None):
    manifest=make_manifest(PROJECT,NUMBER,'a'*32,'Build University')
    p=Provisioner(client,manifest,state=state,save_state=save_state);p._infrastructure_ready=True
    return p


@pytest.fixture
def trusted_context(monkeypatch,tmp_path):
    from src.cloud_setup import context
    monkeypatch.setattr(context,'verify_context',lambda path:copy.deepcopy(RECEIPT))
    # Existing implementation reads the receipt bytes directly; this also makes
    # the initial failing run exercise the real build path instead of file errors.
    (tmp_path/'context-manifest.json').write_text(json.dumps(RECEIPT),encoding='utf8')
    return tmp_path


def test_async_submission_records_own_job_before_interrupted_poll(trusted_context):
    state={};snapshots=[];client=BuildClient(descriptions=[KeyboardInterrupt()])
    p=provision(client,state,lambda:snapshots.append(copy.deepcopy(state)))
    with pytest.raises(KeyboardInterrupt):p.submit_build(trusted_context)
    assert state['build_id']==BUILD_ID and state['build_receipt_sha256']==sha(RECEIPT)
    assert state['build_status']=='QUEUED'
    assert any(s.get('build_id')==BUILD_ID for s in snapshots)
    assert '--async' in client.calls[0]
    assert client.calls[1][:3]==['builds','describe',BUILD_ID]


def test_same_receipt_resumes_recorded_job_without_second_submission(trusted_context):
    state={};first=provision(BuildClient(descriptions=[KeyboardInterrupt()]),state)
    with pytest.raises(KeyboardInterrupt):first.submit_build(trusted_context)
    client=BuildClient();result=provision(client,state).submit_build(trusted_context)
    assert result==REPOSITORY+'@sha256:'+'f'*64
    assert not any(call[:2]==['builds','submit'] for call in client.calls)
    assert state['build_status']=='SUCCESS'


def test_changed_source_refuses_new_job_while_previous_receipt_is_working(monkeypatch,trusted_context):
    state={};first=provision(BuildClient(descriptions=[KeyboardInterrupt()]),state)
    with pytest.raises(KeyboardInterrupt):first.submit_build(trusted_context)
    from src.cloud_setup import context
    changed=dict(RECEIPT,source_commit='d'*40)
    monkeypatch.setattr(context,'verify_context',lambda path:changed)
    client=BuildClient(descriptions=[metadata(RECEIPT,'WORKING')])
    with pytest.raises(ValueError):provision(client,state).submit_build(trusted_context)
    assert [call[:2] for call in client.calls]==[['builds','describe']]
    assert state['build_id']==BUILD_ID and state['build_receipt_sha256']==sha(RECEIPT)


def test_submission_outcome_unknown_never_blindly_retries(trusted_context):
    state={};first=provision(BuildClient(submit_error=KeyboardInterrupt()),state)
    with pytest.raises(KeyboardInterrupt):first.submit_build(trusted_context)
    assert 'build_submission' in state and 'build_id' not in state
    client=BuildClient()
    with pytest.raises(ManualStepRequired):provision(client,state).submit_build(trusted_context)
    assert client.calls==[]


@pytest.mark.parametrize('change',[
    {'projectId':'foreign-build-project'},
    {'substitutions':{'_IMAGE':REPOSITORY+':'+sha(RECEIPT)[:24],'_CONTEXT_SHA256':'0'*64}},
    {'images':['us-central1-docker.pkg.dev/foreign/repo/image:tag']},
    {'source':{'storageSource':{'bucket':'foreign-scientific-bucket','object':'source/context.tgz'}}},
])
def test_unbound_build_metadata_never_promotes_image(trusted_context,change):
    client=BuildClient(descriptions=[dict(metadata(RECEIPT,'SUCCESS'),**change)])
    state={}
    with pytest.raises(ValueError):provision(client,state).submit_build(trusted_context)
    assert 'image' not in state


def test_completed_failed_same_receipt_does_not_automatically_buy_retry(trusted_context):
    state={};client=BuildClient(descriptions=[metadata(RECEIPT,'FAILURE')])
    with pytest.raises(ValueError):provision(client,state).submit_build(trusted_context)
    second=BuildClient(descriptions=[metadata(RECEIPT,'FAILURE')])
    with pytest.raises(ValueError):provision(second,state).submit_build(trusted_context)
    assert [call[:2] for call in second.calls]==[['builds','describe']]


def test_changed_receipt_can_start_once_after_previous_job_is_terminal(monkeypatch,trusted_context):
    state={};first=provision(BuildClient(descriptions=[KeyboardInterrupt()]),state)
    with pytest.raises(KeyboardInterrupt):first.submit_build(trusted_context)
    from src.cloud_setup import context
    changed=dict(RECEIPT,source_commit='d'*40)
    monkeypatch.setattr(context,'verify_context',lambda path:changed)
    new_id='22222222-2222-4222-8222-222222222222'
    new_metadata=metadata(changed,'SUCCESS')
    new_metadata.update(id=new_id,name='projects/'+PROJECT+'/locations/us-central1/builds/'+new_id)
    class NewBuildClient(BuildClient):
        def gcloud(self,args,**kwargs):
            response=super().gcloud(args,**kwargs)
            if args[:2]==['builds','submit']:
                response.update(id=new_id,name=new_metadata['name'])
            return response
    client=NewBuildClient(receipt=changed,descriptions=[metadata(RECEIPT,'CANCELLED'),new_metadata])
    result=provision(client,state).submit_build(trusted_context)
    assert result==REPOSITORY+'@sha256:'+'f'*64
    assert [call[:2] for call in client.calls]==[['builds','describe'],['builds','submit'],['builds','describe']]
    assert state['build_id']==new_id and state['build_receipt_sha256']==sha(changed)
    assert state['build_history']==[dict(id=BUILD_ID,receipt_sha256=sha(RECEIPT),status='CANCELLED')]


def test_poll_deadline_preserves_job_instead_of_resubmitting(monkeypatch,trusted_context):
    import src.cloud_setup.provision as module
    ticks=iter([0,2000]);monkeypatch.setattr(module.time,'monotonic',lambda:next(ticks))
    client=BuildClient(descriptions=[metadata(RECEIPT,'WORKING')]);state={}
    with pytest.raises(ValueError):provision(client,state).submit_build(trusted_context)
    assert state['build_id']==BUILD_ID and state['build_status']=='WORKING'
    assert sum(call[:2]==['builds','submit'] for call in client.calls)==1


def test_verified_failed_job_exposes_one_explicit_costed_retry(trusted_context):
    from src.cloud_setup.provision import BuildRetryRequired
    state={};first=BuildClient(descriptions=[metadata(RECEIPT,'FAILURE')])
    with pytest.raises(BuildRetryRequired) as failure:
        provision(first,state).submit_build(trusted_context)
    assert failure.value.build_id==BUILD_ID and failure.value.status=='FAILURE'
    new_id='33333333-3333-4333-8333-333333333333'
    successful=metadata(RECEIPT,'SUCCESS')
    successful.update(id=new_id,name='projects/'+PROJECT+'/locations/us-central1/builds/'+new_id)
    class RetryClient(BuildClient):
        def gcloud(self,args,**kwargs):
            response=super().gcloud(args,**kwargs)
            if args[:2]==['builds','submit']:response.update(id=new_id,name=successful['name'])
            return response
    second=RetryClient(descriptions=[metadata(RECEIPT,'FAILURE'),successful])
    image=provision(second,state).submit_build(trusted_context,retry_failed=True)
    assert image==REPOSITORY+'@sha256:'+'f'*64
    assert sum(call[:2]==['builds','submit'] for call in second.calls)==1
    assert state['build_history']==[dict(id=BUILD_ID,receipt_sha256=sha(RECEIPT),status='FAILURE')]


@pytest.mark.parametrize('status',['QUEUED','WORKING','SUCCESS'])
def test_explicit_failed_retry_flag_never_starts_job_for_nonfailed_status(trusted_context,status):
    state={};first=provision(BuildClient(descriptions=[KeyboardInterrupt()]),state)
    with pytest.raises(KeyboardInterrupt):first.submit_build(trusted_context)
    client=BuildClient(descriptions=[metadata(RECEIPT,status)])
    with pytest.raises(ValueError):provision(client,state).submit_build(trusted_context,retry_failed=True)
    assert [call[:2] for call in client.calls]==[['builds','describe']]


def test_failed_retry_flag_does_not_override_missing_submission_id(trusted_context):
    state={'build_submission':{'receipt_sha256':sha(RECEIPT)},'intents':['build'],'completed':[]}
    client=BuildClient()
    with pytest.raises(ManualStepRequired):provision(client,state).submit_build(trusted_context,retry_failed=True)
    assert client.calls==[]
