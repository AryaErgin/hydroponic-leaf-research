"""Lazy Torch models with explicit, local-only pretrained initialization."""
import importlib
import json
from pathlib import Path

from .common import bounded, sha

ARCHITECTURES = ('unet_resnet18', 'segformer_b0')
IMAGENET_MEAN = (.485, .456, .406)
IMAGENET_STD = (.229, .224, .225)


def runtime_error_status(error):
    if getattr(error, 'winerror', None) == 4551 or 'WinError 4551' in str(error):
        return 'BLOCKED_WINDOWS_APPLICATION_CONTROL'
    if isinstance(error, ImportError):
        return 'BLOCKED_MISSING_RUNTIME'
    return 'FAILED_RUNTIME'


class RuntimeUnavailable(RuntimeError):
    def __init__(self, cause):
        self.cause = cause
        self.status = runtime_error_status(cause)
        super().__init__(str(cause))


def require_torch():
    """Respect installed runtime policy; never attempt DLL/policy workarounds."""
    try:
        return importlib.import_module('torch')
    except (OSError, ImportError) as error:
        raise RuntimeUnavailable(error) from error


def _cached_file(path, expected=None):
    path = bounded(path)
    if not path.is_file():
        raise FileNotFoundError('Verified official pretrained weights are not cached: ' + str(path))
    digest = sha(path)
    if expected and digest != expected:
        raise ValueError('Pretrained SHA256 mismatch: ' + str(path))
    return path, digest


def _weights(torch, path):
    if path.suffix == '.safetensors':
        from safetensors.torch import load_file
        return load_file(str(path), device='cpu')
    # Official cached .pth/.bin only; disable pickle object deserialization.
    return torch.load(str(path), map_location='cpu', weights_only=True)


def build_model(architecture, pretrained=True, config=None):
    if architecture not in ARCHITECTURES:
        raise ValueError('Unsupported learned architecture')
    config = config or {}
    torch = require_torch()
    nn, functional = torch.nn, torch.nn.functional
    initialization = dict(pretrained=bool(pretrained), architecture=architecture,
                          binary_output='B x 1 x H x W logits', network_downloads=False)
    cache = config.get('pretrained', {})
    if architecture == 'unet_resnet18':
        from torchvision.models import resnet18
        encoder = resnet18(weights=None)
        if pretrained:
            path, digest = _cached_file(cache.get('resnet18_weights', 'data/models/pretrained/resnet18/resnet18-f37072fd.pth'),
                                        cache.get('resnet18_sha256'))
            encoder.load_state_dict(_weights(torch, path), strict=True)
            initialization.update(source_path=str(path), source_sha256=digest,
                                  source_url='https://download.pytorch.org/models/resnet18-f37072fd.pth',
                                  initialization='ImageNet ResNet18 encoder; newly initialized binary U-Net decoder')

        def block(cin, cout):
            return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1, bias=False),
                                 nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
                                 nn.Conv2d(cout, cout, 3, padding=1, bias=False),
                                 nn.BatchNorm2d(cout), nn.ReLU(inplace=True))

        class ResnetUNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.stem = nn.Sequential(encoder.conv1, encoder.bn1, encoder.relu)
                self.pool = encoder.maxpool
                self.layer1, self.layer2 = encoder.layer1, encoder.layer2
                self.layer3, self.layer4 = encoder.layer3, encoder.layer4
                self.decoder3, self.decoder2 = block(512+256, 256), block(256+128, 128)
                self.decoder1, self.decoder0 = block(128+64, 64), block(64+64, 64)
                self.final = nn.Sequential(block(64, 32), nn.Conv2d(32, 1, 1))

            def forward(self, x):
                size = x.shape[-2:]
                s0 = self.stem(x); s1 = self.layer1(self.pool(s0))
                s2 = self.layer2(s1); s3 = self.layer3(s2); s4 = self.layer4(s3)
                current = s4
                for skip, decoder in ((s3, self.decoder3), (s2, self.decoder2),
                                      (s1, self.decoder1), (s0, self.decoder0)):
                    current = functional.interpolate(current, size=skip.shape[-2:], mode='bilinear', align_corners=False)
                    current = decoder(torch.cat((current, skip), dim=1))
                return self.final(functional.interpolate(current, size=size, mode='bilinear', align_corners=False))
        model = ResnetUNet()
    else:
        from transformers import SegformerConfig, SegformerForSemanticSegmentation
        settings = dict(num_labels=1, id2label={0: 'lettuce'}, label2id={'lettuce': 0},
                        hidden_sizes=[32, 64, 160, 256], depths=[2, 2, 2, 2],
                        num_attention_heads=[1, 2, 5, 8], decoder_hidden_size=256)
        if pretrained:
            directory = bounded(cache.get('mit_b0_directory', 'data/models/pretrained/mit-b0'))
            source_config = json.loads((directory/'config.json').read_text(encoding='utf-8'))
            if source_config.get('hidden_sizes') != [32, 64, 160, 256] or source_config.get('depths') != [2, 2, 2, 2]:
                raise ValueError('Official MiT-B0 encoder configuration required')
            source_config.update(settings)
            settings = source_config
        network = SegformerForSemanticSegmentation(SegformerConfig(**settings))
        if pretrained:
            path = directory/'model.safetensors'
            if not path.exists():
                path = directory/'pytorch_model.bin'
            path, digest = _cached_file(path, cache.get('mit_b0_sha256'))
            state = _weights(torch, path)
            prefix = 'segformer.'
            encoder_state = {key[len(prefix):]: value for key, value in state.items()
                             if key.startswith(prefix+'encoder.')}
            if not encoder_state:
                raise ValueError('Official checkpoint contains no MiT encoder tensors')
            network.segformer.load_state_dict(encoder_state, strict=True)
            initialization.update(source_path=str(path), source_sha256=digest,
                source_url='https://huggingface.co/nvidia/mit-b0', revision=cache.get('mit_b0_revision'),
                loaded_encoder_tensor_count=len(encoder_state),
                discarded_source_heads=sorted(key for key in state if not key.startswith(prefix+'encoder.')),
                initialization='ImageNet MiT-B0 encoder; newly initialized binary SegFormer decoder')

        class BinarySegformer(nn.Module):
            def __init__(self):
                super().__init__(); self.network = network

            def forward(self, x):
                logits = self.network(pixel_values=x).logits
                return functional.interpolate(logits, size=x.shape[-2:], mode='bilinear', align_corners=False)
        model = BinarySegformer()
    model.architecture = architecture
    model.initialization_provenance = initialization
    return model
