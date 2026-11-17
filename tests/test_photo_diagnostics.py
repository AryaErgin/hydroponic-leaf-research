import io
from PIL import Image
from fastapi.testclient import TestClient
from src.webapp.app import create_app
from src.webapp.settings import Settings

def client():
    token="synthetic-diagnostic-test-token-1234567890"
    return TestClient(create_app(Settings(auth_token=token,closed_academic_research=False),service_override=object()),headers={"Authorization":"Bearer "+token})

def test_prepare_diagnostic_is_actionable_and_does_not_echo_private_filename(caplog):
    r=client().post("/api/images/prepare",files={"image":("PRIVATE-person-photo.heic",b"not an image","image/heic")})
    assert r.status_code==400
    d=r.json();assert d["code"]
    assert d["diagnostic"]["stage"]=="prepare"
    assert d["diagnostic"]["received_bytes"]==12
    assert "PRIVATE-person-photo" not in r.text
    assert d["diagnostic"]["request_id"]
    assert "PRIVATE-person-photo" not in caplog.text
    assert "photo_prepare_failed" in caplog.text

def test_successful_preparation_has_safe_correlatable_request_id():
    b=io.BytesIO();Image.new("RGB",(32,24),"green").save(b,"PNG")
    r=client().post("/api/images/prepare",files={"image":("private-name.png",b.getvalue(),"image/png")})
    assert r.status_code==200
    assert r.headers["X-Photo-Request-ID"]
    assert r.headers["X-Image-Width"]=="32"
