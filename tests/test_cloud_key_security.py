"""Provider-bound browser key restrictions, without credentials or cloud calls."""
import copy
from urllib.parse import parse_qs,urlsplit

import pytest

from src.cloud_setup.plan import make_manifest


PROJECT='independent-key-lab'
NUMBER='123456789012'
NAME='projects/'+NUMBER+'/locations/global/keys/a1111111-1111-4111-8111-111111111111'
SERVICES=['identitytoolkit.googleapis.com','securetoken.googleapis.com']


def manifest():
    result=make_manifest(PROJECT,NUMBER,'a'*32,'Independent University')
    result['firebase_web']=dict(projectId=PROJECT,authDomain=PROJECT+'.firebaseapp.com',
        appId='1:'+NUMBER+':web:abcdef',apiKey='public-browser-key')
    return result


class Client:
    project_id=PROJECT
    def __init__(self,*,lookup=None,restrictions=None,operation=None,final=None):
        self.calls=[]
        self.lookup=lookup or dict(name=NAME,parent='projects/'+NUMBER+'/locations/global')
        self.metadata=dict(name=NAME,etag='etag-before',restrictions=restrictions or {})
        self.operation=operation or dict(name='operations/akmf.own-operation',done=False)
        self.final=final

    def api(self,method,url,body=None):
        self.calls.append((method,url,copy.deepcopy(body)))
        if 'lookupKey' in url:return self.lookup
        if '/operations/' in url:return dict(name=self.operation['name'],done=True)
        if method=='PATCH':
            self.metadata=copy.deepcopy(body);self.metadata['etag']='etag-after'
            return self.operation
        if self.final is not None and any(call[0]=='PATCH' for call in self.calls):return self.final
        return copy.deepcopy(self.metadata)


def test_key_restriction_helper_is_available():
    import importlib.util
    assert importlib.util.find_spec('src.cloud_setup.key_security') is not None


def test_only_own_public_browser_key_and_two_auth_services_are_used():
    from src.cloud_setup.key_security import restrict_firebase_key
    client=Client();result=restrict_firebase_key(client,manifest())
    assert client.expected_browser_key=='public-browser-key'
    assert result==dict(name=NAME,services=SERVICES,status='AUTH_APIS_ONLY',client_restrictions='none_added_privacy_preserved')
    method,url,body=client.calls[0]
    assert method=='GET' and body is None
    assert parse_qs(urlsplit(url).query)=={'keyString':['public-browser-key'],'fields':['name,parent']}
    patch=next(call for call in client.calls if call[0]=='PATCH')
    assert patch[2]==dict(name=NAME,etag='etag-before',restrictions={'apiTargets':[{'service':s} for s in SERVICES]})
    assert parse_qs(urlsplit(patch[1]).query)['updateMask']==['restrictions']
    for _,url,_ in client.calls[1:]:
        fields=parse_qs(urlsplit(url).query)['fields'][0]
        assert 'keyString' not in fields and 'response' not in fields
    assert 'public-browser-key' not in str(result)


@pytest.mark.parametrize('lookup',[
    dict(name=NAME.replace(NUMBER,'999999999999'),parent='projects/999999999999/locations/global'),
    dict(name=NAME,parent='projects/999999999999/locations/global'),
    dict(name='projects/'+NUMBER+'/locations/global/keys/../other',parent='projects/'+NUMBER+'/locations/global'),
    dict(name='',parent='projects/'+NUMBER+'/locations/global'),
])
def test_foreign_or_noncanonical_lookup_never_reaches_key_get_or_patch(lookup):
    from src.cloud_setup.key_security import restrict_firebase_key
    client=Client(lookup=lookup)
    with pytest.raises(ValueError):restrict_firebase_key(client,manifest())
    assert len(client.calls)==1


@pytest.mark.parametrize('restriction',[
    {'browserKeyRestrictions':{'allowedReferrers':['https://elsewhere.example/*']}},
    {'serverKeyRestrictions':{'allowedIps':['203.0.113.1']}},
    {'androidKeyRestrictions':{'allowedApplications':[{'packageName':'example.app'}]}},
    {'iosKeyRestrictions':{'allowedBundleIds':['example.app']}},
])
def test_existing_client_constraints_require_manual_review_without_weakening(restriction):
    from src.cloud_setup.key_security import restrict_firebase_key
    from src.cloud_setup.provision import ManualStepRequired
    client=Client(restrictions=restriction)
    with pytest.raises(ManualStepRequired):restrict_firebase_key(client,manifest())
    assert [call[0] for call in client.calls]==['GET','GET']


