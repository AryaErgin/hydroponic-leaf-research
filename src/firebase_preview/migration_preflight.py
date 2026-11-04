"""Offline release review only. No account binding, migration, IO or cloud calls."""
import hashlib
import json
import re
from datetime import datetime

FIREBASE_PROJECT = 'hydroponic-leaf-research-aryae'
BINDING_ROLES = ('PENDING', 'MEMBER', 'ORG_ADMIN', 'PLATFORM_ADMIN', 'PLATFORM_OWNER')
BINDING_STATUSES = ('PENDING', 'ACTIVE', 'REJECTED', 'SUSPENDED')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _reviewed(item):
    if item.get('review_status') != 'REVIEWED' or not all(
        isinstance(item.get(key), str) and item[key].strip() and item[key] != 'UNSET'
        for key in ('reviewed_by', 'reviewed_at_utc', 'evidence_reference')):return False
    try:
        stamp = datetime.fromisoformat(item['reviewed_at_utc'].replace('Z', '+00:00'))
        return stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
    except ValueError:return False


def _hash(value):return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate_release_mapping(expected_records, records, bindings, *, forbidden_uids=(),
                             verified_org_ids=()):
    """Compare frozen source-qualified records and explicitly reviewed identity grants.

    expected_records contain source_record_id, source_id, original_id, kind,
    expected_sha256 and parent_source_record_ids. Caller supplies the authoritative
    frozen inventory; this function does not discover identity or institution.
    PASS_OFFLINE_REVIEW_ONLY cannot authorize execution or establish real ownership.
    """
    issues = []
    def issue(reason, identity=None):
        issues.append({'reason': reason, **({'record': identity} if identity else {})})
    expected = {row['source_record_id']: row for row in expected_records}
    if len(expected) != len(expected_records):issue('duplicate_expected_identity')
    supplied = {}
    immutable = ('source_id', 'original_id', 'kind', 'expected_sha256', 'parent_source_record_ids')
    for row in records:
        key = row.get('source_record_id')
        if key in supplied:issue('duplicate_record_mapping', key)
        supplied[key] = row
        if not _reviewed(row):issue('record_review_required', key)
        qualified = (f"{row.get('source_id')}:media:{row.get('original_id')}" if row.get('kind') == 'media'
            else f"{row.get('source_id')}:row:{row.get('kind')}:{row.get('original_id')}")
        if key != qualified or not _hash(row.get('expected_sha256')):
            issue('source_qualification_or_hash_required', key)
        if row.get('target_org_id') not in verified_org_ids:issue('unverified_record_organization', key)
        source = expected.get(key)
        if source is None or any(row.get(k) != source.get(k) for k in immutable):
            issue('source_identity_hash_or_parents_changed', key)
    for key, row in expected.items():
        actual = supplied.get(key)
        if actual is None:issue('missing_record_mapping', key);continue
        for parent in row.get('parent_source_record_ids', []):
            if parent not in supplied or supplied[parent].get('target_org_id') != actual.get('target_org_id'):
                issue('parent_institution_conflict', key)
    uids, accounts, owners = set(), set(), []
    for row in bindings:
        uid = row.get('firebase_uid')
        account = (row.get('backend_auth_source'), row.get('backend_user_id'))
        if not _reviewed(row):issue('identity_review_required')
        if any(not value or value == 'UNSET' for value in (*account, uid, row.get('firebase_project'))):
            issue('explicit_identity_source_required')
        if row.get('firebase_project') != FIREBASE_PROJECT:issue('firebase_project_mismatch')
        if row.get('role') not in BINDING_ROLES:issue('invalid_binding_role')
        if row.get('status') not in BINDING_STATUSES:issue('invalid_binding_status')
        if uid in uids or account in accounts:issue('duplicate_uid_or_backend_account')
        uids.add(uid);accounts.add(account)
        org = row.get('verified_org_id')
        membership = (isinstance(org, str) and bool(org.strip()) and org != 'UNSET'
            and org in verified_org_ids)
        if not membership:issue('unverified_membership')
        if row.get('status') == 'ACTIVE' and (not membership or row.get('role') not in BINDING_ROLES[1:]):
            issue('active_membership_required')
        if row.get('role') == 'PLATFORM_OWNER':
            owners.append(row)
            if uid in forbidden_uids or row.get('identity_kind') != 'REAL_OWNER':issue('synthetic_or_unproven_owner')
            if row.get('status') != 'ACTIVE' or not row.get('provider_and_recovery_proof_reference'):
                issue('owner_activation_or_recovery_proof_required')
        if type(row.get('expected_account_version')) is not int or row['expected_account_version'] < 1 or not _hash(row.get('expected_account_sha256')):
            issue('account_version_and_hash_required')
    if len(owners) != 1:issue('exactly_one_owner_required')
    return {'status': 'PASS_OFFLINE_REVIEW_ONLY' if not issues else 'BLOCKED',
            'issues': issues, 'issue_count': len(issues), 'record_count': len(expected),
            'identity_count': len(bindings), 'migration_performed': False,
            'cloud_accessed': False, 'writes_performed': False}
