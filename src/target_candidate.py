"""Create unreviewed native target-mask proposals from explicit image-bound outlines.

This annotation aid neither imports nor changes the frozen automatic analyzer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import secrets

import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin

from .hydroponic.config import ROOT
from .manual_target import _binary_mask, edit_polygon, mask_overlay

STATUS = 'UNREVIEWED_CANDIDATE'
OUTPUT_ROOT = ROOT/'results/manual_candidates'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def new_folder(root, source):
    root = Path(root).resolve()
    if ROOT not in root.parents:
        raise ValueError('Annotation outputs must be below the project root')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    folder = root/(stamp+'_'+secrets.token_hex(6))/Path(source).stem
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def save_candidate(source, mask, provenance, output_root=OUTPUT_ROOT):
    source = Path(source).resolve()
    data = source.read_bytes()
    with Image.open(io.BytesIO(data)) as original:
        if original.getexif().get(274, 1) != 1:
            raise ValueError('Candidate requires original native image orientation')
        rgb = np.array(original.convert('RGB'))
    mask = _binary_mask(mask, rgb.shape[:2])
    if not mask.any():
        raise ValueError('A nonempty candidate is required')
    folder = new_folder(output_root, source)
    info = PngImagePlugin.PngInfo()
    info.add_text('measurement_status', STATUS)
    info.add_text('human_approved', 'false')
    info.add_text('image_sha256', sha256(data))
    Image.fromarray(mask.astype(np.uint8)*255).save(folder/'candidate_mask.png', pnginfo=info)
    overlay = mask_overlay(rgb, mask)
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, overlay.width, 34), fill=(15, 25, 35))
    draw.text((8, 10), 'UNREVIEWED CANDIDATE - inspect and correct before APPROVE', fill='white')
    overlay.save(folder/'candidate_overlay.png', pnginfo=info)
    meta = dict(schema_version=1, measurement_status=STATUS, human_approved=False,
        source_path=str(source), image_sha256=sha256(data), image_size_px=[rgb.shape[1], rgb.shape[0]],
        candidate_mask_sha256=sha256((folder/'candidate_mask.png').read_bytes()),
        target_definition='Only identifiable visible living lettuce tissue; no hidden tissue reconstruction',
        exclusions=['moss', 'other vegetation', 'growing medium', 'pot', 'background', 'clearly dead/dry tissue'],
        provenance=provenance,
        source_sha256={'src/target_candidate.py':sha256(Path(__file__).read_bytes())},
        next_step='Human correction and explicit APPROVE required; no ground truth or approved area exists')
    (folder/'candidate.json').write_text(json.dumps(meta, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return folder


def generate_candidate(source, outlines_path, output_root=OUTPUT_ROOT):
    data = Path(source).read_bytes(); raw = Path(outlines_path).read_bytes()
    outlines = json.loads(raw)
    with Image.open(io.BytesIO(data)) as image:
        size = list(image.size)
    if outlines.get('image_sha256') != sha256(data) or outlines.get('image_size_px') != size:
        raise ValueError('Outline image hash/native dimensions do not match the input')
    mask = np.zeros((size[1], size[0]), bool)
    for item in outlines['regions']:
        if type(item.get('include')) is not bool:
            raise ValueError('Each outline requires an explicit include boolean')
        mask = edit_polygon(mask, item['points'], item['include'])
    return save_candidate(source, mask, dict(method='Agent-traced native polygons; unreviewed annotation proposal',
        outlines=outlines, outlines_path=str(Path(outlines_path).resolve()), outlines_sha256=sha256(raw)), output_root)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--outlines',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        folder=generate_candidate(args.image,args.outlines)
    except (ValueError,OSError,KeyError) as exc:
        parser.exit(2,f'Error: {exc}\n')
    print(f'Candidate saved: {folder}\nUNREVIEWED_CANDIDATE; human approval pending. No ground truth saved.')


if __name__=='__main__':
    main()
