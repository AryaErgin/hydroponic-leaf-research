"""Loopback-only launcher; ignores all production database/cloud configuration."""
import argparse
from pathlib import Path
import socket

import uvicorn

from .app import create_app
from .config import Config


def main():
    parser=argparse.ArgumentParser(description='Isolated development authentication preview')
    parser.add_argument('--port',type=int,default=8891)
    parser.add_argument('--qa',action='store_true',help='Separate browser QA store and host; never changes the review preview session')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Choose an unprivileged local port')
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',args.port))
        except OSError:parser.error(f'Port {args.port} is occupied. Select another port; do not stop its current server.')
    root=Path(__file__).resolve().parents[2]
    hostname='127.0.0.1' if args.qa else 'localhost'
    instance='auth-browser-qa' if args.qa else 'auth-preview'
    config=Config(data_dir=root/'.runtime'/instance,origin=f'http://{hostname}:{args.port}',development=True)
    uvicorn.run(create_app(config),host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False)


if __name__=='__main__':main()
