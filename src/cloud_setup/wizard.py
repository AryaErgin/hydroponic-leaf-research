"""Guided own-project release. Import and --plan-only never contact Google."""
import argparse
from decimal import Decimal,InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import venv
import webbrowser
from urllib.request import Request
from uuid import uuid4

from .plan import make_manifest,build_plan,validate_project_id,validate_manifest
from .state import save_state,load_state,consent_digest,require_consent
from .transport import CloudClient,CloudError,gcloud_executable,safe_environment,open_google

ROOT=Path(__file__).resolve().parents[2]


def approve_plan(state,plan,*,answer):
    project=plan['manifest']['project_id']
    if answer!='DEPLOY '+project:raise ValueError('Deployment cancelled. No consent was inferred; preserve the setup state to resume.')
    state['approved_plan_sha256']=consent_digest(plan)


def show_plan(plan):
    manifest=plan['manifest']
    print('\nTarget: '+manifest['project_id']+' | '+manifest['region'])
    print('Cloud Run: CPU 2 / memory 4 GiB / min 0 / max 1 / concurrency 1; no GPU or always-on instance.')
    print('Own Firebase identities, Firestore Native database, two private storage buckets, image repository, scoped service accounts and Secret Manager.')
    print('Initial Firestore region cannot be changed in place. Existing unknown resources will not be adopted or deleted.')
    print('Typical light research use may be a few USD/month or less with applicable free allowances. This is not a price guarantee.')
    print('Example before free allowances: 1,000 analyses at 60 billed seconds each ~USD 3.48 compute at current us-central1 rates, plus storage/builds/database/logs/network.')
    print('Budget alerts are not a hard spending cap. Max instances also does not cap the bill; storage and image costs persist at zero instances.')
    if plan.get('billing_account'):print('Selected billing account: '+plan['billing_account']+' | alert amount '+plan['budget_amount']+' in that account currency.')
    print('The authorized model will be uploaded privately to this project for its build; no production data/users/configuration will be imported.')


def _identity_session(client):
    # Local CLI config only; never activate it or change the current production config.
    common=[client.executable,'--configuration='+client.configuration]
    listing=subprocess.run([client.executable,'config','configurations','list','--format=json'],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=safe_environment(),check=False)
    try:configs=json.loads(listing.stdout) if listing.returncode==0 else []
    except ValueError:raise CloudError('Could not inspect local gcloud configurations; no cloud resource was changed.') from None
    if not any(item.get('name')==client.configuration for item in configs):
        result=subprocess.run([client.executable,'config','configurations','create',client.configuration,'--no-activate','--quiet'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=safe_environment(),check=False)
        if result.returncode:raise CloudError('Could not create a separate local gcloud configuration.')
    print('Sign into YOUR Google account in the browser. No service-account key will be created.')
    environment=safe_environment();environment.pop('CLOUDSDK_CORE_DISABLE_PROMPTS',None)
    result=subprocess.run([*common,'auth','login','--brief'],env=environment,check=False)
    if result.returncode:raise CloudError('Google sign-in was not completed; no resource was provisioned.')


def _billing(client,state):
    accounts=client.gcloud(['billing','accounts','list','--filter=open=true'],json_output=True)
    if not isinstance(accounts,list) or not accounts:
        print('Create a billing account personally at https://console.cloud.google.com/billing then rerun. Google may require payment details and terms acceptance.')
        raise ValueError('An open billing account you are authorized to use is required; none was linked.')
    for index,account in enumerate(accounts,1):print(str(index)+'. '+str(account.get('displayName','Billing account'))+' | '+str(account.get('name','')))
    answer=input('Choose your billing account number: ').strip()
    if not answer.isdigit() or not 1<=int(answer)<=len(accounts):raise ValueError('Choose one of the displayed billing accounts.')
    account=accounts[int(answer)-1].get('name','').removeprefix('billingAccounts/')
    if not re.fullmatch(r'[A-Za-z0-9]{6}(?:-[A-Za-z0-9]{6}){2}',account):raise ValueError('Invalid billing account identifier.')
    amount=input('Monthly budget ALERT amount in your billing account currency [5]: ').strip() or '5'
    if not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,2})?',amount) or float(amount)<=0:raise ValueError('Enter a positive budget alert amount.')
    state.update(billing_account=account,budget_amount=amount)


