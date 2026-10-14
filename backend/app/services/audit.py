from sqlalchemy.orm import Session
from app.models import AuditLog, User

def audit(db: Session, action: str, actor_id=None, resource_type='user', resource_id=None, details=None, organization_id=None):
    if organization_id is None and actor_id:
        # Default to the organization of the actor's current session.
        organization_id = getattr(db.get(User, actor_id), 'organization_id', None)
    db.add(AuditLog(actor_id=actor_id, organization_id=organization_id, action=action, resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else None, details=details or {}))
