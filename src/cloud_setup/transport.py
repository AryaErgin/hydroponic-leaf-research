"""Project-bound Google transport. Provider payloads and tokens are never logged."""
import json
import os
import re
import shutil
import subprocess
from urllib.error import HTTPError,URLError
from urllib.parse import parse_qs,unquote,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

from .plan import validate_project_id


class CloudError(RuntimeError):
    def __init__(self,message,status_code=None):super().__init__(message);self.status_code=status_code


def safe_environment():
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('CLOUDSDK_','HYDRO_')):env.pop(key,None)
    for key in ('GOOGLE_APPLICATION_CREDENTIALS','CLOUDSDK_AUTH_ACCESS_TOKEN',
        'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE','CLOUDSDK_CORE_PROJECT','GOOGLE_CLOUD_PROJECT',
        'GCLOUD_PROJECT','FIREBASE_CONFIG','FIREBASE_AUTH_EMULATOR_HOST','FIRESTORE_EMULATOR_HOST',
        'STORAGE_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST','GOOGLE_CLOUD_QUOTA_PROJECT',
        'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'):
        env.pop(key,None)
    env['CLOUDSDK_CORE_DISABLE_PROMPTS']='1'
    env['CLOUDSDK_CORE_DISABLE_USAGE_REPORTING']='1'
    env['CLOUDSDK_CORE_LOG_HTTP']='false'
    env['CLOUDSDK_CORE_VERBOSITY']='warning'
    return env


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def open_google(request,timeout):
    return build_opener(NoRedirect()).open(request,timeout=timeout)


def gcloud_executable():
    executable=shutil.which('gcloud.cmd' if os.name=='nt' else 'gcloud')
    if not executable:raise ValueError('Install Google Cloud CLI from https://cloud.google.com/sdk/docs/install then reopen the terminal.')
    return executable


READ_COMMANDS={('projects','describe'),('projects','list'),('auth','list'),('auth','print-access-token'),
    ('services','list'),('secrets','list'),('secrets','describe'),('secrets','versions','list'),
    ('iam','roles','list'),('iam','roles','describe'),('iam','service-accounts','list'),
    ('iam','service-accounts','describe'),('storage','buckets','list'),('storage','buckets','describe'),
    ('firestore','databases','list'),('firestore','databases','describe'),
    ('artifacts','repositories','list'),('artifacts','repositories','describe'),
    ('run','services','list'),('run','services','describe'),('run','revisions','describe'),
    ('builds','describe'),
    ('billing','accounts','list'),('billing','projects','describe'),('billing','budgets','list')}