def _budget(client,state,save):
    account=state['billing_account'];manifest=state['manifest'];display='Hydroponic '+manifest['installation_id']
    listing=client.gcloud(['billing','budgets','list','--billing-account='+account],json_output=True)
    if not isinstance(listing,list) or any(not isinstance(budget,dict) for budget in listing):
        raise ValueError('Budget inventory returned an unexpected format; no budget was changed.')
    matching=[budget for budget in listing if budget.get('displayName')==display]
    if matching:
        projects=matching[0].get('budgetFilter',{}).get('projects',[])
        if len(matching)!=1 or projects!=['projects/'+manifest['project_number']]:raise ValueError('Existing budget alert ownership/filter differs; review it in your billing console.')
        money=matching[0].get('amount',{}).get('specifiedAmount',{})
        try:existing=Decimal(str(money['units']))+Decimal(str(money.get('nanos',0)))/Decimal(1_000_000_000)
        except (KeyError,InvalidOperation):raise ValueError('Existing budget alert has no fixed verifiable amount; review it manually.') from None
        if existing!=Decimal(state['budget_amount']):raise ValueError('Existing budget amount differs from the approved plan; adjust it in your billing console before resuming.')
        thresholds={float(rule.get('thresholdPercent',0)) for rule in matching[0].get('thresholdRules',[]) if rule.get('spendBasis','CURRENT_SPEND')=='CURRENT_SPEND'}
        if not {.5,.9,1.0}.issubset(thresholds) or matching[0].get('allUpdatesRule',{}).get('disableDefaultIamRecipients') is True:
            raise ValueError('Existing budget alert thresholds/recipients differ from the plan; review them manually.')
        state['budget_status']='EXISTING_PROJECT_ALERT';save();return
    result=client.gcloud(['billing','budgets','create','--billing-account='+account,'--display-name='+display,
        '--budget-amount='+state['budget_amount'],'--calendar-period=month',
        '--filter-projects=projects/'+manifest['project_number'],
        '--threshold-rule=percent=0.5','--threshold-rule=percent=0.9','--threshold-rule=percent=1.0'],json_output=True)
    state.update(budget_status='CREATED_PROJECT_ALERT',budget_resource=result.get('name'));save()


def ensure_billing_target(client,state):
    """Never relink an existing/unknown project before its resource inventory."""
    project=state['project_id'];account=state['billing_account']
    current=client.gcloud(['billing','projects','describe',project],json_output=True)
    if not isinstance(current,dict):raise ValueError('Billing status could not be verified; no billing change was made.')
    if current.get('billingEnabled') is True:
        if current.get('billingAccountName')!='billingAccounts/'+account:
            raise ValueError('This project uses a different billing account. Review/link the intended account personally in its console before setup; no billing change was made.')
        return
    if state.get('project_created') is not True:
        raise ValueError('Existing unbilled projects are not automatically linked before full inventory. Use a NEW project created by this wizard, or personally verify the existing project is empty and link its billing in the console, then resume.')
    return client.gcloud(['billing','projects','link',project,'--billing-account='+account],json_output=True)


def rollback(client,manifest,revision):
    if not isinstance(revision,str) or not re.fullmatch(re.escape(manifest['service_name'])+r'-[A-Za-z0-9-]+',revision):
        raise ValueError('Rollback must use a recorded revision of this installation service.')
    return client.gcloud(['run','services','update-traffic',manifest['service_name'],
        '--region='+manifest['region'],'--to-revisions='+revision+'=100'],json_output=True)


def _health(manifest):
    last=False
    for attempt in range(6):
        try:
            with open_google(Request(manifest['origin']+'/health'),timeout=30) as response:
                data=json.loads(response.read(65536))
            if (data.get('status')=='ok' and data.get('project')==manifest['project_id'] and
                data.get('identity')=='independent_cloud' and data.get('scientific_ready') is True and
                data.get('auth_storage')=='firestore' and data.get('scientific_storage')=='organization_scoped'):return data
        except Exception:last=True
        time.sleep(2)
    raise ValueError('The new website did not pass its own-project health check. No cloud acceptance is claimed.')


def _owner_tools():
    environment=ROOT/'.venv-cloud';python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():venv.EnvBuilder(with_pip=True).create(environment)
    print('Checking pinned operator tools in .venv-cloud (no local model download).')
    result=subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(ROOT/'requirements-local.txt')],
        cwd=ROOT,stderr=subprocess.STDOUT,check=False)
    if result.returncode:raise ValueError('Operator tools installation failed; deployed resources and setup state were preserved.')
    return python


