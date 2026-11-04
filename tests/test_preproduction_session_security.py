"""Bounded anonymous sessions must not prevent authenticated sign-in."""
from types import SimpleNamespace
import time

import pytest
from fastapi import HTTPException
from src.auth_preview.config import Config
from src.auth_preview.service import Service, now_iso
from src.auth_preview.passwords import token_hash
from test_auth_firestore import cloud


@pytest.fixture(params=['sqlite', 'firestore'])
def sessions(request, tmp_path):
    if request.param == 'firestore':
        service, _, _, owner = request.getfixturevalue('cloud')
        return service, owner
    service = Service(Config(tmp_path, 'http://testserver', development=True,
                             integrated=True, require_verified_email=True))
    with service.db.transaction() as db:
        db.execute('INSERT INTO organizations(id,name) VALUES(?,?)', ('org', 'University'))
        for id, role in [('member', 'MEMBER'), ('owner', 'PLATFORM_OWNER')]:
            db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (id, id, 'unused', 'ACTIVE', role, 'org', 'org', now_iso()))
            db.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?', (now_iso(), id))
        service.new_session(db, 'member')
        owner = service.new_session(db, 'owner')[0]
    return service, owner


def test_anonymous_capacity_does_not_block_signin_or_evict_authenticated(sessions):
    service, owner = sessions
    service.session_capacity = 5
    with service.db.transaction() as db:
        for _ in range(3): service.new_session(db)
    # Reproduces a full pool populated by cookie-less visitors.
    with service.db.transaction() as db:
        anonymous = service.new_session(db)[0]
        assert service.actor(db, owner)['id'] == 'owner'
        assert service.session(db, anonymous)
        signed_in = service.new_session(db, 'member')[0]
        assert service.actor(db, signed_in)['id'] == 'member'
        assert service.actor(db, owner)['id'] == 'owner'
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 5


def test_anonymous_bootstrap_expires_quickly_without_shortening_login(sessions):
    service, owner = sessions
    with service.db.transaction() as db:
        anonymous = service.new_session(db)[0]
        row = service.session(db, anonymous)
        assert 0 < row['expires'] - time.time() <= 900
        assert service.session(db, owner)['expires'] - time.time() > 900


def test_authenticated_capacity_fails_closed_without_eviction(sessions):
    service, owner = sessions
    service.session_capacity = 2
    with pytest.raises(HTTPException) as error:
        with service.db.transaction() as db: service.new_session(db)
    assert error.value.status_code == 429
    with service.db.transaction() as db:
        assert service.actor(db, owner)['id'] == 'owner'
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 2


def test_unverified_promotion_blocked_but_demotion_allowed(sessions):
    service, owner = sessions
    with service.db.transaction() as db:
        db.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?', (None, 'member'))
        version = db.execute('SELECT * FROM users WHERE id=?', ('member',)).fetchone()['version']
    with pytest.raises(HTTPException) as error:
        service.role(owner, 'member', SimpleNamespace(role='ORG_ADMIN', version=version))
    assert error.value.status_code == 409
    with service.db.transaction() as db:
        db.execute('UPDATE users SET role=?,version=version+1 WHERE id=?', ('PLATFORM_ADMIN', 'member'))
        version = db.execute('SELECT * FROM users WHERE id=?', ('member',)).fetchone()['version']
    result = service.role(owner, 'member', SimpleNamespace(role='MEMBER', version=version))
    assert result['role'] == 'MEMBER'
