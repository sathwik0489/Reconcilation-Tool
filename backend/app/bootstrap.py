"""One-time administrator provisioning; no public signup or default password."""
import getpass
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import password_hasher
from app.models import User
from app.services.audit import audit

def main():
    email = input('Administrator email: ').strip().lower()
    from email_validator import validate_email
    email = validate_email(email, check_deliverability=False).normalized.lower()
    name = input('Display name: ').strip()
    password = getpass.getpass('Password (12+ characters): ')
    if not name or len(name) > 100 or not 12 <= len(password) <= 128:
        raise SystemExit('Invalid name or password length')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords differ')
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.role == 'admin')):
            raise SystemExit('An administrator already exists. Use the authenticated administration API.')
        user = User(email=email, name=name, role='admin', password_hash=password_hasher.hash(password), preferences={})
        db.add(user)
        db.flush()
        audit(db, 'user.bootstrap', user.id, resource_id=user.id)
        db.commit()
    print('Administrator created.')

if __name__ == '__main__':
    main()
