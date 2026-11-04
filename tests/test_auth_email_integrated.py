"""No real mail: transport receipts here are deterministic test doubles only."""
from types import SimpleNamespace
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.auth_preview.config import Config
from src.auth_preview.service import Service, now_iso
from src.auth_preview.passwords import hash_password


class Mail:
    def __init__(self): self.codes=[]; self.notifications=[]; self.fail=False
    def send(self,email,code,purpose):
        if self.fail: raise OSError('credential and provider body MUST NOT escape')
        self.codes.append((email,code,purpose))
    def notify(self,email,event):
        if self.fail: raise OSError('private provider body')
        self.notifications.append((email,event))


@pytest.fixture
def integrated(tmp_path):
    transport=Mail()
    config=Config(tmp_path,'http://testserver',development=True,integrated=True,
                  require_verified_email=True,smtp=transport,email_hmac_key=b'k'*32)
    service=Service(config)
    with service.db.transaction() as db:
        db.execute("INSERT INTO organizations(id,name) VALUES('org','University')")
        db.execute("INSERT INTO public_signup VALUES(1,'org')")
        encoded=hash_password('A long test passphrase 47!')
        for id,role in [('member','MEMBER'),('owner','PLATFORM_OWNER')]:
            db.execute('INSERT INTO users(id,username,password_hash,status,role,org_id,created_at,email,email_verified_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (id,id,encoded,'ACTIVE',role,'org',now_iso(),id+'@example.test',now_iso()))
        token=service.new_session(db,'member')[0]; owner=service.new_session(db,'owner')[0]
    return service,transport,token,owner


def change(service,token,email='new@example.test'):
    return service.email_change(token,SimpleNamespace(email=email,current_password='A long test passphrase 47!'))


def test_integrated_uses_real_transport_without_plaintext_mailbox(integrated):
    s,m,t,_=integrated
    change(s,t)
    assert m.codes[-1][0]=='new@example.test' and m.codes[-1][2]=='change'
    with s.db.transaction() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='dev_mailbox'").fetchone()
        assert m.codes[-1][1] not in str([tuple(x) for x in db.execute('SELECT * FROM email_challenges')])
    assert s.config.cookie=='hydro_integrated_local_v1'


def test_email_change_retains_old_until_confirm_and_revokes_sessions(integrated):
    s,m,t,_=integrated
    assert change(s,t)==t
    with s.db.transaction() as db:
        u=s.actor(db,t); assert u['email']=='member@example.test' and u['email_verified_at']
        assert u['pending_email']=='new@example.test'
    new=s.email_verify(t,m.codes[-1][1]); assert new and new!=t
    with s.db.transaction() as db:
        assert not s.session(db,t)
        u=s.actor(db,new); assert u['email']=='new@example.test' and not u['pending_email']
    assert ('member@example.test','email_changed') in m.notifications
    with pytest.raises(HTTPException): s.email_verify(new,m.codes[-1][1])


def test_active_and_approval_require_verified_email(integrated):
    s,m,t,owner=integrated
    with s.db.transaction() as db:
        db.execute("UPDATE users SET email_verified_at=NULL WHERE id='member'")
    with pytest.raises(HTTPException) as e:
        with s.db.transaction() as db: s.actor(db,t)
    assert e.value.status_code==403
    with s.db.transaction() as db:
        db.execute("UPDATE users SET status='PENDING',role='PENDING',org_id=NULL WHERE id='member'")
    with pytest.raises(HTTPException) as e: s.decide(owner,'member',SimpleNamespace(action='approve',version=1,verified_org_id='org'))
    assert e.value.status_code==409


def test_invalid_expired_and_purpose_scoped_change_codes(integrated):
    s,m,t,_=integrated
    change(s,t); code=m.codes[-1][1]
    for _ in range(5):
        with pytest.raises(HTTPException): s.email_verify(t,'INVALID!')
    with pytest.raises(HTTPException): s.email_verify(t,code)
    change(s,t); code=m.codes[-1][1]
    with s.db.transaction() as db: db.execute('UPDATE email_challenges SET expires=0')
    with pytest.raises(HTTPException): s.email_verify(t,code)
    with s.db.transaction() as db: assert s.actor(db,t)['email']=='member@example.test'


def test_provider_failure_preserves_existing_email_and_sessions(integrated):
    s,m,t,_=integrated; m.fail=True
    with pytest.raises(HTTPException) as e: change(s,t)
    assert e.value.status_code==503 and 'credential' not in str(e.value.detail)
    with s.db.transaction() as db:
        assert s.actor(db,t)['email']=='member@example.test'
        assert not s.actor(db,t)['pending_email']


def test_notification_failure_is_durable_and_retryable(integrated):
    s,m,t,_=integrated; m.fail=True
    new=s.change_password(t,SimpleNamespace(current_password='A long test passphrase 47!',new_password='Updated long passphrase 84!'))
    with s.db.transaction() as db:
        assert not s.session(db,t) and s.session(db,new)
        row=db.execute('SELECT * FROM email_notifications').fetchone()
        assert row['status']=='pending' and row['event']=='password_changed'
        assert 'private' not in str(tuple(row))
    m.fail=False
    assert s.retry_notifications()==1
    assert ('member@example.test','password_changed') in m.notifications


def test_reset_provider_failures_do_not_enumerate_accounts(integrated):
    s,m,t,_=integrated; m.fail=True
    assert s.request_reset('member@example.test') is None
    assert s.request_reset('unknown@example.test') is None


def test_integrated_missing_hmac_fails_closed(tmp_path):
    s=Service(Config(tmp_path,'http://testserver',development=True,integrated=True,smtp=Mail()))
    with pytest.raises(HTTPException): s.mail.ready()
    assert not (tmp_path/'email-code.key').exists()


def test_sqlite_integrated_cloud_runtime_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('K_SERVICE','cloud-run-test')
    with pytest.raises(ValueError,match='durable'):
        Config(tmp_path,'https://example.test',integrated=True)


def test_resend_https_request_and_sanitized_errors():
    from src.auth_preview.providers import Resend,DeliveryError
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args): return b'{"id":"test-only-receipt"}'
    with patch('src.auth_preview.providers.urlopen',return_value=Response()) as call:
        r=Resend('fake-test-key','sender@example.test',verified_sender=True)
        r.send('recipient@example.test','12345678','change')
        req=call.call_args.args[0]
        assert req.full_url=='https://api.resend.com/emails'
        assert req.get_header('Authorization')=='Bearer fake-test-key'
        assert '12345678' in json.loads(req.data)['text']
    with pytest.raises(ValueError): Resend('key','sender@example.test')
    with patch('src.auth_preview.providers.urlopen',side_effect=OSError('fake-test-key')):
        with pytest.raises(DeliveryError) as error: r.send('recipient@example.test','12345678','verify')
    assert 'fake-test-key' not in str(error.value)


