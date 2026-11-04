import sqlite3
from fastapi.testclient import TestClient
import pytest

from src.auth_preview.app import create_app
from src.auth_preview.config import Config
from test_auth_preview import client,post,signup,decision


@pytest.fixture
def app(tmp_path):
    return create_app(Config(data_dir=tmp_path,origin='http://testserver',development=True))


def latest_code(c,email,purpose):
    messages=c.get('/api/dev/mailbox').json()['messages']
    return next(m['code'] for m in messages if m['email']==email and m['purpose']==purpose)


def test_email_verification_and_self_service_recovery(app):
    c=client(app); u=signup(c,'emailstudent').json()['user']; email='emailstudent@example.test'
    assert u['email']==email and not u['email_verified_at']
    verify=latest_code(c,email,'verify')
    assert post(c,'/api/email/verify',{'code':verify}).status_code==200
    assert c.get('/api/session').json()['user']['email_verified_at']
    u=c.get('/api/session').json()['user']
    decision(client(app,'owner'),u)
    assert post(c,'/api/login',{'username':'emailstudent','password':'A long test passphrase 47!'}).status_code==200
    logged_in_cookie=c.cookies.get('hydro_auth_preview_v1')
    visitor=client(app)
    assert post(visitor,'/api/recovery/request',{'email':email}).status_code==200
    code=latest_code(visitor,email,'reset')
    assert post(visitor,'/api/recovery/complete',{'email':email,'code':code,'new_password':'Recovered passphrase 844!'}).status_code==200
    assert visitor.get('/api/workspace/plants').status_code==200, 'Successful recovery should sign an approved user in immediately'
    assert post(visitor,'/api/recovery/complete',{'email':email,'code':code,'new_password':'Recovered passphrase 844!'}).status_code==400
    replay=client(app);replay.cookies.set('hydro_auth_preview_v1',logged_in_cookie)
    assert replay.get('/api/workspace/plants').status_code==401
    assert post(visitor,'/api/login',{'username':'emailstudent','password':'Recovered passphrase 844!'}).status_code==200
    assert visitor.get('/api/workspace/plants').status_code==200


def test_unknown_and_unverified_email_get_identical_response(app):
    c=client(app);signup(c,'unverified')
    one=post(c,'/api/recovery/request',{'email':'unverified@example.test'})
    two=post(c,'/api/recovery/request',{'email':'unknown@example.test'})
    assert one.status_code==two.status_code==200 and one.json()==two.json()
    assert not any(m['purpose']=='reset' for m in c.get('/api/dev/mailbox').json()['messages'])


def test_wrong_reset_codes_are_bounded_and_codes_hashed(app):
    c=client(app)
    post(c,'/api/recovery/request',{'email':'bau_student@example.test'})
    code=latest_code(c,'bau_student@example.test','reset')
    for _ in range(5):
        assert post(c,'/api/recovery/complete',{'email':'bau_student@example.test','code':'XXXXXXXX','new_password':'Never accepted passphrase!'}).status_code==400
    assert post(c,'/api/recovery/complete',{'email':'bau_student@example.test','code':code,'new_password':'Never accepted passphrase!'}).status_code==400
    with sqlite3.connect(app.state.config.database) as db:
        assert code not in str(db.execute('SELECT * FROM email_challenges').fetchall())


def test_email_change_requires_password_and_reverification(app):
    c=client(app);signup(c,'changemail');old=latest_code(c,'changemail@example.test','verify')
    assert post(c,'/api/email/change',{'email':'newaddress@example.test','current_password':'wrong'}).status_code==401
    assert post(c,'/api/email/change',{'email':'newaddress@example.test','current_password':'A long test passphrase 47!'}).status_code==200
    assert post(c,'/api/email/verify',{'code':old}).status_code==400
    assert post(c,'/api/email/verify',{'code':latest_code(c,'newaddress@example.test','verify')}).status_code==200
    u=c.get('/api/session').json()['user'];assert u['email']=='newaddress@example.test' and u['email_verified_at']


