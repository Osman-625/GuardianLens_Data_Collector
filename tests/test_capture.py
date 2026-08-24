import json
import pytest
from pydantic import ValidationError
from guardianlens.models import CaptureRequest
from guardianlens.capture import INVALID_LISTING_MESSAGE, create_capture
from guardianlens.db import connect
from conftest import data_url, image_bytes

EVIDENCE = {'h1_text':'Sony WH-1000XM5','has_price_signal':True}

def payload(platform='carousell',url='https://www.carousell.com.my/p/sony-wh-1000xm5-123456/'):
    return CaptureRequest(platform=platform,source_url=url,page_title='Sony XM5',screenshot_data_url=data_url(image_bytes()),image_urls=['https://images.example.test/product.jpg'],listing_evidence=EVIDENCE,visible_text='Seller email abc@example.com phone +60 12-345 6789')

def test_manual_capture_creates_private_staging(isolated):
    r=create_capture(payload()); assert not r['duplicate']
    c=connect(); row=c.execute('SELECT * FROM listings').fetchone(); c.close()
    assert row['status']=='captured'
    assert row['marketplace_listing_id']=='123456'
    assert (isolated/row['screenshot_path']).exists()
    staged=json.loads((isolated/row['staging_text_path']).read_text())
    assert staged=={'image_urls':['https://images.example.test/product.jpg']}

def test_duplicate_url_blocked_at_capture(isolated):
    p=payload(); a=create_capture(p); b=create_capture(p)
    assert b['duplicate'] and a['listing_id']==b['listing_id']

def test_duplicate_capture_performs_no_new_filesystem_writes(isolated, monkeypatch):
    p=payload(); first=create_capture(p)
    def unexpected_write(*args, **kwargs):
        pytest.fail('duplicate capture attempted a screenshot write')
    monkeypatch.setattr('guardianlens.capture.save_private_screenshot', unexpected_write)
    second=create_capture(p)
    assert second['duplicate'] and second['listing_id']==first['listing_id']

def test_capture_database_failure_rolls_back_created_files(isolated, monkeypatch):
    def fail_after_files():
        raise RuntimeError('injected database preparation failure')
    monkeypatch.setattr('guardianlens.capture.now_iso', fail_after_files)
    with pytest.raises(RuntimeError, match='injected'):
        create_capture(payload())
    assert not list((isolated/'data/private/screenshots').rglob('*.*'))
    assert not list((isolated/'data/private/staging').rglob('*.*'))
    con=connect(); count=con.execute('SELECT COUNT(*) n FROM listings').fetchone()['n']; con.close()
    assert count == 0

@pytest.mark.parametrize('field', ['source_url', 'visible_text', 'image_urls'])
def test_capture_payload_rejects_oversized_text(field):
    values = payload().model_dump()
    value = {
        'source_url': 'x' * 4097,
        'visible_text': 'x' * 250001,
        'image_urls': ['https://example.test/' + ('x' * 4097)],
    }[field]
    values[field] = value
    with pytest.raises(ValidationError):
        CaptureRequest(**values)

def test_wrong_platform_host_rejected(isolated):
    with pytest.raises(ValueError,match='individual listing page'):
        create_capture(payload('mudah','https://www.carousell.com.my/p/sony-123456/'))

def test_duplicate_marketplace_id_blocked_even_with_different_url(isolated):
    first=create_capture(payload(url='https://www.carousell.com.my/p/first-slug-999999/'))
    second=create_capture(payload(url='https://www.carousell.com.my/p/different-slug-999999/'))
    assert second['duplicate'] and second['listing_id']==first['listing_id']

def test_valid_mudah_listing_pattern_is_accepted(isolated):
    result=create_capture(payload('mudah','https://www.mudah.my/sony-wh-1000xm5-654321.htm'))
    assert not result['duplicate']
    c=connect(); row=c.execute('SELECT marketplace_listing_id FROM listings').fetchone(); c.close()
    assert row['marketplace_listing_id']=='654321'

@pytest.mark.parametrize(('platform','url','expected_id'),[
    ('carousell','https://carousell.com.my/p/item-7/','7'),
    ('mudah','https://mudah.my/item-8.htm','8'),
])
def test_exact_apex_hosts_and_numeric_id_patterns_are_supported(isolated,platform,url,expected_id):
    create_capture(payload(platform,url))
    c=connect(); row=c.execute('SELECT marketplace_listing_id FROM listings').fetchone(); c.close()
    assert row['marketplace_listing_id']==expected_id

@pytest.mark.parametrize(('platform','url'),[
    ('carousell','https://www.carousell.com.my/'),
    ('carousell','https://www.carousell.com.my/search/products'),
    ('carousell','https://www.carousell.com.my/categories/audio'),
    ('carousell','https://www.carousell.com.my/u/example-seller'),
    ('carousell','http://www.carousell.com.my/p/sony-123456/'),
    ('carousell','https://evil.carousell.com.my/p/sony-123456/'),
    ('carousell','https://www.carousell.com.my.evil.test/p/sony-123456/'),
    ('carousell','https://seller@www.carousell.com.my/p/sony-123456/'),
    ('carousell','https://www.carousell.com.my:444/p/sony-123456/'),
    ('carousell','https://www.carousell.com.my/p/sony/'),
    ('mudah','https://www.mudah.my/'),
    ('mudah','https://www.mudah.my/search?query=sony'),
    ('mudah','https://www.mudah.my/sony-654321'),
    ('mudah','https://profile.mudah.my/example-seller-654321.htm'),
])
def test_non_listing_pages_and_deceptive_urls_are_rejected(isolated,platform,url):
    with pytest.raises(ValueError) as exc:
        create_capture(payload(platform,url))
    assert str(exc.value)==INVALID_LISTING_MESSAGE

@pytest.mark.parametrize('change',[
    {'h1_text':None},
    {'h1_text':'Search results'},
    {'has_price_signal':False,'has_product_structured_data':False,'has_offer_signal':False},
])
def test_listing_specific_page_evidence_is_required(isolated,change):
    request=payload()
    for key,value in change.items():
        setattr(request.listing_evidence,key,value)
    with pytest.raises(ValueError) as exc:
        create_capture(request)
    assert str(exc.value)==INVALID_LISTING_MESSAGE

def test_product_image_candidate_is_required(isolated):
    request=payload(); request.image_urls=[]
    with pytest.raises(ValueError) as exc:
        create_capture(request)
    assert str(exc.value)==INVALID_LISTING_MESSAGE

def test_product_structured_data_can_supply_listing_signal(isolated):
    request=payload()
    request.listing_evidence.has_price_signal=False
    request.listing_evidence.has_product_structured_data=True
    assert not create_capture(request)['duplicate']

def test_supplied_marketplace_id_must_match_url(isolated):
    request=payload(); request.marketplace_listing_id='999999'
    with pytest.raises(ValueError) as exc:
        create_capture(request)
    assert str(exc.value)==INVALID_LISTING_MESSAGE
