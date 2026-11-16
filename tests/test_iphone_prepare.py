"""Generated color patterns only; no personal photographs or metadata."""
import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageOps

from src.webapp.calibration import prepare_image, decode_image

SETTINGS = SimpleNamespace(max_upload_bytes=10*1024*1024, max_image_pixels=8_000_000)


def encoded(image, fmt='JPEG', **kwargs):
    stream = io.BytesIO()
    image.save(stream, format=fmt, **kwargs)
    return stream.getvalue()


def pattern(size=(96, 64)):
    a = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    a[:size[1]//2, :size[0]//2] = (220, 35, 20)
    a[size[1]//2:, size[0]//2:] = (25, 180, 65)
    return Image.fromarray(a)


@pytest.mark.parametrize('fmt,name,mime', [('JPEG','IMG_1234.HEIC','image/heic'), ('JPEG','image',''), ('PNG','photo.jpeg','image/jpeg')])
def test_decoded_bytes_determine_format_after_photo_library_conversion(fmt,name,mime):
    raw = encoded(pattern(), fmt)
    prepared, detected, provenance = prepare_image(raw, name, mime, SETTINGS)
    assert prepared == raw
    assert detected == {'JPEG':'image/jpeg','PNG':'image/png'}[fmt]
    assert provenance['original_format'] == fmt
    assert provenance['normalized_size_px'] == [96,64]


@pytest.mark.parametrize('orientation', range(1, 9))
def test_exif_orientation_matches_displayed_coordinate_frame(orientation):
    exif = Image.Exif(); exif[274] = orientation
    raw = encoded(pattern(), exif=exif)
    expected = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    prepared, mime, meta = prepare_image(raw, 'rotated.jpg', 'image/jpeg', SETTINGS)
    actual = decode_image(prepared, 'ready.jpg' if mime=='image/jpeg' else 'ready.png', mime, SETTINGS)
    np.testing.assert_array_equal(actual, np.asarray(expected))
    assert meta['normalized_size_px'] == list(expected.size)
    assert meta['original_exif_orientation'] == orientation


def test_existing_large_image_resize_is_recorded_and_full_frame():
    raw = encoded(pattern((4032,3024)))
    prepared,mime,meta = prepare_image(raw,'large.jpg','image/jpeg',SETTINGS)
    assert meta['resized']
    assert meta['oriented_size_px'] == [4032,3024]
    w,h = meta['normalized_size_px']
    assert w*h <= SETTINGS.max_image_pixels
    assert abs(w/h - 4032/3024) < .001
    assert meta['resize_factors'] == [w/4032,h/3024]
    assert Image.open(io.BytesIO(prepared)).size == (w,h)


def test_animated_png_is_not_silently_treated_as_still():
    raw = encoded(pattern(), 'PNG', save_all=True, append_images=[Image.new('RGB',(96,64),'blue')], duration=100, loop=0)
    with pytest.raises(ValueError, match='still'):
        prepare_image(raw,'animated.png','image/png',SETTINGS)


def test_heif_primary_still_from_multi_image_container():
    heif = pytest.importorskip('pillow_heif')
    heif.register_heif_opener()
    raw = encoded(pattern(),'HEIF',save_all=True,append_images=[Image.new('RGB',(96,64),'blue')],primary_index=1)
    prepared,mime,meta = prepare_image(raw,'library.heic','image/heic',SETTINGS)
    image = Image.open(io.BytesIO(prepared))
    assert image.size == (96,64)
    assert np.asarray(image)[32,48,2] > 200
    assert meta['original_frame_count'] == 2
    assert meta['selected_frame_index'] == 1


def test_single_heif_with_exif_rotation_and_unreliable_filename():
    heif = pytest.importorskip('pillow_heif')
    heif.register_heif_opener()
    exif = Image.Exif(); exif[274] = 6
    raw = encoded(pattern(), 'HEIF', exif=exif.tobytes())
    prepared,mime,meta = prepare_image(raw,'photo','',SETTINGS)
    image = Image.open(io.BytesIO(prepared))
    assert image.size == (64,96)
    assert meta['original_exif_orientation'] == 6
    assert meta['normalized_size_px'] == [64,96]
    # The top-left red quadrant moves to the top-right after clockwise rotation.
    assert np.asarray(image)[20,48,0] > 180
    assert meta['resized'] is False


def test_whatsapp_style_low_quality_jpeg_preserved():
    raw = encoded(pattern((1600,1200)), quality=35, optimize=True)
    prepared,mime,meta = prepare_image(raw,'shared.jpeg','image/jpeg',SETTINGS)
    assert prepared == raw
    assert meta['normalization'] == 'unchanged'
    assert meta['normalized_size_px'] == [1600,1200]


def test_corrupt_or_other_format_rejected():
    for raw in (b'not an image',encoded(pattern(),'GIF')):
        with pytest.raises(ValueError): prepare_image(raw,'photo.jpg','image/jpeg',SETTINGS)
