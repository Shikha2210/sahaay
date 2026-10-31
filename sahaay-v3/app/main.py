import uuid, re
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo import ReturnDocument
from bson import ObjectId
from app.config import settings
from app.db import client, db, init_db
from app.schemas import RegisterIn, LoginIn, PatientIn, BatchIn, ResolveIn
from app.security import hash_password, verify_password, create_token, current_user, require_roles

UTC=lambda: datetime.now(timezone.utc).isoformat()
DOC_FIELDS={'name','age'}
PATIENT_FIELDS={'phone','village'}
CLINICAL_FIELDS={'condition','diagnosis','medications','allergies','treatment_plan','blood_pressure','blood_glucose','dosage'}
SAFE_FIELDS=DOC_FIELDS|PATIENT_FIELDS|CLINICAL_FIELDS|{'last_visit'}
def category(field):
    if field in DOC_FIELDS: return 'DOCUMENT_VERIFIABLE'
    if field in PATIENT_FIELDS: return 'PATIENT_VERIFIABLE'
    if field in CLINICAL_FIELDS: return 'CRITICAL_CLINICAL'
    return 'REQUIRES_REVIEW'
def clean(doc):
    if not doc:return None
    doc=dict(doc); doc.pop('_id',None); return doc
def audit(patient_id, actor, event_type, description, metadata=None):
    db.audit.insert_one({'patient_id':patient_id,'actor_id':str(actor.get('_id','system')) if isinstance(actor,dict) else str(actor),'actor_role':actor.get('role','system') if isinstance(actor,dict) else 'system','event_type':event_type,'description':description,'metadata':metadata or {},'created_at':UTC()})
def next_seq(session):
    counter=db.counters.find_one_and_update({'_id':'global_change_seq'},{'$inc':{'value':1}},return_document=ReturnDocument.AFTER,session=session)
    return counter['value']
def bootstrap():
    if not db.users.find_one({'email':settings.bootstrap_supervisor_email.lower()}):
        db.users.insert_one({'name':'SAHAAY Supervisor','email':settings.bootstrap_supervisor_email.lower(),'password_hash':hash_password(settings.bootstrap_supervisor_password),'role':'supervisor','active':True,'created_at':UTC()})
    if settings.seed_demo_data and db.patients.count_documents({})==0:
        samples=[{'patient_id':'SH-2041','name':'Asha Devi','age':38,'phone':'•••• 4821','village':'Rampur','condition':'Hypertension','identity_status':'VERIFIED','last_visit':'Today, 10:40 AM'}, {'patient_id':'SH-2042','name':'Ravi Kumar','age':51,'phone':'•••• 9012','village':'Khera','condition':'Follow-up due','identity_status':'PENDING','last_visit':'Yesterday'}, {'patient_id':'SH-2043','name':'Meena Sharma','age':25,'phone':'•••• 3350','village':'Ward 4','condition':'Routine care','identity_status':'UNVERIFIED','last_visit':'24 Oct 2026'}]
        for p in samples: p.update(version=1,field_versions={k:1 for k in p if k not in {'version','field_versions'}},updated_at=UTC()); db.patients.insert_one(p)
        # Demo conflict examples are clearly marked as synthetic and do not mutate the seeded patient records.
        for field,pid,a,b in [('Date of birth','SH-2041','12 Apr 1988','21 Apr 1988'),('Phone number','SH-2042','•••• 9012','•••• 9099'),('Medication dosage','SH-2043','10 mg','20 mg')]:
            db.conflicts.update_one({'conflict_id':'CF-DEMO-'+pid},{'$setOnInsert':{'conflict_id':'CF-DEMO-'+pid,'patient_id':pid,'field':field,'category':'DOCUMENT_VERIFIABLE' if field=='Date of birth' else 'PATIENT_VERIFIABLE' if field=='Phone number' else 'CRITICAL_CLINICAL','value_a':a,'value_b':b,'status':'pending','synthetic':True,'created_at':UTC()}},upsert=True)