def _bootstrap_owner(client,state,save,python=None):
    manifest=state['manifest']
    print('\nOwner setup: open '+manifest['origin']+'/signup')
    print('Register YOUR account in the initial institution, open Google’s verification email, then sign in again. It should remain pending until this IAM operator step.')
    webbrowser.open(manifest['origin']+'/signup')
    email=input('Email of the exact verified account to become platform owner (no password): ').strip()
    if not email or '@' not in email:raise ValueError('An exact verified owner email is required; no account was promoted.')
    input('Press Enter only after this account is registered, verified and signed in on the website: ')
    python=python or _owner_tools()
    # Token travels through stdin, never shell arguments/state/console/report.
    payload=json.dumps({'manifest':manifest,'token':client.access_token(),'email':email}).encode()
    script=('import json,sys; from src.cloud_instance.operator import bootstrap_from_operator; '
        'p=json.load(sys.stdin); '
        '\ntry:\n bootstrap_from_operator(p["manifest"],p["token"],p["email"]); print("OWNER_READY")'
        '\nexcept Exception:\n print("OWNER_NOT_READY: Check exact email verification, pending registration, IAM permissions and preserved setup state."); sys.exit(1)')
    environment=safe_environment()
    for name in ('K_SERVICE','HYDRO_LOCAL_MODE','HYDRO_FIRESTORE_PROJECT','HYDRO_GCS_BUCKET','HYDRO_DATABASE_URL'):
        environment.pop(name,None)
    result=subprocess.run([str(python),'-c',script],input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT,env=environment,check=False)
    if result.returncode or result.stdout.strip()!=b'OWNER_READY':raise ValueError('Owner bootstrap did not complete. Verify the exact account and operator permissions; no provider details were printed.')
    state['owner_status']='VERIFIED_BOOTSTRAPPED';save()
    print('Owner ready. Sign out and sign in again to activate the owner role. Other signups still require verification and approval.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id');parser.add_argument('--project-number');parser.add_argument('--institution')
    parser.add_argument('--model-bundle');parser.add_argument('--plan-only',action='store_true')
    args=parser.parse_args(argv)
    try:
        if sys.version_info[:2]!=(3,11) or sys.maxsize<2**32:raise ValueError('Install 64-bit Python 3.11 before cloud setup.')
        project=validate_project_id(args.project_id or input('Your dedicated Google Cloud project ID (existing empty or new): ').strip())
        if args.plan_only:
            if not args.project_number or not args.institution:raise ValueError('Offline plans require --project-number and --institution. No identity/project is verified offline.')
            manifest=make_manifest(project,args.project_number,uuid4().hex,args.institution)
            plan=build_plan(manifest);show_plan(plan)
            save_state(ROOT/'.cloud/dry-run-plan.json',plan)
            print('OFFLINE PLAN ONLY: no authentication, Google API calls, resources, billing or email. Cloud build/deployment is UNVERIFIED.')
            return 0
        from .context import create_context
        from .provision import Provisioner,ManualStepRequired,BuildRetryRequired
        from ..local_setup.model_assets import _read_bundle,_load_policy
        path=ROOT/'.cloud/instances'/project/'state.json'
        state=load_state(path) if path.exists() else {'schema_version':1,'project_id':project,'installation_id':uuid4().hex,'provision':{}}
        if state.get('project_id')!=project or state.get('schema_version')!=1:raise ValueError('Preserve this mismatched deployment state; do not adopt it for another project.')
        save=lambda:save_state(path,state)
        # Validate every local build input before project creation, billing,
        # API activation or other persistent cloud writes.
        bundle=Path(args.model_bundle or input('Authorized research-model-exp006.zip path: ').strip().strip('"')).expanduser().resolve()
        _read_bundle(bundle,_load_policy())
        print('Authorized model bundle hashes/config/provenance verified locally; nothing uploaded yet.')
        if input('Confirm you are authorized to use and privately upload this research/evaluation model under its retained license [yes/no]: ').strip().lower()!='yes':
            raise ValueError('Model use/upload was not authorized; setup stopped.')
        head=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        context=ROOT/'.cloud/build-contexts'/head[:16]
        receipt=create_context(bundle,context)
        operator_python=_owner_tools()
        save()
        configuration='hlr-own-'+hashlib.sha256(project.encode()).hexdigest()[:12]
        client=CloudClient(project,configuration)
        _identity_session(client)
        if 'manifest' not in state:
            mode=input('Use an existing EMPTY dedicated project, or create a new project? [existing/new]: ').strip().lower()
            if mode=='new':
                print('Creating a Google Cloud project is a lasting administrative action. Billing/resources are approved separately.')
                if input('Type CREATE '+project+' to create it: ').strip()!='CREATE '+project:raise ValueError('Project creation cancelled.')
                state['project_create_intent']=True;save()
                client.approve(consent_digest({'create_project':project}))
                client.gcloud(['projects','create',project],json_output=True)
                state['project_created']=True;save()
                client.approval_digest=None
            elif mode!='existing':raise ValueError('Choose existing or new. No resource was provisioned.')
            record=client.gcloud(['projects','describe',project],json_output=True)
            if record.get('projectId')!=project or record.get('lifecycleState')!='ACTIVE':raise ValueError('The selected project is not active or could not be verified.')
            institution=args.institution or input('Initial university/institution name: ').strip()
            state['manifest']=make_manifest(project,str(record['projectNumber']),state['installation_id'],institution)
            save()
        manifest=validate_manifest(state['manifest']);client.project_number=manifest['project_number']
        provision=Provisioner(client,manifest,state=state['provision'],save_state=save)
        provision.preflight()  # Unknown/different installation before ANY billing write.
        _billing(client,state)
        plan={**build_plan(manifest),'billing_account':state['billing_account'],'budget_amount':state['budget_amount'],
            'source_commit':receipt['source_commit']}
        show_plan(plan)
        if state.get('approved_plan_sha256')!=consent_digest(plan):
            approve_plan(state,plan,answer=input('Type DEPLOY '+project+' to approve these billable resources and operation costs: ').strip());save()
        require_consent(state,plan);client.approve(state['approved_plan_sha256'])
        print('Configuring your selected billing link and project-scoped alert...')
        ensure_billing_target(client,state)
        client.gcloud(['services','enable','cloudbilling.googleapis.com','billingbudgets.googleapis.com'],json_output=True)
        try:_budget(client,state,save)
        except CloudError:
            print('Budget alert could not be configured automatically. Open https://console.cloud.google.com/billing/'+state['billing_account']+'/budgets and create an alert restricted to this project. It is not a cap.')
            if input('Type ALERT READY only after the billing administrator configured it, or Enter to stop: ').strip()!='ALERT READY':raise ValueError('Budget setup is incomplete; infrastructure has not been provisioned.')
            state['budget_status']='MANUALLY_CONFIRMED_NOT_API_VERIFIED';save()
        print('Provisioning your own private infrastructure. Existing unknown resources will stop setup.')
        state['manifest']=provision.apply_infrastructure();save()
        while True:
            try:provision.configure_auth();break
            except ManualStepRequired as error:
                print(str(error))
                print('Use standard Firebase Authentication > Get started > Email/Password. Accept terms personally if prompted. Do not upgrade to Identity Platform.')
                webbrowser.open('https://console.firebase.google.com/project/'+project+'/authentication/providers')
                if input('Press Enter after saving Email/Password, or type stop to resume later: ').strip():raise ValueError('Firebase setup paused; all state was preserved.')
        if not state['provision'].get('runtime_secret_version'):provision.put_runtime_secret(secrets.token_urlsafe(48).encode())
        from .key_security import restrict_firebase_key
        print('Verifying Firebase Auth and restricting the public browser key to authentication APIs...')
        restrict_firebase_key(client,state['manifest'])
        state['browser_key_status']='AUTH_API_TARGETS_VERIFIED';save()
        config_digest=hashlib.sha256(json.dumps(state['manifest'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if state['provision'].get('config_manifest_sha256')!=config_digest:
            provision.put_instance_config();state['provision']['config_manifest_sha256']=config_digest;save()
        if state['provision'].get('image') and state['provision'].get('build_source_commit')==receipt['source_commit']:
            image=state['provision']['image']
        else:
            print('Uploading only the verified source/model context and building the CPU image. This can take several minutes.')
            print('Own-project build status: https://console.cloud.google.com/cloud-build/builds?project='+project)
            try:image=provision.submit_build(context)
            except BuildRetryRequired as error:
                print('Own-project build '+error.build_id+' ended '+error.status+'. A new attempt can incur additional build charges.')
                if input('Type RETRY BUILD to authorize one additional attempt, or Enter to stop: ').strip()!='RETRY BUILD':
                    raise ValueError('No additional build was authorized. Existing resources/state were preserved.')
                image=provision.submit_build(context,retry_failed=True)
            state['provision']['build_source_commit']=receipt['source_commit'];save()
        previous=state.get('accepted_revision')
        try:
            print('Deploying the immutable image and checking real model/Firestore startup readiness...')
            result=provision.deploy(image);_health(state['manifest'])
        except Exception:
            if previous:
                rollback(client,manifest,previous)
                state['provision']['revision']=previous;save()
                print('Restored traffic to the previously recorded revision. Scientific data was not deleted.')
            else:
                state['last_failed_revision']=state['provision'].pop('revision',None);save()
            raise
        state['accepted_revision']=state['provision']['revision'];save()
        _bootstrap_owner(client,state,save,operator_python)
        print('WEBSITE READY: '+result['origin'])
        print('Owner configured. Complete the real cloud/email/scientific acceptance checklist before claiming the installation fully validated.')
        return 0
    except (ValueError,CloudError,OSError) as error:
        print('ERROR: '+str(error));return 1
    except KeyboardInterrupt:
        print('Stopped. Resume with the same project ID; existing setup data/resources were preserved.');return 1
    except Exception:
        # Unexpected provider/library failures may contain credential material.
        # Do not emit a raw exception/traceback or dump configuration objects.
        print('ERROR: Setup encountered an unexpected response. Keep .cloud state and inspect your selected project console or contact the maintainer. No provider payload was printed.');return 1


if __name__=='__main__':raise SystemExit(main())
