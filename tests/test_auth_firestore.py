"""Real emulator tests. No credentials and no production Firestore writes."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.auth_preview.config import Config
from src.auth_preview.service import Service,now_iso
from src.auth_preview.passwords import hash_password
from test_auth_email_integrated import Mail


@pytest.fixture
def cloud(tmp_path):
    if not os.getenv('FIRESTORE_EMULATOR_HOST'):pytest.skip('Requires real Firestore emulator')
    config=Config(tmp_path,'http://testserver',development=True,integrated=True,require_verified_email=True,
                  smtp=Mail(),email_hmac_key=b'k'*32,firestore_project='hydroponic-leaf-research-aryae',
                  firestore_prefix='auth_test_'+uuid4().hex)
    s=Service(config)
    with s.db.transaction() as db:
        db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',('org','University'))
        db.execute('INSERT INTO public_signup VALUES(1,?)',('org',))
        encoded=hash_password('A long test passphrase 47!')
        for id,role in [('member','MEMBER'),('owner','PLATFORM_OWNER')]:
            db.execute('INSERT INTO users(id,username,password_hash,status,role,requested_org_id,org_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (id,id,encoded,'ACTIVE',role,'org','org',now_iso()))
            db.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?',(id+'@example.test',id))
            db.execute('UPDATE users SET email_verified_at=?,version=version+1 WHERE id=?',(now_iso(),id))
        token=s.new_session(db,'member')[0];owner=s.new_session(db,'owner')[0]
    yield s,config.smtp,token,owner
    # Delete only this unique emulator fixture's known auth collections.
    for collection in s.db.collections():
        for snapshot in collection.stream():snapshot.reference.delete()
    s.db.close()


def test_firestore_config_and_unsupported_queries_fail_closed(cloud):
    s,m,t,_=cloud
    assert s.db.durable
    with s.db.transaction() as db:
        with pytest.raises(ValueError):db.execute('SELECT * FROM arbitrary_private_table')
        with pytest.raises(ValueError):db.execute('DELETE FROM users')
        assert db.execute('SELECT 1').fetchone()[0]==1


def test_firestore_persistence_and_revocation(cloud):
    s,m,t,_=cloud
    restarted=Service(s.config)
    with restarted.db.transaction() as db:assert restarted.actor(db,t)['id']=='member'
    new=restarted.change_password(t,SimpleNamespace(current_password='A long test passphrase 47!',new_password='Updated long passphrase 84!'))
    with s.db.transaction() as db:
        assert not s.session(db,t) and s.actor(db,new)['id']=='member'


def test_firestore_pending_email_and_notifications(cloud):
    s,m,t,_=cloud
    s.email_change(t,SimpleNamespace(email='new@example.test',current_password='A long test passphrase 47!'))
    with s.db.transaction() as db:
        assert s.actor(db,t)['email']=='member@example.test'
        assert s.actor(db,t)['pending_email']=='new@example.test'
    new=s.email_verify(t,m.codes[-1][1])
    with s.db.transaction() as db:
        assert not s.session(db,t)
        assert s.actor(db,new)['email']=='new@example.test'
    assert ('member@example.test','email_changed') in m.notifications


def test_firestore_rollback_and_unique_reservations(cloud):
    s,m,t,_=cloud
    with pytest.raises(RuntimeError):
        with s.db.transaction() as db:
            db.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?',('rolledback@example.test','member'))
            raise RuntimeError('rollback')
    with s.db.transaction() as db:assert s.actor(db,t)['email']=='member@example.test'
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with s.db.transaction() as db:
            db.execute('UPDATE users SET email=?,email_verified_at=NULL,version=version+1 WHERE id=?',('owner@example.test','member'))


def test_firestore_password_reset_expiry_reuse_and_suspension(cloud):
    s,m,t,owner=cloud
    with s.db.transaction() as db:u=s.actor(db,t)
    s.decide(owner,'member',SimpleNamespace(action='suspend',version=u['version'],verified_org_id=None))
    s.request_reset('member@example.test')
    data=SimpleNamespace(email='member@example.test',code=m.codes[-1][1],new_password='New suspended passphrase 84!')
    new,status=s.complete_reset(data)
    assert new is None and status=='SUSPENDED'
    with pytest.raises(HTTPException):s.complete_reset(data)


def test_firestore_concurrent_account_decision_is_atomic(cloud):
    s,m,t,owner=cloud
    with s.db.transaction() as db:u=s.actor(db,t)
    s.decide(owner,'member',SimpleNamespace(action='suspend',version=u['version'],verified_org_id=None))
    with s.db.transaction() as db:u=db.execute('SELECT * FROM users WHERE id=?',('member',)).fetchone()
    def reactivate(_):
        try:
            s.decide(owner,'member',SimpleNamespace(action='reactivate',version=u['version'],verified_org_id=None))
            return 200
        except HTTPException as e:return e.status_code
    with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(reactivate,range(2)))
    assert sorted(statuses)==[200,409]


def test_firestore_rate_limit_persists_after_restart(cloud):
    s,m,t,_=cloud
    s.rate_limit('test-key',1,900)
    restarted=Service(s.config)
    with pytest.raises(HTTPException) as e:restarted.rate_limit('test-key',1,900)
    assert e.value.status_code==429


def test_firestore_session_capacity_uses_counter_not_collection_scan(cloud):
    from unittest.mock import patch
    from src.auth_preview.firestore_database import Connection
    s,m,t,_=cloud
    with patch.object(Connection,'scan',side_effect=AssertionError('Collection scan forbidden for capacity')):
        with s.db.transaction() as db:assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==2


def test_firestore_concurrent_username_reservation(cloud):
    s,m,t,_=cloud
    def signup(n):
        data=SimpleNamespace(username='Concurrent',email=f'concurrent{n}@example.test',password='A long test passphrase 47!',
                             display_name='',requested_org_id='org',institution_request='')
        try:s.signup(data,'');return 201
        except HTTPException as e:return e.status_code
    with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(signup,range(2)))
    assert sorted(statuses)==[201,409]
    with s.db.transaction() as db:
        users=db.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
    assert sum(u['username']=='Concurrent' for u in users)==1


def test_firestore_invalid_code_attempts_persist(cloud):
    s,m,t,_=cloud
    s.email_change(t,SimpleNamespace(email='new@example.test',current_password='A long test passphrase 47!'))
    code=m.codes[-1][1]
    for _ in range(5):
        with pytest.raises(HTTPException):s.email_verify(t,'INVALID!')
    with pytest.raises(HTTPException):s.email_verify(t,code)
    with s.db.transaction() as db:assert s.actor(db,t)['email']=='member@example.test'


def test_firestore_outbox_failure_survives_restart(cloud):
    s,m,t,_=cloud;m.fail=True
    new=s.change_password(t,SimpleNamespace(current_password='A long test passphrase 47!',new_password='Updated long passphrase 84!'))
    restarted=Service(s.config)
    assert restarted.email_delivery_status(new)['pending_security_notifications']==1
    m.fail=False
    assert restarted.retry_notifications(new)==1
    assert restarted.email_delivery_status(new)['pending_security_notifications']==0


def test_firestore_sent_notifications_do_not_fill_pending_query(cloud):
    s,m,t,_=cloud
    with s.db.transaction() as db:
        for n in range(251):
            db.insert('email_notifications',('id','user_id','email','event','created','status'),
                      (f'old-{n}','member','member@example.test','password_changed',float(n),'sent'))
    assert s.email_delivery_status(t)['pending_security_notifications']==0


def test_firestore_expired_session_cleanup_is_batched(cloud):
    s,m,t,_=cloud
    with s.db.transaction() as db:
        for n in range(101):db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(f'expired-{n}',None,'test-csrf',0,0))
    with s.db.transaction() as db:
        s.new_session(db)
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==4


def test_firestore_http_session_cookie_csrf_and_email_confirmation(cloud):
    from fastapi.testclient import TestClient
    from src.auth_preview.app import create_app
    from test_auth_preview import post
    s,m,t,_=cloud
    app=create_app(s.config);client=TestClient(app)
    client.cookies.set(s.config.cookie,t)
    session=client.get('/api/session').json()
    assert session['user']['id']=='member' and session['integrated']
    assert client.get('/api/dev/mailbox').status_code==404
    assert client.post('/api/email/change',json={}).status_code==403
    assert post(client,'/api/email/change',{'email':'http-new@example.test','current_password':'A long test passphrase 47!'}).status_code==200
    assert post(client,'/api/email/verify',{'code':m.codes[-1][1]}).status_code==200
    current=client.get('/api/session').json()
    assert current['user']['email']=='http-new@example.test'
    assert current['csrf_token']!=session['csrf_token']
    with s.db.transaction() as db:assert not s.session(db,t)


def test_firestore_http_signup_verify_approve_login(cloud):
    from fastapi.testclient import TestClient
    from src.auth_preview.app import create_app
    from test_auth_preview import post,signup,decision
    s,m,t,owner=cloud
    app=create_app(s.config);student=TestClient(app);admin=TestClient(app)
    admin.cookies.set(s.config.cookie,owner)
    response=signup(student,'firestorestudent',org='org')
    assert response.status_code==201,response.text
    user=response.json()['user']
    assert decision(admin,user).status_code==409
    assert post(student,'/api/email/verify',{'code':m.codes[-1][1]}).status_code==200
    user=student.get('/api/session').json()['user']
    assert decision(admin,user).status_code==200
    assert post(student,'/api/login',{'username':'firestorestudent','password':'A long test passphrase 47!'}).status_code==200
    assert student.get('/api/session').json()['user']['status']=='ACTIVE'