class CloudClient:
    def __init__(self,project_id,configuration,*,executable=None,runner=subprocess.run):
        self.project_id=validate_project_id(project_id)
        if not re.fullmatch(r'(?:hlr-own|hydroponic-own)-[a-z0-9-]{1,45}',configuration):
            raise ValueError('Use a separate own-instance gcloud configuration.')
        self.configuration=configuration;self.executable=executable or gcloud_executable()
        self.runner=runner;self.approval_digest=None
        self._operations=set()

    def approve(self,digest):
        if not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('Invalid approval digest.')
        self.approval_digest=digest

    def gcloud(self,args,*,json_output=False,input_bytes=None):
        args=list(args)
        if not all(isinstance(arg,str) and '\x00' not in arg for arg in args):raise ValueError('Invalid command arguments.')
        cleaned=[];index=0
        while index<len(args):
            arg=args[index]
            if arg in ('--project','--configuration'):
                value=args[index+1] if index+1<len(args) else ''
                expected=self.project_id if arg=='--project' else self.configuration
                if value!=expected:raise ValueError('Cloud command cannot change the selected project or identity.')
                index+=2;continue
            if arg.startswith(('--project=','--configuration=')):
                expected=self.project_id if arg.startswith('--project=') else self.configuration
                if arg.split('=',1)[1]!=expected:raise ValueError('Cloud target override is forbidden.')
                index+=1;continue
            if arg.startswith(('--flags-file','--billing-project','--impersonate-service-account',
                               '--access-token-file','--credential-file-override','--log-http','--verbosity')):
                raise ValueError('Credential/target overrides are forbidden.')
            if arg!='--quiet':cleaned.append(arg)
            index+=1
        if cleaned[:1]==['projects'] and len(cleaned)>2 and cleaned[1] not in ('list',):
            if cleaned[2]!=self.project_id:raise ValueError('Project resource must equal the selected project.')
        for arg in cleaned:
            for bucket in re.findall(r'gs://([a-z0-9._-]+)',arg):
                if bucket not in {self.project_id+'-hlr-science',self.project_id+'-hlr-build'}:
                    raise ValueError('Bucket resource is outside the selected installation.')
            for project in re.findall(r'projects/([a-zA-Z0-9_-]+)',arg):
                if project not in (self.project_id,getattr(self,'project_number',None)):
                    raise ValueError('Full resource name is outside the selected project.')
            if 'serviceAccount:' in arg:
                member=arg.split('serviceAccount:',1)[1]
                if not member.endswith('@'+self.project_id+'.iam.gserviceaccount.com'):
                    raise ValueError('IAM grant targets a different project service account.')
            for address in re.findall(r'[A-Za-z0-9_-]+@[A-Za-z0-9_-]+\.iam\.gserviceaccount\.com',arg):
                if not address.endswith('@'+self.project_id+'.iam.gserviceaccount.com'):
                    raise ValueError('Service-account selector belongs to another project.')
            image=re.search(r'[a-z0-9-]+-docker\.pkg\.dev/([^/\s]+)',arg)
            if image and image.group(1)!=self.project_id:raise ValueError('Image belongs to a different project.')
        if cleaned[:2]==['billing','projects'] and len(cleaned)>3 and cleaned[3]!=self.project_id:
            raise ValueError('Billing changes must target the selected project.')
        read=any(tuple(cleaned[:len(prefix)])==prefix for prefix in READ_COMMANDS)
        if not read and not self.approval_digest:raise ValueError('Approve the exact resource/cost plan before cloud writes.')
        command=[self.executable,'--configuration='+self.configuration,'--project='+self.project_id,'--quiet']
        if json_output:command.append('--format=json')
        command.extend(cleaned)
        try:
            result=self.runner(command,input=input_bytes,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env=safe_environment(),timeout=60 if read else 1500,check=False)
        except (OSError,subprocess.TimeoutExpired):raise CloudError('Google Cloud CLI failed or timed out. Preserve setup state and check the selected project in its console.') from None
        if result.returncode:
            raise CloudError('Google Cloud command failed. Check IAM, billing and API availability for the selected project; provider output was withheld.')
        output=result.stdout.decode('utf8') if isinstance(result.stdout,bytes) else result.stdout
        if not json_output:return output.strip()
        try:return json.loads(output) if output.strip() else {}
        except ValueError:raise CloudError('Google Cloud CLI returned an unexpected response; no provider payload was printed.') from None

    def access_token(self):
        value=self.gcloud(['auth','print-access-token'])
        if not isinstance(value,str) or not value or any(char.isspace() for char in value):raise CloudError('Google sign-in did not provide a usable token; sign in through the dedicated setup configuration.')
        return value

    def _validate_url(self,url):
        parsed=urlsplit(url);path=unquote(parsed.path);query=parse_qs(parsed.query,keep_blank_values=True)
        allowed={'firebase.googleapis.com','identitytoolkit.googleapis.com','firebaserules.googleapis.com',
                 'apikeys.googleapis.com','cloudbilling.googleapis.com','billingbudgets.googleapis.com'}
        if (parsed.scheme!='https' or parsed.hostname not in allowed or parsed.username or parsed.password or
            parsed.port not in (None,443) or parsed.fragment or '\\' in path or
            any(part in ('.','..') for part in path.split('/')) or any(len(values)!=1 for values in query.values())):
            raise ValueError('Unsafe Google API URL.')
        if '/projects/' in path:
            selected=path.split('/projects/',1)[1].split('/',1)[0].split(':',1)[0]
            if selected not in (self.project_id,getattr(self,'project_number',None)):
                raise ValueError('API resource belongs to a different project.')
        elif not (
            (parsed.hostname,path.removeprefix('/v1beta1/').removeprefix('/v2/')) in self._operations or
            (parsed.hostname=='apikeys.googleapis.com' and path=='/v2/keys:lookupKey' and
             set(query)=={'keyString','fields'} and query['fields']==['name,parent'] and
             query['keyString']==[getattr(self,'expected_browser_key',None)])
        ) and parsed.hostname not in ('cloudbilling.googleapis.com','billingbudgets.googleapis.com'):
            raise ValueError('API resource is not project-bound.')
        if parsed.hostname=='identitytoolkit.googleapis.com' and path.endswith('/config'):
            fields=query.get('fields',[''])[0].split(',')
            if not fields or any(field not in {'name','subtype','authorizedDomains','signIn.email','emailPrivacyConfig'} for field in fields):
                raise ValueError('Firebase auth diagnostics require a narrow safe response projection; hash configuration is forbidden.')
        return parsed

    def api(self,method,url,body=None):
        self._validate_url(url)
        if method not in ('GET','POST','PATCH','DELETE'):raise ValueError('Unsupported Google API method.')
        if method!='GET' and not self.approval_digest:raise ValueError('Approve the exact resource/cost plan before cloud writes.')
        payload=None if body is None else json.dumps(body,separators=(',',':')).encode()
        request=Request(url,data=payload,method=method,headers={'Authorization':'Bearer '+self.access_token(),'Content-Type':'application/json'})
        try:
            with open_google(request,timeout=60) as response:encoded=response.read(2*1024*1024+1)
        except HTTPError as error:raise CloudError('Google API request failed. Check this project console and permissions; response details were withheld.',error.code) from None
        except (URLError,TimeoutError):raise CloudError('Google API connection failed; no response payload was printed.') from None
        if len(encoded)>2*1024*1024:raise CloudError('Google API returned an oversized response.')
        try:result=json.loads(encoded)
        except ValueError:raise CloudError('Google API returned an invalid response; body was withheld.') from None
        host=urlsplit(url).hostname
        if (method in ('POST','PATCH') and host in ('firebase.googleapis.com','apikeys.googleapis.com') and
            isinstance(result,dict) and isinstance(result.get('name'),str) and
            re.fullmatch(r'operations/[A-Za-z0-9_.:=/-]+',result['name']) and
            not any(part in ('.','..') for part in result['name'].split('/'))):
            self._operations.add((host,result['name']))
        return result
