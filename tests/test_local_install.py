import json
from pathlib import Path

import pytest

from src.local_setup.install import choose_device, resolve_bundle, validate_prerequisites, write_profile
from src.webapp.settings import Settings


def test_prerequisite_versions_are_bounded():
    validate_prerequisites((3, 11, 9), 'v24.18.0')
    for python, node in [((3, 14, 0), 'v24.18.0'), ((3, 11, 9), 'v18.0.0')]:
        with pytest.raises(ValueError): validate_prerequisites(python, node)


def test_device_does_not_claim_gpu_without_compatible_hardware():
    assert choose_device('auto', None) == 'cpu'
    assert choose_device('auto', '610.88') == 'cuda'
    assert choose_device('auto', '550.00') == 'cpu'
    with pytest.raises(ValueError): choose_device('cuda', None)


def test_full_bundle_required_but_demo_is_explicit(tmp_path):
    with pytest.raises(ValueError, match='authorized'): resolve_bundle(tmp_path, None, False)
    assert resolve_bundle(tmp_path, None, True) is None
    directory=tmp_path/'.model-bundles';directory.mkdir()
    bundle=directory/'research-model-exp006.zip';bundle.write_bytes(b'fixture')
    assert resolve_bundle(tmp_path, None, False) == bundle


def test_profile_written_without_account_password(tmp_path):
    path=tmp_path/'profile.json'
    write_profile(path, python=Path('/python'), node=Path('/node'), tools=Path('/tools'), mode='FULL', device='cpu')
    result=json.loads(path.read_text())
    assert result['version']==1 and result['mode']=='FULL' and result['device']=='cpu'
    assert 'password' not in path.read_text().lower()


def test_inference_device_defaults_to_cpu_and_rejects_unknown():
    assert Settings().inference_device=='cpu'
    with pytest.raises(ValueError, match='device'):
        Settings(inference_device='untrusted-device').validate()
