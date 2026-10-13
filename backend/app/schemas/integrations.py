import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,Field,ConfigDict,model_validator,field_validator

IDENTIFIER=r'^[A-Za-z_][A-Za-z0-9_]{0,62}$'
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Pagination(Strict):
    mode: Literal['none','offset','page','cursor']='none'
    size_parameter: str=Field(default='limit',pattern=IDENTIFIER)
    offset_parameter: str=Field(default='offset',pattern=IDENTIFIER)
    next_cursor_path: str=Field(default='next_cursor',max_length=128)
    page_size: int=Field(default=100,ge=1,le=1000)

class ResourceInput(Strict):
    name: str=Field(pattern=IDENTIFIER)
    path: str=Field(min_length=1,max_length=512)
    record_path: str=Field(default='',max_length=128,pattern=r'^[A-Za-z0-9_.]*$')
    pagination: Pagination=Field(default_factory=Pagination)
    available_filters: list[str]=Field(default_factory=list,max_length=20)
    @field_validator('path')
    @classmethod
    def safe_path(cls,value):
        from urllib.parse import unquote
        decoded=unquote(value)
        if not decoded.startswith('/') or decoded.startswith('//') or any(x in decoded for x in ['..','?','#','\\','://']):
            raise ValueError('Use a relative resource path without queries or traversal')
        return value

class IntegrationInput(Strict):
    application: Literal['rest','postgresql','servicenow']='rest'
    name: str=Field(min_length=1,max_length=100)
    classification: Literal['source','target']='source'
    authentication_type: Literal['none','api_key','bearer','basic','oauth2','password']='bearer'
    base_url: str=Field(default='',max_length=512)
    hostname: str=Field(default='',max_length=255)
    port: int=Field(default=5432,ge=1,le=65535)
    database_name: str=Field(default='',max_length=128)
    schema_name: str=Field(default='public',pattern=IDENTIFIER)
    sslmode: Literal['verify-full','require']='verify-full'
    username: str=Field(default='',max_length=128)
    secret: str=Field(default='',max_length=4096)
    api_key_header: str=Field(default='X-API-Key',pattern=r'^[A-Za-z][A-Za-z0-9-]{0,63}$')
    client_id: str=Field(default='',max_length=512)
    client_secret: str=Field(default='',max_length=4096)
    authorization_url: str=Field(default='',max_length=1024)
    token_url: str=Field(default='',max_length=1024)
    revocation_url: str=Field(default='',max_length=1024)
    scopes: list[str]=Field(default_factory=list,max_length=30)
    resources: list[ResourceInput]=Field(default_factory=list,max_length=50)
    webhook_secret: str=Field(default='',max_length=256)
    @model_validator(mode='after')
    def compatible(self):
        if self.application=='postgresql':
            if self.authentication_type!='password' or not all([self.hostname,self.database_name,self.username,self.secret]):
                raise ValueError('PostgreSQL needs hostname, database, username, and password authentication')
        else:
            if self.authentication_type=='password':raise ValueError('Password authentication is only for databases')
            if not self.base_url:raise ValueError('HTTPS base URL is required')
            if self.authentication_type in ['api_key','bearer','basic'] and not self.secret:raise ValueError('A credential is required')
            if self.authentication_type=='basic' and not self.username:raise ValueError('Basic auth requires username')
            if self.authentication_type=='oauth2' and not all([self.client_id,self.authorization_url,self.token_url]):raise ValueError('OAuth requires client ID and official authorization/token endpoints')
            if self.application=='servicenow' and self.authentication_type in ['none','api_key','basic']:
                raise ValueError('Use the official OAuth flow or an issued bearer token for ServiceNow')
            if self.api_key_header.lower() in ['host','authorization','cookie','content-length','connection','transfer-encoding']:
                raise ValueError('Reserved API key header')
        return self

class IngestInput(Strict):
    resource_id: UUID
    destination_id: UUID
    table_name: str=Field(pattern=IDENTIFIER)
    schema_name: str=Field(default='public',pattern=IDENTIFIER)
    layer: Literal['bronze','silver','gold']='bronze'
    mode: Literal['full','incremental']='full'
    columns: list[str]=Field(min_length=1,max_length=100)
    watermark_column: str|None=None
    configuration_id: UUID|None=None
    confirmed: bool=False
    @model_validator(mode='after')
    def incremental(self):
        if self.table_name.startswith('_reconcile_'):raise ValueError('This table prefix is reserved for sync bookkeeping')
        if self.mode=='incremental' and self.watermark_column not in self.columns:
            raise ValueError('Incremental ingestion requires a selected watermark column')
        if len(set(self.columns))!=len(self.columns):raise ValueError('Duplicate columns')
        return self

class ColumnInput(Strict):
    name: str=Field(pattern=IDENTIFIER)
    data_type: Literal['INTEGER','BIGINT','VARCHAR','TEXT','DECIMAL','DATE','TIMESTAMP','BOOLEAN']='TEXT'
    nullable: bool=True
    primary_key: bool=False

class TableInput(Strict):
    connection_id: UUID
    table_name: str=Field(pattern=IDENTIFIER)
    schema_name: str=Field(default='public',pattern=IDENTIFIER)
    layer: Literal['bronze','silver','gold']='bronze'
    action: Literal['create','register']='create'
    columns: list[ColumnInput]=Field(default_factory=list,max_length=100)
    confirmed: bool=False
    @field_validator('table_name')
    @classmethod
    def not_reserved(cls,name):
        if name.startswith('_reconcile_'):raise ValueError('Reserved table prefix')
        return name