def test_pending_email_resend_invalidates_first_code(integrated):
    s,m,t,_=integrated
    change(s,t); first=m.codes[-1][1]
    s.email_resend(t); second=m.codes[-1][1]
    assert m.codes[-1][0]=='new@example.test' and m.codes[-1][2]=='change'
    with pytest.raises(HTTPException): s.email_verify(t,first)
    assert s.email_verify(t,second)


def test_reset_revokes_sessions_not_suspension_and_notifies(integrated):
    s,m,t,_=integrated
    with s.db.transaction() as db: db.execute("UPDATE users SET status='SUSPENDED' WHERE id='member'")
    s.request_reset('member@example.test')
    data=SimpleNamespace(email='member@example.test',code=m.codes[-1][1],new_password='New suspended passphrase 84!')
    new,status=s.complete_reset(data,t)
    assert new is None and status=='SUSPENDED'
    with s.db.transaction() as db: assert not s.session(db,t)
    assert ('member@example.test','password_reset') in m.notifications
    with pytest.raises(HTTPException): s.complete_reset(data)


def test_expired_session_and_persistent_rate_limit(integrated):
    s,m,t,_=integrated
    with s.db.transaction() as db: db.execute('UPDATE sessions SET expires=0')
    with pytest.raises(HTTPException) as e:
        with s.db.transaction() as db: s.actor(db,t)
    assert e.value.status_code==401
    s.rate_limit('test-account',1,900)
    with pytest.raises(HTTPException) as e: s.rate_limit('test-account',1,900)
    assert e.value.status_code==429


def test_delivery_status_reports_recoverable_failure(integrated):
    s,m,t,_=integrated; m.fail=True
    new=s.change_password(t,SimpleNamespace(current_password='A long test passphrase 47!',new_password='Updated long passphrase 84!'))
    status=s.email_delivery_status(new)
    assert status['pending_security_notifications']==1 and 'operator' in status['message']
    m.fail=False; s.retry_notifications()
    assert s.email_delivery_status(new)['pending_security_notifications']==0


def test_secret_loading_is_explicit_and_errors_sanitized(tmp_path,monkeypatch):
    from src.auth_preview.secrets import local_hmac_key,secret_manager_value
    path=tmp_path/'hmac.key';path.write_bytes(b'protected-test-secret'*3)
    import os
    if os.name!='nt':path.chmod(0o600)
    assert len(local_hmac_key(path))>=32
    path.write_bytes(b'short')
    with pytest.raises(ValueError):local_hmac_key(path)
    with pytest.raises(ValueError):secret_manager_value('implicit-secret-name')
    assert 'protected-test-secret' not in repr(Config(tmp_path,'http://testserver',development=True,email_hmac_key=b'protected-test-secret'))


