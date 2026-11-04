"""One-command incoming-folder smoke test using the existing hydroponic pipeline."""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin

from .hydroponic.config import ROOT, load_profiles, validate_profiles
from .hydroponic.pipeline import fingerprint, output_directory, preview, process_file, process_rgb, rgb_digest, save_result
from .hydroponic.color import segment
from .hydroponic.segmentation import quality_check
from .hydroponic.calibration import homography, projected_area
from .pot_scale import calibrate_pot, pot_overlay, validate_diameter
from .manual_pot import cached_review_path, select_manual_diameter


EXTENSIONS = {'.jpg', '.jpeg', '.png'}
EXPERIMENTAL_LABEL = 'EXPERIMENTAL / UNVALIDATED'
EXPERIMENTAL_STATUS = 'EXPERIMENTAL_UNVALIDATED'
EXPERIMENTAL_SOURCE = 'Borrowed color and seed_color from configs/species/lettuce.json; generic baseline, not Rumex calibration'
FIELDS = (
    'filename', 'species', 'raw_pixel_area', 'reported_pixel_area',
    'projected_area_cm2', 'qc_status', 'confidence', 'qc_reasons',
    'mask_available', 'mask_status', 'scale_status', 'scale_reason',
    'output_directory', 'calibration_path', 'error',
)
POT_FIELDS = ('pot_diameter_cm', 'pot_diameter_pixels', 'cm_per_pixel', 'foreground_area_cm2',
              'pot_scale_status', 'pot_detection_confidence', 'segmentation_qc_status')


def species_from_name(name):
    lower = name.casefold()
    for prefix, species in (('lettuce_', 'lettuce'), ('rumex_', 'rumex_acetosa')):
        if lower.startswith(prefix):
            return species
    return None


