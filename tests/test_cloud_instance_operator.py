"""IAM operator token stays in memory; constrained account lookup, no config API."""
import importlib
import json
from types import SimpleNamespace
import pytest
from test_cloud_instance import manifest,clean_environment


def test_operator_lookup_uses_exact_project_safe_fields_and_no_redirect(monkeypatch):
    operator=importlib.import_module('src.cloud_instance.operator')
    requests=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return json.dumps(dict(users=[dict(localId='exact-uid',email='owner@example.test',emailVerified=True,disabled=False)])).encode()
    def open_request(request,timeout):
        requests.append(request);return Response()
    monkeypatch.setattr(operator,'_open',open_request)
    lookup=operator.OperatorIdentityLookup(operator.CloudInstanceConfig.from_mapping(manifest()),'SECRET_OPERATOR_TOKEN')
    result=lookup.lookup_owner('exact-uid','owner@example.test')
    assert result['uid']=='exact-uid' and result['email_verified'] is True
    request=requests[0]
    assert '/projects/developer-research-123/accounts:lookup?' in request.full_url
    assert 'fields=users%28localId%2Cemail%2CemailVerified%2Cdisabled%29' in request.full_url
    assert json.loads(request.data)==dict(localId=['exact-uid'])
    assert request.get_header('Authorization')=='Bearer SECRET_OPERATOR_TOKEN'
    assert 'SECRET_OPERATOR_TOKEN' not in repr(lookup)
    assert operator._NoRedirect().redirect_request(None,None,302,'redirect',{},'https://foreign.example') is None


def test_operator_refuses_disabled_ambiguous_and_mismatched_lookup(monkeypatch):
    operator=importlib.import_module('src.cloud_instance.operator')
    lookup=operator.OperatorIdentityLookup(operator.CloudInstanceConfig.from_mapping(manifest()),'memory-token')
    for users in [[],[{},{}],[dict(localId='different',email='owner@example.test',emailVerified=True)],
        [dict(localId='uid',email='owner@example.test',emailVerified=False)],
        [dict(localId='uid',email='owner@example.test',emailVerified=True,disabled=True)]]:
        monkeypatch.setattr(lookup,'_lookup',lambda **kwargs:users)
        with pytest.raises(ValueError):lookup.lookup_owner('uid','owner@example.test')
