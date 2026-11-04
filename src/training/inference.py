"""Native geometry inference; imports Torch only for a real saved checkpoint."""
import json
import hashlib
from threading import RLock
from pathlib import Path
import numpy as np

from .common import bounded, sha
from .datasets import letterbox, restore_probability
from .models import IMAGENET_MEAN, IMAGENET_STD, build_model, require_torch

# Third-party lazy imports can expose partially initialized modules to concurrent
# cold requests. Serialize load and forward; also bound CPU/RAM per process.
_INFERENCE_LOCK = RLock()


def normalize_canvas(canvas):
    value = np.asarray(canvas)
    if value.ndim != 3 or value.shape[2] != 3 or value.dtype != np.uint8:
        raise ValueError('Expected uint8 RGB canvas')
    return np.ascontiguousarray(((value.astype(np.float32)/255 - np.asarray(IMAGENET_MEAN, np.float32)) /
                                np.asarray(IMAGENET_STD, np.float32)).transpose(2, 0, 1))


def predict_native(rgb, predictor, size=512, threshold=.5):
    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8 or min(rgb.shape[:2]) <= 0:
        raise ValueError('Expected nonempty native uint8 RGB image')
    if not np.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError('Finite binary threshold in (0,1) required')
    canvas, _, geometry = letterbox(rgb, np.zeros(rgb.shape[:2], bool), size)
    probability = np.asarray(predictor(canvas), dtype=np.float32)
    if probability.shape != (size, size) or not np.isfinite(probability).all() or np.any((probability < 0) | (probability > 1)):
        raise ValueError('Predictor must return finite canvas probabilities in [0,1]')
    native = restore_probability(probability, geometry)
    return dict(mask=native >= threshold, probability=native,
                metadata=dict(architecture=getattr(predictor, 'architecture', 'injected_predictor'),
                              threshold=float(threshold), geometry=geometry,
                              status='UNVALIDATED / REVIEW REQUIRED',
                              measurement_target='visible_projected_lettuce_canopy',
                              physical_units_available=False))


def load_predictor(checkpoint, config, device='cpu'):
    checkpoint = bounded(checkpoint)
    if checkpoint.suffix != '.safetensors':
        raise ValueError('Learned checkpoints must use safetensors')
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    settings = json.loads(bounded(config).read_text(encoding='utf-8')) if not isinstance(config, dict) else dict(config)
    torch = require_torch()
    from safetensors.torch import load_file
    model = build_model(settings['architecture'], pretrained=False, config=settings)
    model.load_state_dict(load_file(str(checkpoint), device='cpu'), strict=True)
    model.to(device); model.eval()

    class Predictor:
        architecture = settings['architecture']
        checkpoint_sha256 = sha(checkpoint)

        def __call__(self, canvas):
            tensor = torch.from_numpy(normalize_canvas(canvas)).unsqueeze(0).to(device)
            with torch.inference_mode():
                values = model(tensor)
                if values.shape != (1, 1, canvas.shape[0], canvas.shape[1]):
                    raise ValueError('Binary model logits do not match canvas')
                return torch.sigmoid(values)[0, 0].detach().cpu().numpy()
    return Predictor()


def predict_version(rgb, version=None, registry=None, device='cpu'):
    with _INFERENCE_LOCK:
        return _predict_version(rgb, version, registry, device)


def _predict_version(rgb, version=None, registry=None, device='cpu'):
    if registry is None:
        from .model_registry import ModelRegistry
        registry = ModelRegistry()
    record = registry.champion() if version is None else registry.get(version)
    if record is None:
        raise ValueError('No human-promoted learned lettuce champion is available')
    if record.get('species') != 'lettuce':
        raise ValueError('Only lettuce learned candidates are supported')
    refs = record['artifacts']
    config_path = bounded(registry.root/refs['config']['path'])
    settings = json.loads(config_path.read_text(encoding='utf-8'))
    if settings.get('architecture') != record['architecture']:
        raise ValueError('Registry/config architecture mismatch')
    predictor = load_predictor(bounded(registry.root/refs['checkpoint']['path']), settings, device)
    result = predict_native(rgb, predictor, settings.get('input_size', 512), settings.get('threshold', .5))
    result['metadata'].update(version=record['version'], checkpoint_sha256=refs['checkpoint']['sha256'],
                              checkpoint_loaded=True,config_sha256=refs['config'].get('sha256'),
                              preprocessing=dict(color='RGB',input_size=settings.get('input_size',512),
                                scale=1/255,mean=list(IMAGENET_MEAN),std=list(IMAGENET_STD),
                                resize='letterbox',probability_restore='native_bilinear'),
                              mask_sha256=hashlib.sha256(result['mask'].astype(np.uint8).tobytes()).hexdigest(),
                              registry_selection='explicit_version' if version is not None else 'human_promoted_champion')
    return result
