import httpx
import json
from urllib.parse import urlsplit
import pytest
from fastapi import HTTPException
from app.connectors import network
from app.core.config import get_settings
from app.reconciliation.engine import compare
from tests.test_integrations import branch

def test_http_transport_pins_address_sni_and_disables_redirects(monkeypatch):
    monkeypatch.setattr(network,'validate_url',lambda url:(urlsplit(url),'203.0.113.10'))
    real_client=httpx.Client
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.url.host=='203.0.113.10'
        assert request.headers['host']=='api.example.com'
        assert request.extensions['sni_hostname']=='api.example.com'
        return httpx.Response(200,json={'data':[{'id':1}]})
    monkeypatch.setattr(network.httpx,'Client',lambda **kwargs:real_client(**kwargs,transport=httpx.MockTransport(handler)))
    assert network.request_json('GET','https://api.example.com/accounts')['data'][0]['id']==1
    assert len(requests)==1

def test_rate_limit_retries_bounded_and_redirect_rejected(monkeypatch):
    monkeypatch.setattr(network,'validate_url',lambda url:(urlsplit(url),'203.0.113.10'))
    real_client=httpx.Client;calls=[]
    def handler(request):calls.append(request);return httpx.Response(429,headers={'Retry-After':'0'},json={'message':'rate limited'})
    monkeypatch.setattr(network.httpx,'Client',lambda **kwargs:real_client(**kwargs,transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(network.time,'sleep',lambda seconds:None)
    with pytest.raises(HTTPException) as raised:network.request_json('GET','https://api.example.com/accounts')
    assert raised.value.status_code==429 and len(calls)==3
    monkeypatch.setattr(network.httpx,'Client',lambda **kwargs:real_client(**kwargs,transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'Location':'https://evil.example.com'}))))
    with pytest.raises(HTTPException):network.request_json('GET','https://api.example.com/accounts')

def test_invalid_json_and_large_response_rejected(monkeypatch):
    monkeypatch.setattr(network,'validate_url',lambda url:(urlsplit(url),'203.0.113.10'))
    real_client=httpx.Client
    monkeypatch.setattr(network.httpx,'Client',lambda **kwargs:real_client(**kwargs,transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*5000001,headers={'Content-Type':'application/json'}))))
    with pytest.raises(HTTPException):network.request_json('GET','https://api.example.com/accounts')

def test_csv_validation_before_destination_access(signed_in):
    path='/api/integrations/files/ingest'
    assert signed_in.post(path,data={'configuration':'{}'},files={'file':('bad.exe',b'hello','application/octet-stream')}).status_code==422
    assert signed_in.post(path,data={'configuration':'{}'},files={'file':('bad.csv',b'id,id\n1,2','text/csv')}).status_code==422
    assert signed_in.post(path,data={'configuration':'{}'},files={'file':('bad.csv',b'id\n1,2','text/csv')}).status_code==422
    assert signed_in.post(path,data={'configuration':'{}'},files={'file':('bad.csv',b'id\n1','text/csv')}).status_code==422

def test_normalized_keys_and_case_rules():
    rule=branch(case_sensitive=False)
    source=[{'source.id':'2','source.amount':'10.0','__ids':{'a'}}]
    stats,*_=compare(source,[{'id':2,'amount':10}],rule)
    assert stats['matched']==1

def test_oversized_request_body_rejected_before_parsing(signed_in):
    response=signed_in.post('/api/auth/login',content=b' '*6000001,headers={'Content-Type':'application/json'})
    assert response.status_code==413
