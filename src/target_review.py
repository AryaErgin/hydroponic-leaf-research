"""Local browser correction and explicit human approval of a native candidate mask.

python -m src.target_review --image IMAGE --candidate CANDIDATE_PNG --open
"""
import argparse
import base64
import binascii
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import secrets
import webbrowser

import numpy as np
from PIL import Image, PngImagePlugin

from .manual_target import (OUTPUT_ROOT, REVIEW_KEYS, load_reviewed_calibration,
                            load_target_mask, mask_overlay, save_ground_truth)
from .target_candidate import new_folder, sha256


class ReviewSession:
    def __init__(self, source, candidate, calibration_path=None, output_root=OUTPUT_ROOT):
        self.source = Path(source).resolve()
        self.candidate = Path(candidate).resolve()
        self.context = load_reviewed_calibration(self.source, calibration_path)
        self.rgb = self.context['rgb']
        self.height, self.width = self.rgb.shape[:2]
        raw = self.candidate.read_bytes()
        self.mask = load_target_mask(io.BytesIO(raw), (self.width, self.height))
        meta_path = self.candidate.parent/('draft.json' if self.candidate.name=='draft_mask.png' else 'candidate.json')
        meta_raw = meta_path.read_bytes()
        self.meta = json.loads(meta_raw)
        status = self.meta.get('measurement_status')
        if (status not in ('UNREVIEWED_CANDIDATE','UNREVIEWED_DRAFT') or
            self.meta.get('human_approved') is not False or
            self.meta.get('image_sha256') != self.context['image_sha256'] or
            self.meta.get('image_size_px') != [self.width,self.height] or
            self.meta.get('candidate_mask_sha256') != sha256(raw)):
            raise ValueError('Candidate provenance, image binding or mask hash is invalid')
        self.provenance = dict(path=str(self.candidate), candidate_mask_sha256=sha256(raw),
            candidate_metadata_path=str(meta_path), candidate_metadata_sha256=sha256(meta_raw),
            initial_measurement_status=status, candidate_provenance=self.meta)
        self.output_root = output_root
        self.token = secrets.token_urlsafe(32)
        self.approved = False
        stream = io.BytesIO(); Image.fromarray(self.rgb).save(stream,format='PNG')
        self.image_png = stream.getvalue()

    def _decode(self, payload):
        if self.approved:
            raise ValueError('This session has already been approved; reopen for a new review')
        if (not isinstance(payload,dict) or type(payload.get('width')) is not int or
            type(payload.get('height')) is not int or
            payload['width'] != self.width or payload['height'] != self.height):
            raise ValueError('Mask dimensions must exactly match the original native image')
        try:
            raw = base64.b64decode(payload['mask_base64'], validate=True)
        except (KeyError, ValueError, TypeError, binascii.Error) as exc:
            raise ValueError('Invalid binary mask encoding') from exc
        if len(raw) != self.width*self.height:
            raise ValueError('Wrong native mask pixel count')
        array = np.frombuffer(raw,dtype=np.uint8).reshape(self.height,self.width)
        if not np.isin(array,[0,1]).all():
            raise ValueError('Only binary 0/1 mask bytes are accepted')
        # Snapshot shown by the editor must still match the actual image and scale.
        if sha256(self.source.read_bytes()) != self.context['image_sha256']:
            raise ValueError('Image changed during review; reopen the editor')
        if sha256(self.context['calibration_path'].read_bytes()) != self.context['calibration_sha256']:
            raise ValueError('Pot calibration changed during review; reopen the editor')
        return array.astype(bool)

    def describe(self):
        return dict(width=self.width,height=self.height,
            measurement_status=self.meta['measurement_status'],
            mask_base64=base64.b64encode(self.mask.astype(np.uint8).tobytes()).decode(),
            review_keys=REVIEW_KEYS,pot_diameter_cm=self.context['calibration']['known_pot_diameter_cm'],
            review_notes=self.meta.get('provenance',{}).get('outlines',{}).get('review_notes',[]))

    def save_draft(self, payload):
        mask = self._decode(payload)
        folder = new_folder(self.candidate.parent/'drafts', self.source)
        info = PngImagePlugin.PngInfo()
        info.add_text('measurement_status','UNREVIEWED_DRAFT')
        info.add_text('human_approved','false')
        Image.fromarray(mask.astype(np.uint8)*255).save(folder/'draft_mask.png',pnginfo=info)
        mask_overlay(self.rgb,mask).save(folder/'draft_overlay.png',pnginfo=info)
        meta = dict(schema_version=1,measurement_status='UNREVIEWED_DRAFT',human_approved=False,
            image_sha256=self.context['image_sha256'],image_size_px=[self.width,self.height],
            candidate_mask_sha256=sha256((folder/'draft_mask.png').read_bytes()),
            provenance=self.meta.get('provenance',{}),seed=self.provenance,
            next_step='Not ground truth. Reopen this draft and explicitly APPROVE after human review.')
        (folder/'draft.json').write_text(json.dumps(meta,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        return dict(output=str(folder),measurement_status='UNREVIEWED_DRAFT')

    def approve(self, payload):
        mask = self._decode(payload)
        if payload.get('approval') != 'APPROVE':
            raise ValueError('Explicit human APPROVE action required')
        review = payload.get('review')
        output = save_ground_truth(self.source,mask,review,self.output_root,
            self.context['calibration_path'],self.context['image_sha256'],self.context['calibration_sha256'],
            imported_mask={**self.provenance, 'editor_source_sha256': {
                'src/target_review.py':sha256(Path(__file__).read_bytes()),
                'src/target_review.html':sha256(Path(__file__).with_suffix('.html').read_bytes())}},
            review_method='explicit_browser_APPROVE',
            label_origin=f'Human-reviewed/edited from {self.meta["measurement_status"]}; explicit APPROVE, no automatic promotion')
        self.approved = True
        meta=json.loads((output/'measurement.json').read_text(encoding='utf-8'))
        return dict(output=str(output),measurement_status='MANUAL_GROUND_TRUTH',
                    pixel_area=meta['pixel_area'],projected_area_cm2=meta['projected_area_cm2'])


def make_server(session, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log access tokens or image content.

        def reply(self, status, data, kind='application/json'):
            if kind=='application/json':
                data=json.dumps(data,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)

        def authorized(self, public=False):
            host=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin', 'http://'+host) != 'http://'+host:
                self.reply(403,dict(error='Local origin required')); return False
            if not public and not secrets.compare_digest(self.headers.get('X-Review-Token',''), session.token):
                self.reply(403,dict(error='Review session token required')); return False
            return True

        def do_GET(self):
            if not self.authorized(public=self.path=='/'):
                return
            if self.path=='/':
                self.reply(200,Path(__file__).with_suffix('.html').read_bytes(),'text/html; charset=utf-8')
            elif self.path=='/session':
                self.reply(200,session.describe())
            elif self.path=='/image.png':
                self.reply(200,session.image_png,'image/png')
            else:
                self.reply(404,dict(error='Unknown route'))

        def do_POST(self):
            # Consume bounded request bytes before returning a rejection. Closing
            # with an unread POST body can reset the TCP connection on Windows.
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>session.width*session.height*2+8192:
                    raise ValueError('Request too large or empty')
                self.connection.settimeout(15)
                raw=self.rfile.read(length)
                if len(raw)!=length:
                    raise ValueError('Incomplete request body')
            except (ValueError,OSError) as exc:
                self.reply(400,dict(error=str(exc))); return
            if not self.authorized():
                return
            if self.path not in ('/draft','/approve'):
                self.reply(404,dict(error='Unknown route')); return
            try:
                if self.headers.get('Content-Type')!='application/json':
                    raise ValueError('JSON request required')
                payload=json.loads(raw)
                result=session.approve(payload) if self.path=='/approve' else session.save_draft(payload)
            except (ValueError,OSError,TypeError) as exc:
                self.reply(400,dict(error=str(exc))); return
            self.reply(200,result)
    return HTTPServer(('127.0.0.1',port),Handler)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--open',action='store_true',help='Open the local editor in your browser')
    args=parser.parse_args(argv)
    try:
        session=ReviewSession(args.image,args.candidate)
        server=make_server(session,args.port)
    except (OSError,ValueError) as exc:
        parser.exit(2,f'Error: {exc}\n')
    url=f'http://127.0.0.1:{server.server_port}/#{session.token}'
    print(f'Review URL: {url}',flush=True)
    print('Human approval pending. Save draft does not create ground truth. Ctrl+C stops the server.',flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nReview server stopped.')
    finally:
        server.server_close()


if __name__=='__main__':
    main()
