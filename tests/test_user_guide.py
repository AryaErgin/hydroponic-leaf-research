"""Public guide packaging, source consistency and private-workspace boundaries."""
import runpy
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from src.hydroponic.config import ROOT
from test_integrated_app import application, login


def test_runtime_guide_matches_authoritative_markdown():
    assert '<html lang="en">' in (ROOT / 'src/webapp/static/user-guide.html').read_text(encoding='utf-8')
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
    assert '1. Sign in' in response.text
    assert '8. Growth history and CSV' in response.text
    assert '<script' not in response.text
    assert response.text.count('<figure') == 12
    assert response.text.count('<img ') == 12
    assert client.get('/science-static/user-guide.css').status_code == 200
    image = client.get('/science-static/tutorial/step-01.webp')
    assert image.status_code == 200 and image.headers['content-type'] == 'image/webp'
    assert not application.state.scientific.services


def test_guide_is_linked_from_real_analyzer_and_navigation(application):
    page = login(application).get('/analyze')
    assert page.status_code == 200
    assert 'href="/help"' in page.text and 'How to use' in page.text
    # Separate tab keeps an unfinished review in its existing browser tab.
    assert 'target="_blank" rel="noopener"' in page.text
    # Analyze keeps one persistent header link; duplicating it in accountChrome
    # made the mobile navigation confusing. The account app retains its own help.
    assert page.text.count('href="/help"') == 1
    source = (ROOT / 'src/auth_preview/static/app.js').read_text(encoding='utf-8')
    assert "['How to use','/help']" in source


def test_renderer_escapes_literal_markup():
    build = runpy.run_path(str(ROOT / 'scripts/build_user_guide.py'))
    html = build['render']('# Test\n\n## Section\n\n<script>alert(1)</script>\n\n- **Tool:** A & B\n')
    assert '<script>' not in html
    assert '&lt;script&gt;' in html and '<strong>Tool:</strong> A &amp; B' in html


def test_tutorial_images_have_safe_native_enlargement_and_alt_text():
    build = runpy.run_path(str(ROOT / 'scripts/build_user_guide.py'))
    page = build['render']('# Test\n\n![Select the plant & Add plant button](/science-static/tutorial/step-01.webp)\n')
    assert '<figure class="tutorial-shot">' in page
    assert 'href="/science-static/tutorial/step-01.webp"' in page
    assert 'target="_blank" rel="noopener"' in page
    assert 'alt="Select the plant &amp; Add plant button"' in page
    assert 'loading="lazy"' in page and 'Tap to enlarge' in page
    relative = build['render']('# Test\n\n![Select plant](../src/webapp/static/tutorial/step-01.webp)\n')
    assert 'src="/science-static/tutorial/step-01.webp"' in relative
    for unsafe in ('https://example.test/private.png', '/science-static/tutorial/../private.webp', 'javascript:alert(1)'):
        page = build['render']('# Test\n\n![Unsafe]('+unsafe+')\n')
        assert '<img' not in page and '<figure' not in page


def test_tutorial_assets_are_small_metadata_free_and_match_current_ui():
    import hashlib
    import json
    from PIL import Image
    base = ROOT / 'src/webapp/static/tutorial'
    manifest = json.loads((base / 'manifest.json').read_text(encoding='utf8'))
    assert len(manifest['images']) == 12
    assert manifest['data_kind'] == 'original_synthetic_local_demo'
    for relative, expected in manifest['ui_sources'].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, 'UI changed: review and recapture tutorial images'
    for item in manifest['images']:
        path = base / item['file']
        assert path.name.startswith('step-') and path.suffix == '.webp'
        assert path.stat().st_size < 220_000
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        with Image.open(path) as image:
            assert image.format == 'WEBP' and not image.getexif()
            assert not any(key in image.info for key in ('exif', 'xmp', 'icc_profile', 'comment'))
            assert list(image.size) == item['size'] == [780, 1688]
            assert manifest['viewport_css_px'] == [390, 844]
            assert manifest['physical_iphone_safari'] is False


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
    assert client.get('/static/tutorial/step-01.webp').headers['content-type'] == 'image/webp'


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
            assert page.status_code == 200 and '8. Growth history and CSV' in page.text
            assert client.get('/science-static/user-guide.css').status_code == 200
            assert 'href="/help"' in client.get('/signin').text
            assert client.get('/api/profiles').status_code == 401
            assert not app.state.scientific.services
