from pathlib import Path
from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    database_url: str
    jwt_secret: str = Field(min_length=32)
    credential_key: str
    app_env: str = 'development'
    allowed_origins: str = 'http://localhost:3000'
    cookie_secure: bool = False
    allow_development_login: bool = False
    session_minutes: int = Field(default=60, ge=5, le=1440)
    integration_allowed_hosts: str = ''
    integration_private_hosts: str = ''
    integration_max_rows: int = Field(default=10000, ge=100, le=100000)
    frontend_public_url: str = 'http://localhost:3000'
    dataset_storage: str = str(ROOT / '.runtime' / 'datasets')

    @model_validator(mode='after')
    def production_security(self):
        from cryptography.fernet import Fernet
        Fernet(self.credential_key.encode())
        if self.app_env == 'production':
            if self.allow_development_login:
                raise ValueError('Development login must be disabled in production')
            if not self.cookie_secure or any(not x.strip().startswith('https://') for x in self.allowed_origins.split(',')):
                raise ValueError('Production requires secure cookies and HTTPS origins')
        if not self.database_url.startswith('postgresql+psycopg://'):
            raise ValueError('PostgreSQL with psycopg is required')
        return self

    @property
    def origins(self):
        return [x.strip() for x in self.allowed_origins.split(',')]

@lru_cache
def get_settings():
    return Settings()