@asynccontextmanager
async def lifespan(app):
    init_db(); bootstrap(); yield; client.close()
app=FastAPI(title='SAHAAY API',version='3.0.0',lifespan=lifespan)
origins=[x.strip() for x in settings.cors_origins.split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

@app.get('/health')
def health():
    try: client.admin.command('ping'); return {'status':'ok','mongodb':'connected','database':settings.mongodb_database}
    except Exception: raise HTTPException(503,'MongoDB is unavailable')

@app.post('/api/auth/register')
def register(body:RegisterIn):
    if body.role!='patient': raise HTTPException(403,'Public registration can only create patient accounts')
    doc={'name':body.name.strip(),'email':body.email.lower(),'password_hash':hash_password(body.password),'role':'patient','active':True,'created_at':UTC()}
    try: result=db.users.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'Email already registered')
    doc['_id']=result.inserted_id
    return {'access_token':create_token(doc),'token_type':'bearer','user':{'name':doc['name'],'email':doc['email'],'role':doc['role']}}

@app.post('/api/auth/login')
def login(body:LoginIn):
    user=db.users.find_one({'email':body.email.lower(),'active':True})
    if not user or not verify_password(body.password,user['password_hash']): raise HTTPException(401,'Incorrect email or password')
    return {'access_token':create_token(user),'token_type':'bearer','user':{'name':user['name'],'email':user['email'],'role':user['role']}}

@app.get('/api/auth/me')
def me(user=Depends(current_user)): return {'name':user['name'],'email':user['email'],'role':user['role']}

@app.post('/api/admin/users',status_code=201)
def provision_user(body:RegisterIn,user=Depends(require_roles('supervisor'))):
    if body.role not in {'worker','clinician','supervisor'}: raise HTTPException(400,'Role must be worker, clinician or supervisor')
    doc={'name':body.name.strip(),'email':body.email.lower(),'password_hash':hash_password(body.password),'role':body.role,'active':True,'created_at':UTC()}
    try: db.users.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'Email already registered')
    audit(None,user,'USER_PROVISIONED',f"Provisioned {body.role} account",{'email':doc['email']})
    return {'created':True,'email':doc['email'],'role':doc['role']}

@app.get('/api/patients')
def list_patients(q:str|None=None,user=Depends(require_roles('patient','worker','supervisor','clinician'))):
    query={}
    if q: query={'$or':[{'name':{'$regex':re.escape(q),'$options':'i'}},{'patient_id':{'$regex':re.escape(q),'$options':'i'}},{'village':{'$regex':re.escape(q),'$options':'i'}}]}
    # Patient accounts are intentionally not allowed to enumerate the registry in this prototype.
    if user['role']=='patient': raise HTTPException(403,'Patient-specific record linking is not configured in this prototype')
    return [clean(x) for x in db.patients.find(query).sort('patient_id',1).limit(500)]

@app.post('/api/patients',status_code=201)
def create_patient(body:PatientIn,user=Depends(require_roles('worker','supervisor'))):
    p=body.model_dump(); p.update(version=1,field_versions={k:1 for k in p},updated_at=UTC(),created_by=str(user['_id']))
    try: db.patients.insert_one(p)
    except DuplicateKeyError: raise HTTPException(409,'Patient ID already exists')
    audit(p['patient_id'],user,'PATIENT_CREATED','Patient record created',{'patient_id':p['patient_id']})
    return clean(p)

@app.get('/api/patients/{patient_id}')
def get_patient(patient_id:str,user=Depends(require_roles('worker','supervisor','clinician'))):
    p=db.patients.find_one({'patient_id':patient_id})
    if not p: raise HTTPException(404,'Patient not found')
    return clean(p)

