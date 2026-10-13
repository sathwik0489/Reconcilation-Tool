from datetime import datetime, timedelta, timezone
from uuid import UUID
import jwt
from pwdlib import PasswordHash
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_db
from app.models import User

password_hasher = PasswordHash.recommended()
# One precomputed dummy hash equalizes unknown account verification cost.
dummy_hash = password_hasher.hash('unusable-placeholder-password')

def encrypt_credential(value: str) -> str:
    return Fernet(get_settings().credential_key.encode()).encrypt(value.encode()).decode()

def decrypt_credential(value: str) -> str:
    return Fernet(get_settings().credential_key.encode()).decrypt(value.encode()).decode()

def make_token(user: User):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({'sub': str(user.id), 'ver': user.token_version, 'iat': now,
                       'exp': now + timedelta(minutes=settings.session_minutes),
                       'iss': 'reconcile-api', 'aud': 'reconcile-web'}, settings.jwt_secret, algorithm='HS256')

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get('recon_session')
    try:
        claims = jwt.decode(token or '', get_settings().jwt_secret, algorithms=['HS256'],
                            issuer='reconcile-api', audience='reconcile-web',
                            options={'require': ['sub','exp','iat','ver']})
        user = db.get(User, UUID(claims['sub']))
        if not user or not user.active or user.token_version != claims['ver']:
            raise ValueError('Invalid session')
        return user
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, 'Sign in to continue') from None

def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != 'admin':
        raise HTTPException(403, 'Administrator role required')
    return user
