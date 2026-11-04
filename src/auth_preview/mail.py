"""Explicit SMTP transport, or a local-only development mailbox. No cloud discovery."""
from dataclasses import dataclass, field
from email.message import EmailMessage
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
import time
from uuid import uuid4

from .service_errors import unavailable


def code_content(code,purpose):
    label={'verify':'email verification','change':'new email verification','reset':'password reset'}.get(purpose)
    if not label:raise ValueError('Unknown email challenge purpose')
    return ('Leaf Research: '+label.title(),
            f'Your Leaf Research {label} code is:\n\n{code}\n\nThis code expires in 10 minutes and can be used once. If you did not request it, ignore this email. Account approval and institution membership are unchanged.\n')


def notification_content(event):
    label={'password_changed':'Your password was changed','password_reset':'Your password was reset',
           'email_changed':'Your account email was changed'}.get(event)
    if not label:raise ValueError('Unknown security notification')
    return ('Leaf Research: '+label,
            label+'. All previous account sessions were revoked. If you did not make this change, contact the platform operator immediately. Never send anyone a password or verification code.\n')


def normalize_email(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",value):
        raise ValueError('Enter a valid email address')
    if '..' in value or value.startswith('.') or '.@' in value:raise ValueError('Enter a valid email address')
    return value


@dataclass(frozen=True)
class SMTP:
    host:str
    username:str
    password:str=field(repr=False)
    sender:str
    port:int=587

    def send(self,email,code,purpose):
        subject,body=code_content(code,purpose)
        self._deliver(email,subject,body)

    def notify(self,email,event):
        subject,body=notification_content(event)
        self._deliver(email,subject,body)

    def _deliver(self,email,subject,body):
        message=EmailMessage()
        message['From']=normalize_email(self.sender)
        message['To']=normalize_email(email)
        message['Subject']=subject
        message.set_content(body)
        context=ssl.create_default_context()
        if self.port==465:
            with smtplib.SMTP_SSL(self.host,self.port,timeout=20,context=context) as smtp:
                smtp.login(self.username,self.password)
                if smtp.send_message(message):raise smtplib.SMTPException('Recipient rejected')
        elif self.port==587:
            with smtplib.SMTP(self.host,self.port,timeout=20) as smtp:
                smtp.ehlo();smtp.starttls(context=context);smtp.ehlo()
                smtp.login(self.username,self.password)
                if smtp.send_message(message):raise smtplib.SMTPException('Recipient rejected')
        else:raise ValueError('Only TLS SMTP on port 465 or STARTTLS on 587 is supported')


class MailCodes:
    def __init__(self,config):
        self.config=config
        self.key=config.email_hmac_key
        if config.development and not config.integrated:
            path=config.data_dir/'email-code.key'
            try:
                fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:pass
            else:
                with os.fdopen(fd,'wb') as file:file.write(secrets.token_bytes(32))
            self.key=path.read_bytes()

    def ready(self):
        if (self.config.integrated or not self.config.development) and (not self.config.smtp or not self.key or len(self.key)<32):
            unavailable('Email delivery is not configured. Account recovery is unavailable until the operator configures the mail sender and recovery secret.')

    def digest(self,id,email,purpose,code):
        return hmac.new(self.key,f'{id}:{email}:{purpose}:{code}'.encode(),hashlib.sha256).hexdigest()

    def issue(self,db,user,email,purpose):
        self.ready()
        code=f'{secrets.randbelow(100_000_000):08d}'
        id=str(uuid4())
        db.execute('DELETE FROM email_challenges WHERE user_id=? AND purpose=?',(user['id'],purpose))
        db.execute('DELETE FROM email_challenges WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO email_challenges(id,user_id,email,purpose,digest,expires) VALUES(?,?,?,?,?,?)',
                   (id,user['id'],email,purpose,self.digest(id,email,purpose,code),time.time()+600))
        if self.config.development and not self.config.integrated:
            db.execute('CREATE TABLE IF NOT EXISTS dev_mailbox (id TEXT PRIMARY KEY,email TEXT NOT NULL,purpose TEXT NOT NULL,code TEXT NOT NULL,created REAL NOT NULL)')
            db.execute('DELETE FROM dev_mailbox WHERE created<?',(time.time()-3600,))
            db.execute('INSERT INTO dev_mailbox VALUES(?,?,?,?,?)',(id,email,purpose,code,time.time()))
        else:
            failed=False
            try:self.config.smtp.send(email,code,purpose)
            except (OSError,smtplib.SMTPException,ValueError):
                failed=True
            if failed:
                unavailable('Email delivery failed. Retry later; ask the operator to check the provider key, verified sender, quota and connectivity if it persists. Your previous account email remains unchanged.')

    def consume(self,db,user,email,purpose,code):
        if not self.key or len(self.key)<32:
            unavailable('Email verification is unavailable until the operator restores the configured recovery secret.')
        row=db.execute('SELECT * FROM email_challenges WHERE user_id=? AND email=? AND purpose=?',
                       (user['id'],email,purpose)).fetchone()
        if not row:return False
        valid=row['expires']>time.time() and row['attempts']<5 and hmac.compare_digest(row['digest'],self.digest(row['id'],email,purpose,code))
        if valid or row['attempts']>=4 or row['expires']<=time.time():
            db.execute('DELETE FROM email_challenges WHERE id=?',(row['id'],))
        else:db.execute('UPDATE email_challenges SET attempts=attempts+1 WHERE id=?',(row['id'],))
        return valid
