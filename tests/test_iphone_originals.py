"""Procedurally encoded fixtures only; never personal Camera/Photo Library files."""
import io
import struct
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageOps, TiffImagePlugin

from src.webapp import calibration

SETTINGS = SimpleNamespace(max_upload_bytes=10*1024*1024, max_image_pixels=8_000_000)
APPLE_XMP = b'''<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:a="http://ns.apple.com/pixeldatainfo/1.0/" xmlns:h="http://ns.apple.com/HDRGainMap/1.0/" a:AuxiliaryImageType="urn:com:apple:photo:2020:aux:hdrgainmap" h:HDRGainMapVersion="65536"/></rdf:RDF></x:xmpmeta>'''


def encoded(image, fmt='JPEG', **kwargs):
    out = io.BytesIO(); image.save(out, fmt, **kwargs); return out.getvalue()


def pattern(size=(96,64)):
    rgb = np.zeros((size[1],size[0],3),np.uint8)
    rgb[:,:size[0]//2] = (210,35,20); rgb[:,size[0]//2:] = (15,180,50)
    return Image.fromarray(rgb)


def mpf_jpeg(*, xmp=APPLE_XMP, secondary_type=0, orientation=1, primary_xmp=None, primary_image=None):
    """Real JPEG+MPF container with primary RGB and auxiliary grayscale JPEG."""
    exif = Image.Exif(); exif[274] = orientation
    primary = encoded(primary_image if primary_image is not None else pattern(), quality=95, exif=exif, xmp=primary_xmp)
    secondary = encoded(Image.new('L',(24,16),128), quality=95, xmp=xmp)
    def segment(primary_size):
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[0xB000] = b'0100'; ifd[0xB001] = 2
        # TIFF starts at file offset 10 (SOI, APP2 marker+length, MPF magic).
        ifd[0xB002] = struct.pack('<LLLHH',0x030000,primary_size,0,0,0) + struct.pack('<LLLHH',secondary_type,len(secondary),primary_size-10,0,0)
        payload = b'MPF\0II\x2a\x00' + struct.pack('<L',8) + ifd.tobytes(8)
        return b'\xff\xe2' + struct.pack('>H',len(payload)+2) + payload
    header = segment(10); header = segment(len(primary)+len(header))
    return primary[:2]+header+primary[2:]+secondary, primary


@pytest.mark.parametrize('orientation',[1,6,8])
def test_apple_hdr_jpeg_mpf_primary_is_not_rejected_as_animation(orientation):
    raw, base = mpf_jpeg(orientation=orientation)
    assert Image.open(io.BytesIO(raw)).format == 'MPO'
    expected = np.asarray(ImageOps.exif_transpose(Image.open(io.BytesIO(base))).convert('RGB'))
    ready,mime,meta = calibration.prepare_image(raw,'IMG_0001.HEIC','image/heic',SETTINGS)
    actual = calibration.decode_image(ready,'ready.png',mime,SETTINGS)
    np.testing.assert_array_equal(actual,expected)
    assert meta['selected_frame_index'] == 0
    assert meta['original_frame_count'] == 2
    assert meta['auxiliary_image_policy'] == 'apple_hdr_gainmap_base_sdr'
    assert meta['resized'] is False
    assert meta['resize_factors'] == [1,1]
    assert Image.open(io.BytesIO(ready)).format == 'PNG'
    assert b'http://ns.apple.com/' not in ready


def test_camera_mpf_failure_and_whatsapp_base_copy_reproduction():
    raw, base = mpf_jpeg()
    calibration.prepare_image(raw,'camera.jpg','image/jpeg',SETTINGS)
    ready,_,meta = calibration.prepare_image(base,'whatsapp.jpeg','image/jpeg',SETTINGS)
    assert ready == base and meta['normalization'] == 'unchanged'


@pytest.mark.parametrize('kind',[0,0x020001,0x020002,0x020003])
def test_ambiguous_or_stereo_mpo_is_not_arbitrarily_selected(kind):
    raw,_ = mpf_jpeg(xmp=None,secondary_type=kind)
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(raw,'private-person-name.jpg','image/jpeg',SETTINGS)
    assert error.value.code == 'unsupported_multi_image'
    assert error.value.diagnostic['detected_format'] == 'MPO'
    assert 'private-person-name' not in str(error.value.diagnostic)


def test_hdr_marker_cannot_override_explicit_stereo_type():
    raw,_ = mpf_jpeg(secondary_type=0x020002)
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(raw,'photo.jpg','image/jpeg',SETTINGS)
    assert error.value.code == 'unsupported_multi_image'


@pytest.mark.parametrize('payload,code',[(b'', 'empty_photo'),(b'corrupt private metadata', 'invalid_photo_bytes'),(b'0'*(10*1024*1024+1),'photo_too_large'),(b'\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  ','video_not_still')],ids=['empty','corrupt','over-upload-limit','quicktime-video'])
def test_sanitized_actionable_preparation_errors(payload,code):
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(payload,'person-location.jpg','text/private',SETTINGS)
    assert error.value.code == code
    safe = repr(error.value.diagnostic)
    assert 'person-location' not in safe and 'private' not in safe
    assert not any(key in safe for key in ['sha256','GPS','exif','filename'])
    assert isinstance(error.value,ValueError)



def test_adobe_ultrahdr_jpeg_keeps_existing_single_base_decoder_behavior():
    adobe=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0"/></rdf:RDF></x:xmpmeta>'
    raw,_=mpf_jpeg(primary_xmp=adobe)
    assert Image.open(io.BytesIO(raw)).format == 'JPEG'
    ready,mime,meta=calibration.prepare_image(raw,'hdr.jpeg','image/jpeg',SETTINGS)
    rgb=calibration.decode_image(ready,'hdr.jpeg',mime,SETTINGS)
    assert ready == raw and rgb.shape == (64,96,3)
    assert meta['normalization'] == 'unchanged'


@pytest.mark.parametrize('transfer',[1,16,18],ids=['standard-10bit','HDR-PQ','HDR-HLG'])
def test_ten_bit_heif_is_decoded_to_the_same_full_frame_rgb_used_by_analysis(transfer):
    heif=pytest.importorskip('pillow_heif');heif.register_heif_opener()
    a=np.zeros((64,96,3),np.uint16);a[:,:48]=[65535,8192,4096];a[:,48:]=[4096,49152,8192]
    source=heif.from_bytes('RGB;16',(96,64),a.tobytes())
    stream=io.BytesIO();source.save(stream,quality=95,bit_depth=10,save_nclx_profile=True,color_primaries=9,transfer_characteristics=transfer,matrix_coefficients=9)
    raw=stream.getvalue();decoded=Image.open(io.BytesIO(raw))
    assert decoded.info['bit_depth']==10
    assert decoded.info['nclx_profile']['transfer_characteristics']==transfer
    expected=np.asarray(decoded.convert('RGB'))
    ready,mime,meta=calibration.prepare_image(raw,'HDR.HEIC','image/heic',SETTINGS)
    actual=calibration.decode_image(ready,'ready.png',mime,SETTINGS)
    np.testing.assert_array_equal(actual,expected)
    assert meta['normalized_size_px']==[96,64] and meta['resize_factors']==[1,1]


@pytest.mark.parametrize('size',[(6000,4000),(8064,6048)],ids=['24-megapixel','48-megapixel'])
def test_large_camera_jpeg_remains_full_frame_with_recorded_existing_resize(size):
    image=Image.new('RGB',size,(20,150,30))
    from PIL import ImageDraw
    draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,size[0]//5,size[1]//5),fill=(220,10,15))
    draw.rectangle((size[0]*4//5,size[1]*4//5,size[0]-1,size[1]-1),fill=(15,20,220))
    raw=encoded(image,quality=95);image.close()
    ready,mime,meta=calibration.prepare_image(raw,'camera-original.jpeg','image/jpeg',SETTINGS)
    actual=calibration.decode_image(ready,'ready.png',mime,SETTINGS)
    w,h=meta['normalized_size_px']
    assert w*h<=8_000_000 and meta['original_size_px']==list(size)
    assert abs(w/h-size[0]/size[1])<.001
    assert meta['resize_factors']==[w/size[0],h/size[1]]
    assert actual[0,0,0]>180 and actual[-1,-1,2]>180


def test_over_sixty_megapixel_header_is_rejected_before_pixel_decode():
    raw=bytearray(encoded(pattern()));sof=raw.index(b'\xff\xc0')
    raw[sof+5:sof+9]=struct.pack('>HH',8000,8000)
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(bytes(raw),'large.jpg','image/jpeg',SETTINGS)
    assert error.value.code=='photo_too_many_pixels'
    assert error.value.diagnostic['width']==8000


@pytest.mark.parametrize('xmp',[
    APPLE_XMP.replace(b'HDRGainMapVersion="65536"',b'HDRGainMapVersion="none"'),
    APPLE_XMP.replace(b'urn:com:apple:photo:2020:aux:hdrgainmap',b'unknown'),
    b'<!DOCTYPE doc [<!ENTITY secret SYSTEM "file:///not-authorized">]>'+APPLE_XMP,
    b'<invalid',
],ids=['invalid-version','wrong-auxiliary-type','external-entity','invalid-xml'])
def test_untrusted_auxiliary_metadata_cannot_enable_ambiguous_frame_selection(xmp):
    raw,_=mpf_jpeg(xmp=xmp)
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(raw,'photo.jpg','image/jpeg',SETTINGS)
    assert error.value.code=='unsupported_multi_image'


def test_missing_heic_decoder_has_distinct_safe_recovery(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'pillow_heif',None)
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(b'\x00\x00\x00\x14ftypheic\x00\x00\x00\x00heic','file','',SETTINGS)
    assert error.value.code=='photo_codec_unavailable'
    assert error.value.diagnostic=={'decoder_error_type':'ModuleNotFoundError'}


def test_decoder_exception_message_and_filename_never_enter_safe_diagnostic(monkeypatch):
    def fail(*args,**kwargs):raise OSError('SECRET GPS personal-image-path')
    monkeypatch.setattr(calibration.Image,'open',fail)
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(b'encodedimage','private-image.jpg','image/jpeg',SETTINGS)
    assert error.value.diagnostic=={'decoder_error_type':'OSError'}
    assert 'SECRET' not in str(error.value) and 'private' not in repr(error.value.diagnostic)


@pytest.mark.parametrize('encoding',['utf-16','utf-16-be','utf-32','utf-32-be'])
@pytest.mark.parametrize('entities',[False,True],ids=['alternate-encoding','entity-declaration'])
def test_xmp_entity_guard_cannot_be_bypassed_by_alternate_xml_encoding(encoding,entities):
    xml=APPLE_XMP.decode('utf-8')
    if entities:
        xml='<!DOCTYPE x [<!ENTITY kind "urn:com:apple:photo:2020:aux:hdrgainmap">]>'+xml.replace('urn:com:apple:photo:2020:aux:hdrgainmap','&kind;')
    raw,_=mpf_jpeg(xmp=xml.encode(encoding))
    with pytest.raises(calibration.ImagePreparationError) as error:
        calibration.prepare_image(raw,'photo.jpg','image/jpeg',SETTINGS)
    assert error.value.code=='unsupported_multi_image'
