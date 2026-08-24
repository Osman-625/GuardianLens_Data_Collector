import threading
from guardianlens.models import CaptureRequest
from guardianlens.capture import create_capture
from guardianlens.processor import process_listing
from guardianlens.openai_extractor import MockExtractor
from guardianlens.review import begin_review, approve, get_listing
from conftest import data_url, image_bytes

def test_concurrent_duplicate_approval_allows_only_one(isolated, monkeypatch):
    counter={'n':0}
    def dl(url):
        counter['n']+=1
        return image_bytes((400,300),value=80+counter['n']*30)
    monkeypatch.setattr('guardianlens.processor.download_image',dl)
    ids=[]
    for n in [31,32]:
        r=create_capture(CaptureRequest(platform='carousell',source_url=f'https://www.carousell.com.my/p/concurrent-{n}00000/',page_title='Same',screenshot_data_url=data_url(image_bytes()),image_urls=[f'https://fixture.test/{n}.jpg'],listing_evidence={'h1_text':'Concurrent product','has_price_signal':True}))
        process_listing(r['listing_id'],MockExtractor())
        begin_review(r['listing_id'])
        ids.append(r['listing_id'])
    results=[]
    def worker(lid):
        try: approve(lid);results.append(('ok',lid))
        except Exception as e: results.append(('error',lid,str(e)))
    ts=[threading.Thread(target=worker,args=(x,)) for x in ids]
    [t.start() for t in ts];[t.join() for t in ts]
    statuses=[get_listing(x)['status'] for x in ids]
    assert statuses.count('approved')==1
    assert sum(1 for x in results if x[0]=='error')==1