@app.post('/api/sync/batch')
def sync_batch(body:BatchIn,user=Depends(require_roles('worker','supervisor','clinician'))):
    results=[]
    for op in body.operations:
        if not op.changes: results.append({'operation_id':op.operation_id,'status':'rejected','reason':'empty_changes'}); continue
        is_create=bool(op.changes.get('__create__'))
        incoming_changes={k:v for k,v in op.changes.items() if k!='__create__'}
        unknown=set(incoming_changes)-SAFE_FIELDS
        if unknown: results.append({'operation_id':op.operation_id,'status':'rejected','reason':'unsupported_fields','fields':sorted(unknown)}); continue
        try:
            with client.start_session() as session:
                with session.start_transaction():
                    existing=db.operations.find_one({'operation_id':op.operation_id},session=session)
                    if existing:
                        results.append({**existing['response'],'status':'duplicate'}); continue
                    patient=db.patients.find_one({'patient_id':op.patient_id},session=session)
                    if not patient and is_create:
                        seed_fields=dict(incoming_changes); seed_fields.update({'patient_id':op.patient_id,'version':1,'field_versions':{k:1 for k in incoming_changes},'created_at':UTC(),'updated_at':UTC(),'created_by':str(user['_id'])})
                        db.patients.insert_one(seed_fields,session=session); patient=seed_fields
                        seq=next_seq(session); db.changes.insert_one({'seq':seq,'patient_id':op.patient_id,'version':1,'changes':incoming_changes,'operation_id':op.operation_id,'created_at':UTC()},session=session)
                        response={'operation_id':op.operation_id,'patient_id':op.patient_id,'status':'applied','server_version':1,'applied_fields':list(incoming_changes),'conflicts':[]}
                        db.operations.insert_one({'operation_id':op.operation_id,'patient_id':op.patient_id,'device_id':op.device_id,'actor_id':str(user['_id']),'base_version':op.base_version,'changes':op.changes,'status':'applied','response':response,'created_at':UTC()},session=session)
                        db.audit.insert_one({'patient_id':op.patient_id,'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'PATIENT_CREATED_SYNC','description':'Patient created through offline sync','metadata':{},'created_at':UTC()},session=session)
                        results.append(response); continue
                    if not patient:
                        response={'operation_id':op.operation_id,'patient_id':op.patient_id,'status':'rejected','reason':'patient_not_found','conflicts':[]}
                        db.operations.insert_one({'operation_id':op.operation_id,'patient_id':op.patient_id,'device_id':op.device_id,'actor_id':str(user['_id']),'status':'rejected','response':response,'created_at':UTC()},session=session)
                        results.append(response); continue
                    versions=patient.get('field_versions',{}); updates={}; conflicts=[]
                    for field,incoming in incoming_changes.items():
                        current=patient.get(field)
                        if current==incoming: continue
                        if int(versions.get(field,1))>op.base_version:
                            cid='CF-'+uuid.uuid4().hex[:12].upper(); cat=category(field)
                            c={'conflict_id':cid,'operation_id':op.operation_id,'patient_id':op.patient_id,'field':field,'category':cat,'value_a':current,'value_b':incoming,'base_version':op.base_version,'server_version':patient.get('version',1),'status':'pending','created_at':UTC(),'actor_id':str(user['_id']),'device_id':op.device_id}
                            db.conflicts.insert_one(c,session=session); conflicts.append({'conflict_id':cid,'field':field,'category':cat})
                        else: updates[field]=incoming
                    new_version=patient.get('version',1)
                    if updates:
                        new_version+=1; set_doc={f'{k}':v for k,v in updates.items()}; set_doc.update({f'field_versions.{k}':new_version for k in updates}); set_doc.update({'version':new_version,'updated_at':UTC()})
                        db.patients.update_one({'patient_id':op.patient_id,'version':patient.get('version',1)},{'$set':set_doc},session=session)
                        seq=next_seq(session)
                        db.changes.insert_one({'seq':seq,'patient_id':op.patient_id,'version':new_version,'changes':updates,'operation_id':op.operation_id,'created_at':UTC()},session=session)
                    status='conflict' if conflicts else 'applied'
                    response={'operation_id':op.operation_id,'patient_id':op.patient_id,'status':status,'server_version':new_version,'applied_fields':list(updates),'conflicts':conflicts}
                    db.operations.insert_one({'operation_id':op.operation_id,'patient_id':op.patient_id,'device_id':op.device_id,'actor_id':str(user['_id']),'base_version':op.base_version,'changes':op.changes,'status':status,'response':response,'created_at':UTC()},session=session)
                    db.audit.insert_one({'patient_id':op.patient_id,'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'SYNC_OPERATION','description':f"{op.operation_id}: {status}",'metadata':{'applied_fields':list(updates),'conflicts':conflicts},'created_at':UTC()},session=session)
                    results.append(response)
        except DuplicateKeyError:
            old=db.operations.find_one({'operation_id':op.operation_id}); results.append({**old['response'],'status':'duplicate'} if old else {'operation_id':op.operation_id,'status':'retryable_error'})
        except PyMongoError as exc:
            results.append({'operation_id':op.operation_id,'status':'retryable_error','reason':'database_transaction_failed'})
    return {'results':results,'server_time':UTC()}

@app.get('/api/sync/pull')
def sync_pull(since_seq:int=Query(default=0,ge=0),limit:int=Query(default=200,ge=1,le=500),user=Depends(require_roles('worker','supervisor','clinician'))):
    docs=list(db.changes.find({'seq':{'$gt':since_seq}}).sort('seq',1).limit(limit))
    changes=[{'seq':d['seq'],'patient_id':d['patient_id'],'version':d['version'],'changes':d['changes'],'operation_id':d['operation_id'],'created_at':d['created_at']} for d in docs]
    next_seq=docs[-1]['seq'] if docs else since_seq
    return {'changes':changes,'next_seq':next_seq,'has_more':len(docs)==limit}

@app.get('/api/conflicts')
def conflicts(status:str='pending',user=Depends(require_roles('supervisor','clinician'))):
    return [clean(c) for c in db.conflicts.find({'status':status}).sort('created_at',-1).limit(500)]

@app.post('/api/conflicts/{conflict_id}/resolve')
def resolve_conflict(conflict_id:str,body:ResolveIn,user=Depends(require_roles('supervisor','clinician'))):
    """Resolve only after category-specific proof is recorded. Uses a MongoDB transaction."""
    if body.resolution not in {'accept_a','accept_b','custom'}:
        raise HTTPException(400,"resolution must be accept_a, accept_b or custom")
    try:
        with client.start_session() as session:
            with session.start_transaction():
                c=db.conflicts.find_one({'conflict_id':conflict_id,'status':'pending'},session=session)
                if not c: raise HTTPException(404,'Pending conflict not found')
                if c.get('synthetic'): raise HTTPException(400,'Synthetic demonstration conflicts cannot modify patient records')
                if c['category']=='CRITICAL_CLINICAL' and user['role']!='clinician':
                    raise HTTPException(403,'Only an authorized clinician can resolve clinical conflicts')
                if c['category']=='REQUIRES_REVIEW' and user['role']!='clinician':
                    raise HTTPException(403,'Unknown fields require clinician review')
                if c['category']=='DOCUMENT_VERIFIABLE':
                    ev=db.evidence.find_one({'evidence_reference':body.evidence_reference,'conflict_id':conflict_id,'status':'approved'},session=session)
                    if not ev: raise HTTPException(400,'A clinician-approved evidence record is required')
                if c['category']=='PATIENT_VERIFIABLE':
                    conf=db.patient_confirmations.find_one({'confirmation_id':body.patient_confirmation_id,'conflict_id':conflict_id,'status':'confirmed'},session=session)
                    if not conf: raise HTTPException(400,'A verified patient confirmation record is required')
                patient=db.patients.find_one({'patient_id':c['patient_id']},session=session)
                if not patient: raise HTTPException(404,'Patient not found')
                chosen=c['value_a'] if body.resolution=='accept_a' else c['value_b'] if body.resolution=='accept_b' else body.selected_value
                if chosen is None: raise HTTPException(400,'A selected value is required for custom resolution')
                field=c['field']; old_version=patient.get('version',1); new_version=old_version+1
                updated=db.patients.update_one({'patient_id':c['patient_id'],'version':old_version},
                    {'$set':{field:chosen,f'field_versions.{field}':new_version,'version':new_version,'updated_at':UTC()}},session=session)
                if updated.modified_count!=1: raise HTTPException(409,'Patient changed during review. Reload the conflict and review again.')
                changed=db.conflicts.update_one({'conflict_id':conflict_id,'status':'pending'},
                    {'$set':{'status':'resolved','resolution':body.resolution,'selected_value':chosen,'resolved_by':str(user['_id']),'resolved_role':user['role'],'resolved_at':UTC(),'evidence_reference':body.evidence_reference,'patient_confirmation_id':body.patient_confirmation_id}},session=session)
                if changed.modified_count!=1: raise HTTPException(409,'Conflict was already resolved by another reviewer')
                seq=next_seq(session)
                db.changes.insert_one({'seq':seq,'patient_id':c['patient_id'],'version':new_version,'changes':{field:chosen},'operation_id':'resolution:'+conflict_id,'created_at':UTC()},session=session)
                db.audit.insert_one({'patient_id':c['patient_id'],'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'CONFLICT_RESOLVED','description':f'Resolved {conflict_id}','metadata':{'field':field,'category':c['category'],'resolution':body.resolution,'evidence_reference':body.evidence_reference,'patient_confirmation_id':body.patient_confirmation_id},'created_at':UTC()},session=session)
        return {'resolved':True,'conflict_id':conflict_id,'patient_version':new_version,'selected_value':chosen}
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(503,'Conflict resolution transaction failed; no success was recorded') from exc

@app.post('/api/admin/patients/{patient_id}/link-account')
def link_patient_account(patient_id:str,email:str,user=Depends(require_roles('supervisor'))):
    """Link only after the supervisor has completed an out-of-band identity check."""
    account=db.users.find_one({'email':email.lower(),'role':'patient','active':True})
    if not account: raise HTTPException(404,'Active patient account not found')
    if not db.patients.find_one({'patient_id':patient_id}): raise HTTPException(404,'Patient record not found')
    existing=db.users.find_one({'linked_patient_id':patient_id,'_id':{'$ne':account['_id']}})
    if existing: raise HTTPException(409,'Patient record is already linked to another account')
    db.users.update_one({'_id':account['_id']},{'$set':{'linked_patient_id':patient_id,'linked_by':str(user['_id']),'linked_at':UTC()}})
    audit(patient_id,user,'PATIENT_ACCOUNT_LINKED','Patient account linked after supervisor identity check',{'email':email.lower()})
    return {'linked':True,'patient_id':patient_id,'email':email.lower()}

@app.post('/api/conflicts/{conflict_id}/evidence')
def submit_evidence(conflict_id:str,reference:str,description:str,user=Depends(require_roles('worker','supervisor','clinician'))):
    c=db.conflicts.find_one({'conflict_id':conflict_id,'status':'pending','category':'DOCUMENT_VERIFIABLE'})
    if not c: raise HTTPException(404,'Pending document-verifiable conflict not found')
    if not reference.strip() or not description.strip(): raise HTTPException(400,'Evidence reference and description are required')
    doc={'evidence_reference':reference.strip(),'conflict_id':conflict_id,'patient_id':c['patient_id'],'description':description.strip(),'submitted_by':str(user['_id']),'status':'pending_review','created_at':UTC()}
    db.evidence.insert_one(doc)
    audit(c['patient_id'],user,'EVIDENCE_SUBMITTED','Evidence reference submitted for review',{'conflict_id':conflict_id,'evidence_reference':reference.strip()})
    return {'evidence_reference':reference.strip(),'status':'pending_review','warning':'This records a reference only; it does not authenticate the document.'}

@app.post('/api/conflicts/{conflict_id}/evidence/{evidence_reference}/review')
def review_evidence(conflict_id:str,evidence_reference:str,decision:str,review_note:str,user=Depends(require_roles('clinician'))):
    if decision not in {'approve','reject'}: raise HTTPException(400,"decision must be approve or reject")
    ev=db.evidence.find_one({'conflict_id':conflict_id,'evidence_reference':evidence_reference,'status':'pending_review'})
    if not ev: raise HTTPException(404,'Pending evidence reference not found')
    status='approved' if decision=='approve' else 'rejected'
    db.evidence.update_one({'_id':ev['_id'],'status':'pending_review'},{'$set':{'status':status,'reviewed_by':str(user['_id']),'review_note':review_note,'reviewed_at':UTC()}})
    audit(ev['patient_id'],user,'EVIDENCE_REVIEWED','Evidence reference reviewed',{'conflict_id':conflict_id,'evidence_reference':evidence_reference,'decision':decision})
    return {'evidence_reference':evidence_reference,'status':status,'note':'Approval records a reviewer decision; it is not cryptographic document authentication.'}

@app.post('/api/identity/{patient_id}/attempt')
def identity_attempt(patient_id:str,body:dict,user=Depends(require_roles('worker','supervisor'))):
    if not body.get('consent'): raise HTTPException(400,'Recorded consent is required')
    if not db.patients.find_one({'patient_id':patient_id}): raise HTTPException(404,'Patient not found')
    # Do not accept/store raw QR payloads, XML files, Aadhaar numbers or share phrases.
    safe={'patient_id':patient_id,'method':body.get('method'),'status':'PENDING_AUTHENTIC_VERIFICATION','consent_recorded':True,'created_by':str(user['_id']),'created_at':UTC(),'note':'Credential authenticity and holder match are not verified by this endpoint.'}
    db.identity_attempts.insert_one(safe); audit(patient_id,user,'IDENTITY_ATTEMPT_RECORDED','Identity attempt recorded',{'method':safe['method']})
    return {'status':safe['status'],'note':safe['note']}

@app.get('/api/audit')
def audit_list(limit:int=Query(default=100,ge=1,le=500),user=Depends(require_roles('supervisor','clinician'))):
    return [clean(x) for x in db.audit.find().sort('created_at',-1).limit(limit)]

@app.get('/api/dashboard/summary')
def dashboard(user=Depends(require_roles('worker','supervisor','clinician'))):
    return {'patients':db.patients.count_documents({}),'pending_conflicts':db.conflicts.count_documents({'status':'pending'}),'pending_operations':db.operations.count_documents({'status':{'$in':['pending','retryable_error']}}),'identity_pending':db.identity_attempts.count_documents({'status':'PENDING_AUTHENTIC_VERIFICATION'}),'recent_audit':db.audit.count_documents({})}

@app.post('/api/chatbot/message')
def chatbot(body:dict,user=Depends(current_user)):
    question=str(body.get('message','')).strip()
    if not question or len(question)>2000: raise HTTPException(400,'Message must be 1–2000 characters')
    if not settings.groq_api_key: raise HTTPException(503,'Chatbot is not configured. Add GROQ_API_KEY to backend .env')
    import httpx
    system='You are SAHAAY, a general health-information assistant. Explain medical terms and general precautions in plain language. Do not diagnose, prescribe, recommend medication/dose changes, interpret a person-specific record, or replace a clinician. For potentially urgent symptoms, advise contacting local emergency services or urgent medical care. Do not request Aadhaar or identifying information. If asked for a diagnosis or treatment decision, explain the limitation and suggest a qualified clinician.'
    try:
        response=httpx.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {settings.groq_api_key}'},json={'model':settings.groq_model,'messages':[{'role':'system','content':system},{'role':'user','content':question}],'temperature':0.3,'max_tokens':500},timeout=30)
        response.raise_for_status(); answer=response.json()['choices'][0]['message']['content']
    except Exception: raise HTTPException(502,'The AI assistant is temporarily unavailable')
    db.chat_messages.insert_one({'user_id':str(user['_id']),'question':question,'answer':answer,'created_at':UTC()})
    return {'answer':answer}