def process_single_plant_rgb(rgb, species, acquisition, calibration=None, image_sha256=None):
    """Existing color segmentation/QC with an operator-supplied full-image ROI."""
    validate_profiles(species, acquisition)
    # Reuse the no-segmentation path for input validation, calibration and metadata.
    # This guarantees that the frozen plant localizer is never invoked in this mode.
    template = copy.deepcopy(species)
    template.update(color=None, seed_color=None, validation={'status': 'unvalidated', 'evidence_id': None})
    result = process_rgb(rgb, template, acquisition, calibration, image_sha256)
    meta = result['metadata']
    meta['effective_profiles'] = copy.deepcopy({'species': species, 'acquisition': acquisition})
    meta['config_sha256'] = fingerprint(meta['effective_profiles'])
    meta.update(processing_mode='single_plant',
                qc_confidence_basis='operator-supplied single-plant crop and existing foreground self-consistency')
    meta['source_sha256']['src/quick_test.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if species['color'] is None:
        return result  # Unconfigured Rumex still has no mask or measurement.
    mask = segment(rgb, species['color'])  # Exact existing HSV/ExG and morphology.
    roi = np.ones(mask.shape, dtype=bool)
    diagnostics = dict(localization_source='full_image_assumed_single_plant', localization_bypassed=True,
                       localization_confidence=acquisition['confidence']['supported'] if mask.any() else 0.,
                       roi_pixel_area=int(roi.sum()), raw_pixel_area=int(mask.sum()))
    qc = quality_check(mask, diagnostics, acquisition['qc'])
    reasons = list(filter(None, qc['qc_reasons'].split(';')))
    for label, profile in (('species', species), ('acquisition', acquisition)):
        if profile['validation']['status'] == 'unvalidated':
            reasons.append(f'unvalidated_{label}_profile')
    status = 'REVIEW_REQUIRED' if reasons else (
        'PASS_DEVELOPMENT' if any(p['validation']['status'] == 'development_only' for p in (species, acquisition))
        else 'PASS')
    meta.update(raw_pixel_area=int(mask.sum()), confidence=qc['confidence'], qc_status=status,
                qc_reasons=reasons, diagnostics={**diagnostics, **qc})
    if not reasons:
        meta['reported_pixel_area'] = meta['raw_pixel_area']
        if meta['scale_status'] == 'valid':
            transform = homography(calibration, rgb.shape[:2], meta['image_sha256'], acquisition['acquisition_id'])
            meta['projected_area_cm2'] = projected_area(mask, transform)
    result.update(mask=mask, roi=roi, support=None, preview=preview(rgb, mask, roi, meta))
    return result


def process_input_file(path, species, acquisition, calibration_path=None, single_plant=False):
    """Normalize color mode in memory, keeping native geometry and original file identity."""
    path = Path(path)
    data = path.read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        if image.mode == 'RGB' and not single_plant:
            return process_file(path, species, acquisition, calibration_path)
        if image.getexif().get(274, 1) != 1:
            raise ValueError('Normalize EXIF orientation and bind calibration to normalized image first')
        mode = image.mode
        if mode not in ('RGB', 'RGBA', '1', 'L', 'LA', 'I', 'F') and not mode.startswith('I;16'):
            raise ValueError(f'Unsupported input color mode: {mode}')
        rgb = np.array(image.convert('RGB'))
    calibration = None
    if calibration_path:
        try:
            calibration = json.loads(Path(calibration_path).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            calibration = {'invalid_calibration_input': str(exc)}
    processor = process_single_plant_rgb if single_plant else process_rgb
    result = processor(rgb, species, acquisition, calibration, hashlib.sha256(data).hexdigest())
    result['metadata'].update(
        source_path=str(path.resolve()), image_identity_encoding='SHA256 of source file bytes',
    )
    if mode != 'RGB':
        result['metadata']['input_normalization'] = dict(
            source_mode=mode, target_mode='RGB', geometry='unchanged',
            alpha_handling='discarded' if mode in ('RGBA', 'LA') else 'not_present',
            normalized_rgb_sha256=rgb_digest(rgb))
    return result


def write_csv(path, records, fields=FIELDS):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def label_experimental(result):
    meta = result['metadata']
    meta.update(measurement_status=EXPERIMENTAL_LABEL, experimental_baseline_source=EXPERIMENTAL_SOURCE,
                pipeline_qc_status=meta['qc_status'], qc_status=f"{EXPERIMENTAL_STATUS}_{meta['qc_status']}")
    meta['qc_reasons'].append('experimental_unvalidated_rumex_baseline')
    original = result['preview']
    label = EXPERIMENTAL_LABEL + '\nRumex diagnostic baseline; no accuracy claim'
    bounds = ImageDraw.Draw(original).multiline_textbbox((0, 0), label)
    # Add a visible footer without covering any of the original image/diagnostics.
    canvas = Image.new('RGB', (max(original.width, bounds[2] + 12), original.height + bounds[3] + 12))
    canvas.paste(original, (0, 0))
    ImageDraw.Draw(canvas).multiline_text((6, original.height + 6), label, fill='yellow')
    result['preview'] = canvas


def apply_real_data_scale(result, source, diameter_cm, pot_path):
    meta = result['metadata']
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != meta['image_sha256']:
        raise ValueError('Image changed during processing; calibration withheld')
    with Image.open(io.BytesIO(data)) as image:
        rgb = np.array(image.convert('RGB'))
    sidecar = None
    if pot_path:
        try:
            sidecar = json.loads(pot_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            sidecar = {'invalid_pot_sidecar_input': str(exc)}
    scale = calibrate_pot(rgb, diameter_cm, meta['image_sha256'], sidecar)
    # Scaling the candidate foreground cannot turn a contaminated mask into lettuce GT.
    original_status = meta['qc_status']
    reportable_before_domain_review = meta['reported_pixel_area'] is not None
    foreground_cm2 = None
    if scale['cm_per_pixel'] is not None and reportable_before_domain_review:
        foreground_cm2 = meta['raw_pixel_area'] * scale['cm_per_pixel']**2
    meta.update(calibration_mode='real_data', pot_calibration=scale, segmentation_qc_status=original_status,
                pot_diameter_cm=diameter_cm, pot_diameter_pixels=scale['diameter_pixels'],
                cm_per_pixel=scale['cm_per_pixel'], foreground_area_cm2=foreground_cm2,
                pot_scale_status=scale['status'], pot_detection_confidence=scale['confidence'],
                reported_pixel_area=None, projected_area_cm2=None,
                scale_status='pot_reviewed_approximation' if scale['cm_per_pixel'] is not None else 'pot_review_required',
                scale_reason=';'.join(scale['reasons']) or 'Scalar approximation at reviewed pot-rim plane',
                physical_area_definition='approximate segmented foreground at pot-rim plane, not lettuce-only or true 3D area',
                target_domain_issue='user-reported moss/other green vegetation; frozen color mask does not distinguish lettuce')
    meta['qc_reasons'] = list(meta['qc_reasons']) + ['target_domain_green_contamination']
    if scale['cm_per_pixel'] is None:
        meta['qc_reasons'].append('uncertain_pot_scale')
    if original_status != 'UNCONFIGURED':
        meta['qc_status'] = 'REVIEW_REQUIRED'
    meta['source_sha256']['src/pot_scale.py'] = hashlib.sha256((ROOT/'src/pot_scale.py').read_bytes()).hexdigest()
    if scale['candidate'] and scale['candidate']['method']=='manual_two_point_diameter':
        meta['source_sha256']['src/manual_pot.py'] = hashlib.sha256((ROOT/'src/manual_pot.py').read_bytes()).hexdigest()
    result['pot_overlay'] = pot_overlay(rgb, scale)
    result['pot_review_template'] = scale['review_template'] if scale['status']=='REVIEW_REQUIRED' else None
    original = preview(rgb, result['mask'], result['roi'], meta)
    area = f'{foreground_cm2:.4f} cm2 (approximate)' if foreground_cm2 is not None else 'WITHHELD'
    label = f'REAL DATA / REVIEW REQUIRED\nMoss/other green vegetation: mask is NOT lettuce-only.\nScaled segmented foreground: {area}'
    bounds = ImageDraw.Draw(original).multiline_textbbox((0,0),label)
    canvas = Image.new('RGB',(max(original.width,bounds[2]+12),original.height+bounds[3]+12))
    canvas.paste(original,(0,0)); ImageDraw.Draw(canvas).multiline_text((6,original.height+6),label,fill='yellow')
    result['preview'] = canvas


def save_capture(result, destination):
    meta = result['metadata']
    available = result['mask'] is not None
    meta['mask_available'] = available
    meta['mask_status'] = 'SEGMENTED' if available else 'UNAVAILABLE_PLACEHOLDER'
    if meta.get('measurement_status') == EXPERIMENTAL_LABEL:
        meta['mask_status'] = f"{EXPERIMENTAL_STATUS}_{meta['mask_status']}"
    save_result(result, destination)
    (destination / 'preview.png').rename(destination / 'annotated.png')
    if 'pot_overlay' in result:
        result['pot_overlay'].save(destination / 'pot_overlay.png')
        (destination / 'pot_calibration.json').write_text(json.dumps(meta['pot_calibration'],indent=2,allow_nan=False),encoding='utf-8')
        if result['pot_review_template'] is not None:
            (destination / 'pot_review_template.json').write_text(json.dumps(result['pot_review_template'],indent=2),encoding='utf-8')
    if not available:
        # A required output-file placeholder, NEVER a segmentation or zero-area result.
        info = PngImagePlugin.PngInfo()
        info.add_text('mask_status', 'UNAVAILABLE_PLACEHOLDER: no species thresholds; no area measurement')
        Image.new('L', tuple(meta['image_size_px']), 0).save(destination / 'mask.png', pnginfo=info)
    if meta.get('measurement_status') == EXPERIMENTAL_LABEL or meta.get('calibration_mode') == 'real_data':
        info = PngImagePlugin.PngInfo()
        if meta.get('measurement_status') == EXPERIMENTAL_LABEL:
            info.add_text('measurement_status', EXPERIMENTAL_LABEL)
            info.add_text('experimental_baseline_source', EXPERIMENTAL_SOURCE)
        if meta.get('calibration_mode') == 'real_data':
            info.add_text('calibration_mode', 'real_data / REVIEW_REQUIRED')
            info.add_text('target_domain_issue', 'Moss/other green vegetation; mask NOT lettuce-only')
            if not available:
                info.add_text('mask_status', meta['mask_status'])
        for path in destination.glob('*.png'):
            with Image.open(path) as image:
                annotated = image.copy()
            annotated.save(path, pnginfo=info)
    return meta


def run_folder(input_folder=ROOT / 'data/incoming', output_root=ROOT / 'results/quick_test', acquisition=None,
               experimental_rumex=False, single_plant=False, calibration_mode='marker', pot_diameter_cm=None,
               manual_pot_diameter=False):
    if manual_pot_diameter and calibration_mode!='real_data':
        raise ValueError('--manual-pot-diameter requires --calibration-mode real_data')
    if calibration_mode not in ('marker','real_data'):
        raise ValueError('Calibration mode must be marker or real_data')
    if calibration_mode == 'real_data':
        if not single_plant:
            raise ValueError('real_data pot calibration requires --single-plant')
        validate_diameter(pot_diameter_cm)
    elif pot_diameter_cm is not None:
        raise ValueError('--pot-diameter-cm requires --calibration-mode real_data')
    incoming = Path(input_folder).resolve()
    if not incoming.is_dir():
        raise ValueError(f'Input folder does not exist: {incoming}')
    inputs = sorted((p for p in incoming.iterdir() if p.is_file() and p.suffix.casefold() in EXTENSIONS),
                    key=lambda p: (p.name.casefold(), p.name))
    # Only explicit experimental Rumex mode borrows colors; profiles on disk never change.
    profiles = {name: load_profiles(name, acquisition) for name in ('lettuce', 'rumex_acetosa')}
    if experimental_rumex:
        rumex, acquisition_profile = profiles['rumex_acetosa']
        for key in ('color', 'seed_color'):
            rumex[key] = copy.deepcopy(profiles['lettuce'][0][key])
        rumex['validation'] = {'status': 'unvalidated', 'evidence_id': None}
        validate_profiles(rumex, acquisition_profile)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output = output_directory(Path(output_root) / stamp)
    fields = FIELDS + POT_FIELDS if calibration_mode=='real_data' else FIELDS
    records = []
    print('filename | species | pixel area | QC')
    with (output / 'measurements.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, source in enumerate(inputs, 1):
            species = species_from_name(source.name)
            relative = f'{index:04d}_{source.name}'
            if experimental_rumex and species == 'rumex_acetosa':
                relative = f'{index:04d}_{EXPERIMENTAL_STATUS}_{source.name}'
            destination = output / relative
            sidecar = source.with_name(source.name + ('.pot.json' if calibration_mode=='real_data' else '.calibration.json'))
            calibration = sidecar if sidecar.is_file() else None
            if calibration_mode=='real_data':
                cached=cached_review_path(source)
                if cached.is_file():calibration=cached
            row = dict.fromkeys(fields)
            row.update(filename=source.name, species=species or 'unknown',
                       output_directory=relative, mask_available=False,
                       mask_status='UNAVAILABLE', scale_status='not_evaluated',
                       calibration_path=str(calibration) if calibration else None)
            if species is None:
                destination.mkdir()
                row.update(qc_status='SKIPPED_PREFIX', qc_reasons='unknown_filename_prefix',
                           error='Filename must begin lettuce_ or rumex_')
            else:
                try:
                    options = {'single_plant': True} if single_plant else {}
                    result = process_input_file(source, *profiles[species],
                                                calibration_path=calibration if calibration_mode=='marker' else None, **options)
                    if calibration_mode == 'real_data':
                        if manual_pot_diameter:
                            calibration=select_manual_diameter(source,pot_diameter_cm)
                            row['calibration_path']=str(calibration) if calibration else None
                        apply_real_data_scale(result, source, pot_diameter_cm, calibration)
                except (OSError, ValueError) as exc:
                    destination.mkdir()
                    row.update(qc_status='INPUT_ERROR', qc_reasons='input_read_or_format_error', error=str(exc))
                else:
                    if experimental_rumex and species == 'rumex_acetosa':
                        label_experimental(result)
                    meta = save_capture(result, destination)
                    for field in ('raw_pixel_area', 'reported_pixel_area', 'projected_area_cm2',
                                  'qc_status', 'confidence', 'mask_available', 'mask_status',
                                  'scale_status', 'scale_reason'):
                        row[field] = meta[field]
                    row['qc_reasons'] = ';'.join(meta['qc_reasons'])
                    if calibration_mode == 'real_data':
                        for field in POT_FIELDS:
                            row[field] = meta[field]
            if experimental_rumex and species == 'rumex_acetosa' and row['error']:
                row['qc_status'] = f"{EXPERIMENTAL_STATUS}_{row['qc_status']}"
                row['mask_status'] = f"{EXPERIMENTAL_STATUS}_{row['mask_status']}"
                row['qc_reasons'] += ';experimental_unvalidated_rumex_baseline'
            if row['error']:
                (destination / 'error.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
            status = f"{row['qc_status']}\nconfidence={row['confidence']}\nreasons={row['qc_reasons']}\nmask_status={row['mask_status']}\n"
            (destination / 'qc_status.txt').write_text(status, encoding='utf-8')
            write_csv(destination / 'measurements.csv', [row], fields)
            writer.writerow(row)
            stream.flush()
            records.append(row)
            area = str(row['raw_pixel_area']) if row['raw_pixel_area'] is not None else 'N/A'
            if row['raw_pixel_area'] is not None and row['reported_pixel_area'] is None:
                area += ' (diagnostic)'
            print(f"{source.name} | {row['species']} | {area} | {row['qc_status']}")
    counts = {status: sum(row['qc_status'] == status for row in records)
              for status in sorted({row['qc_status'] for row in records})}
    run = dict(
        created_at_utc=stamp, input_folder=str(incoming), image_count=len(records),
        qc_counts=counts, acquisition_profiles={name: pair[1] for name, pair in profiles.items()},
        species_profiles={name: pair[0] for name, pair in profiles.items()},
        area_units='pixels; reference-plane cm2 only with valid image-bound calibration and reportable QC',
        missing_mask_policy='UNCONFIGURED uses labeled zero-valued file placeholder; area stays null',
    )
    if experimental_rumex:
        run.update(experimental_rumex=True, rumex_measurement_status=EXPERIMENTAL_LABEL,
                   experimental_baseline_source=EXPERIMENTAL_SOURCE)
    if single_plant:
        run.update(single_plant=True, segmentation_mode='full_image_hsv_exg')
    if calibration_mode == 'real_data':
        run.update(calibration_mode='real_data',pot_diameter_cm=pot_diameter_cm,
                   target_domain_issue='user-reported moss/other green vegetation; not validated lettuce-only area',
                   scale_policy='Automatic proposals require reviewed image-bound outer-rim points and view/plane checks; no marker fallback',
                   area_units='pixels and optional approximate segmented-foreground cm2; reported lettuce area withheld')
        if manual_pot_diameter:run['manual_pot_diameter']=True
    (output / 'run.json').write_text(json.dumps(run, indent=2, allow_nan=False), encoding='utf-8')
    print(f'{len(records)} image(s). Outputs: {output}')
    if not records:
        print('No JPG/JPEG/PNG files found. Add lettuce_ or rumex_ images to the input folder.')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/incoming', help='Input folder (non-recursive)')
    parser.add_argument('--acquisition', type=Path, help='Existing acquisition JSON; default frozen EXP-003/OAK-1')
    parser.add_argument('--experimental-rumex', action='store_true',
                        help='Explicit EXPERIMENTAL / UNVALIDATED Rumex baseline using borrowed lettuce colors')
    parser.add_argument('--single-plant', action='store_true',
                        help='Assume one target plant crop; full-image existing HSV/ExG segmentation and QC, no localization')
    parser.add_argument('--calibration-mode', choices=['marker','real_data'], default='marker',
                        help='real_data: reviewed circular pot-rim scale for single-plant crops')
    parser.add_argument('--pot-diameter-cm', type=float, help='Measured outer rim diameter; requires real_data and --single-plant')
    parser.add_argument('--manual-pot-diameter',action='store_true',
                        help='Two-click reviewed OUTER-rim diameter; save image-bound calibration for reuse')
    parser.add_argument('--manual-target-mask',type=Path, metavar='IMAGE',
                        help='Review a visible-only lettuce mask for one image using its saved pot calibration; no automatic inference')
    parser.add_argument('--target-mask',type=Path, metavar='PNG',
                        help='Optional native binary PNG to import into --manual-target-mask editor')
    parser.add_argument('--confirm-target-mask-review',action='store_true',
                        help='Explicitly attest provided mask contains all visible living lettuce only, excludes moss/medium/dead tissue/pot/background, and accept reviewed pot-plane scale; save without GUI')
    args = parser.parse_args(argv)
    try:
        if args.target_mask is not None and args.manual_target_mask is None:
            raise ValueError('--target-mask requires --manual-target-mask IMAGE')
        if args.confirm_target_mask_review and (args.target_mask is None or args.manual_target_mask is None):
            raise ValueError('--confirm-target-mask-review requires --manual-target-mask IMAGE and --target-mask PNG')
        if args.manual_target_mask is not None:
            if args.calibration_mode != 'real_data':
                raise ValueError('Manual ground truth requires --calibration-mode real_data')
            if args.manual_pot_diameter or args.experimental_rumex or args.acquisition or args.single_plant or args.pot_diameter_cm is not None:
                raise ValueError('Manual target review reuses the saved pot scale; omit automatic segmentation/pot-selection options')
            from .manual_target import review_target_image
            review_target_image(args.manual_target_mask, args.target_mask,
                                confirm_external_review=args.confirm_target_mask_review)
            return
        options = {'experimental_rumex': True} if args.experimental_rumex else {}
        if args.single_plant:
            options['single_plant'] = True
        if args.calibration_mode != 'marker' or args.pot_diameter_cm is not None:
            options.update(calibration_mode=args.calibration_mode, pot_diameter_cm=args.pot_diameter_cm)
        if args.manual_pot_diameter:options['manual_pot_diameter']=True
        run_folder(args.input, ROOT / 'results/quick_test', args.acquisition, **options)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
