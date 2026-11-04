"""Production/local start: python -m src.webapp --host 127.0.0.1 --port 8080."""
import argparse
import os
import ipaddress
import uvicorn
from .settings import Settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default=os.getenv('HYDRO_HOST','127.0.0.1'))
    parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8080')))
    args=parser.parse_args()
    settings=Settings.from_env().validate()
    try:local_listener=ipaddress.ip_address(args.host).is_loopback
    except ValueError:local_listener=args.host=='localhost'
    if not local_listener and not settings.auth_token:
        parser.error('Set HYDRO_AUTH_TOKEN (at least 32 characters) before starting a nonloopback listener')
    uvicorn.run('src.webapp.app:create_app',factory=True,host=args.host,port=args.port,
                workers=1,proxy_headers=True,forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS','127.0.0.1'))


if __name__=='__main__':main()
