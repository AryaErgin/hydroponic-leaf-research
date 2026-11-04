import json
import pytest
from src.firebase_preview.diagnostics import auth_config_summary,redact


@pytest.mark.parametrize('key',['hashConfig','signer_key','salt-separator','Authorization',
    'Set-Cookie','refresh_token','idToken','oobCode','private_key','password_hash','csrf_token'])
def test_sensitive_keys_are_redacted_recursively(key):
    result=redact({'items':[{'nested':{key:'SYNTHETIC_SENSITIVE_VALUE'}}]})
    assert 'SYNTHETIC_SENSITIVE_VALUE' not in json.dumps(result)
    assert '[REDACTED]' in json.dumps(result)


def test_auth_config_summary_cannot_include_unknown_provider_fields():
    config={'signIn':{'email':{'enabled':True,'passwordRequired':True},
            'hashConfig':{'signerKey':'SYNTHETIC_SENSITIVE_VALUE'}},
            'emailPrivacyConfig':{'enableImprovedEmailPrivacy':True},
            'futureField':'SYNTHETIC_SENSITIVE_VALUE','authorizedDomains':['example.test']}
    assert auth_config_summary(config)==dict(email_password_enabled=True,password_required=True,
        email_enumeration_protection=True,hash_parameters_present=True,authorized_domain_count=1)
    assert 'SYNTHETIC_SENSITIVE_VALUE' not in json.dumps(auth_config_summary(config))


@pytest.mark.parametrize('text',['Bearer SYNTHETIC_SENSITIVE_VALUE',
    'https://example.test/action?oobCode=SYNTHETIC_SENSITIVE_VALUE#token',
    'https://user:SYNTHETIC_SENSITIVE_VALUE@example.test/action',
    'eyJhbGciOiJIUzI1NiJ9.c3ludGhldGlj.c2lnbmF0dXJl'])
def test_request_credentials_and_email_action_urls_are_not_logged(text):
    result=redact({'diagnostic':text})
    assert 'SYNTHETIC_SENSITIVE_VALUE' not in json.dumps(result)
    assert 'eyJ' not in json.dumps(result)


def test_failure_summary_never_formats_an_untrusted_exception(capsys):
    from src.firebase_preview.diagnostics import safe_error_summary
    class ProviderError(Exception):
        def __str__(self):raise AssertionError('Exception details must not be formatted')
        def __repr__(self):raise AssertionError('Exception details must not be formatted')
    error=ProviderError('SYNTHETIC_UNKNOWN_SECRET')
    print(json.dumps(safe_error_summary(error)))
    assert json.loads(capsys.readouterr().out)==dict(error='operation_failed',category='UnexpectedError',details_suppressed=True)
