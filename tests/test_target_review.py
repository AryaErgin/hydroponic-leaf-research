"""Native-mask persistence and human approval boundaries for the browser workflow."""
import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from src.hydroponic.config import ROOT
from src.manual_pot import save_reviewed_diameter
from src.manual_target import REVIEW_KEYS


class TestTargetReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base/'lettuce_sample.png'
        Image.new('RGB', (120, 100), (40, 130, 30)).save(self.source)
        self.calibration = save_reviewed_diameter(self.source, [[10, 50], [110, 50]], 5.82,
            reviewed=True, cache_root=self.base/'calibration')
        self.mask = np.zeros((100, 120), np.uint8)
        self.mask[20:30, 30:40] = 1  # 100 independently counted pixels.

    def candidate(self):
        from src.target_candidate import save_candidate
        return save_candidate(self.source, self.mask, {'method': 'synthetic test outline'},
                              self.base/'candidates')

    def session(self):
        from src.target_review import ReviewSession
        return ReviewSession(self.source, self.candidate()/'candidate_mask.png',
            calibration_path=self.calibration, output_root=self.base/'ground_truth')

    def payload(self, mask=None):
        array = self.mask if mask is None else mask
        return dict(width=120, height=100,
                    mask_base64=base64.b64encode(array.tobytes()).decode())

    def test_candidate_is_native_unapproved_create_only_with_bound_provenance(self):
        folder = self.candidate()
        before = (folder/'candidate_mask.png').read_bytes()
        meta = json.loads((folder/'candidate.json').read_text())
        self.assertEqual(meta['measurement_status'], 'UNREVIEWED_CANDIDATE')
        self.assertIs(meta['human_approved'], False)
        self.assertNotIn('projected_area_cm2', meta)
        self.assertEqual(meta['image_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        with Image.open(folder/'candidate_mask.png') as image:
            self.assertEqual(image.size, (120, 100))
            self.assertEqual(image.mode, 'L')
            self.assertEqual(set(np.unique(image)), {0, 255})
            self.assertEqual(image.info['measurement_status'], 'UNREVIEWED_CANDIDATE')
        with Image.open(folder/'candidate_overlay.png') as image:
            self.assertEqual(image.size, (120, 100))
            self.assertEqual(image.info['measurement_status'], 'UNREVIEWED_CANDIDATE')
        self.assertNotEqual(self.candidate(), folder)
        self.assertEqual((folder/'candidate_mask.png').read_bytes(), before)
        self.assertFalse((self.base/'ground_truth').exists())

    def test_draft_never_creates_ground_truth_and_approval_uses_edited_pixels_and_scale(self):
        session = self.session()
        edited = self.mask.copy(); edited[20:25, 30:40] = 0; edited[40:42, 50:55] = 1
        payload = self.payload(edited)  # 50 remaining + 10 painted = 60.
        draft = session.save_draft(payload)
        with Image.open(Path(draft['output'])/'draft_mask.png') as image:
            self.assertEqual(image.size, (120, 100))
            self.assertEqual(np.count_nonzero(np.array(image)), 60)
            self.assertEqual(image.info['measurement_status'], 'UNREVIEWED_DRAFT')
        self.assertFalse((self.base/'ground_truth').exists())
        for approval in [{}, {'approval': 'APPROVE'},
                         {'approval': 'APPROVE', 'review': dict.fromkeys(REVIEW_KEYS, 'true')}]:
            with self.assertRaises(ValueError):session.approve({**payload, **approval})
        self.assertFalse((self.base/'ground_truth').exists())
        result = session.approve({**payload, 'approval': 'APPROVE',
                                  'review': dict.fromkeys(REVIEW_KEYS, True)})
        output = Path(result['output'])
        meta = json.loads((output/'measurement.json').read_text())
        self.assertEqual(meta['measurement_status'], 'MANUAL_GROUND_TRUTH')
        self.assertEqual(meta['pixel_area'], 60)
        self.assertAlmostEqual(meta['projected_area_cm2'], .2032344)
        self.assertEqual(meta['pot_diameter_cm'], 5.82)
        self.assertEqual(meta['review_method'], 'explicit_browser_APPROVE')
        self.assertIn('UNREVIEWED_CANDIDATE', meta['label_origin'])
        self.assertEqual((output/'reviewed_pot_calibration.json').read_bytes(), self.calibration.read_bytes())
        with Image.open(output/'reviewed_mask.png') as image:
            self.assertTrue(np.array_equal(np.array(image), edited*255))
        self.assertIn('candidate_mask_sha256', meta['imported_mask'])
        with self.assertRaises(ValueError):session.approve({**payload, 'approval':'APPROVE', 'review':dict.fromkeys(REVIEW_KEYS,True)})

    def test_same_clock_tick_cannot_overwrite_or_block_new_candidate(self):
        with patch('src.target_candidate.datetime') as clock:
            clock.now.return_value.strftime.return_value='20000101T000000_000000Z'
            first=self.candidate()
            before=(first/'candidate_mask.png').read_bytes()
            second=self.candidate()
        self.assertNotEqual(first,second)
        self.assertEqual((first/'candidate_mask.png').read_bytes(),before)

    def test_rejects_invalid_geometry_soft_pixels_and_stale_inputs_before_writes(self):
        session = self.session()
        payload = self.payload()
        for update in [dict(width=60), dict(height=50), dict(mask_base64='?'),
                       dict(mask_base64=base64.b64encode(bytes(120)).decode()),
                       dict(mask_base64=base64.b64encode(bytes([128])*12000).decode())]:
            with self.subTest(update=list(update)):
                with self.assertRaises(ValueError):session.save_draft({**payload, **update})
        original = self.source.read_bytes()
        self.source.write_bytes(original+b'changed')
        with self.assertRaises(ValueError):session.save_draft(payload)
        self.source.write_bytes(original)
        self.calibration.write_bytes(self.calibration.read_bytes()+b' ')
        with self.assertRaises(ValueError):session.approve({**payload, 'approval': 'APPROVE', 'review':dict.fromkeys(REVIEW_KEYS, True)})
        self.assertFalse((self.base/'ground_truth').exists())

    def test_candidate_binding_rejects_other_image_or_modified_candidate(self):
        from src.target_review import ReviewSession
        candidate = self.candidate()/'candidate_mask.png'
        Image.new('L', (120,100), 255).save(candidate)
        with self.assertRaises(ValueError):
            ReviewSession(self.source, candidate, calibration_path=self.calibration)
        candidate = self.candidate()/'candidate_mask.png'
        Image.new('RGB', (120,100), (0,0,0)).save(self.source)
        with self.assertRaises(ValueError):
            ReviewSession(self.source, candidate, calibration_path=self.calibration)

    def test_http_requires_local_origin_token_and_explicit_approval(self):
        from src.target_review import make_server
        session = self.session()
        server = make_server(session, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        host = '127.0.0.1:'+str(server.server_port)
        def request(method, route, data=None, headers=None):
            client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            client.request(method,route,json.dumps(data) if data is not None else None,headers or {})
            response=client.getresponse(); result=response.status,response.read(); client.close(); return result
        headers={'X-Review-Token':session.token,'Content-Type':'application/json','Origin':'http://'+host}
        self.assertEqual(request('GET','/')[0],200)
        self.assertEqual(request('GET','/session')[0],403)
        self.assertEqual(request('GET','/session',headers=headers)[0],200)
        self.assertEqual(request('POST','/approve',self.payload(),{**headers,'Origin':'https://example.org'})[0],403)
        self.assertEqual(request('POST','/approve',self.payload(),headers)[0],400)
        self.assertFalse((self.base/'ground_truth').exists())
        status,raw=request('POST','/draft',self.payload(),headers)
        self.assertEqual(status,200); self.assertEqual(json.loads(raw)['measurement_status'],'UNREVIEWED_DRAFT')
        self.assertFalse((self.base/'ground_truth').exists())
        self.assertEqual(request('GET','/../TASKS.md',headers=headers)[0],404)
        self.assertEqual(request('GET','/session',headers={**headers,'Host':'foreign.example'})[0],403)


if __name__ == '__main__':
    unittest.main()
