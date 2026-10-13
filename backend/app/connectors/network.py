"""Exact host allowlists + pinned DNS, HTTPS, bounded reads, no redirects/proxies."""
import ipaddress
import socket
import time
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from app.core.config import get_settings

def allowed_address(host,port):
    settings=get_settings()
    allowed={h.strip().lower() for h in settings.integration_allowed_hosts.split(',') if h.strip()}
    private={h.strip().lower() for h in settings.integration_private_hosts.split(',') if h.strip()}
    if host.lower() not in allowed:
        raise HTTPException(422,'Host is not authorized. Add the exact hostname to INTEGRATION_ALLOWED_HOSTS on the server.')
    try:addresses=sorted({entry[4][0] for entry in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)})
    except socket.gaierror:raise HTTPException(422,'Authorized host could not be resolved') from None
    if not addresses:raise HTTPException(422,'Host has no reachable address')
    for address in addresses:
        ip=ipaddress.ip_address(address)
        if not ip.is_global and host.lower() not in private:
            raise HTTPException(422,'Private addresses require an explicit INTEGRATION_PRIVATE_HOSTS authorization')
        if ip.is_unspecified or ip.is_multicast or str(ip)=='169.254.169.254':
            raise HTTPException(422,'This address is prohibited')
    return addresses[0]

def validate_url(url):
    try:
        parts=urlsplit(url)
        port=parts.port
    except ValueError:raise HTTPException(422,'Endpoint URL is invalid') from None
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.fragment or parts.query:
        raise HTTPException(422,'Use an HTTPS URL without credentials, query parameters, or fragments')
    if port not in [None,443,8443]:raise HTTPException(422,'Only HTTPS ports 443 and 8443 are supported')
    address=allowed_address(parts.hostname,parts.port or 443)
    return parts,address

def request_json(method,url,headers=None,params=None,data=None,retry=True,allow_empty=False):
    parts,address=validate_url(url)
    pinned=httpx.URL(url).copy_with(host=address)
    host=parts.hostname+(f':{parts.port}' if parts.port else '')
    headers={**(headers or {}),'Host':host,'Accept':'application/json'}
    for attempt in range(3 if retry else 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False,trust_env=False) as client:
                with client.stream(method,pinned,headers=headers,params=params,data=data,extensions={'sni_hostname':parts.hostname}) as response:
                    if response.status_code in [429,502,503,504] and retry and attempt<2:
                        delay=response.headers.get('Retry-After','1')
                        try:delay=float(delay)
                        except ValueError:delay=1
                        if delay>5:raise HTTPException(429,'Provider rate limit reached. Retry later.',headers={'Retry-After':str(int(delay))})
                        time.sleep(max(0,min(delay,5)));continue
                    if response.status_code in [401,403]:raise HTTPException(422,'Provider denied access. Check authorization and scopes.')
                    if response.status_code==429:raise HTTPException(429,'Provider rate limit reached. Retry later.')
                    if response.status_code>=300:raise HTTPException(422,f'Provider returned HTTP {response.status_code}; redirects are not followed.')
                    is_json='json' in response.headers.get('content-type','').lower()
                    if not is_json and not allow_empty:raise HTTPException(422,'Provider response must be JSON')
                    body=bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body)>5_000_000:raise HTTPException(422,'Provider response exceeds 5 MB')
                    if allow_empty and not body:return {}
                    if not is_json:raise HTTPException(422,'Provider response must be JSON')
                    import json
                    def invalid_constant(value):raise ValueError('Invalid JSON numeric constant')
                    try:return json.loads(body,parse_constant=invalid_constant)
                    except (ValueError,UnicodeDecodeError):raise HTTPException(422,'Provider returned invalid JSON') from None
        except httpx.HTTPError:
            if retry and attempt<2:time.sleep(attempt+1);continue
            raise HTTPException(422,'Provider request failed or timed out; no credentials were disclosed') from None
