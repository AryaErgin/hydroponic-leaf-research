"""Static release guard: web email actions must not become mobile Dynamic Links.

Delivery remains a receiving-inbox acceptance check; these tests send no email.
"""
from pathlib import Path
import json
import re

import pytest

from src.firebase_preview.diagnostics import auth_config_summary

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'src/firebase_preview/static/app.js'
ENTRY = ROOT / 'src/firebase_preview/client/sdk-entry.js'


def test_web_client_does_not_request_dynamic_links_or_mobile_handlers():
    authored = APP.read_text(encoding='utf-8') + ENTRY.read_text(encoding='utf-8')
    for forbidden in ('dynamicLinkDomain', 'handleCodeInApp', 'signInWithEmailLink',
                      'sendSignInLinkToEmail', 'firebase/dynamic-links', '.page.link',
                      'cordova', 'bundleId', 'packageName'):
        assert forbidden not in authored


@pytest.mark.parametrize(('action', 'expected_calls'), [
    ('sendEmailVerification', ['credential.user', 'user', 'user']),
    ('sendPasswordResetEmail', ['auth,data.email']),
    ('verifyBeforeUpdateEmail', ['user,data.new_email']),
])
def test_authored_email_actions_use_default_web_settings(action, expected_calls):
    # Imports are excluded by requiring the call parenthesis. A changed settings
    # argument must receive an explicit mobile/deprecation review.
    calls = re.findall(r'\b' + action + r'\s*\(([^()]*)\)', APP.read_text(encoding='utf-8'))
    assert [re.sub(r'\s+', '', call) for call in calls] == expected_calls


def test_direct_stdout_summary_excludes_unknown_fields_at_every_provider_level(capsys):
    secret = 'SYNTHETIC_UNKNOWN_PROVIDER_SECRET'
    config = {'unknown': secret, 'signIn': {'unknown': secret,
              'email': {'enabled': True, 'passwordRequired': True, 'unknown': secret},
              'hashConfig': {'signerKey': secret, 'unknown': secret}},
              'emailPrivacyConfig': {'enableImprovedEmailPrivacy': True, 'unknown': secret},
              'authorizedDomains': [secret]}
    # This is the relevant call-site contract: safe summary is built BEFORE
    # serialization/printing; a recursive denylist is insufficient for unknowns.
    print(json.dumps(auth_config_summary(config)))
    output = capsys.readouterr().out
    assert secret not in output
    assert set(json.loads(output)) == {'email_password_enabled', 'password_required',
        'email_enumeration_protection', 'hash_parameters_present', 'authorized_domain_count'}
    assert all(type(value) in (bool, int) for value in json.loads(output).values())
