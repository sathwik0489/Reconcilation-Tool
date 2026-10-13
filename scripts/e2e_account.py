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
from app.models import User,AuditLog
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
        user=User(email=email,name='Workspace Tester',role='engineer',password_hash=password_hasher.hash(password),preferences={})
        db.add(user);db.commit()
        file.write_text(json.dumps({'email':email,'password':password}),encoding='utf-8')
        print('Temporary E2E engineer account created.')
    else:
        if file.exists():
            email=json.loads(file.read_text())['email']
            user=db.scalar(select(User).where(User.email==email))
            if user:
                # Restrict cleanup to this explicitly generated test identity.
                if not user.email.startswith('e2e-'):raise SystemExit('Refusing to remove a non-test account')
                db.execute(delete(AuditLog).where(AuditLog.actor_id==user.id))
                db.delete(user);db.commit()
            file.unlink()
        print('Temporary E2E account removed.')
