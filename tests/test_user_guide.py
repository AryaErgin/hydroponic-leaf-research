"""Public guide packaging, source consistency and private-workspace boundaries."""
import runpy
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from src.hydroponic.config import ROOT
from test_integrated_app import application, login


def test_runtime_guide_matches_authoritative_markdown():
    build = runpy.run_path(str(ROOT / 'scripts/build_user_guide.py'))
    assert (ROOT / 'src/webapp/static/user-guide.html').read_text(encoding='utf-8') == build['render'](
        (ROOT / 'docs/USER_GUIDE.md').read_text(encoding='utf-8'))


def test_public_guide_never_authorizes_or_opens_scientific_data(application, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('A public guide must not authorize a scientific workspace')
    monkeypatch.setattr(application.state.scientific, 'authorize', refuse)
    client = TestClient(application)
    response = client.get('/help')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert '1. Select or add a plant' in response.text
    assert '7. View growth history and export' in response.text
    assert '<script' not in response.text and '<img' not in response.text
    assert client.get('/science-static/user-guide.css').status_code == 200
    assert not application.state.scientific.services


def test_guide_is_linked_from_real_analyzer_and_navigation(application):
    page = login(application).get('/analyze')
    assert page.status_code == 200
    assert 'href="/help"' in page.text and 'How to use' in page.text
    # Separate tab keeps an unfinished review in its existing browser tab.
    assert 'target="_blank" rel="noopener"' in page.text
    for relative in ('src/webapp/static/app.js', 'src/auth_preview/static/app.js'):
        source = (ROOT / relative).read_text(encoding='utf-8')
        assert "['User guide','/help']" in source


def test_renderer_escapes_literal_markup():
    build = runpy.run_path(str(ROOT / 'scripts/build_user_guide.py'))
    html = build['render']('# Test\n\n## Section\n\n<script>alert(1)</script>\n\n- **Tool:** A & B\n')
    assert '<script>' not in html
    assert '&lt;script&gt;' in html and '<strong>Tool:</strong> A &amp; B' in html


def test_standalone_guide_links_use_standalone_static_mount():
    from src.webapp.app import create_app
    from src.webapp.settings import Settings
    # The page uses no service operations or scientific data.
    app = create_app(Settings(closed_academic_research=False), service_override=object())
    client = TestClient(app)
    page = client.get('/help')
    assert page.status_code == 200
    assert '/static/user-guide.css' in page.text and 'href="/"' in page.text
    assert client.get('/static/user-guide.css').status_code == 200


def test_firebase_shell_retains_public_guide_without_identity_provider_calls(monkeypatch):
    from src.firebase_preview.app import create_app
    from src.firebase_preview.identity import FirebaseConfig
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
    parent = ROOT / '.runtime/firebase'
    parent.mkdir(parents=True, exist_ok=True)
    class RefuseGateway:
        def __getattr__(self, name):
            raise AssertionError('Public help must not call the identity provider: ' + name)
    with tempfile.TemporaryDirectory(dir=parent) as temp:
        base = Path(temp)
        app = create_app(FirebaseConfig(base/'auth', 'http://testserver', development=True,
            integrated=True, require_verified_email=True), RefuseGateway(), base/'science')
        with TestClient(app) as client:
            page = client.get('/help')
            assert page.status_code == 200 and '7. View growth history and export' in page.text
            assert client.get('/science-static/user-guide.css').status_code == 200
            assert 'href="/help"' in client.get('/signin').text
            assert client.get('/api/profiles').status_code == 401
            assert not app.state.scientific.services
