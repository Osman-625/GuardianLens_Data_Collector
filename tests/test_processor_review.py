import json
import time
import pytest
from guardianlens.models import CaptureRequest
from guardianlens.capture import create_capture
from guardianlens.config import settings
from guardianlens.openai_extractor import MockExtractor
from guardianlens.processor import process_listing
from guardianlens.errors import ProviderOperationError
from guardianlens.review import begin_review, edit_listing, approve, action, get_listing
from guardianlens.db import connect
from conftest import data_url, image_bytes

def make_capture(platform='carousell', suffix='1', image=True):
    host='www.carousell.com.my' if platform=='carousell' else 'www.mudah.my'
    listing_id=100000+int(suffix)
    url=f'https://{host}/p/test-{suffix}-{listing_id}/' if platform=='carousell' else f'https://{host}/test-{suffix}-{listing_id}.htm'
    result=create_capture(CaptureRequest(platform=platform,source_url=url,page_title='Fixture',screenshot_data_url=data_url(image_bytes()),image_urls=['https://images.example.test/product.jpg'],listing_evidence={'h1_text':'Fixture product','has_price_signal':True}))
    if not image:
        c=connect(); row=c.execute('SELECT staging_text_path FROM listings WHERE listing_id=?',(result['listing_id'],)).fetchone(); c.close()
        (settings.root/row['staging_text_path']).write_text(json.dumps({'image_urls':[]}),encoding='utf-8')
    return result

def test_full_mock_processing_review_and_approval(isolated, monkeypatch):
    r=make_capture(); monkeypatch.setattr('guardianlens.processor.download_image', lambda u:image_bytes((400,300),value=120))
    out=process_listing(r['listing_id'],MockExtractor()); assert out['status']=='ready_for_review'
    begin_review(r['listing_id']); changes=edit_listing(r['listing_id'],{'category':'Audio','title':'Sony XM5'})
    assert 'title' in changes
    approve(r['listing_id']); item=get_listing(r['listing_id']); assert item['status']=='approved'
    assert item['approved_at']
    assert item['screenshot_path'] is None
    assert not (isolated/item['screenshot_storage_path']).exists()
    c=connect(); n=c.execute("SELECT COUNT(*) n FROM status_history WHERE listing_id=?",(r['listing_id'],)).fetchone()['n']; c.close(); assert n>=5

def test_missing_image_goes_needs_attention_and_cannot_approve(isolated):
    r=make_capture(image=False); out=process_listing(r['listing_id'],MockExtractor()); assert out['status']=='needs_attention'
    begin_review(r['listing_id'])
    with pytest.raises(ValueError,match='product image'): approve(r['listing_id'])

def test_ai_invalid_output_moves_ai_failed(isolated):
    r=make_capture()
    bad=MockExtractor({'title':'x','confidence':0.9})
    with pytest.raises(Exception): process_listing(r['listing_id'],bad)
    assert get_listing(r['listing_id'])['status']=='ai_failed'

def test_edit_scrubs_pii(isolated, monkeypatch):
    r=make_capture(); monkeypatch.setattr('guardianlens.processor.download_image', lambda u:image_bytes((400,300),value=121));process_listing(r['listing_id'],MockExtractor());begin_review(r['listing_id'])
    edit_listing(r['listing_id'],{'description':'email abc@example.com'})
    assert 'abc@example.com' not in get_listing(r['listing_id'])['description']

def test_skip_flag_reject_transitions(isolated, monkeypatch):
    r=make_capture(); monkeypatch.setattr('guardianlens.processor.download_image', lambda u:image_bytes((400,300),value=122));process_listing(r['listing_id'],MockExtractor());begin_review(r['listing_id']);action(r['listing_id'],'skipped');assert get_listing(r['listing_id'])['status']=='skipped'
    begin_review(r['listing_id']);action(r['listing_id'],'flagged');assert get_listing(r['listing_id'])['status']=='flagged'
    begin_review(r['listing_id']);action(r['listing_id'],'rejected');assert get_listing(r['listing_id'])['status']=='rejected'

def test_all_images_failing_leaves_no_partial_image_rows(isolated, monkeypatch):
    from guardianlens.db import connect
    from guardianlens.models import CaptureRequest
    r=create_capture(CaptureRequest(platform='carousell',source_url='https://www.carousell.com.my/p/atomic-777777/',page_title='Atomic',screenshot_data_url=data_url(image_bytes()),image_urls=['https://x.test/one.jpg','https://x.test/two.jpg'],listing_evidence={'h1_text':'Atomic product','has_price_signal':True}))
    def downloader(url):
        raise TimeoutError('simulated image timeout')
    monkeypatch.setattr('guardianlens.processor.download_image',downloader)
    with pytest.raises(ValueError): process_listing(r['listing_id'],MockExtractor())
    c=connect();n=c.execute('SELECT COUNT(*) n FROM images WHERE listing_id=?',(r['listing_id'],)).fetchone()['n'];c.close()
    assert n==0 and get_listing(r['listing_id'])['status']=='image_failed'

