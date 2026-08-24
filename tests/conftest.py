from __future__ import annotations
import base64, io, shutil
from pathlib import Path
import pytest
from PIL import Image
from guardianlens.config import settings
from guardianlens.db import init_db


def image_bytes(size=(320,240), fmt='JPEG', value=180):
    im = Image.new('RGB', size, (value, value, value))
    b = io.BytesIO(); im.save(b, format=fmt); return b.getvalue()

def data_url(data: bytes, mime='image/jpeg'):
    return f"data:{mime};base64," + base64.b64encode(data).decode('ascii')

@pytest.fixture()
def isolated(tmp_path):
    original_root = settings.root
    original_mode = settings.mode
    root = tmp_path / 'project'
    (root / 'src/guardianlens').mkdir(parents=True)
    shutil.copy(original_root / 'src/guardianlens/schema.sql', root / 'src/guardianlens/schema.sql')
    shutil.copytree(original_root / 'config', root / 'config')
    for p in [
        'data/private/screenshots','data/private/staging','data/images/carousell','data/images/mudah',
        'data/temporary','data/exports','data/reports','data/soft_test','logs','extension'
    ]: (root / p).mkdir(parents=True, exist_ok=True)
    shutil.copy(original_root / 'extension/manifest.json', root / 'extension/manifest.json')
    object.__setattr__(settings, 'root', root)
    object.__setattr__(settings, 'mode', 'production')
    init_db()
    try:
        yield root
    finally:
        object.__setattr__(settings, 'root', original_root)
        object.__setattr__(settings, 'mode', original_mode)

@pytest.fixture()
def sample_image():
    return image_bytes()
