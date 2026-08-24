import hashlib
import io
import base64
import pytest
from PIL import Image

from guardianlens.config import settings
from guardianlens.images import (
    _validate_remote_url,
    decode_data_url,
    download_image,
    persist_product_image,
    save_private_screenshot,
    validate_image_bytes,
)
from guardianlens.db import connect, transaction, now_iso
from conftest import image_bytes
import uuid

def seed(con):
    lid=str(uuid.uuid4()); n=now_iso();con.execute("INSERT INTO listings(listing_id,platform,source_url,source_url_hash,status,captured_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(lid,'carousell','https://www.carousell.com.my/p/'+lid,lid,'captured',n,n,n));return lid

def test_corrupt_and_small_image_rejected(isolated):
    with pytest.raises(ValueError): validate_image_bytes(b'bad')
    with pytest.raises(ValueError): validate_image_bytes(image_bytes((40,40)))

def test_persist_image_and_exact_duplicate_detected(isolated):
    data=image_bytes()
    with transaction() as con:
        a=seed(con); b=seed(con)
        r1=persist_product_image(con,a,'carousell',data,1)
        r2=persist_product_image(con,b,'carousell',data,1)
    assert not r1['duplicate'] and r2['duplicate']

def test_persisted_product_image_is_reencoded_without_exif_and_hashes_stored_bytes(isolated):
    image = Image.new('RGB', (400, 300), (20, 40, 60))
    exif = Image.Exif(); exif[0x010E] = 'private metadata'
    raw = io.BytesIO(); image.save(raw, format='JPEG', exif=exif)
    with transaction() as con:
        listing_id = seed(con)
        result = persist_product_image(con, listing_id, 'carousell', raw.getvalue(), 1)
    path = isolated / result['path']
    stored = path.read_bytes()
    with Image.open(path) as reopened:
        assert len(reopened.getexif()) == 0
    con = connect(); row = con.execute('SELECT * FROM images WHERE image_id=?',(result['image_id'],)).fetchone(); con.close()
    assert row['sha256'] == hashlib.sha256(stored).hexdigest()
    assert row['file_size_bytes'] == len(stored)
    assert 'image_001_' in path.name

def test_private_screenshot_is_reencoded_without_exif(isolated):
    image = Image.new('RGB', (400, 300), (70, 80, 90))
    exif = Image.Exif(); exif[0x010E] = 'private screenshot metadata'
    raw = io.BytesIO(); image.save(raw, format='JPEG', exif=exif)
    encoded = 'data:image/jpeg;base64,' + base64.b64encode(raw.getvalue()).decode('ascii')
    relative = save_private_screenshot('carousell', 'fixture', encoded)
    with Image.open(isolated / relative) as reopened:
        assert len(reopened.getexif()) == 0

def test_pixel_limit_is_enforced(isolated):
    original = settings.max_image_pixels
    object.__setattr__(settings, 'max_image_pixels', 100_000)
    try:
        with pytest.raises(ValueError, match='pixel limit'):
            validate_image_bytes(image_bytes((400,300)))
    finally:
        object.__setattr__(settings, 'max_image_pixels', original)

@pytest.mark.parametrize('url', [
    'https://127.0.0.1/image.jpg',
    'https://[::1]/image.jpg',
    'https://localhost/image.jpg',
    'https://169.254.169.254/latest/meta-data',
])
def test_remote_image_url_rejects_local_and_private_targets(url):
    with pytest.raises(ValueError, match='local|non-public'):
        _validate_remote_url(url)

def test_remote_image_url_requires_https():
    with pytest.raises(ValueError, match='HTTPS'):
        _validate_remote_url('http://8.8.8.8/image.jpg')

def test_data_url_size_is_rejected_before_decode(monkeypatch):
    import guardianlens.images as images
    original = settings.max_image_bytes
    object.__setattr__(settings, 'max_image_bytes', 3)
    monkeypatch.setattr(images.base64, 'b64decode', lambda *a, **k: pytest.fail('decoder should not run'))
    try:
        with pytest.raises(ValueError, match='maximum size'):
            decode_data_url('data:image/jpeg;base64,AAAAAAAA')
    finally:
        object.__setattr__(settings, 'max_image_bytes', original)

def test_streamed_download_stops_at_size_limit(monkeypatch):
    import guardianlens.images as images

    class Response:
        status_code = 200
        headers = {'content-type':'image/jpeg'}
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def raise_for_status(self): return None
        def iter_bytes(self): return iter([b'123456',b'789012'])

    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def stream(self,*args,**kwargs): return Response()

    monkeypatch.setattr(images.httpx, 'Client', Client)
    monkeypatch.setattr(images, '_validate_remote_url', lambda url: None)
    original = settings.max_image_bytes
    object.__setattr__(settings, 'max_image_bytes', 10)
    try:
        with pytest.raises(ValueError, match='maximum size'):
            download_image('https://images.example.test/a.jpg')
    finally:
        object.__setattr__(settings, 'max_image_bytes', original)

@pytest.mark.parametrize('location', [
    'http://8.8.8.8/image.jpg',
    'https://127.0.0.1/image.jpg',
])
def test_redirects_cannot_downgrade_or_target_private_addresses(monkeypatch, location):
    import guardianlens.images as images

    class Response:
        status_code = 302
        headers = {'location': location}
        extensions = {}
        def __enter__(self): return self
        def __exit__(self,*args): return False

    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def stream(self,*args,**kwargs): return Response()

    public = [('AF_INET', 1, 6, '', ('8.8.8.8', 443))]
    monkeypatch.setattr(images.socket, 'getaddrinfo', lambda *a, **k: public)
    monkeypatch.setattr(images.httpx, 'Client', Client)
    with pytest.raises(ValueError, match='HTTPS|non-public'):
        download_image('https://images.example.test/start.jpg')

def test_connected_peer_must_match_preflight_resolution(monkeypatch):
    import guardianlens.images as images
    import ipaddress

    class Stream:
        def get_extra_info(self, key): return ('127.0.0.1', 443)
    class Response:
        status_code = 200
        headers = {'content-type':'image/jpeg'}
        extensions = {'network_stream': Stream()}
        def __enter__(self): return self
        def __exit__(self,*args): return False
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def stream(self,*args,**kwargs): return Response()

    monkeypatch.setattr(images.httpx, 'Client', Client)
    monkeypatch.setattr(images, '_validate_remote_url', lambda url: frozenset({ipaddress.ip_address('8.8.8.8')}))
    with pytest.raises(ValueError, match='connection address'):
        download_image('https://images.example.test/start.jpg')
