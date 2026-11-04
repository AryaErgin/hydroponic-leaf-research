"""Owner-operated dedicated-project Monitoring email configuration, never UI."""
import argparse,json
from pathlib import Path
import requests
from .owner_notifications import PROJECT,cloud_credentials,SPECIES

def policy(species,channel):
    if species not in SPECIES or not channel.startswith('projects/'+PROJECT+'/notificationChannels/'):
        raise ValueError('Dedicated owner channel required')
    label='Lettuce'if species=='lettuce'else'Sorrel (Rumex acetosa)'
    return dict(displayName='Hydroponic local model update - '+species,enabled=True,combiner='OR',
        userLabels={'hydroponic_owner_only':'true','species':species},
        conditions=[dict(displayName='One completed approval batch',conditionMatchedLog={
            'filter':f'jsonPayload.event="owner_retraining_batch_ready" AND jsonPayload.registry_namespace="hydroponic" AND jsonPayload.species="{species}" AND jsonPayload.owner_only=true',
            'labelExtractors':{'batch':'EXTRACT(jsonPayload.batch_id)','count':'EXTRACT(jsonPayload.new_approved_masks)'}})],
        documentation=dict(mimeType='text/markdown',content=f'Hydroponic model update ready for local training.\nSpecies: {label}\nNew approved masks: ${{log.extracted_label.count}}\nBatch: ${{log.extracted_label.batch}}\nRun the local update workflow when convenient.\nNo automatic training, promotion or deployment.'),
        alertStrategy={'notificationRateLimit':{'period':'300s'},'autoClose':'604800s','notificationPrompts':['OPENED']},
        notificationChannels=[channel])

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--owner-email',required=True)
    p.add_argument('--receipt',type=Path,required=True);a=p.parse_args(argv)
    if '\n'in a.owner_email or '@'not in a.owner_email:raise ValueError('Owner email required')
    cred=cloud_credentials();s=requests.Session();s.headers.update(Authorization='Bearer '+cred.token,**{'X-Goog-User-Project':PROJECT})
    root='https://monitoring.googleapis.com/v3/projects/'+PROJECT
    def call(method,path,**kwargs):
        if method!='GET':cloud_credentials() # fresh dedicated-project guard before mutation
        r=s.request(method,root+path,timeout=90,**kwargs);r.raise_for_status();return r.json()
    channels=call('GET','/notificationChannels').get('notificationChannels',[])
    own=[c for c in channels if c.get('displayName')=='Hydroponic project owner'and c.get('type')=='email'and c.get('labels',{}).get('email_address')==a.owner_email]
    if len(own)>1:raise ValueError('Duplicate owner channels require review')
    channel=own[0]if own else call('POST','/notificationChannels',json=dict(type='email',displayName='Hydroponic project owner',labels={'email_address':a.owner_email},enabled=True,userLabels={'hydroponic_owner_only':'true'}))
    existing=call('GET','/alertPolicies').get('alertPolicies',[]);saved=[]
    for species in SPECIES:
        body=policy(species,channel['name']);matching=[x for x in existing if x.get('displayName')==body['displayName']]
        if len(matching)>1:raise ValueError('Duplicate species alert policies require review')
        if matching:
            body['name']=matching[0]['name'];saved.append(call('PATCH','/alertPolicies/'+body['name'].split('/')[-1],json=body))
        else:saved.append(call('POST','/alertPolicies',json=body))
    for item in saved:
        assert item['notificationChannels']==[channel['name']]
        assert item['alertStrategy']['notificationPrompts']==['OPENED']
    result=dict(project=PROJECT,owner_email=a.owner_email,channel=channel,policies=saved,
        professor_channels=[],owner_only=True,training_automatic=False,
        estimated_incremental_monthly_cost='Currently no alerting charge; future published price up to approximately $0.70/month for two references. Logging within free allowance at this usage.',
        sources=['https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts','https://cloud.google.com/products/observability/pricing'])
    with a.receipt.open('x',encoding='utf8')as f:json.dump(result,f,indent=2)
    print(json.dumps(dict(owner_only=True,owner_email=a.owner_email,channel=channel['name'],policies=[r['name']for r in saved],verification_status=channel.get('verificationStatus')),indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
