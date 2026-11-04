"""Create-only reproducible binary challenger training; never auto-promotes."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import shutil
import numpy as np

from .augmentation import CONFIG as AUGMENTATION_CONFIG, augment
from .common import ROOT, bounded, read_manifest, relative, sha, write_json, write_manifest
from .datasets import load_sample
from .inference import normalize_canvas, predict_native
from .metrics import aggregate, binary_metrics
from .models import ARCHITECTURES, RuntimeUnavailable, build_model, require_torch

FIXED = dict(seed=6006, size=512, batch_size=8, max_epochs=60, min_epochs=20,
             patience=12, threshold=.5, real_anchor_probability=.1)
LEARNING_RATES = dict(unet_resnet18=3e-4, segformer_b0=6e-5)
LOCK_SOURCES = tuple('src/training/'+name+'.py' for name in
                     ('datasets', 'augmentation', 'models', 'inference', 'train', 'evaluate', 'metrics', 'common'))
DEFAULT_LOCK = ROOT/'experiments/EXP-006/governance/training_lock.json'


class TrainingLockError(ValueError):
    pass


def create_training_lock(manifest, configs, output, additional_files=()):
    """Future runs take fresh source/config/augmentation snapshots, never reuse initial locks."""
    output = bounded(output); manifest = bounded(manifest)
    if output.exists(): raise FileExistsError(output)
    augmentation = output.with_name(output.stem+'_augmentation_config.json')
    write_json(augmentation, AUGMENTATION_CONFIG)
    references = [(ROOT/path, 'training_source') for path in LOCK_SOURCES]
    references += [(manifest, 'manifest'), (augmentation, 'augmentation_config')]
    references += [(bounded(path), 'config:'+architecture) for architecture, path in configs.items()]
    references += [(bounded(path), 'additional') for path in additional_files]
    unique = {relative(path): dict(path=relative(path), sha256=sha(path), role=role) for path, role in references}
    record = dict(schema_version=1, files=list(unique.values()), manifest_sha256=sha(manifest),
                  configs={architecture: sha(bounded(path)) for architecture, path in configs.items()})
    write_json(output, record)
    return output


def verify_training_lock(lock, manifest, config, architecture):
    try:
        lock = bounded(lock)
        record = json.loads(lock.read_text(encoding='utf-8'))
        if record.get('schema_version') != 1 or not isinstance(record.get('files'), list):
            raise ValueError('Invalid training lock schema')
        entries = record['files']; paths = set(); augmentations = []
        for entry in entries:
            path = bounded(ROOT/entry['path']); paths.add(relative(path))
            if sha(path) != entry['sha256']:
                raise ValueError('Locked training artifact changed: '+relative(path))
            if entry.get('role') in ('augmentation_config', 'augmentation_configuration') or path.name == 'augmentation_config.json':
                augmentations.append(json.loads(path.read_text(encoding='utf-8')))
        if not set(LOCK_SOURCES).issubset(paths):
            raise ValueError('Training lock is missing required runtime source modules')
        if relative(manifest) not in paths or relative(config) not in paths:
            raise ValueError('Manifest/config paths are not locked')
        if sha(manifest) != record.get('manifest_sha256') or sha(config) != record.get('configs', {}).get(architecture):
            raise ValueError('Selected manifest/config differs from training lock')
        if not augmentations or any(value != AUGMENTATION_CONFIG for value in augmentations):
            raise ValueError('Current augmentation CONFIG differs from locked configuration')
        return sha(lock)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise TrainingLockError(str(error)) from error


def training_config(config, architecture):
    if architecture not in ARCHITECTURES:
        raise ValueError('Unsupported architecture')
    if config.get('architecture', architecture) != architecture:
        raise ValueError('Config architecture differs from selected architecture')
    settings = {**FIXED, **config}
    for key, value in FIXED.items():
        if settings[key] != value:
            raise ValueError('Frozen EXP-006 training setting differs: ' + key)
    rate = LEARNING_RATES[architecture]
    if settings.get('learning_rate', rate) != rate:
        raise ValueError('Frozen architecture learning rate differs')
    settings.update(architecture=architecture, learning_rate=rate, input_size=settings['size'],
                    normalization=dict(mean=[.485, .456, .406], std=[.229, .224, .225]),
                    optimizer='AdamW', loss='BCEWithLogits + soft Dice',
                    checkpoint_selection='DEV native mean IoU only; diagnostic rows excluded')
    return settings


def partition_rows(rows,species='lettuce'):
    public = [r for r in rows if r.get('split') == 'TRAIN']
    anchors = [r for r in rows if r.get('split') == 'TRAIN_ANCHOR']
    dev = [r for r in rows if r.get('split') == 'DEV']
    if not public or not dev:
        raise ValueError('Nonempty TRAIN and separate DEV rows are required')
    if any(r.get('diagnostic') or r.get('synthetic') for r in dev):
        raise ValueError('Diagnostic/synthetic rows cannot select checkpoints')
    for row in public + anchors + dev:
        if row.get('species', 'lettuce') != species or row.get('license_permits_research') is not True:
            raise ValueError('Verified research permission and lettuce species required')
    if any(r.get('diagnostic') for r in public):
        raise ValueError('Real diagnostic must be explicitly TRAIN_ANCHOR')
    train_groups = {r.get('split_group_id', r['group_id']) for r in public + anchors}
    train_images = {r['image_sha256'] for r in public + anchors}
    withheld = [r for r in rows if r.get('split') in ('DEV', 'PUBLIC_TEST', 'EXTERNAL_TEST', 'real_validation')]
    if train_groups & {r.get('split_group_id', r['group_id']) for r in withheld} or train_images & {r['image_sha256'] for r in withheld}:
        raise ValueError('Training and development/holdout identity overlap')
    ids = [r['id'] for r in public + anchors + dev]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate model-development IDs')
    return public, dev, anchors


def epoch_samples(public, anchors, count, seed=6006, epoch=1):
    if not public or count < 1:
        raise ValueError('Training samples required')
    rng = np.random.default_rng(np.random.SeedSequence([seed, epoch]))
    real_count = round(count * .1) if anchors else 0
    samples = [public[int(i)] for i in rng.integers(0, len(public), count-real_count)]
    samples += [anchors[int(i)] for i in rng.integers(0, len(anchors), real_count)] if real_count else []
    rng.shuffle(samples)
    return samples


def bce_dice_loss(logits, target, torch):
    if logits.shape != target.shape or logits.ndim != 4 or logits.shape[1] != 1:
        raise ValueError('Binary aligned Bx1xHxW logits/targets required')
    bce = torch.nn.functional.binary_cross_entropy_with_logits(logits, target)
    probability = torch.sigmoid(logits)
    dims = (1, 2, 3)
    dice = (2*(probability*target).sum(dim=dims)+1) / (probability.sum(dim=dims)+target.sum(dim=dims)+1)
    return bce + (1-dice).mean()


def _canvas_predictor(model, torch, device):
    def predict(canvas):
        with torch.inference_mode():
            tensor = torch.from_numpy(normalize_canvas(canvas)).unsqueeze(0).to(device)
            return torch.sigmoid(model(tensor))[0, 0].detach().cpu().numpy()
    predict.architecture = model.architecture
    return predict


def save_training_curves(curves_path, output):
    """Render only recorded epoch observations; never extrapolate blocked runs."""
    from PIL import Image, ImageDraw
    rows = read_manifest(curves_path)
    if not rows:
        raise ValueError('Training curves require actual epoch records')
    epochs = [row['epoch'] for row in rows]
    if epochs != list(range(1, len(rows)+1)):
        raise ValueError('Training epochs must be contiguous observed records')
    values = ([float(row['training_loss']) for row in rows],
              [float(row['dev']['mean_iou']) for row in rows])
    if not all(math.isfinite(value) for series in values for value in series):
        raise ValueError('Training curves contain nonfinite observations')
    output = bounded(output)
    if output.exists(): raise FileExistsError(output)
    image = Image.new('RGB', (850, 640), 'white'); draw = ImageDraw.Draw(image)
    for index, (label, series, color) in enumerate(zip(
            ('Training BCE + soft Dice loss', 'Public DEV native mean IoU'), values,
            ((170, 75, 30), (35, 120, 70)))):
        left, right, top, bottom = 85, 815, 40+index*320, 265+index*320
        low, high = (0., 1.) if index else (0., max(max(series)*1.05, 1e-9))
        draw.text((left, top-25), label, fill='black')
        draw.line((left, top, left, bottom, right, bottom), fill='black', width=2)
        for tick in range(6):
            value = low+(high-low)*tick/5; y = bottom-(bottom-top)*tick/5
            draw.line((left, y, right, y), fill=(225, 225, 225))
            draw.text((8, y-5), f'{value:.3f}', fill='black')
        points = [(left+(epoch-1)*(right-left)/max(1, len(rows)-1),
                   bottom-(value-low)*(bottom-top)/(high-low)) for epoch, value in zip(epochs, series)]
        if len(points) > 1: draw.line(points, fill=color, width=3)
        for x, y in points: draw.ellipse((x-2, y-2, x+2, y+2), fill=color)
        draw.text((left, bottom+8), '1', fill='black')
        draw.text((right-20, bottom+8), str(epochs[-1]), fill='black')
        draw.text((385, bottom+25), 'Observed epoch', fill='black')
    image.save(output)
    return dict(curves_path=relative(curves_path), curves_sha256=sha(curves_path),
                preview_path=relative(output), observed_epochs=len(rows), extrapolated=False)


def train_run(manifest, config, architecture, output, lock=None,species='lettuce'):
    if species not in ('lettuce','rumex_acetosa'):raise ValueError('Unsupported training species')
    manifest, config, output = bounded(manifest), bounded(config), bounded(output)
    output.mkdir(parents=True, exist_ok=False)
    base = dict(architecture=architecture,species=species, training_started=False, trained_model_available=False,
                target_validation='UNVALIDATED / REVIEW REQUIRED', automatic_promotion=False,
                manifest_sha256=sha(manifest), source_config_sha256=sha(config),
                started_at=datetime.now(timezone.utc).isoformat())
    try:
        base['training_lock_sha256'] = verify_training_lock(lock or DEFAULT_LOCK, manifest, config, architecture)
        settings = training_config(json.loads(config.read_text(encoding='utf-8')), architecture)
        # This gate precedes data iteration and pretrained loading. No workaround is attempted.
        torch = require_torch()
        public, dev, anchors = partition_rows(read_manifest(manifest),species=species)
        for row in public + dev + anchors:
            load_sample(row, verify=True)
        os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
        random.seed(settings['seed']); np.random.seed(settings['seed']); torch.manual_seed(settings['seed'])
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(settings['seed'])
        # Bilinear upsampling backward on CUDA has no deterministic implementation.
        # Seeds/data/augmentations remain deterministic; record rather than conceal
        # unavoidable kernel nondeterminism while keeping those two models usable.
        torch.use_deterministic_algorithms(True, warn_only=True)
        torch.backends.cudnn.benchmark = False
        device = settings.get('device', 'cuda' if torch.cuda.is_available() else 'cpu')
        model = build_model(architecture, pretrained=True, config=settings).to(device)
        from safetensors.torch import save_file
        optimizer = torch.optim.AdamW(model.parameters(), lr=settings['learning_rate'], weight_decay=settings.get('weight_decay', .01))
        initialization = model.initialization_provenance
        write_json(output/'config.json', settings)
        write_manifest(output/'train_manifest.jsonl', public + anchors)
        write_manifest(output/'dev_manifest.jsonl', dev)
        write_json(output/'augmentation_config.json', AUGMENTATION_CONFIG)
        provenance = {**base, 'initialization': initialization, 'runtime': dict(torch=torch.__version__, device=str(device)),
                      'determinism': dict(seeds_fixed=True, data_and_augmentation_deterministic=True,
                          torch_deterministic_algorithms='warn_only',
                          limitation='CUDA bilinear interpolation backward may be nondeterministic; no bit-exact GPU replay claim'),
                      'train_count': len(public), 'anchor_count': len(anchors), 'dev_count': len(dev),
                      'seed': settings['seed'], 'actual_anchor_fraction': .1 if anchors else 0.,
                      'diagnostic_anchor_is_not_confirmation': True,
                      'augmentation': AUGMENTATION_CONFIG, 'training_started': True}
        write_json(output/'provenance.json', provenance)
        checkpoints = output/'checkpoints'; checkpoints.mkdir()
        best_score, best_epoch, best_path, stale = -math.inf, 0, None, 0
        count = math.ceil((len(public)/(1-.1 if anchors else 1))/10)*10
        base['training_started'] = True
        with (output/'curves.jsonl').open('x', encoding='utf-8') as curve:
            for epoch in range(1, settings['max_epochs']+1):
                model.train(); total_loss = 0.; seen = 0
                samples = epoch_samples(public, anchors, count, settings['seed'], epoch)
                for start in range(0, len(samples), settings['batch_size']):
                    images, targets = [], []
                    for offset, row in enumerate(samples[start:start+settings['batch_size']]):
                        rgb, mask = load_sample(row)
                        image, target, _ = augment(rgb, mask,
                            seed=settings['seed']*1000000 + epoch*10000 + start + offset, size=settings['size'])
                        images.append(normalize_canvas(image)); targets.append(target.astype(np.float32)[None])
                    x = torch.from_numpy(np.stack(images)).to(device)
                    y = torch.from_numpy(np.stack(targets)).to(device)
                    optimizer.zero_grad(set_to_none=True)
                    loss = bce_dice_loss(model(x), y, torch)
                    if not bool(torch.isfinite(loss).item()):
                        raise ValueError('Nonfinite training loss; scientific run aborted')
                    loss.backward(); optimizer.step()
                    total_loss += float(loss.detach().cpu()) * len(images); seen += len(images)
                model.eval(); predictor = _canvas_predictor(model, torch, device); measures = []
                for row in dev:
                    rgb, mask = load_sample(row)
                    native = predict_native(rgb, predictor, settings['size'], settings['threshold'])
                    measures.append(binary_metrics(native['mask'], mask))
                summary = aggregate(measures); score = summary['mean_iou']
                if not math.isfinite(score):
                    raise ValueError('Nonfinite DEV score')
                improved = score > best_score
                if improved:
                    best_score, best_epoch, stale = score, epoch, 0
                    best_path = checkpoints/f'epoch_{epoch:04d}.safetensors'
                    if best_path.exists(): raise FileExistsError(best_path)
                    save_file({key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()}, str(best_path),
                              metadata={'architecture': architecture, 'epoch': str(epoch), 'selection': 'DEV native mean IoU'})
                else: stale += 1
                record = dict(epoch=epoch, training_loss=total_loss/seen, dev=summary,
                              best_epoch=best_epoch, checkpoint_improved=improved,
                              anchor_samples=sum(r.get('split') == 'TRAIN_ANCHOR' for r in samples))
                curve.write(json.dumps(record, allow_nan=False)+'\n'); curve.flush()
                print(json.dumps(record, allow_nan=False), flush=True)
                if epoch >= settings['min_epochs'] and stale >= settings['patience']:
                    break
        with best_path.open('rb') as source, (output/'model.safetensors').open('xb') as destination:
            shutil.copyfileobj(source, destination)
        curves = save_training_curves(output/'curves.jsonl', output/'training_curves.png')
        result = {**base, 'status': 'COMPLETED', 'trained_model_available': True,
                  'epochs_completed': epoch, 'best_epoch': best_epoch, 'best_dev_mean_iou': best_score,
                  'checkpoint_sha256': sha(output/'model.safetensors'), 'device': str(device),
                  'training_curves': curves,
                  'finished_at': datetime.now(timezone.utc).isoformat()}
    except TrainingLockError as error:
        result = {**base, 'status': 'BLOCKED_TRAINING_LOCK', 'error': str(error), 'policy_changed': False}
    except RuntimeUnavailable as error:
        result = {**base, 'status': error.status, 'error': str(error), 'policy_changed': False}
    except Exception as error:
        result = {**base, 'status': 'FAILED_TRAINING', 'error': repr(error)}
    write_json(output/'status.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--species',choices=('lettuce','rumex_acetosa'),default='lettuce')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--architecture', choices=ARCHITECTURES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    result = train_run(args.manifest, args.config, args.architecture, args.output, lock=args.lock,species=args.species)
    print(json.dumps(result, allow_nan=False))
    return 0 if result['status'] == 'COMPLETED' else 2


if __name__ == '__main__': raise SystemExit(main())
