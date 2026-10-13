import logging
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.api.foundation import router
from app.api.integrations import router as integration_router
from app.api.data_workflows import router as workflow_router
from app.core.config import get_settings
from app.core.request_limits import RequestSizeLimit

settings = get_settings()
app = FastAPI(title='Data Reconciliation Platform', version='0.2.0',
              description='Authenticated external integrations, authorized data staging, metadata-driven reconciliation, and visualization.',
              docs_url='/api/docs' if settings.app_env != 'production' else None,
              openapi_url='/api/openapi.json' if settings.app_env != 'production' else None,
              redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
                   allow_methods=['GET','POST','PATCH','DELETE'], allow_headers=['Content-Type'])
app.add_middleware(RequestSizeLimit)

@app.middleware('http')
async def security_headers(request: Request, call_next):
    request_id = str(uuid.uuid4())
    webhook_path=request.url.path.startswith('/api/integrations/') and request.url.path.endswith('/webhook')
    if request.method in {'POST','PUT','PATCH','DELETE'} and not webhook_path and request.headers.get('origin') not in settings.origins:
        return JSONResponse({'detail':'Request origin is not permitted'}, status_code=403)
    response = await call_next(request)
    response.headers.update({'X-Request-ID':request_id,'X-Content-Type-Options':'nosniff',
                             'X-Frame-Options':'DENY','Referrer-Policy':'same-origin','Cache-Control':'no-store'})
    if settings.app_env == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response

@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    # Never include SQL parameters, connection strings, or driver messages in logs.
    logging.getLogger('reconcile').error('Database operation failed: %s', type(exc).__name__)
    return JSONResponse({'detail':'Database operation failed. Please retry.'}, status_code=503)

@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    # Default validation details may echo rejected password/token input. Never return it.
    errors=[{'loc':error['loc'],'msg':error['msg'],'type':error['type']} for error in exc.errors()]
    return JSONResponse({'detail':errors},status_code=422)

app.include_router(router)
app.include_router(integration_router)
app.include_router(workflow_router)