def test_already_correct_key_is_verified_without_patch():
    from src.cloud_setup.key_security import restrict_firebase_key
    client=Client(restrictions={'apiTargets':[{'service':s} for s in reversed(SERVICES)]})
    restrict_firebase_key(client,manifest())
    assert [call[0] for call in client.calls]==['GET','GET']


def test_default_extra_firebase_targets_are_narrowed_preserving_empty_client_config():
    from src.cloud_setup.key_security import restrict_firebase_key
    targets=[{'service':s} for s in SERVICES+['firebaserules.googleapis.com']]
    client=Client(restrictions={'apiTargets':targets,'browserKeyRestrictions':{}})
    restrict_firebase_key(client,manifest())
    patch=next(call for call in client.calls if call[0]=='PATCH')
    assert patch[2]['restrictions']['browserKeyRestrictions']=={}
    assert patch[2]['restrictions']['apiTargets']==[{'service':s} for s in SERVICES]


@pytest.mark.parametrize('final',[
    dict(name=NAME,etag='after',restrictions={}),
    dict(name=NAME.replace(NUMBER,'999999999999'),etag='after',restrictions={'apiTargets':[{'service':s} for s in SERVICES]}),
    dict(name=NAME,etag='after',restrictions={'apiTargets':[{'service':s} for s in SERVICES+['bigquery.googleapis.com']]}),
])
def test_unverified_final_restrictions_are_not_reported_as_complete(final):
    from src.cloud_setup.key_security import restrict_firebase_key
    with pytest.raises(ValueError):restrict_firebase_key(Client(final=final),manifest())


def test_operation_name_must_be_receipt_bound_and_canonical_before_poll():
    from src.cloud_setup.key_security import restrict_firebase_key
    client=Client(operation=dict(name='operations/../foreign',done=False))
    with pytest.raises(ValueError):restrict_firebase_key(client,manifest())
    assert not any('/operations/' in call[1] for call in client.calls)


def test_wrong_transport_project_stops_before_lookup():
    from src.cloud_setup.key_security import restrict_firebase_key
    client=Client();client.project_id='other-lab-project'
    with pytest.raises(ValueError):restrict_firebase_key(client,manifest())
    assert client.calls==[]


def test_custom_auth_method_allowlist_is_not_silently_broadened():
    from src.cloud_setup.key_security import restrict_firebase_key
    from src.cloud_setup.provision import ManualStepRequired
    restrictions={'apiTargets':[{'service':SERVICES[0],'methods':['restricted.Method']},
                                {'service':SERVICES[1]}]}
    client=Client(restrictions=restrictions)
    with pytest.raises(ManualStepRequired):restrict_firebase_key(client,manifest())
    assert [c[0] for c in client.calls]==['GET','GET']


def test_polled_operation_must_match_exact_update_receipt():
    from src.cloud_setup.key_security import restrict_firebase_key
    class SwappedReceipt(Client):
        def api(self,method,url,body=None):
            result=super().api(method,url,body)
            if '/operations/' in url:return {'name':'operations/akmf.foreign-receipt','done':True}
            return result
    client=SwappedReceipt()
    with pytest.raises(ValueError):restrict_firebase_key(client,manifest())
    assert client.calls[-1][0]=='GET' and '/operations/akmf.own-operation?' in client.calls[-1][1]


def test_operation_error_never_reports_restrictions_success():
    from src.cloud_setup.key_security import restrict_firebase_key
    client=Client(operation={'name':'operations/akmf.own-operation','done':True,'error':{'code':7}})
    with pytest.raises(ValueError):restrict_firebase_key(client,manifest())
    assert [call[0] for call in client.calls]==['GET','GET','PATCH']


def test_email_privacy_is_enabled_by_narrow_auth_update():
    from src.cloud_setup.provision import Provisioner
    from tests.test_cloud_plan import FreshProjectClient,fixture
    client=FreshProjectClient();p=Provisioner(client,fixture())
    p.configure_auth()
    patch=next(call for call in client.calls if call[0]=='PATCH')
    assert patch[2]['emailPrivacyConfig']=={'enableImprovedEmailPrivacy':True}
    assert 'emailPrivacyConfig.enableImprovedEmailPrivacy' in parse_qs(urlsplit(patch[1]).query)['updateMask'][0]

