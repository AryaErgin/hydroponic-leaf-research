"""IAM-authenticated owner setup, used only by the interactive deployment wizard.

The caller supplies its own short-lived OAuth token. No credential file, API key,
password, Firebase hash-signing config, email action or public route is involved.
"""
from dataclasses import dataclass,field
import json
from urllib.parse import urlencode
from urllib.request import Request,HTTPRedirectHandler,build_opener

from ..auth_preview.mail import normalize_email
from .config import CloudInstanceConfig


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _open(request,timeout):return build_opener(_NoRedirect()).open(request,timeout=timeout)


@dataclass
class OperatorIdentityLookup:
    instance: CloudInstanceConfig
    token: str=field(repr=False)

    def __post_init__(self):
        self.instance.guard()
        if not self.token or '\n' in self.token or '\r' in self.token:raise ValueError('A short-lived IAM operator token is required')

    def _lookup(self,**selector):
        self.instance.guard()
        query=urlencode(dict(fields='users(localId,email,emailVerified,disabled)'))
        url=f'https://identitytoolkit.googleapis.com/v1/projects/{self.instance.project_id}/accounts:lookup?{query}'
        request=Request(url,data=json.dumps(selector).encode(),method='POST',
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with _open(request,timeout=30) as response:
                content=response.read(65537)
            if len(content)>65536:raise ValueError('Account lookup response exceeded its bound')
            result=json.loads(content)
        except Exception:
            # Never stringify provider response, request, headers, or token.
            raise ValueError('IAM account lookup failed; check operator access and registered identity') from None
        users=result.get('users',[])
        if not isinstance(users,list):raise ValueError('Invalid Firebase account lookup response')
        return users

    def lookup_owner(self,uid,email):
        email=normalize_email(email)
        users=self._lookup(localId=[uid]) if uid else self._lookup(email=[email])
        if len(users)!=1:raise ValueError('One exact registered Firebase identity is required')
        user=users[0]
        if (not isinstance(user,dict) or not user.get('localId') or uid and user['localId']!=uid or
            normalize_email(user.get('email',''))!=email or user.get('emailVerified') is not True or
            user.get('disabled',False) is not False):
            raise ValueError('The exact Firebase identity must be enabled and email verified')
        return dict(uid=user['localId'],email=email,email_verified=True,disabled=False)


def bootstrap_from_operator(manifest,oauth_token,email,uid=None):
    """Called after human registration and verification on this installation."""
    from google.oauth2.credentials import Credentials
    from google.cloud import firestore
    from ..auth_preview.firestore_database import FirestoreDatabase
    from .service import IndependentService,bootstrap_owner
    instance=manifest if isinstance(manifest,CloudInstanceConfig) else CloudInstanceConfig.from_mapping(manifest)
    instance.guard();config=instance.auth_config()
    lookup=OperatorIdentityLookup(instance,oauth_token)
    identity=lookup.lookup_owner(uid,email)
    lookup.config=config
    client=firestore.Client(project=instance.project_id,database=instance.firestore_database,
        credentials=Credentials(token=oauth_token))
    database=None
    try:
        database=FirestoreDatabase(config,credential_authority='firebase-v1',client=client)
        service=IndependentService(config,lookup,database=database)
        return bootstrap_owner(service,identity)
    finally:
        client.close()
        lookup.token=''
