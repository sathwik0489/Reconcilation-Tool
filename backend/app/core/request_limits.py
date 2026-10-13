from fastapi.responses import JSONResponse
from fastapi import HTTPException

class RequestTooLarge(HTTPException):
    pass

class RequestSizeLimit:
    """Count request bytes before multipart parsing, including chunked bodies."""
    def __init__(self,app,maximum=6_000_000):
        self.app=app;self.maximum=maximum
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        count=0
        async def limited_receive():
            nonlocal count
            message=await receive()
            if message['type']=='http.request':
                count+=len(message.get('body',b''))
                if count>self.maximum:raise RequestTooLarge(413,'Request body exceeds 6 MB')
            return message
        try:await self.app(scope,limited_receive,send)
        except RequestTooLarge:
            await JSONResponse({'detail':'Request body exceeds 6 MB'},status_code=413)(scope,receive,send)
