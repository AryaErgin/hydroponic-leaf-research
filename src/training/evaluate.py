"""Native public/real challenger comparison; no fabricated metrics or promotion."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image, ImageDraw

from .common import ROOT, bounded, read_manifest, relative, sha, write_json, write_manifest
from .datasets import load_sample
from .inference import load_predictor, predict_native
from .metrics import aggregate, binary_metrics
from .models import RuntimeUnavailable

REQUIRED_BASELINE_SOURCES = tuple(sorted(
    [relative(path) for path in (ROOT/'src/hydroponic').glob('*.py')] +
    [relative(path) for path in (ROOT/'configs').glob('**/*.json')] +
    ['src/quick_test.py', 'src/lettuce_cv.py', 'src/roi_cv.py']))


def frozen_baseline(rgb):
    from ..hydroponic.config import load_profiles
    from ..quick_test import process_single_plant_rgb
    species, acquisition = load_profiles('lettuce')
    result = process_single_plant_rgb(rgb, species, acquisition)
    if result['mask'] is None:
        raise ValueError('Frozen baseline mask unavailable')
    return result['mask']


def _structural_failure(area, total):
    return area == 0 or area == total


def _summary(scores):
    result = aggregate(scores)
    result['qc_failure_rate'] = float(np.mean([bool(r['structural_qc_failure']) for r in scores]))
    return result


def _comparison(summary):
    return dict(iou=summary['mean_iou'], dice=summary['mean_dice'],
                area_mape=summary['mean_abs_percentage_area_error'],
                qc_failure_rate=summary['qc_failure_rate'])


def evaluate_rows(samples, predictor, baseline_predictor=None, *, threshold=.5,
                  training_rows=None, previous_predictor=None, real_validation=False,
                  current_champion_version=None, baseline_scores=None, artifact_writer=None):
    baseline_predictor = baseline_predictor or frozen_baseline
    scores, baselines, previous, sample_records = [], [], [], []
    for row, rgb, reference in samples:
        sample_records.append(row)
        start = time.perf_counter()
        candidate = predict_native(rgb, predictor, threshold=threshold)
        elapsed = time.perf_counter()-start
        identity = dict(id=row['id'], dataset=row['dataset'], split=row['split'], group_id=row['group_id'],
                        domain=row.get('domain', row['dataset']), diagnostic=bool(row.get('diagnostic')),
                        synthetic=bool(row.get('synthetic')), training_anchor=row.get('split') == 'TRAIN_ANCHOR',
                        image_sha256=row.get('image_sha256'), mask_sha256=row.get('mask_sha256'))
        measure = binary_metrics(candidate['mask'], reference)
        measure.update(inference_seconds=elapsed,
                       structural_qc_failure=_structural_failure(measure['predicted_pixel_area'], reference.size))
        scores.append({**identity, **measure})
        if artifact_writer is not None:
            artifact_writer(row, rgb, reference, candidate['mask'], scores[-1])
        if baseline_scores is not None:
            cached = baseline_scores[row['id']]
            if cached['true_pixel_area'] != int(reference.sum()):
                raise ValueError('Cached baseline reference area differs')
            baseline = {key: cached[key] for key in binary_metrics(np.zeros(reference.shape, bool), reference)}
            baseline['inference_seconds'] = cached['inference_seconds']
        else:
            start = time.perf_counter()
            baseline = binary_metrics(np.asarray(baseline_predictor(rgb)), reference)
            baseline['inference_seconds'] = time.perf_counter()-start
        baseline['structural_qc_failure'] = _structural_failure(baseline['predicted_pixel_area'], reference.size)
        baselines.append({**identity, **baseline})
        if previous_predictor is not None:
            start = time.perf_counter()
            prior = predict_native(rgb, previous_predictor, threshold=threshold)
            values = binary_metrics(prior['mask'], reference)
            values.update(inference_seconds=time.perf_counter()-start,
                          structural_qc_failure=_structural_failure(values['predicted_pixel_area'], reference.size))
            previous.append({**identity, **values})
    if not scores: raise ValueError('No evaluation samples')
    grouped, baseline_groups = defaultdict(list), defaultdict(list)
    for score, baseline in zip(scores, baselines):
        grouped[(score['dataset'], score['split'])].append(score)
        baseline_groups[(score['dataset'], score['split'])].append(baseline)
    domains = [dict(dataset=dataset, split=split, **_summary(items),
                    baseline=_summary(baseline_groups[(dataset, split)]))
               for (dataset, split), items in sorted(grouped.items())]
    diagnostic_items = [r for r in scores if r['diagnostic'] or r['training_anchor'] or 'DIAGNOSTIC' in r['split']]
    report = dict(status='COMPLETED', scope='REAL_RETRAIN' if real_validation else 'EXP006_PUBLIC_RESEARCH',
                  target_validation='UNVALIDATED / REVIEW REQUIRED',
                  measurement_target='Visible projected lettuce reference pixels; no true 3D area',
                  qc_definition='Same structural sanity for all models: empty or full-image foreground; semantic QC unvalidated',
                  rows=scores, baseline_rows=baselines, domains=domains,
                  diagnostic=_summary(diagnostic_items) if diagnostic_items else None,
                  automatic_promotion=False, confirmation=[], selection=None,
                  reference_limitations='OAK classical exposure; AgRibot oracle annotation-localized crops and uncertain SAM2 contours; own anchor is development only')
    if diagnostic_items:
        report['diagnostic'].update(scope='DEVELOPMENT / DIAGNOSTIC',
            training_anchor=any(r['training_anchor'] for r in diagnostic_items), individual_scores=diagnostic_items)
    select = [r for r in scores if r['dataset'] == 'oak1' and r['split'] == 'DEV' and not r['diagnostic'] and not r['synthetic']]
    select_ids = {r['id'] for r in select}
    if select:
        report['selection'] = dict(domain='oak1_dev', **_summary(select),
            synthetic_count=0, baseline=_summary([r for r in baselines if r['id'] in select_ids]))
    known_training = training_rows is not None and bool(training_rows)
    training_rows = training_rows or []
    selection_rows = [r for r in sample_records if r.get('split') == 'DEV']
    development_groups = {r.get('split_group_id', r.get('group_id')) for r in training_rows + selection_rows}
    development_images = {r.get('image_sha256') for r in training_rows + selection_rows}
    for domain in domains:
        if domain['split'] not in ('PUBLIC_TEST', 'EXTERNAL_TEST'):
            continue
        entries = [r for r in sample_records if (r['dataset'], r['split']) == (domain['dataset'], domain['split'])]
        disjoint = (known_training and
            not development_groups & {r.get('split_group_id', r.get('group_id')) for r in entries} and
            not development_images & {r.get('image_sha256') for r in entries} and
            all(r.get('group_id') and r.get('image_sha256') for r in entries))
        report['confirmation'].append({**domain, 'domain': entries[0].get('domain', domain['dataset']),
            'group_disjoint': bool(disjoint), 'synthetic': any(r.get('synthetic') for r in entries),
            'physical_plant_independence': 'Metadata-group separation; physical identity outside documented groups remains limited'})
    if real_validation:
        original = sample_records
        train_groups = {r.get('group_id') for r in training_rows}
        train_images = {r.get('image_sha256') for r in training_rows}
        overlaps = sum(r.get('group_id') in train_groups or r.get('image_sha256') in train_images for r in original)
        groups = {r.get('group_id') for r in original}; images = {r.get('image_sha256') for r in original}
        independent = (known_training and None not in groups and None not in images and
                       len(images) == len(original) and overlaps == 0 and
                       all(r.get('dataset') == 'real_approved' and r.get('split') == 'real_validation' for r in original))
        report['validation'] = dict(kind='real_untouched', mask_count=len(original), plant_count=len(groups),
            plant_group_count=len(groups), plant_disjoint=bool(independent),
            synthetic_count=sum(bool(r.get('synthetic')) for r in original),
            diagnostic_count=sum(bool(r.get('diagnostic') or r.get('training_anchor')) for r in original),
            training_overlap_count=overlaps)
        report['comparison'] = dict(status='COMPLETED', current_champion_version=current_champion_version,
            candidate=_comparison(_summary(scores)), baseline=_comparison(_summary(baselines)),
            champion=_comparison(_summary(previous)) if previous else None)
    # Raise rather than serialize any NaN/Inf, including injected/cached metric corruption.
    json.dumps(report, allow_nan=False)
    return report


def evaluate_manifest(manifest, predictor, baseline_predictor=None, splits=None, threshold=.5, **options):
    rows = read_manifest(manifest)
    selected = [r for r in rows if r.get('split') in splits] if splits else [r for r in rows if r.get('split') != 'TRAIN']
    training = options.pop('training_rows', [r for r in rows if r.get('split') in ('TRAIN', 'TRAIN_ANCHOR')])
    samples = ((row, *load_sample(row)) for row in selected)
    report = evaluate_rows(samples, predictor, baseline_predictor, threshold=threshold, training_rows=training, **options)
    report.update(manifest=relative(manifest), manifest_sha256=sha(manifest))
    if report.get('scope') == 'REAL_RETRAIN':
        report['validation']['manifest_sha256'] = sha(manifest)
    return report


def _baseline_cache(path, manifest, binding_path=None):
    path = bounded(path)
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('status') != 'COMPLETED' or report.get('manifest_sha256') != sha(manifest):
        raise ValueError('Baseline cache is incomplete or bound to a different manifest')
    metrics_path = path.parent/'per_sample_metrics.jsonl'
    binding = report
    legacy = not report.get('per_sample_metrics_sha256')
    if legacy:
        legacy_path = bounded(binding_path) if binding_path else path.parent/'cache_binding_v2.json'
        if not legacy_path.exists() and binding_path is None: legacy_path = path.parent/'cache_binding.json'
        binding = json.loads(legacy_path.read_text(encoding='utf-8'))
        if binding.get('report_sha256') != sha(path) or binding.get('manifest_sha256') != sha(manifest):
            raise ValueError('Legacy baseline report/manifest binding differs')
    if sha(metrics_path) != binding.get('per_sample_metrics_sha256'):
        raise ValueError('Cached baseline metrics SHA256 differs')
    sources = binding.get('sources', {})
    normalized = {key.replace('\\', '/'): value for key, value in sources.items()}
    if not set(REQUIRED_BASELINE_SOURCES).issubset(normalized):
        raise ValueError('Baseline cache does not retain complete classical source provenance')
    if any(sha(bounded(ROOT/source)) != digest for source, digest in sources.items()):
        raise ValueError('Frozen baseline source changed since cached evaluation')
    scores = read_manifest(metrics_path)
    if len({r['id'] for r in scores}) != len(scores):
        raise ValueError('Duplicate cached baseline sample IDs')
    rows = read_manifest(manifest); samples = {r['id']: r for r in rows}
    if len(samples) != len(rows) or set(samples) != {r['id'] for r in scores}:
        raise ValueError('Cached baseline sample IDs differ from source manifest')
    correspondence = binding.get('samples', {}) if legacy else {r['id']: r for r in scores}
    if set(correspondence) != set(samples) or any(
        correspondence[key].get(field) != row[field] for key, row in samples.items()
        for field in ('image_sha256', 'mask_sha256')):
        raise ValueError('Cached baseline source image/mask identity differs')
    return {r['id']: r for r in scores}


class CaseArtifacts:
    """Bounded best/worst reservoir; saves native masks and display-only evidence."""
    def __init__(self, output):
        self.output = output; self.examples = defaultdict(list); self.diag = []

    def __call__(self, row, rgb, gt, pred, score):
        if row.get('diagnostic') or row.get('split') == 'TRAIN_ANCHOR':
            self.diag.append((row, rgb.copy(), gt.copy(), pred.copy(), score))
        if row['dataset'] in ('oak1', 'agribot_target_crop'):
            pool = self.examples[row['dataset']]
            pool.append((row, rgb.copy(), gt.copy(), pred.copy(), score))
            pool.sort(key=lambda item: (item[4]['iou'], item[0]['id']))
            if len(pool) > 6: pool[:] = pool[:3]+pool[-3:]

    def save(self):
        records = []
        selections = [('development_diagnostic', item) for item in self.diag]
        for dataset, pool in self.examples.items():
            selections += [(dataset+'/worst', item) for item in pool[:3]]
            selections += [(dataset+'/best', item) for item in pool[-3:]]
        for index, (kind, (row, rgb, gt, pred, score)) in enumerate(selections):
            directory = self.output/'cases'/kind/f'{index:03d}'; directory.mkdir(parents=True, exist_ok=False)
            Image.fromarray(pred.astype(np.uint8)*255).save(directory/'mask.png')
            overlay = rgb.copy(); overlay[pred] = (.55*rgb[pred]+.45*np.array([0,220,240])).astype(np.uint8)
            errors = rgb.copy(); errors[pred & ~gt] = [255,70,70]; errors[gt & ~pred] = [80,120,255]
            Image.fromarray(overlay).save(directory/'overlay.png'); Image.fromarray(errors).save(directory/'errors.png')
            # Native diagnostic artifacts above are never resized. Panel previews are display-only.
            width = min(650, rgb.shape[1]); height = round(rgb.shape[0]*width/rgb.shape[1])
            panel = Image.new('RGB', (3*width, height+70), 'white')
            for column, array in enumerate((rgb, overlay, errors)):
                panel.paste(Image.fromarray(array).resize((width,height)), (column*width,70))
            draw = ImageDraw.Draw(panel)
            draw.text((5,5), row['id'], fill='black')
            draw.text((5,23), f"{row['split']} group={row['group_id']} stage={row.get('stage','not supplied')}", fill='black')
            draw.text((5,41), f"IoU={score['iou']:.4f} Dice={score['dice']:.4f} FP={score['false_positive_pixels']} FN={score['false_negative_pixels']} px", fill='black')
            panel.save(directory/'panel.png')
            record = dict(kind=kind, id=row['id'], split=row['split'], group_id=row['group_id'],
                          stage=row.get('stage'), native_size_px=[rgb.shape[1],rgb.shape[0]],
                          artifacts=relative(directory), metrics=score, reference_policy=row.get('target_definition'))
            write_json(directory/'case.json', record); records.append(record)
        return records


def evaluation_run(manifest, checkpoint, config, output, *, previous_checkpoint=None,
                   previous_config=None, real_validation=False, current_champion_version=None,
                   baseline_report=None, train_manifest=None, baseline_binding=None):
    manifest, checkpoint, config, output = map(bounded, (manifest, checkpoint, config, output))
    output.mkdir(parents=True, exist_ok=False)
    base = dict(status='INCOMPLETE', scope='REAL_RETRAIN' if real_validation else 'EXP006_PUBLIC_RESEARCH',
                checkpoint_sha256=sha(checkpoint) if checkpoint.exists() else None,
                manifest_sha256=sha(manifest), target_validation='UNVALIDATED / REVIEW REQUIRED',
                started_at=datetime.now(timezone.utc).isoformat(), automatic_promotion=False)
    try:
        settings = json.loads(config.read_text(encoding='utf-8'))
        predictor = load_predictor(checkpoint, settings, settings.get('evaluation_device', 'cpu'))
        previous = None
        if (previous_checkpoint is None) != (previous_config is None):
            raise ValueError('Previous checkpoint and config must be supplied together')
        if previous_checkpoint is not None:
            previous = load_predictor(previous_checkpoint, previous_config, settings.get('evaluation_device', 'cpu'))
        if current_champion_version is not None and previous is None:
            raise ValueError('Current champion requires an actual previous-model comparison')
        training_path = bounded(train_manifest) if train_manifest else checkpoint.parent/'train_manifest.jsonl'
        training = read_manifest(training_path) if training_path.exists() else None
        if real_validation and training is None:
            raise ValueError('Real validation requires immutable challenger training manifest')
        artifacts = CaseArtifacts(output)
        options = dict(previous_predictor=previous, real_validation=real_validation, artifact_writer=artifacts,
                       current_champion_version=current_champion_version)
        if training is not None: options['training_rows'] = training
        if baseline_report: options['baseline_scores'] = _baseline_cache(baseline_report, manifest, baseline_binding)
        report = evaluate_manifest(manifest, predictor, threshold=settings.get('threshold', .5), **options)
        report.update(base, status='COMPLETED', finished_at=datetime.now(timezone.utc).isoformat(),
                      config_sha256=sha(config), architecture=settings['architecture'],
                      model_size_bytes=checkpoint.stat().st_size,
                      actual_device=settings.get('evaluation_device','cpu'),
                      inference_timing_method='Synchronous wall time per native image: letterbox, normalization, forward, sigmoid CPU transfer, native restore; checkpoint load excluded',
                      case_artifacts=artifacts.save())
        if real_validation: report['validation']['manifest_sha256'] = sha(manifest)
        if previous_checkpoint:
            report['previous_checkpoint_sha256'] = sha(bounded(previous_checkpoint))
        write_manifest(output/'per_sample_metrics.jsonl', report['rows'])
        write_manifest(output/'baseline_metrics.jsonl', report['baseline_rows'])
        if report.get('diagnostic') is not None:
            report['diagnostic']['label'] = 'DEVELOPMENT / DIAGNOSTIC; never target confirmation'
    except RuntimeUnavailable as error:
        report = {**base, 'status': error.status, 'error': str(error), 'learned_metrics_available': False, 'policy_changed': False}
    except Exception as error:
        report = {**base, 'status': 'FAILED_EVALUATION', 'error': repr(error), 'learned_metrics_available': False}
    write_json(output/'evaluation.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous-checkpoint', type=Path)
    parser.add_argument('--previous-config', type=Path)
    parser.add_argument('--current-champion-version')
    parser.add_argument('--train-manifest', type=Path)
    parser.add_argument('--baseline-report', type=Path)
    parser.add_argument('--baseline-binding', type=Path)
    parser.add_argument('--real-validation', action='store_true')
    args = parser.parse_args(argv)
    result = evaluation_run(args.manifest, args.checkpoint, args.config, args.output,
        previous_checkpoint=args.previous_checkpoint, previous_config=args.previous_config,
        current_champion_version=args.current_champion_version, real_validation=args.real_validation,
        baseline_report=args.baseline_report, train_manifest=args.train_manifest, baseline_binding=args.baseline_binding)
    print(json.dumps(result, allow_nan=False))
    return 0 if result['status'] == 'COMPLETED' else 2


if __name__ == '__main__': raise SystemExit(main())