def test_unverified_user_can_verify_but_not_enter_workspace(integrated):
    s,m,t,_=integrated
    with s.db.transaction() as db:
        db.execute("UPDATE users SET email_verified_at=NULL WHERE id='member'")
        user=db.execute("SELECT * FROM users WHERE id='member'").fetchone()
        s.mail.issue(db,user,user['email'],'verify')
    with pytest.raises(HTTPException):
        with s.db.transaction() as db:s.actor(db,t)
    assert s.email_verify(t,m.codes[-1][1]) is None
    with s.db.transaction() as db:assert s.actor(db,t)['email_verified_at']


def test_self_service_notification_retry_is_scoped(integrated):
    s,m,t,owner=integrated
    with s.db.transaction() as db:
        for id in ('member','owner'):
            user=db.execute('SELECT * FROM users WHERE id=?',(id,)).fetchone()
            s.queue_notification(db,user,'password_changed')
    assert s.retry_notifications(t)==1
    assert m.notifications==[('member@example.test','password_changed')]
    assert s.retry_notifications(owner)==1


def test_lost_hmac_key_fails_safely_during_confirmation(integrated):
    s,m,t,_=integrated
    change(s,t);s.mail.key=None
    with pytest.raises(HTTPException) as error:s.email_verify(t,m.codes[-1][1])
    assert error.value.status_code==503


def test_reactivation_requires_verified_email(integrated):
    s,m,t,owner=integrated
    with s.db.transaction() as db:
        db.execute("UPDATE users SET status='SUSPENDED',email_verified_at=NULL WHERE id='member'")
    with pytest.raises(HTTPException) as error:
        s.decide(owner,'member',SimpleNamespace(action='reactivate',version=1,verified_org_id=None))
    assert error.value.status_code==409


def test_postcommit_outbox_store_failure_preserves_success(integrated,monkeypatch):
    s,m,t,_=integrated
    def unavailable(*args,**kwargs):raise HTTPException(503,'Synthetic store outage after commit')
    monkeypatch.setattr(s,'retry_notifications',unavailable)
    new=s.change_password(t,SimpleNamespace(current_password='A long test passphrase 47!',new_password='Updated long passphrase 84!'))
    with s.db.transaction() as db:assert s.session(db,new) and not s.session(db,t)


def test_phone_review_owner_exception_preserves_unverified_field(integrated):
    from dataclasses import replace
    s,m,t,owner=integrated
    staged=Service(replace(s.config,phone_review_owner_id='owner'))
    with s.db.transaction() as db:db.execute("UPDATE users SET email_verified_at=NULL")
    with staged.db.transaction() as db:
        user=staged.actor(db,owner)
        assert user['id']=='owner' and user['email_verified_at'] is None
    with pytest.raises(HTTPException):
        with staged.db.transaction() as db:staged.actor(db,t)


def test_phone_review_exception_requires_exact_owner_identity_and_role(integrated):
    from dataclasses import replace
    s,m,t,owner=integrated
    staged=Service(replace(s.config,phone_review_owner_id='member'))
    with s.db.transaction() as db:db.execute("UPDATE users SET email_verified_at=NULL")
    for token in (t,owner):
        with pytest.raises(HTTPException):
            with staged.db.transaction() as db:staged.actor(db,token)


@pytest.mark.parametrize('changes',[
    {'integrated':False},
    {'development':False,'origin':'https://production.example.test','firestore_project':'explicit-project','firestore_prefix':'hydroponic_phone_review_test'},
    {'development':False,'origin':'https://phone-review---service.example.test','firestore_project':'explicit-project','firestore_prefix':'hydroponic_auth_v1'},
    {'development':False,'origin':'https://phone-review---service.example.test','firestore_prefix':'hydroponic_phone_review_test'},
])
def test_phone_review_flag_rejects_nonstaging_config(tmp_path,changes):
    options=dict(data_dir=tmp_path,origin='http://testserver',development=True,integrated=True,phone_review_owner_id='owner')
    options.update(changes)
    with pytest.raises(ValueError):Config(**options)


def test_phone_review_cloud_config_accepts_only_isolated_tag_contract(tmp_path):
    config=Config(tmp_path,'https://phone-review---service.example.test',integrated=True,
                  firestore_project='explicit-project',firestore_prefix='hydroponic_phone_review_test',phone_review_owner_id='owner')
    assert config.phone_review_owner_id=='owner'