def test_email_is_unique_and_validated_and_owner_not_in_loop(app):
    c=client(app)
    assert signup(c,'one',email='duplicate@example.test').status_code==201
    assert signup(c,'two',email='DUPLICATE@example.test').status_code==409
    assert signup(c,'three',email='invalid\r\nBcc:attacker@example.test').status_code==422
    owner=client(app,'owner')
    assert post(owner,'/api/admin/users/bau-student/recovery',{}).status_code==404


def test_expired_codes_and_resend_invalidate_previous_code(app):
    c=client(app);signup(c,'expiry');email='expiry@example.test'
    first=latest_code(c,email,'verify')
    assert post(c,'/api/email/resend',{}).status_code==200
    assert post(c,'/api/email/verify',{'code':first}).status_code==400
    second=latest_code(c,email,'verify')
    with sqlite3.connect(app.state.config.database) as db:
        db.execute('UPDATE email_challenges SET expires=0')
    assert post(c,'/api/email/verify',{'code':second}).status_code==400


def test_recovery_does_not_reactivate_suspended_account(app):
    c=client(app)
    email='suspended_student@example.test'
    assert post(c,'/api/recovery/request',{'email':email}).status_code==200
    code=latest_code(c,email,'reset')
    assert post(c,'/api/recovery/complete',{'email':email,'code':code,'new_password':'Still suspended passphrase 123!'}).status_code==200
    assert post(c,'/api/login',{'username':'suspended_student','password':'Still suspended passphrase 123!'}).status_code==403


def test_pending_recovery_signs_in_to_status_only(app):
    c=client(app);signup(c,'pendingreset');email='pendingreset@example.test'
    assert post(c,'/api/email/verify',{'code':latest_code(c,email,'verify')}).status_code==200
    assert post(c,'/api/recovery/request',{'email':email}).status_code==200
    r=post(c,'/api/recovery/complete',{'email':email,'code':latest_code(c,email,'reset'),'new_password':'Pending reset passphrase 14!'})
    assert r.status_code==200 and r.json()['account_status']=='PENDING'
    assert c.get('/api/session').json()['user']['status']=='PENDING'
    assert c.get('/api/workspace/plants').status_code==403


def test_anonymous_session_issuance_is_limited_and_no_mailbox_in_production(app,tmp_path):
    responses=[TestClient(app).get('/api/session').status_code for _ in range(130)]
    assert 429 in responses
    production=create_app(Config(data_dir=tmp_path/'production',origin='https://auth.example.test'))
    c=TestClient(production,base_url='https://auth.example.test')
    assert c.get('/api/dev/mailbox').status_code==404


def test_smtp_requires_tls_before_authentication(monkeypatch):
    from src.auth_preview.mail import SMTP
    events=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs):events.append('connect')
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def ehlo(self):events.append('ehlo')
        def starttls(self,context):events.append('tls')
        def login(self,user,password):events.append('login')
        def send_message(self,message):
            events.append('send')
            assert message['To']=='student@example.test'
            assert '12345678' in message.get_content()
    monkeypatch.setattr('src.auth_preview.mail.smtplib.SMTP',FakeSMTP)
    SMTP('smtp.example.test','test-login','test-secret','research@example.test').send('student@example.test','12345678','reset')
    assert events==['connect','ehlo','tls','ehlo','login','send']


def test_unconfigured_production_mail_fails_closed(tmp_path):
    app=create_app(Config(data_dir=tmp_path,origin='https://auth.example.test'))
    c=TestClient(app,base_url='https://auth.example.test')
    csrf=c.get('/api/session').json()['csrf_token']
    r=c.post('/api/recovery/request',json={'email':'unknown@example.test'},headers={'X-CSRF-Token':csrf,'Origin':'https://auth.example.test'})
    assert r.status_code==503
