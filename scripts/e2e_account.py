"""Provision/revoke a temporary browser-test account. No credentials are printed."""
import argparse
from pathlib import Path
import secrets
import sys
import json
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from app.core.database import SessionLocal
from app.core.security import password_hasher
from app.models import User,AuditLog,Organization,OrganizationMembership
from sqlalchemy import select,delete

parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['create','remove'])
args=parser.parse_args()
file=root/'.runtime'/'e2e-account.json'
with SessionLocal() as db:
    if args.action=='create':
        if file.exists():raise SystemExit('Temporary account file already exists.')
        email=f'e2e-{secrets.token_hex(8)}@example.com'
        password=secrets.token_urlsafe(32)
        # A throwaway organization keeps the test identity away from real workspace data.
        organization=Organization(name='E2E '+email[4:12])
        user=User(email=email,name='Workspace Tester',password_hash=password_hasher.hash(password),preferences={})
        db.add_all([organization,user]);db.flush()
        db.add(OrganizationMembership(organization_id=organization.id,user_id=user.id,role='engineer'));db.commit()
        file.write_text(json.dumps({'email':email,'password':password}),encoding='utf-8')
        print('Temporary E2E engineer account created.')
    else:
        if file.exists():
            email=json.loads(file.read_text())['email']
            user=db.scalar(select(User).where(User.email==email))
            if user:
                # Restrict cleanup to this explicitly generated test identity.
                if not user.email.startswith('e2e-'):raise SystemExit('Refusing to remove a non-test account')
                organizations=db.scalars(select(OrganizationMembership.organization_id).where(OrganizationMembership.user_id==user.id)).all()
                db.execute(delete(AuditLog).where(AuditLog.actor_id==user.id))
                db.delete(user);db.flush()
                # Remove only the 'E2E' organization this script created for the account.
                for organization in db.scalars(select(Organization).where(Organization.id.in_(organizations),Organization.name.startswith('E2E '))):db.delete(organization)
                db.commit()
            file.unlink()
        print('Temporary E2E account removed.')
