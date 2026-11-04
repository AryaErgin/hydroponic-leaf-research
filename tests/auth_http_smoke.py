"""Explicit loopback-only smoke against a running isolated QA instance.

Creates additive synthetic test accounts; never contacts production or GCS.
Run: python tests/auth_http_smoke.py http://127.0.0.1:8892
"""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def main():
    base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8892'
    if urlsplit(base).hostname not in ('127.0.0.1','localhost'):raise SystemExit('Loopback QA hosts only')
    user=httpx.Client(base_url=base,timeout=30);owner=httpx.Client(base_url=base,timeout=30)
    results={}
    def post(client,path,body):
        session=client.get('/api/session');session.raise_for_status()
        return client.post(path,json=body,headers={'X-CSRF-Token':session.json()['csrf_token'],'Origin':base})
    username='http_'+uuid4().hex[:12];email=username+'@example.test';password='Synthetic HTTP passphrase 843!'
    signup=post(user,'/api/signup',{'username':username,'email':email,'password':password,
        'confirm_password':password,'requested_org_id':'org-bau'})
    assert signup.status_code==201,signup.text
    id=signup.json()['user']['id'];results['registration']=201
    assert user.get('/api/workspace/measurements').status_code==403;results['pending_denial']=403
    messages=user.get('/api/dev/mailbox').json()['messages']
    code=next(m['code'] for m in messages if m['email']==email and m['purpose']=='verify')
    assert post(user,'/api/email/verify',{'code':code}).status_code==200;results['email_verified']=200
    assert post(owner,'/api/dev/switch',{'user_id':'owner'}).status_code==200
    account=next(u for u in owner.get('/api/admin/users').json()['users'] if u['id']==id)
    approved=post(owner,f'/api/admin/users/{id}/decision',{'action':'approve','version':account['version']})
    assert approved.status_code==200;results['approval']=200
    assert post(user,'/api/login',{'username':username,'password':password}).status_code==200
    rows=user.get('/api/workspace/measurements').json()['items'];assert rows and all(r['org_id']=='org-bau' for r in rows)
    results['own_tenant_read']=200
    assert user.get('/api/workspace/measurements?organization_id=org-b').status_code==403;results['cross_tenant_denial']=403
    assert user.get('/api/workspace/plants/b-plants').status_code==404;results['guessed_plant_denial']=404
    assert user.get('/api/artifacts/b-masks').status_code==404;results['other_private_artifact_denial']=404
    export=user.get('/api/workspace/export.csv');assert export.status_code==200 and ',org-b,' not in export.text
    results['scoped_csv']=200
    promoted=post(owner,f'/api/admin/users/{id}/role',{'role':'ORG_ADMIN','version':approved.json()['user']['version']})
    assert promoted.status_code==200;results['owner_promotion']=200
    assert user.get('/api/workspace/plants').status_code==401;results['role_change_revokes_sessions']=401
    assert post(user,'/api/login',{'username':username,'password':password}).status_code==200
    assert post(user,f'/api/admin/users/{id}/role',{'role':'PLATFORM_ADMIN','version':promoted.json()['user']['version']}).status_code==403
    results['self_escalation_denial']=403
    visitor=httpx.Client(base_url=base,timeout=30)
    assert post(visitor,'/api/recovery/request',{'email':email}).status_code==200
    code=next(m['code'] for m in visitor.get('/api/dev/mailbox').json()['messages'] if m['email']==email and m['purpose']=='reset')
    assert post(visitor,'/api/recovery/complete',{'email':email,'code':code,'new_password':'Recovered HTTP passphrase 934!'}).status_code==200
    assert user.get('/api/workspace/plants').status_code==401
    assert visitor.get('/api/workspace/plants').status_code==200;results['self_service_recovery_and_auto_login']=200
    assert post(visitor,'/api/login',{'username':username,'password':'Recovered HTTP passphrase 934!'}).status_code==200
    cookie=visitor.cookies.get('hydro_auth_preview_v1')
    assert post(visitor,'/api/logout',{}).status_code==200
    replay=httpx.Client(base_url=base,timeout=30,cookies={'hydro_auth_preview_v1':cookie})
    assert replay.get('/api/workspace/plants').status_code==401;results['logout_replay_denial']=401
    output=Path('artifacts/auth_preview_001/http-smoke.json');output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'base':base,'synthetic_account':username,'checks':results,'all_passed':True},indent=2),encoding='utf-8')
    print(f'Actual HTTP backend smoke: {len(results)} checks passed; {output}')
    for client in [user,owner,visitor,replay]:client.close()


if __name__=='__main__':main()
