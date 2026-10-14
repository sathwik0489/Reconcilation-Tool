"""One-time provisioning of the first organization and its administrator; no public signup or default password."""
import getpass
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import password_hasher
from app.models import User, Organization, OrganizationMembership
from app.services.audit import audit

def main():
    email = input('Administrator email: ').strip().lower()
    from email_validator import validate_email
    email = validate_email(email, check_deliverability=False).normalized.lower()
    name = input('Display name: ').strip()
    organization_name = input('Organization name: ').strip()
    password = getpass.getpass('Password (12+ characters): ')
    if not name or len(name) > 100 or not organization_name or len(organization_name) > 100 or not 12 <= len(password) <= 128:
        raise SystemExit('Invalid name, organization name, or password length')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords differ')
    with SessionLocal() as db:
        if db.scalar(select(OrganizationMembership.id).where(OrganizationMembership.role == 'admin')):
            raise SystemExit('An administrator already exists. Use the authenticated administration API.')
        organization = Organization(name=organization_name)
        user = User(email=email, name=name, password_hash=password_hasher.hash(password), preferences={})
        db.add_all([organization, user])
        db.flush()
        db.add(OrganizationMembership(organization_id=organization.id, user_id=user.id, role='admin'))
        audit(db, 'user.bootstrap', user.id, resource_id=user.id, organization_id=organization.id)
        db.commit()
    print('Organization and administrator created.')

if __name__ == '__main__':
    main()
