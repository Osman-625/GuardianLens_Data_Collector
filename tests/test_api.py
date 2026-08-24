from fastapi.testclient import TestClient
from guardianlens.app import app
from guardianlens.capture import INVALID_LISTING_MESSAGE
from guardianlens.security import ensure_local_token
from conftest import data_url, image_bytes

def test_health_and_capture_auth(isolated):
    with TestClient(app) as client:
        assert client.get('/health').status_code==200
        payload={'platform':'carousell','source_url':'https://www.carousell.com.my/p/test-listing-123456/','page_title':'Test listing','screenshot_data_url':data_url(image_bytes()),'image_urls':['https://images.example.test/product.jpg'],'listing_evidence':{'h1_text':'Test listing','has_price_signal':True}}
        assert client.post('/api/capture',json=payload).status_code==401
        token=ensure_local_token();r=client.post('/api/capture',json=payload,headers={'X-GuardianLens-Token':token});assert r.status_code==200

def test_api_rejects_marketplace_homepage_with_explicit_message(isolated):
    with TestClient(app) as client:
        token=ensure_local_token()
        payload={'platform':'carousell','source_url':'https://www.carousell.com.my/','page_title':'Carousell','screenshot_data_url':data_url(image_bytes()),'image_urls':['https://images.example.test/product.jpg'],'listing_evidence':{'h1_text':'Carousell','has_price_signal':True}}
        response=client.post('/api/capture',json=payload,headers={'X-GuardianLens-Token':token})
        assert response.status_code==400
        assert response.json()['detail']==INVALID_LISTING_MESSAGE
