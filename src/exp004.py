"""Frozen EXP-003 C external validation. Screening never invokes inference.

Run from the project root: python -m src.exp004 screen|lock|evaluate
Review decisions must be completed between screen and lock.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/EXP-004'
RAW = ROOT / 'data/raw/AgRibot'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fields); w.writeheader(); w.writerows(rows)


def load_mask(path):
    with Image.open(path) as im:
        a = np.asarray(im).copy()
    if a.ndim != 2 or not set(np.unique(a)).issubset({0, 1, 255}):
        raise ValueError('nonbinary_reference')
    return a > 0


def inventory(raw):
    """Tags resolve within the same date/plot; any unassigned mask is an error."""
    raw = Path(raw); frames = []; pairs = []; assigned = set()
    for p in sorted(raw.rglob('color_*.png')):
        match = re.fullmatch(r'color_(\d+(?:-\d+)*)\.png', p.name)
        if not match: raise ValueError('Unexpected RGB name: ' + str(p))
        tags = match[1].split('-')
        if len(tags) != len(set(tags)): raise ValueError('Duplicate tag in frame')
        date, plot = p.relative_to(raw).parts[:2]
        datetime.strptime(date, '%Y-%m-%d')
        if not re.fullmatch(r'N[0-3](?:I|II|III|IV)', plot): raise ValueError('Unexpected plot')
        frame_id = date + '_' + plot + '_' + p.stem
        rgb_error = ''
        try:
            with Image.open(p) as im:
                im.load(); w, h = im.size
                if im.mode != 'RGB': rgb_error = 'non_RGB_input'
        except (OSError, ValueError): w = h = 0; rgb_error = 'corrupt_RGB'
        common = dict(frame_id=frame_id, rgb_path=str(p.resolve()), date=date, plot=plot,
                      treatment=plot[:2], target_count=len(tags), width=w, height=h)
        frames.append(common)
        for tag in tags:
            mask = p.parent / ('plant_mask_' + tag + '.png')
            if mask in assigned: raise ValueError('Mask assigned more than once')
            assigned.add(mask); error = rgb_error; area = 0
            if not mask.is_file(): error = 'missing_reference'
            else:
                try:
                    gt = load_mask(mask); area = int(gt.sum())
                    if gt.shape != (h, w): error = 'canvas_mismatch'
                    elif not area: error = 'empty_reference'
                except (OSError, ValueError) as e: error = str(e)
            pairs.append(dict(common, pair_id=f'{frame_id}_tag{tag}', tag=tag,
                              mask_path=str(mask.resolve()), gt_pixel_area=area, structural_error=error))
    if set(raw.rglob('plant_mask_*.png')) != assigned:
        raise ValueError('Orphan/unassigned target masks; pairing must be resolved before screening')
    return frames, pairs


def union_masks(masks):
    if not masks: raise ValueError('Empty target set')
    return np.logical_or.reduce(masks)


def included(row):
    return row['include'] is True or str(row['include']).lower() == 'true'


def complete_frames(rows):
    groups = defaultdict(list)
    for r in rows: groups[r['frame_id']].append(r)
    return {f for f, rr in groups.items() if all(included(r) for r in rr)}


def pixel_metrics(gt, pred):
    if gt.shape != pred.shape or not gt.any(): raise ValueError('Invalid metric canvas/reference')
    g = int(gt.sum()); p = int(pred.sum()); i = int((gt & pred).sum()); u = g + p - i
    signed = 100 * (p-g)/g
    return dict(iou=i/u, dice=2*i/(g+p), gt_pixel_area=g, predicted_pixel_area=p,
                absolute_pixel_area_error=abs(p-g), percentage_area_error=abs(signed),
                signed_percentage_area_error=signed, intersection_pixels=i, union_pixels=u,
                false_positive_pixels=p-i, false_negative_pixels=g-i,
                catastrophic_over=signed > 100, catastrophic_under=signed < -50)


def aggregate(rows):
    if not rows: return dict(n=0)
    n = len(rows); e = np.array([float(r['percentage_area_error']) for r in rows])
    signed = np.array([float(r['signed_percentage_area_error']) for r in rows])
    flagged = np.array([str(r.get('qc_flag', False)) == 'True' for r in rows])
    bad = (signed > 100) | (signed < -50)
    tp = int((flagged & bad).sum()); fp = int((flagged & ~bad).sum())
    fn = int((~flagged & bad).sum()); tn = int((~flagged & ~bad).sum())
    return dict(n=n, mean_iou=float(np.mean([float(r['iou']) for r in rows])),
                mean_dice=float(np.mean([float(r['dice']) for r in rows])),
                mean_area_error_percent=float(e.mean()), median_area_error_percent=float(np.median(e)),
                p90_area_error_percent=float(np.quantile(e, .9)), p95_area_error_percent=float(np.quantile(e, .95)),
                mean_absolute_pixel_area_error=float(np.mean([float(r['absolute_pixel_area_error']) for r in rows])),
                mean_signed_area_error_percent=float(signed.mean()), overestimates=int((signed > 0).sum()),
                underestimates=int((signed < 0).sum()), exact_area=int((signed == 0).sum()),
                catastrophic_over=int((signed > 100).sum()), catastrophic_under=int((signed < -50).sum()),
                absolute_error_over50=int((e > 50).sum()), qc_flagged=int(flagged.sum()),
                qc_coverage=float((~flagged).mean()), qc_tp=tp, qc_fp=fp, qc_fn=fn, qc_tn=tn,
                qc_precision=tp/(tp+fp) if tp+fp else None, qc_recall=tp/(tp+fn) if tp+fn else None)


def frozen_predict(rgb, cfg):
    """Exact original C call and QC self-reference; GT cannot be supplied."""
    from .robust_roi import robust_segment, quality_check, measurement
    pred, roi, card, diag = robust_segment(rgb, cfg['A'], cfg['C'])
    qc = quality_check(pred, pred, diag, cfg['qc_threshold'])
    return pred, roi, diag, qc, measurement(pred, qc)


def verify_lock(out):
    lock = json.loads((Path(out) / 'screen_lock.json').read_text(encoding='utf-8'))
    for path, expected in lock['files'].items():
        if sha(path) != expected: raise ValueError('Screen/source lock drift: ' + path)
    return lock


def tint(rgb, mask, color=(255, 40, 160)):
    a = rgb.copy(); a[mask] = .55*a[mask] + .45*np.array(color)
    return Image.fromarray(a)


def tile(image, size=(384, 216)):
    canvas = Image.new('RGB', size)
    image = image.copy(); image.thumbnail(size)
    canvas.paste(image, ((size[0]-image.width)//2, (size[1]-image.height)//2))
    return canvas


def screen(out=OUT):
    if (out / 'pair_inventory.csv').exists(): raise FileExistsError('Existing screen; no overwrite')
    frames, pairs = inventory(RAW)
    write_csv(out / 'frame_inventory.csv', frames); write_csv(out / 'pair_inventory.csv', pairs)
    decisions = []
    (out / 'screening').mkdir()
    for start in range(0, len(pairs), 8):
        sheet = Image.new('RGB', (1536, 8*244), 'white'); draw = ImageDraw.Draw(sheet)
        for j, r in enumerate(pairs[start:start+8]):
            number = start+j+1; y = j*244
            decisions.append(dict(pair_id=r['pair_id'], review_id=number, include='', reason='', warning='', reviewer='Codex'))
            draw.text((4, y+2), f"{number:03d} {r['date']} {r['plot']} tag {r['tag']} | RGB / mask / overlay / target zoom | structural: {r['structural_error']}", fill='black')
            if r['structural_error']: continue
            rgb = np.asarray(Image.open(r['rgb_path']).convert('RGB')); gt = load_mask(r['mask_path'])
            yy, xx = np.where(gt); box=(max(0,int(xx.min())-20), max(0,int(yy.min())-20), min(rgb.shape[1],int(xx.max())+21), min(rgb.shape[0],int(yy.max())+21))
            panels = [Image.fromarray(rgb), Image.fromarray(gt.astype(np.uint8)*255), tint(rgb, gt), tint(rgb,gt).crop(box)]
            for k, im in enumerate(panels): sheet.paste(tile(im), (k*384, y+24))
        sheet.save(out / 'screening' / f'page_{start//8+1:02d}.jpg', quality=95)
    write_csv(out / 'review_decisions.csv', decisions)
    print(f'Pre-prediction inventory: {len(frames)} frames / {len(pairs)} masks; review all sheets, then lock', flush=True)


def lock_screen(out=OUT):
    if (out / 'screen_lock.json').exists(): raise FileExistsError('Existing screen lock')
    pairs = read_csv(out / 'pair_inventory.csv'); reviews = read_csv(out / 'review_decisions.csv')
    decisions = {r['pair_id']: r for r in reviews}
    if len(decisions) != len(reviews) or set(decisions) != {r['pair_id'] for r in pairs}: raise ValueError('Review inventory mismatch')
    for r in pairs:
        d = decisions[r['pair_id']]
        if d['include'] not in ('True', 'False') or not d['reason'] or d['reviewer'] != 'Codex': raise ValueError('Incomplete reference review')
        if r['structural_error'] and d['include'] == 'True': raise ValueError('Cannot retain structural defect')
        r.update(d)
    complete = complete_frames(pairs)
    for r in pairs:
        r['primary_frame_eligible'] = r['frame_id'] in complete
        r['frame_exclusion_reason'] = '' if r['frame_id'] in complete else 'incomplete_target_set_after_reference_screen'
    fields = list(pairs[0])
    write_csv(out / 'selected_images.csv', [r for r in pairs if included(r)], fields)
    write_csv(out / 'excluded_images.csv', [r for r in pairs if not included(r)], fields)
    prov = json.loads((out / 'provenance_before.json').read_text())
    files = {str(ROOT / p): v for category in ['raw_sha256', 'previous_artifact_sha256', 'prior_source_sha256'] for p,v in prov[category].items()}
    for name, expected in prov['source_sha256'].items():
        for p in [ROOT/'src'/name, out/'frozen_source'/name]: files[str(p)] = expected
    files[str(out/'config.json')] = prov['config_sha256']
    for p in [out/'methodology.md', out/'pair_inventory.csv', out/'frame_inventory.csv', out/'review_decisions.csv', out/'selected_images.csv', out/'excluded_images.csv', ROOT/'src/exp004.py']:
        files[str(p)] = sha(p)
    write_json(out/'screen_lock.json', dict(status='SCREEN_LOCKED_BEFORE_PREDICTIONS', locked_at_utc=datetime.now(timezone.utc).isoformat(), files=files,
        usable_pairs=sum(included(r) for r in pairs), complete_frames=len(complete), excluded_pairs=sum(not included(r) for r in pairs)))
    verify_lock(out); print('Reference screen and inference files locked before predictions', flush=True)


def save_mask(path, mask):
    Image.fromarray(mask.astype(np.uint8)*255).save(path)


def diagnostic_record(frame_id, diag, qc, area):
    if 'raw_pixel_area' in diag and diag['raw_pixel_area'] != area['raw_pixel_area']:
        raise ValueError('Diagnostic/measurement area mismatch')
    return {'frame_id': frame_id, **diag, **qc, **area}


def evaluate(out=OUT):
    if any((out/n).exists() for n in ['masks', 'metrics.csv', 'pair_metrics.csv']): raise FileExistsError('Existing evaluation; use a separate derived run, never overwrite')
    verify_lock(out)
    cfg = json.loads((out/'config.json').read_text()); pairs = read_csv(out/'selected_images.csv')
    grouped = defaultdict(list)
    for r in pairs: grouped[r['frame_id']].append(r)
    for folder in ['masks', 'roi_masks', 'target_union_masks', 'diagnostics']: (out/folder).mkdir()
    frame_rows=[]; pair_rows=[]; diags=[]
    from .lettuce_cv import segment
    for idx, (frame_id, rr) in enumerate(sorted(grouped.items())):
        rgb = np.asarray(Image.open(rr[0]['rgb_path']).convert('RGB'))
        pred, roi, diag, qc, area = frozen_predict(rgb, cfg)
        save_mask(out/'masks'/f'{frame_id}.png', pred); save_mask(out/'roi_masks'/f'{frame_id}.png', roi)
        diags.append(diagnostic_record(frame_id, diag, qc, area))
        for r in rr:
            gt = load_mask(r['mask_path'])
            pair_rows.append(dict(r, **pixel_metrics(gt,pred), **qc, **area))
        if rr[0]['primary_frame_eligible'] == 'True':
            gt = union_masks([load_mask(r['mask_path']) for r in rr]); g=int(gt.sum())
            save_mask(out/'target_union_masks'/f'{frame_id}.png', gt)
            base = segment(rgb, cfg['A']) # Post-hoc diagnostic only; C input remains full RGB.
            common = {k:rr[0][k] for k in ['frame_id','rgb_path','date','plot','treatment','target_count','width','height']}
            frame_rows.append(dict(common, **pixel_metrics(gt,pred), **qc, **area,
                gt_outside_roi_percent=100*int((gt & ~roi).sum())/g,
                gt_rejected_by_A_percent=100*int((gt & ~base).sum())/g,
                localization_source=diag['localization_source']))
        if idx%8==0: print(f'Frozen C inputs {idx+1}/{len(grouped)}', flush=True)
    write_csv(out/'metrics.csv', frame_rows); write_csv(out/'pair_metrics.csv', pair_rows); write_csv(out/'roi_diagnostics.csv', diags)
    groups=[]
    for field in ['date','plot','treatment','target_count']:
        for value in sorted({r[field] for r in frame_rows}):
            groups.append(dict(group_by=field, group=value, **aggregate([r for r in frame_rows if r[field]==value])))
    write_csv(out/'group_metrics.csv', groups)
    summaries = dict(primary_complete_frames=aggregate(frame_rows), single_target_frames=aggregate([r for r in frame_rows if int(r['target_count'])==1]),
        shared_two_target_frames=aggregate([r for r in frame_rows if int(r['target_count'])==2]),
        paired_target_diagnostics_NOT_independent=aggregate(pair_rows),
        QC_accepted_only=aggregate([r for r in frame_rows if not r['qc_flag']]),
        inference_inputs=len(grouped), annotation_pairs=len(pair_rows), no_scale_calibration=True)
    write_json(out/'results.json', summaries)
    verify_lock(out)
    print(json.dumps(summaries, indent=2), flush=True)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('command', choices=['screen','lock','evaluate']); args=ap.parse_args()
    {'screen':screen,'lock':lock_screen,'evaluate':evaluate}[args.command]()


if __name__ == '__main__': main()
