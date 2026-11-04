"""Optional transactional provider; constructing it never sends or verifies mail.

The operator must verify sender ownership in the provider dashboard before setting
verified_sender. Provider responses and credentials are deliberately not logged.
"""
from dataclasses import dataclass, field
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .mail import normalize_email, code_content, notification_content


class DeliveryError(OSError):
    pass


@dataclass(frozen=True)
class Resend:
    api_key: str = field(repr=False)
    sender: str
    verified_sender: bool = False

    def __post_init__(self):
        normalize_email(self.sender)
        if not self.api_key or not self.verified_sender:
            raise ValueError('Configure an API key and explicitly verified sender before enabling Resend')

    def _deliver(self,email,subject,text):
        request=Request('https://api.resend.com/emails',
            data=json.dumps({'from':self.sender,'to':[normalize_email(email)],'subject':subject,'text':text}).encode(),
            headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json',
                     'User-Agent':'HydroponicResearch/1.0'},method='POST')
        try:
            with urlopen(request,timeout=20) as response:
                result=json.loads(response.read(16384))
            if not isinstance(result,dict) or not result.get('id'):
                raise DeliveryError('Mail provider returned no delivery identifier. Retry or contact the operator.')
        except HTTPError as error:
            if error.code in (401,403): message='Mail provider rejected the key or sender. Ask the operator to check credentials and sender verification.'
            elif error.code==429: message='Mail provider rate limit reached. Retry later.'
            else: message='Mail provider rejected delivery. Ask the operator to check provider status and sender configuration.'
            raise DeliveryError(message) from None
        except (OSError,URLError,ValueError,KeyError):
            raise DeliveryError('Mail provider is unavailable. Retry later or ask the operator to check connectivity.') from None

    def send(self,email,code,purpose):
        subject,text=code_content(code,purpose)
        self._deliver(email,subject,text)

    def notify(self,email,event):
        subject,text=notification_content(event)
        self._deliver(email,subject,text)
