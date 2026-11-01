from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.server_api import ServerApi
from app.config import settings

client = MongoClient(settings.mongodb_uri, server_api=ServerApi('1'), serverSelectionTimeoutMS=10000, retryWrites=True)
db = client[settings.mongodb_database]

def init_db():
    client.admin.command('ping')
    db.users.create_index([('email', ASCENDING)], unique=True)
    db.patients.create_index([('patient_id', ASCENDING)], unique=True)
    db.operations.create_index([('operation_id', ASCENDING)], unique=True)
    db.operations.create_index([('patient_id', ASCENDING), ('created_at', DESCENDING)])
    db.conflicts.create_index([('conflict_id', ASCENDING)], unique=True)
    db.conflicts.create_index([('status', ASCENDING), ('category', ASCENDING)])
    db.changes.create_index([('seq', ASCENDING)], unique=True)
    db.audit.create_index([('created_at', DESCENDING)])
    db.audit.create_index([('event_type', ASCENDING), ('created_at', DESCENDING)])
    db.audit.create_index([('patient_id', ASCENDING), ('created_at', DESCENDING)])
    db.users.create_index([('role', ASCENDING)])
    db.users.create_index([('login_id', ASCENDING)], unique=True, name='uniq_login_id', partialFilterExpression={'login_id': {'$type': 'string'}})
    db.chat_messages.create_index([('user_id', ASCENDING), ('created_at', ASCENDING)])
    db.identity_attempts.create_index([('patient_id', ASCENDING), ('created_at', DESCENDING)])
    db.evidence.create_index([('evidence_reference', ASCENDING), ('conflict_id', ASCENDING)], unique=True)
    db.patient_confirmations.create_index([('confirmation_id', ASCENDING)], unique=True)
    db.patient_confirmations.create_index([('conflict_id', ASCENDING), ('user_id', ASCENDING)], unique=True)
    db.conflicts.create_index([('patient_id', ASCENDING), ('status', ASCENDING)])
    # One PENDING conflict per (patient, field, proposed value): repeated syncs of the same competing value cannot pile up (C9).
    db.conflicts.create_index([('patient_id', ASCENDING), ('field', ASCENDING), ('dedupe_key', ASCENDING)], unique=True, name='uniq_pending_conflict',
                              partialFilterExpression={'status': 'pending', 'dedupe_key': {'$type': 'string'}})
    # A patient record can be linked to at most one account (C10); enforced by the database, not only by application checks.
    db.users.create_index([('linked_patient_id', ASCENDING)], unique=True, name='uniq_linked_patient',
                          partialFilterExpression={'linked_patient_id': {'$type': 'string'}})
    db.counters.update_one({'_id':'global_change_seq'}, {'$setOnInsert':{'value':0}}, upsert=True)
    return True
