"""Constrain this installation's public Firebase key to its two Auth APIs.

Firebase browser keys identify projects; they are not server credentials or an
authorization boundary. IAM, browser-deny database rules and backend sessions
remain essential. No HTTP-referrer restriction is added: the application keeps
its privacy-preserving no-referrer policy. Existing custom client constraints
are never removed or silently weakened; setup asks for deliberate manual review.
"""
import copy
import re
import time
from urllib.parse import urlencode

from .plan import validate_manifest
from .provision import ManualStepRequired

AUTH_SERVICES = ('identitytoolkit.googleapis.com','securetoken.googleapis.com')
KEY_FIELDS = 'name,restrictions,etag,serviceAccountEmail,deleteTime'
OPERATION_FIELDS = 'name,done,error(code)'
CLIENT_FIELDS = frozenset(('browserKeyRestrictions','serverKeyRestrictions',
                           'androidKeyRestrictions','iosKeyRestrictions'))


def _metadata(value, expected_name):
    if (not isinstance(value,dict) or value.get('name') != expected_name or
        not isinstance(value.get('etag'),str) or not value['etag'] or
        value.get('deleteTime') or value.get('serviceAccountEmail')):
        raise ValueError('API key metadata is not an active ordinary key of this selected project.')
    restrictions = value.get('restrictions',{})
    if not isinstance(restrictions,dict): raise ValueError('API key restrictions were not an object.')
    if any(field not in CLIENT_FIELDS|{'apiTargets'} for field in restrictions):
        raise ManualStepRequired('This Firebase browser key has unfamiliar restrictions. Review this own project API key in Google Cloud console; setup will not weaken its protections.')
    if any(restrictions.get(field) for field in CLIENT_FIELDS):
        raise ManualStepRequired('This Firebase browser key already has browser/IP/mobile application restrictions. Review them in this project Google Cloud API Keys console. The app keeps Referrer-Policy: no-referrer; do not remove existing restrictions silently. Use a separate dedicated Firebase web key configured for this application, then resume.')
    return copy.deepcopy(restrictions)


def _targets(restrictions):
    targets = restrictions.get('apiTargets',[])
    if not isinstance(targets,list): raise ValueError('API key service restrictions were not a list.')
    services=[]
    for target in targets:
        if (not isinstance(target,dict) or set(target)-{'service','methods'} or
            not isinstance(target.get('service'),str)):
            raise ValueError('Unexpected API key target schema.')
        service=target['service'].lower()
        if target.get('methods'):
            raise ManualStepRequired('This Firebase browser key has custom method restrictions. Review the dedicated key in this project Google Cloud console; setup will not broaden existing method permissions.')
        services.append(service)
    if len(set(services)) != len(services): raise ValueError('API key has duplicate target restrictions.')
    return set(services)


def restrict_firebase_key(client, manifest):
    """Look up, narrow, await and independently re-read the exact own-project key.

    Client transport enforces typed plan consent before PATCH. Key lookup and
    metadata reads use strict partial responses; operation responses deliberately
    exclude `response`, which can otherwise contain the browser key string.
    """
    manifest = validate_manifest(manifest,require_runtime=True)
    if (getattr(client,'project_id',None) != manifest['project_id'] or
        getattr(client,'project_number',manifest['project_number']) != manifest['project_number']):
        raise ValueError('API key transport and own-project manifest disagree; no lookup was attempted.')
    client.project_number = manifest['project_number']
    client.expected_browser_key = manifest['firebase_web']['apiKey']

    def request(method,path,body=None,*,query=None):
        if client.project_id != manifest['project_id'] or client.project_number != manifest['project_number']:
            raise ValueError('API key transport target changed during setup.')
        url='https://apikeys.googleapis.com/v2/'+path
        if query:url+='?'+urlencode(query)
        return client.api(method,url,body=body)

    lookup = request('GET','keys:lookupKey',query={
        'keyString':client.expected_browser_key,'fields':'name,parent'})
    parent='projects/'+manifest['project_number']+'/locations/global'
    name=lookup.get('name','') if isinstance(lookup,dict) else ''
    if (not isinstance(lookup,dict) or lookup.get('parent') != parent or not isinstance(name,str) or
        not re.fullmatch(re.escape(parent)+r'/keys/[A-Za-z0-9][A-Za-z0-9_-]{0,127}',name)):
        raise ValueError('Firebase key lookup did not resolve to this numeric project own global key.')
    current=request('GET',name,query={'fields':KEY_FIELDS})
    restrictions=_metadata(current,name)
    services=_targets(restrictions)
    required=set(AUTH_SERVICES)
    if services and not required.issubset(services):
        raise ManualStepRequired('This dedicated Firebase key lacks an Auth API in its existing restrictions. Review the key deliberately in this project Google Cloud console; setup will not broaden an existing service allowlist.')
    if services != required:
        updated=copy.deepcopy(restrictions)
        updated['apiTargets']=[{'service':service} for service in AUTH_SERVICES]
        operation=request('PATCH',name,{'name':name,'etag':current['etag'],'restrictions':updated},
            query={'updateMask':'restrictions','fields':OPERATION_FIELDS})
        receipt=operation.get('name','') if isinstance(operation,dict) else ''
        if not re.fullmatch(r'operations/[A-Za-z0-9][A-Za-z0-9_.-]{0,191}',receipt):
            raise ValueError('API key update did not return a canonical own-request operation receipt.')
        for attempt in range(13):
            if not isinstance(operation,dict) or operation.get('name') != receipt:
                raise ValueError('API key operation response changed its receipt identity.')
            if operation.get('error'):
                raise ValueError('API key restriction update failed; inspect this own project API Keys console and resume.')
            if operation.get('done') is True:break
            if attempt == 12:
                raise ValueError('API key restriction update is still pending. Resources were preserved; resume setup later.')
            if attempt:time.sleep(10)
            operation=request('GET',receipt,query={'fields':OPERATION_FIELDS})
        final=request('GET',name,query={'fields':KEY_FIELDS})
        final_restrictions=_metadata(final,name)
        if _targets(final_restrictions) != required:
            raise ValueError('Final Firebase key restrictions did not match the two required Auth APIs.')
        preserved={k:v for k,v in restrictions.items() if k!='apiTargets'}
        if {k:v for k,v in final_restrictions.items() if k!='apiTargets'} != preserved:
            raise ValueError('Firebase key client restrictions changed during setup; review this project console.')
    return dict(name=name,services=list(AUTH_SERVICES),status='AUTH_APIS_ONLY',
                client_restrictions='none_added_privacy_preserved')
