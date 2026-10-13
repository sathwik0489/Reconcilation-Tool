from sqlalchemy.orm import Session
from app.models import AuditLog

def audit(db: Session, action: str, actor_id=None, resource_type='user', resource_id=None, details=None):
    db.add(AuditLog(actor_id=actor_id, action=action, resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else None, details=details or {}))
