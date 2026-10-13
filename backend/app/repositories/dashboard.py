from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models import (RegisteredTable, DatabaseConnection, ReconciliationJob,IntegrationConnection,ExternalApplication,
                        ReconciliationRun, ReconciliationResult, MedallionLayer, AuditLog)

def summary(db: Session,user=None):
    count = lambda model, *where: db.scalar(select(func.count()).select_from(model).where(*where)) or 0
    # Latest completed run per job only. Historical repetitions must not inflate metrics.
    ranked = select(ReconciliationRun.id, func.row_number().over(
        partition_by=ReconciliationRun.job_id,
        order_by=(ReconciliationRun.created_at.desc(), ReconciliationRun.id.desc())).label('rank')).where(
            ReconciliationRun.status == 'succeeded').subquery()
    latest = select(ranked.c.id).where(ranked.c.rank == 1)
    totals = db.execute(select(func.coalesce(func.sum(ReconciliationResult.matched),0),
        func.coalesce(func.sum(ReconciliationResult.mismatched),0),
        func.coalesce(func.sum(ReconciliationResult.missing),0),
        func.coalesce(func.sum(ReconciliationResult.extra),0)).where(ReconciliationResult.run_id.in_(latest))).one()
    denominator = totals[0] + totals[1] + totals[2]
    layers = db.execute(select(MedallionLayer.name, MedallionLayer.description, func.count(RegisteredTable.id)).outerjoin(
        RegisteredTable, RegisteredTable.layer_id == MedallionLayer.id).group_by(
        MedallionLayer.id).order_by(MedallionLayer.ordinal)).all()
    activity = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(6)).all()
    integration_query=select(IntegrationConnection,ExternalApplication.name).join(ExternalApplication).where(IntegrationConnection.status!='revoked')
    if user and user.role!='admin':integration_query=integration_query.where(IntegrationConnection.owner_id==user.id)
    recent_connections=[{'id':r.id,'name':r.name,'application':name,'status':r.status} for r,name in db.execute(integration_query.order_by(IntegrationConnection.created_at.desc()).limit(4))]
    recent_tables=[{'id':r.id,'name':r.table_name,'schema_name':r.schema_name,'classification':r.classification} for r in db.scalars(select(RegisteredTable).order_by(RegisteredTable.created_at.desc()).limit(4))]
    return {'total_tables': count(RegisteredTable), 'source_tables': count(RegisteredTable, RegisteredTable.classification == 'source'),
        'target_tables': count(RegisteredTable, RegisteredTable.classification == 'target'),
        'connections': count(DatabaseConnection), 'jobs': count(ReconciliationJob),
        'successful_runs': count(ReconciliationRun, ReconciliationRun.status == 'succeeded'),
        'failed_runs': count(ReconciliationRun, ReconciliationRun.status == 'failed'),
        'matched': totals[0], 'mismatched': totals[1], 'missing': totals[2], 'extra': totals[3],
        'match_percentage': round(totals[0]/denominator*100,2) if denominator else None,
        'metric_scope': 'Latest successful run per job; counts are table comparisons, not unique source records across branches.',
        'layers': [{'name': n,'description': d,'tables': c} for n,d,c in layers],
        'activity': [{'id': str(a.id), 'action': a.action, 'resource_type': a.resource_type, 'created_at': a.created_at} for a in activity],
        'recent_connections':recent_connections,'recent_tables':recent_tables}
