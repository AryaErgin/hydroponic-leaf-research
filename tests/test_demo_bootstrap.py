"""Developer bootstrap must reject cloud/remote and existing privileged accounts."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location('demo_bootstrap', Path(__file__).parents[1] / 'scripts/bootstrap_demo_owner.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.mark.parametrize('host,cloud', [(None, None), ('localhost:9099', None),
    ('remote.example.test:9099', None), ('127.0.0.1:9099', 'service')])
def test_guard_rejects_before_runtime_or_password_access(monkeypatch, host, cloud):
    monkeypatch.delenv('FIREBASE_AUTH_EMULATOR_HOST', raising=False)
    monkeypatch.delenv('K_SERVICE', raising=False)
    if host: monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', host)
    if cloud: monkeypatch.setenv('K_SERVICE', cloud)
    def forbidden(*args):
        raise AssertionError('Must reject before asking for a password')
    monkeypatch.setattr(demo, 'getpass', forbidden)
    with pytest.raises(ValueError): demo.bootstrap()


def test_only_exact_loopback_is_accepted(monkeypatch):
    monkeypatch.delenv('K_SERVICE', raising=False)
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
    demo.require_local_environment()
    assert demo.EMAIL == 'owner@example.test'


@pytest.mark.parametrize('length', [0, 12, 14, 129])
def test_password_matches_existing_frontend_minimum(monkeypatch, length):
    monkeypatch.delenv('K_SERVICE', raising=False)
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
    monkeypatch.setenv('HYDRO_DEMO_PASSWORD', 'x' * length)
    monkeypatch.setattr(demo, 'getpass', lambda prompt: 'x' * length)
    with pytest.raises(ValueError, match='15 to 128'):
        demo.bootstrap()


def test_existing_owner_or_demo_account_is_never_overwritten():
    class DB:
        @contextmanager
        def transaction(self): yield self
        def execute(self, query, arguments):
            assert query.startswith('SELECT ')
            assert "role='PLATFORM_OWNER'" in query
            assert arguments == (demo.USERNAME, demo.EMAIL)
            return self
        def fetchone(self): return {'id': 'existing'}
    with pytest.raises(ValueError, match='cannot replace'):
        demo.require_empty_owner_slot(SimpleNamespace(db=DB()))
