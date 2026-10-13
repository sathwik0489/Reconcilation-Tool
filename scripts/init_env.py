"""Generate local secrets once. Values are never printed."""
from pathlib import Path
import secrets
from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
env = root / '.env'
if env.exists():
    raise SystemExit('.env already exists; kept unchanged.')
runtime = root / '.runtime'
runtime.mkdir(exist_ok=True)
password = secrets.token_urlsafe(32)
(runtime / 'pg-password').write_text(password, encoding='utf-8')
env.write_text(f'''POSTGRES_USER=recon_dev
POSTGRES_PASSWORD={password}
POSTGRES_DB=reconcile
DATABASE_URL=postgresql+psycopg://recon_dev:{password}@127.0.0.1:55432/reconcile
JWT_SECRET={secrets.token_urlsafe(48)}
CREDENTIAL_KEY={Fernet.generate_key().decode()}
APP_ENV=development
COOKIE_SECURE=false
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
API_INTERNAL_URL=http://127.0.0.1:8000
FRONTEND_PUBLIC_URL=http://localhost:3000
INTEGRATION_ALLOWED_HOSTS=
INTEGRATION_PRIVATE_HOSTS=
INTEGRATION_MAX_ROWS=10000
''', encoding='utf-8')
print('Created .env and local PostgreSQL password file. Keep both private.')