def test_one_bad_image_url_does_not_fail_the_whole_listing(isolated, monkeypatch):
    from guardianlens.db import connect
    from guardianlens.models import CaptureRequest
    r=create_capture(CaptureRequest(platform='carousell',source_url='https://www.carousell.com.my/p/atomic-888888/',page_title='Atomic',screenshot_data_url=data_url(image_bytes()),image_urls=['https://x.test/one.jpg','https://x.test/promo.svg'],listing_evidence={'h1_text':'Atomic product','has_price_signal':True}))
    def downloader(url):
        if url.endswith('.svg'): raise ValueError('corrupt or unreadable image')
        return image_bytes((400,300),value=131)
    monkeypatch.setattr('guardianlens.processor.download_image',downloader)
    out=process_listing(r['listing_id'],MockExtractor())
    assert out['status']=='ready_for_review'
    c=connect();n=c.execute('SELECT COUNT(*) n FROM images WHERE listing_id=?',(r['listing_id'],)).fetchone()['n'];c.close()
    assert n==1


def test_ai_timeout_moves_to_ai_failed(isolated):
    class TimeoutExtractor:
        provider='test';model='timeout';prompt_version='v1'
        def extract(self,*a,**k): raise TimeoutError('simulated API timeout')
    r=make_capture(suffix='8')
    old_attempts = settings.ai_max_attempts
    object.__setattr__(settings, 'ai_max_attempts', 1)
    try:
        with pytest.raises(ProviderOperationError) as exc:
            process_listing(r['listing_id'],TimeoutExtractor())
    finally:
        object.__setattr__(settings, 'ai_max_attempts', old_attempts)
    assert exc.value.classification.kind == 'timeout'
    assert get_listing(r['listing_id'])['status']=='ai_failed'


def test_exact_metadata_duplicate_is_blocked_after_first_approval(isolated, monkeypatch):
    values=iter([101,151])
    monkeypatch.setattr('guardianlens.processor.download_image',lambda u:image_bytes((400,300),value=next(values)))
    a=make_capture(suffix='21');process_listing(a['listing_id'],MockExtractor());begin_review(a['listing_id']);approve(a['listing_id'])
    b=make_capture(suffix='22');out=process_listing(b['listing_id'],MockExtractor())
    assert out['status']=='duplicate_blocked'


def test_double_approval_is_rejected(isolated, monkeypatch):
    monkeypatch.setattr('guardianlens.processor.download_image',lambda u:image_bytes((400,300),value=103))
    r=make_capture(suffix='23');process_listing(r['listing_id'],MockExtractor());begin_review(r['listing_id']);approve(r['listing_id'])
    with pytest.raises(ValueError): approve(r['listing_id'])


def test_review_session_timing_is_recorded(isolated, monkeypatch):
    from guardianlens.db import connect
    monkeypatch.setattr('guardianlens.processor.download_image',lambda u:image_bytes((400,300),value=104))
    r=make_capture(suffix='24');process_listing(r['listing_id'],MockExtractor());begin_review(r['listing_id']);action(r['listing_id'],'skipped')
    c=connect();row=c.execute("SELECT finished_at,duration_seconds FROM review_actions WHERE listing_id=? AND action='review_session' ORDER BY created_at DESC LIMIT 1",(r['listing_id'],)).fetchone();c.close()
    assert row['finished_at'] is not None and row['duration_seconds'] is not None

def test_near_duplicate_image_is_warning_not_auto_reject(isolated, monkeypatch):
    from guardianlens.db import connect
    # Same perceptual structure but different JPEG bytes/quality through two generated gray levels.
    vals=iter([90,140])
    monkeypatch.setattr('guardianlens.processor.download_image',lambda u:image_bytes((400,300),value=next(vals)))
    a=make_capture(suffix='41'); pa=MockExtractor().payload.copy();pa['title']='Product A';process_listing(a['listing_id'],MockExtractor(pa));begin_review(a['listing_id']);approve(a['listing_id'])
    b=make_capture(suffix='42'); pb=MockExtractor().payload.copy();pb['title']='Product B';out=process_listing(b['listing_id'],MockExtractor(pb))
    c=connect(); kinds=[r['warning_type'] for r in c.execute('SELECT warning_type FROM warnings WHERE listing_id=?',(b['listing_id'],))];c.close()
    assert out['status']=='needs_attention' and 'near_duplicate_image' in kinds
