"""Conservative diagnostic output; never serialize an administrative response.

The configuration summary is deliberately an allowlist. Recursive redaction is
an additional guard for bounded local evidence, not permission to log raw bodies.
"""
import re
from urllib.parse import urlsplit, urlunsplit


_SECRET_KEYS=frozenset(('hashconfig','signerkey','saltseparator','password','passwordhash',
    'apikey','accesstoken','refreshtoken','idtoken','oobcode','secret','clientsecret',
    'authorization','cookie','setcookie','credential','credentials','privatekey',
    'token','resettoken','verificationcode','csrf','csrftoken'))


def safe_error_summary(error):
    """Never format provider/CLI exception text, even for unexpected classes."""
    categories={RuntimeError:'RuntimeError',AssertionError:'AssertionError',
                ValueError:'ValueError',TimeoutError:'TimeoutError',PermissionError:'PermissionError'}
    return dict(error='operation_failed',category=categories.get(type(error),'UnexpectedError'),
                details_suppressed=True)


def redact(value):
    if isinstance(value,dict):
        return {key:('[REDACTED]' if re.sub('[^a-z0-9]','',str(key).lower()) in _SECRET_KEYS
                     else redact(item)) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [redact(item) for item in value]
    if isinstance(value,str):
        if re.search(r'(?i)\bBearer\s+\S+',value) or re.search(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',value):
            return '[REDACTED]'
        if value.startswith(('https://','http://')):
            parsed=urlsplit(value)
            if parsed.username or parsed.password:return '[REDACTED]'
            return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'',''))
    return value


def auth_config_summary(config):
    """Fixed safe schema: no raw strings, account data, hashes or key material."""
    signin=config.get('signIn',{})
    email=signin.get('email',{})
    privacy=config.get('emailPrivacyConfig',{})
    return dict(email_password_enabled=email.get('enabled') is True,
                password_required=email.get('passwordRequired') is True,
                email_enumeration_protection=privacy.get('enableImprovedEmailPrivacy') is True,
                hash_parameters_present=bool(signin.get('hashConfig')),
                authorized_domain_count=len(config.get('authorizedDomains',[])))
