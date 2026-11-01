import uuid, re, json, hashlib, time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo import ReturnDocument
from bson import ObjectId
from app.config import settings
from app.db import client, db, init_db
from app import ai
from app.schemas import RegisterIn, LoginIn, PatientIn, BatchIn, ResolveIn, IdentityAttemptIn, ChatIn, SummaryIn, ExplainIn, operation_problem, validate_change_values, EvidenceSubmitIn, EvidenceReviewIn, ConfirmIn, LinkAccountIn
from app.security import hash_password, verify_password, create_token, current_user, require_roles, public_user, login_limiter, ip_login_limiter, register_limiter
from app.config import validate_bootstrap_password, validate_bootstrap_email

UTC=lambda: datetime.now(timezone.utc).isoformat()
_DUMMY_HASH=hash_password('not-a-real-password-used-only-for-timing')
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
SECURITY_EVENTS={'LOGIN_SUCCESS','LOGIN_FAILED','LOGIN_BLOCKED','LOGOUT','ACCOUNT_REGISTERED'}   # visible to supervisors only
_blocked_logged={}   # key -> monotonic time of the last LOGIN_BLOCKED audit row (so an attacker cannot flood the audit log)
def audit_security(event,actor,description,metadata):
    """Best-effort audit of authentication events. A failure to write the audit row never changes the login result."""
    try: audit(None,actor,event,description,metadata)
    except PyMongoError: pass
def next_seq(session):
    counter=db.counters.find_one_and_update({'_id':'global_change_seq'},{'$inc':{'value':1}},return_document=ReturnDocument.AFTER,session=session)
    return counter['value']
def bootstrap():
    # The first supervisor is only created when none exists. There is NO built-in password:
    # BOOTSTRAP_SUPERVISOR_PASSWORD must be set and strong, otherwise startup stops with a clear message.
    if not db.users.find_one({'role':'supervisor'}):
        try: email=validate_bootstrap_email(settings.bootstrap_supervisor_email); pw=validate_bootstrap_password(settings.bootstrap_supervisor_password, email)
        except ValueError as exc:
            raise RuntimeError(f'No supervisor account exists yet and the bootstrap password is not usable: {exc}') from exc
        db.users.insert_one({'name':'SAHAAY Supervisor','email':email.lower(),'password_hash':hash_password(pw),'role':'supervisor','active':True,'created_at':UTC()})
    if settings.seed_demo_data and db.patients.count_documents({})==0:
        samples=[{'patient_id':'SH-2041','name':'Asha Devi','age':38,'phone':'•••• 4821','village':'Rampur','condition':'Hypertension','identity_status':'VERIFIED','last_visit':'Today, 10:40 AM'}, {'patient_id':'SH-2042','name':'Ravi Kumar','age':51,'phone':'•••• 9012','village':'Khera','condition':'Follow-up due','identity_status':'PENDING','last_visit':'Yesterday'}, {'patient_id':'SH-2043','name':'Meena Sharma','age':25,'phone':'•••• 3350','village':'Ward 4','condition':'Routine care','identity_status':'UNVERIFIED','last_visit':'24 Oct 2026'}]
        for p in samples: p.update(version=1,field_versions={k:1 for k in p if k not in {'version','field_versions'}},updated_at=UTC()); db.patients.insert_one(p)
        # Demo conflict examples use real schema fields, are clearly marked synthetic and can never be resolved (they would not
        # correspond to a real competing edit). They do not change the seeded patients.
        for field,pid,a,b in [('age','SH-2041',38,39),('phone','SH-2042','•••• 9012','•••• 9099'),('dosage','SH-2043','10 mg','20 mg')]:
            db.conflicts.update_one({'conflict_id':'CF-DEMO-'+pid},{'$setOnInsert':{'conflict_id':'CF-DEMO-'+pid,'patient_id':pid,'field':field,'category':category(field),'value_a':a,'value_b':b,'status':'pending','synthetic':True,'field_version_at_conflict':1,'history':[],'created_at':UTC()}},upsert=True)

@asynccontextmanager
async def lifespan(app):
    init_db(); bootstrap(); yield; client.close()
PRODUCTION=settings.environment=='production'
app=FastAPI(title='SAHAAY API',version='3.0.0',lifespan=lifespan,**({'docs_url':None,'redoc_url':None,'openapi_url':None} if PRODUCTION else {}))

def client_ip(request:Request):
    """Address of the caller. X-Forwarded-For is believed only when TRUST_PROXY_HEADERS=true (set it only behind your own proxy)."""
    if settings.trust_proxy_headers:
        fwd=request.headers.get('x-forwarded-for','').split(',')[-1].strip()
        if fwd: return fwd[:64]
    return request.client.host if request.client else 'unknown'
origins=[x.strip() for x in settings.cors_origins.split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST','OPTIONS'],allow_headers=['Authorization','Content-Type'])

@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-site'
    response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"   # the API only returns JSON
    if PRODUCTION: response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    if request.url.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'  # no PHI in shared caches
    return response

@app.get('/health')
def health():
    try: client.admin.command('ping'); return {'status':'ok','mongodb':'connected','database':settings.mongodb_database}
    except Exception: raise HTTPException(503,'MongoDB is unavailable')

@app.post('/api/auth/register')
def register(body:RegisterIn,request:Request):
    if body.role!='patient': raise HTTPException(403,'Public registration can only create patient accounts')
    rip=client_ip(request)
    if register_limiter.blocked(rip): raise HTTPException(429,'Too many accounts were created from this address. Try again later.',headers={'Retry-After':'3600'})
    register_limiter.record_failure(rip)
    doc={'name':body.name.strip(),'email':body.email.lower(),'password_hash':hash_password(body.password),'role':'patient','active':True,'created_at':UTC()}
    try: result=db.users.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'Email already registered')
    doc['_id']=result.inserted_id
    audit_security('ACCOUNT_REGISTERED',doc,'Patient account registered',{})
    return {'access_token':create_token(doc),'token_type':'bearer','user':public_user(doc)}

@app.post('/api/auth/login')
def login(body:LoginIn,request:Request):
    ip=client_ip(request)
    key=f"{ip}|{body.email.lower()}"
    who_hash=hashlib.sha256(body.email.lower().encode()).hexdigest()[:16]   # attempted account is recorded as a hash, not the address
    anon={'_id':'anonymous','role':'anonymous'}
    if login_limiter.blocked(key) or ip_login_limiter.blocked(ip):
        if time.monotonic()-_blocked_logged.get(key,-1e9)>300:
            _blocked_logged[key]=time.monotonic(); audit_security('LOGIN_BLOCKED',anon,'Sign-in blocked after repeated failures',{'ip':ip,'account_hash':who_hash})
        raise HTTPException(429,'Too many failed sign-in attempts. Wait a few minutes and try again.',headers={'Retry-After':'300'})
    user=db.users.find_one({'email':body.email.lower(),'active':True})
    # Same message and similar work whether the account exists or not (no account enumeration).
    valid=verify_password(body.password,user['password_hash']) if user else (verify_password(body.password,_DUMMY_HASH) and False)
    if not valid:
        login_limiter.record_failure(key); ip_login_limiter.record_failure(ip)
        audit_security('LOGIN_FAILED',anon,'Failed sign-in attempt',{'ip':ip,'account_hash':who_hash,'reason':'wrong_password' if user else 'unknown_account'})
        raise HTTPException(401,'Incorrect email or password')
    login_limiter.reset(key)
    audit_security('LOGIN_SUCCESS',user,'Signed in',{'ip':ip})
    return {'access_token':create_token(user),'token_type':'bearer','user':public_user(user)}

@app.post('/api/auth/logout')
def logout(user=Depends(current_user)):
    """Records the sign-out. Tokens are stateless and stay valid until they expire (short-lived); this endpoint is for accountability."""
    audit_security('LOGOUT',user,'Signed out',{})
    return {'ok':True}

@app.get('/api/auth/me')
def me(user=Depends(current_user)): return public_user(user)

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

# identity_status may be sent ONLY when creating a patient and ONLY as UNVERIFIED. Sync can never mark anyone VERIFIED.
CREATE_ONLY_FIELDS={'identity_status'}


class RetryableConflict(Exception):
    """The patient record changed under us inside the transaction; the client should retry the operation."""


def detect_field_conflicts(patient, incoming, base_version, device_id):
    """Field-level optimistic concurrency (pure function, no database access).

    A field is a CONFLICT when it was changed on the server after the version the client based its edit on,
    UNLESS the last writer of that field is the same device (the device's own earlier operation, e.g. two
    sequential edits made offline). A newer write by anyone else is always a conflict: clinical data is never
    resolved by last-write-wins. Returns (updates, conflicting) where conflicting maps field -> current value.
    """
    versions=patient.get('field_versions',{}); writers=patient.get('field_writers',{})
    updates={}; conflicting={}
    for field,value in incoming.items():
        current=patient.get(field)
        if current==value: continue
        changed_since_base=int(versions.get(field,1))>base_version
        if changed_since_base and writers.get(field)!=device_id: conflicting[field]=current
        else: updates[field]=value
    return updates,conflicting


def _replay(response):
    """Answer for an operation_id that was already processed (idempotency)."""
    return {**response,'status':'duplicate'} if response.get('status') in ('applied','conflict') else response


def _precheck(op):
    """Validation that needs no database. Returns a rejection response or None."""
    def rejected(reason,**extra): return {'operation_id':op.operation_id,'patient_id':op.patient_id,'status':'rejected','reason':reason,**extra}
    problem=operation_problem(op)
    if problem: return rejected(problem)
    if not op.changes: return rejected('empty_changes')
    is_create=bool(op.changes.get('__create__'))
    incoming={k:v for k,v in op.changes.items() if k!='__create__'}
    if not incoming: return rejected('empty_changes')
    allowed=SAFE_FIELDS|(CREATE_ONLY_FIELDS if is_create else set())
    unknown=set(incoming)-allowed
    if unknown: return rejected('unsupported_fields',fields=sorted(unknown))
    if incoming.get('identity_status','UNVERIFIED')!='UNVERIFIED': return rejected('identity_status_not_allowed')
    return None


def apply_operation(op, user, session):
    """Apply ONE sync operation. Runs inside a MongoDB transaction (session.with_transaction), which may re-run it after a
    transient error - so it has no side effects outside the transaction and builds its response only from committed-or-
    rolled-back work. The caller must not report the result until the transaction has committed."""
    existing=db.operations.find_one({'operation_id':op.operation_id},session=session)
    if existing: return _replay(existing['response'])
    is_create=bool(op.changes.get('__create__'))
    incoming={k:v for k,v in op.changes.items() if k!='__create__'}
    patient=db.patients.find_one({'patient_id':op.patient_id},session=session)
    now=UTC()
    def receipt(response):
        db.operations.insert_one({'operation_id':op.operation_id,'patient_id':op.patient_id,'device_id':op.device_id,'actor_id':str(user['_id']),'base_version':op.base_version,'changes':op.changes,'status':response['status'],'response':response,'created_at':now},session=session)
    if not patient and is_create:
        doc=dict(incoming); doc.setdefault('identity_status','UNVERIFIED')
        doc.update({'patient_id':op.patient_id,'version':1,'field_versions':{k:1 for k in incoming},'field_writers':{k:op.device_id for k in incoming},'created_at':now,'updated_at':now,'created_by':str(user['_id'])})
        db.patients.insert_one(doc,session=session)
        seq=next_seq(session); db.changes.insert_one({'seq':seq,'patient_id':op.patient_id,'version':1,'changes':incoming,'operation_id':op.operation_id,'created_at':now},session=session)
        response={'operation_id':op.operation_id,'patient_id':op.patient_id,'status':'applied','server_version':1,'applied_fields':list(incoming),'conflicts':[]}
        receipt(response)
        db.audit.insert_one({'patient_id':op.patient_id,'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'PATIENT_CREATED_SYNC','description':'Patient created through offline sync','metadata':{},'created_at':now},session=session)
        return response
    if not patient:
        # No receipt is stored: the operation may become valid later (e.g. its create arrives), and a stored
        # rejection would permanently poison this operation_id.
        return {'operation_id':op.operation_id,'patient_id':op.patient_id,'status':'rejected','reason':'patient_not_found','conflicts':[]}
    updates,conflicting=detect_field_conflicts(patient,incoming,op.base_version,op.device_id)
    conflicts=[]
    for field,current in conflicting.items():
        cat=category(field); key=dedupe_key(incoming[field])
        again=db.conflicts.find_one({'patient_id':op.patient_id,'field':field,'status':'pending','dedupe_key':key},session=session)
        if again:   # the same competing value is already waiting for review: do not create a second pending conflict (C9)
            db.conflicts.update_one({'conflict_id':again['conflict_id']},{'$push':{'history':{'at':now,'actor_id':str(user['_id']),'actor_role':user['role'],'action':'proposed_again','operation_id':op.operation_id,'device_id':op.device_id}}},session=session)
            conflicts.append({'conflict_id':again['conflict_id'],'field':field,'category':cat,'existing':True}); continue
        cid='CF-'+uuid.uuid4().hex[:12].upper()
        db.conflicts.insert_one({'conflict_id':cid,'operation_id':op.operation_id,'patient_id':op.patient_id,'field':field,'category':cat,'value_a':current,'value_b':incoming[field],'base_version':op.base_version,'server_version':patient.get('version',1),'field_version_at_conflict':int(patient.get('field_versions',{}).get(field,1)),'dedupe_key':key,'status':'pending','created_at':now,'actor_id':str(user['_id']),'device_id':op.device_id,'history':[{'at':now,'actor_id':str(user['_id']),'actor_role':user['role'],'action':'created','operation_id':op.operation_id,'device_id':op.device_id}]},session=session)
        conflicts.append({'conflict_id':cid,'field':field,'category':cat})
    new_version=patient.get('version',1)
    if updates:
        new_version+=1
        set_doc={k:v for k,v in updates.items()}
        set_doc.update({f'field_versions.{k}':new_version for k in updates}); set_doc.update({f'field_writers.{k}':op.device_id for k in updates})
        set_doc.update({'version':new_version,'updated_at':now})
        done=db.patients.update_one({'patient_id':op.patient_id,'version':patient.get('version',1)},{'$set':set_doc},session=session)
        if done.matched_count!=1: raise RetryableConflict()
        seq=next_seq(session)
        db.changes.insert_one({'seq':seq,'patient_id':op.patient_id,'version':new_version,'changes':updates,'operation_id':op.operation_id,'created_at':now},session=session)
    status='conflict' if conflicts else 'applied'
    response={'operation_id':op.operation_id,'patient_id':op.patient_id,'status':status,'server_version':new_version,'applied_fields':list(updates),'conflicts':conflicts}
    receipt(response)
    db.audit.insert_one({'patient_id':op.patient_id,'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'SYNC_OPERATION','description':f"{op.operation_id}: {status}",'metadata':{'applied_fields':list(updates),'conflicts':conflicts},'created_at':now},session=session)
    return response


@app.post('/api/sync/batch')
def sync_batch(body:BatchIn,user=Depends(require_roles('worker','supervisor','clinician'))):
    """Operations are processed strictly IN ORDER. Each one is its own transaction. A result is only reported after that
    transaction has committed. If an operation for a patient fails (or its create is rejected), later operations for the
    same patient in this batch are held back ('blocked_by_earlier_operation') so they can never be applied out of order."""
    results=[]; blocked=set()
    for op in body.operations:
        def result(status,**extra): return {'operation_id':op.operation_id,'patient_id':op.patient_id,'status':status,**extra}
        rejected=_precheck(op)
        if rejected:
            results.append(rejected)
            if op.changes.get('__create__'): blocked.add(op.patient_id)
            continue
        if op.patient_id in blocked:
            results.append(result('retryable_error',reason='blocked_by_earlier_operation')); continue
        try:
            with client.start_session() as session:
                response=session.with_transaction(lambda s,op=op: apply_operation(op,user,s))
        except DuplicateKeyError:
            old=db.operations.find_one({'operation_id':op.operation_id})
            response=_replay(old['response']) if old else result('retryable_error',reason='database_transaction_failed')
        except (PyMongoError,RetryableConflict):
            response=result('retryable_error',reason='database_transaction_failed')
        results.append(response)
        if response['status']=='retryable_error' or (response['status']=='rejected' and response.get('reason')=='patient_not_found'):
            blocked.add(op.patient_id)
    return {'results':results,'server_time':UTC()}

@app.get('/api/sync/pull')
def sync_pull(since_seq:int=Query(default=0,ge=0),limit:int=Query(default=200,ge=1,le=500),user=Depends(require_roles('worker','supervisor','clinician'))):
    docs=list(db.changes.find({'seq':{'$gt':since_seq}}).sort('seq',1).limit(limit))
    changes=[{'seq':d['seq'],'patient_id':d['patient_id'],'version':d['version'],'changes':d['changes'],'operation_id':d['operation_id'],'created_at':d['created_at']} for d in docs]
    next_seq=docs[-1]['seq'] if docs else since_seq
    return {'changes':changes,'next_seq':next_seq,'has_more':len(docs)==limit}

# =====================================================================================================================
# CONFLICT ENGINE (Milestone 3).  Policy table + rationale: documentation/CONFLICT_POLICY.md
# Principles: never last-write-wins; a resolution value always comes from approved evidence / a patient's confirmation /
# a clinician's explicit decision; a reviewer's decision is valid only for the field version they actually reviewed;
# everything a resolution touches commits in ONE transaction; every refusal is a structured {code, message} error.
# =====================================================================================================================
POLICY={
 'DOCUMENT_VERIFIABLE':{'resolve_roles':{'supervisor','clinician'},'resolutions':{'apply_evidence'}},
 'PATIENT_VERIFIABLE':{'resolve_roles':{'supervisor','clinician'},'resolutions':{'apply_confirmation'}},
 'CRITICAL_CLINICAL':{'resolve_roles':{'clinician'},'resolutions':{'accept_current','accept_incoming','custom'}},
 'REQUIRES_REVIEW':{'resolve_roles':{'clinician'},'resolutions':{'accept_current','accept_incoming','custom'}},
}
ALL_RESOLUTIONS={'apply_evidence','apply_confirmation','accept_current','accept_incoming','custom','reject_incoming'}
REJECT_ROLES={'clinician'}
EVIDENCE_SUBMIT_ROLES=('worker','supervisor','clinician')
AUDITED_REFUSALS={'role_not_permitted','stale_conflict','value_must_come_from_evidence','confirmation_no_longer_valid','resolution_not_allowed','approved_evidence_required','patient_confirmation_required'}

def fail(http_status,code,message,**extra): raise HTTPException(http_status,{'code':code,'message':message,**extra})
def dedupe_key(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
def field_state(patient,field): return patient.get(field),int(patient.get('field_versions',{}).get(field,1))
def check_value(field,value):
    if field not in SAFE_FIELDS: fail(422,'unsupported_field',f"'{field}' cannot be changed through conflict resolution",field=field)
    if value is None or (isinstance(value,str) and not value.strip()): fail(422,'invalid_value','A non-empty value is required',field=field)
    try: validate_change_values({field:value})
    except ValueError as exc: fail(422,'invalid_value',str(exc),field=field)
def history_entry(user,action,**detail): return {'at':UTC(),'actor_id':str(user['_id']),'actor_role':user['role'],'action':action,**detail}
def run_tx(fn):
    """Run fn(session) as ONE MongoDB transaction (the driver retries transient errors). Nothing is reported as done unless it committed."""
    try:
        with client.start_session() as session: return session.with_transaction(fn)
    except (HTTPException,DuplicateKeyError): raise
    except PyMongoError: fail(503,'transaction_failed','The change could not be saved and nothing was changed. Please retry.')
def conflict_view(c,patient):
    cur,fv=field_state(patient or {},c['field'])
    v=clean(c); v.pop('dedupe_key',None)
    v.update(current_value=cur,current_field_version=fv,changed_since_raised=(cur!=c.get('value_a')) or fv!=c.get('field_version_at_conflict',fv))
    return v
def allowed_actions(role,c,evidence,confirmations):
    none={'resolutions':[],'can_reject':False,'can_submit_evidence':False,'can_review_evidence':False,'can_confirm':False}
    if c['status']!='pending' or c.get('synthetic'): return none
    pol=POLICY[c['category']]; res=[]
    if role in pol['resolve_roles']:
        for r in sorted(pol['resolutions']):
            if r=='apply_evidence' and not any(e['status']=='approved' for e in evidence): continue
            if r=='apply_confirmation' and not any(x['status']=='confirmed' for x in confirmations): continue
            res.append(r)
    doc=c['category']=='DOCUMENT_VERIFIABLE'
    return {'resolutions':res,'can_reject':role in REJECT_ROLES,'can_submit_evidence':doc and role in EVIDENCE_SUBMIT_ROLES,'can_review_evidence':doc and role=='clinician','can_confirm':role=='patient' and c['category']=='PATIENT_VERIFIABLE'}

@app.get('/api/conflicts')
def conflicts(status:str='pending',user=Depends(require_roles('supervisor','clinician'))):
    if status not in {'pending','resolved','rejected','all'}: fail(400,'invalid_status',"status must be pending, resolved, rejected or all")
    rows=list(db.conflicts.find({} if status=='all' else {'status':status}).sort('created_at',-1).limit(500))
    pats={p['patient_id']:p for p in db.patients.find({'patient_id':{'$in':list({r['patient_id'] for r in rows})}})}
    return [conflict_view(r,pats.get(r['patient_id'])) for r in rows]

@app.get('/api/conflicts/{conflict_id}')
def conflict_detail(conflict_id:str,user=Depends(require_roles('supervisor','clinician'))):
    c=db.conflicts.find_one({'conflict_id':conflict_id})
    if not c: fail(404,'conflict_not_found','Conflict not found')
    ev=[clean(e) for e in db.evidence.find({'conflict_id':conflict_id})]; cf=[clean(x) for x in db.patient_confirmations.find({'conflict_id':conflict_id})]
    for x in cf: x.pop('user_id',None)
    v=conflict_view(c,db.patients.find_one({'patient_id':c['patient_id']}))
    v.update(evidence=ev,confirmations=cf,allowed=allowed_actions(user['role'],c,ev,cf))
    return v

PATIENT_VISIBLE=('patient_id','name','age','phone','village','condition','diagnosis','medications','allergies','treatment_plan','blood_pressure','blood_glucose','dosage','last_visit','identity_status','version','updated_at','created_at')

@app.get('/api/me/record')
def my_record(user=Depends(require_roles('patient'))):
    """The signed-in patient's OWN linked record (never anyone else's). Internal bookkeeping fields are not returned."""
    pid=user.get('linked_patient_id')
    if not pid: return {'linked':False}
    p=db.patients.find_one({'patient_id':pid})
    if not p: return {'linked':True,'patient_id':pid,'record':None}
    return {'linked':True,'patient_id':pid,'record':{k:p[k] for k in PATIENT_VISIBLE if k in p}}

@app.get('/api/me/conflicts')
def my_conflicts(status:str='pending',user=Depends(require_roles('patient'))):
    """The signed-in patient's OWN eligible conflicts (patient-verifiable, on the record linked to this account)."""
    if status not in {'pending','resolved','rejected','all'}: fail(400,'invalid_status',"status must be pending, resolved, rejected or all")
    pid=user.get('linked_patient_id')
    if not pid: return {'linked':False,'conflicts':[]}
    q={'patient_id':pid,'category':'PATIENT_VERIFIABLE'}
    if status!='all': q['status']=status
    out=[]
    for c in db.conflicts.find(q).sort('created_at',-1).limit(100):
        mine=db.patient_confirmations.find_one({'conflict_id':c['conflict_id'],'user_id':str(user['_id'])})
        item={'conflict_id':c['conflict_id'],'patient_id':pid,'field':c['field'],'status':c['status'],'value_a':c['value_a'],'value_b':c['value_b'],'created_at':c['created_at'],'my_confirmation':({'confirmation_id':mine['confirmation_id'],'confirmed_value':mine['confirmed_value'],'confirmed_at':mine.get('confirmed_at')} if mine else None)}
        if c['status']!='pending': item.update(selected_value=c.get('selected_value'),resolved_at=c.get('resolved_at'))
        out.append(item)
    return {'linked':True,'patient_id':pid,'conflicts':out}

@app.post('/api/patients/{patient_id}/confirm-field')
def confirm_field(patient_id:str,body:ConfirmIn,user=Depends(require_roles('patient'))):
    """A patient confirms which of the two competing values is correct for THEIR OWN linked record. Records a confirmation only;
    it never changes the patient record - a supervisor/clinician applies it with resolution=apply_confirmation."""
    if user.get('linked_patient_id')!=patient_id: fail(403,'not_your_record','This account is not linked to that patient record.')
    c=db.conflicts.find_one({'conflict_id':body.conflict_id,'patient_id':patient_id})
    if not c: fail(404,'conflict_not_found','No such conflict on your record.')
    if c['category']!='PATIENT_VERIFIABLE': fail(403,'not_patient_verifiable','This information cannot be confirmed by the patient.',category=c['category'])
    if c['status']!='pending' or c.get('synthetic'): fail(409,'conflict_not_pending','This conflict is no longer open.',status=c['status'])
    check_value(c['field'],body.confirmed_value)
    if body.confirmed_value not in (c['value_a'],c['value_b']): fail(422,'value_not_proposed','Choose one of the two values shown.',proposed=[c['value_a'],c['value_b']])
    patient=db.patients.find_one({'patient_id':patient_id}) or {}
    _,fv=field_state(patient,c['field'])
    conf={'confirmation_id':'PC-'+uuid.uuid4().hex[:12].upper(),'conflict_id':c['conflict_id'],'patient_id':patient_id,'user_id':str(user['_id']),'field':c['field'],'confirmed_value':body.confirmed_value,'status':'confirmed','field_version_seen':fv,'confirmed_at':UTC()}
    def work(s):
        db.patient_confirmations.insert_one(conf,session=s)
        db.conflicts.update_one({'conflict_id':c['conflict_id']},{'$push':{'history':history_entry(user,'patient_confirmed',confirmation_id=conf['confirmation_id'])}},session=s)
        db.audit.insert_one({'patient_id':patient_id,'actor_id':str(user['_id']),'actor_role':'patient','event_type':'PATIENT_CONFIRMATION','description':f"Patient confirmed a value for {c['field']}",'metadata':{'conflict_id':c['conflict_id'],'field':c['field'],'confirmation_id':conf['confirmation_id']},'created_at':UTC()},session=s)
    try: run_tx(work)
    except DuplicateKeyError: fail(409,'already_confirmed','You have already confirmed a value for this conflict.')
    return {'confirmation_id':conf['confirmation_id'],'status':'confirmed','confirmed_value':body.confirmed_value,'note':'Recorded. The record itself is changed only when a supervisor or clinician applies your confirmation.'}

@app.post('/api/conflicts/{conflict_id}/evidence')
def submit_evidence(conflict_id:str,body:EvidenceSubmitIn,user=Depends(require_roles(*EVIDENCE_SUBMIT_ROLES))):
    c=db.conflicts.find_one({'conflict_id':conflict_id})
    if not c: fail(404,'conflict_not_found','Conflict not found')
    if c['category']!='DOCUMENT_VERIFIABLE': fail(409,'not_document_verifiable','Evidence applies to document-verifiable conflicts only.',category=c['category'])
    if c['status']!='pending' or c.get('synthetic'): fail(409,'conflict_not_pending','This conflict is no longer open.',status=c['status'])
    check_value(c['field'],body.claimed_value)
    doc={'evidence_reference':body.reference,'conflict_id':conflict_id,'patient_id':c['patient_id'],'description':body.description.strip(),'claimed_value':body.claimed_value,'submitted_by':str(user['_id']),'submitted_role':user['role'],'status':'pending_review','created_at':UTC()}
    def work(s):
        db.evidence.insert_one(doc,session=s)
        db.conflicts.update_one({'conflict_id':conflict_id},{'$push':{'history':history_entry(user,'evidence_submitted',evidence_reference=body.reference)}},session=s)
        db.audit.insert_one({'patient_id':c['patient_id'],'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'EVIDENCE_SUBMITTED','description':'Evidence reference submitted for review','metadata':{'conflict_id':conflict_id,'evidence_reference':body.reference,'field':c['field']},'created_at':UTC()},session=s)
    try: run_tx(work)
    except DuplicateKeyError:
        old=db.evidence.find_one({'conflict_id':conflict_id,'evidence_reference':body.reference}) or {}
        fail(409,'evidence_reference_already_used','This evidence reference was already submitted for this conflict. Submit the document under a NEW reference.',existing_status=old.get('status'))
    return {'evidence_reference':body.reference,'status':'pending_review','warning':'This records a reference only; it does not authenticate the document.'}

@app.post('/api/conflicts/{conflict_id}/evidence/{evidence_reference}/review')
def review_evidence(conflict_id:str,evidence_reference:str,body:EvidenceReviewIn,user=Depends(require_roles('clinician'))):
    c=db.conflicts.find_one({'conflict_id':conflict_id})
    if not c: fail(404,'conflict_not_found','Conflict not found')
    if c['category']!='DOCUMENT_VERIFIABLE': fail(409,'not_document_verifiable','Evidence applies to document-verifiable conflicts only.')
    if c['status']!='pending': fail(409,'conflict_not_pending','This conflict is no longer open.',status=c['status'])
    ev=db.evidence.find_one({'conflict_id':conflict_id,'evidence_reference':evidence_reference})
    if not ev: fail(404,'evidence_not_found','Evidence reference not found for this conflict.')
    if ev['status']!='pending_review': fail(409,'evidence_already_reviewed','This evidence has already been reviewed.',status=ev['status'])
    if ev['submitted_by']==str(user['_id']): fail(403,'separation_of_duties','You submitted this evidence, so you cannot review it.')
    approved=None
    if body.decision=='approve':
        if body.approved_value is None: fail(422,'approved_value_required','When approving, record the exact value the evidence supports.')
        check_value(c['field'],body.approved_value); approved=body.approved_value
    new_status='approved' if body.decision=='approve' else 'rejected'
    def work(s):
        r=db.evidence.update_one({'_id':ev['_id'],'status':'pending_review'},{'$set':{'status':new_status,'approved_value':approved,'reviewed_by':str(user['_id']),'review_note':body.review_note,'reviewed_at':UTC()}},session=s)
        if r.matched_count!=1: fail(409,'evidence_already_reviewed','This evidence has already been reviewed.')
        db.conflicts.update_one({'conflict_id':conflict_id},{'$push':{'history':history_entry(user,'evidence_'+new_status,evidence_reference=evidence_reference,approved_value=approved)}},session=s)
        db.audit.insert_one({'patient_id':c['patient_id'],'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'EVIDENCE_REVIEWED','description':'Evidence reference reviewed','metadata':{'conflict_id':conflict_id,'evidence_reference':evidence_reference,'decision':body.decision,'claimed_value':ev.get('claimed_value'),'approved_value':approved,'field':c['field']},'created_at':UTC()},session=s)
    run_tx(work)
    return {'evidence_reference':evidence_reference,'status':new_status,'approved_value':approved,'note':'Approval records a reviewer decision and the exact approved value; it is not cryptographic document authentication.'}

def _resolve(conflict_id,body,user,s):
    """Runs INSIDE one transaction (re-run by the driver on transient errors, so it only reads current state and writes at the end)."""
    c=db.conflicts.find_one({'conflict_id':conflict_id},session=s)
    if not c: fail(404,'conflict_not_found','Conflict not found')
    if c['status']!='pending': fail(409,'conflict_not_pending',f"This conflict is already {c['status']}.",status=c['status'])
    if c.get('synthetic'): fail(400,'synthetic_conflict','Synthetic demonstration conflicts cannot modify patient records.')
    cat=c['category']; pol=POLICY[cat]; res=body.resolution; field=c['field']
    roles=REJECT_ROLES if res=='reject_incoming' else pol['resolve_roles']
    if user['role'] not in roles: fail(403,'role_not_permitted',f"Only {' or '.join(sorted(roles))} may do this for a {cat} conflict.",required_roles=sorted(roles),category=cat)
    if res!='reject_incoming' and res not in pol['resolutions']: fail(400,'resolution_not_allowed',f"'{res}' is not allowed for a {cat} conflict.",allowed=sorted(pol['resolutions']|{'reject_incoming'}),category=cat)
    patient=db.patients.find_one({'patient_id':c['patient_id']},session=s)
    if not patient: fail(404,'patient_not_found','Patient record not found')
    current,fv=field_state(patient,field)
    note=(body.note or '').strip() or None
    chosen=current; write=False; ev_ref=None; conf_id=None
    if res=='reject_incoming':
        if not note or len(note)<3: fail(422,'note_required','Explain why the incoming change is rejected (note).')
    else:
        if body.expected_field_version is None: fail(422,'expected_field_version_required','Send expected_field_version: the field version you reviewed.',current_field_version=fv)
        if body.expected_field_version!=fv:
            fail(409,'stale_conflict','This field changed after you opened the conflict. Nothing was changed - review the current value and decide again.',expected_field_version=body.expected_field_version,current_field_version=fv,current_value=current,conflict_value_a=c['value_a'],conflict_value_b=c['value_b'])
        if res=='apply_evidence':
            ev=db.evidence.find_one({'conflict_id':conflict_id,'evidence_reference':body.evidence_reference,'status':'approved'},session=s) if body.evidence_reference else None
            if not ev: fail(400,'approved_evidence_required','Name an evidence reference that a clinician has approved for this conflict.')
            if body.selected_value is not None and body.selected_value!=ev['approved_value']: fail(400,'value_must_come_from_evidence','The value is taken from the approved evidence; it cannot be chosen by the resolver.',approved_value=ev['approved_value'])
            chosen=ev['approved_value']; ev_ref=ev['evidence_reference']; write=True
        elif res=='apply_confirmation':
            conf=db.patient_confirmations.find_one({'confirmation_id':body.patient_confirmation_id,'conflict_id':conflict_id,'status':'confirmed'},session=s) if body.patient_confirmation_id else None
            if not conf: fail(400,'patient_confirmation_required','Name a confirmation the linked patient made for this conflict.')
            who=db.users.find_one({'_id':ObjectId(conf['user_id']),'linked_patient_id':c['patient_id'],'active':True},session=s)
            if not who: fail(400,'confirmation_no_longer_valid','The account that confirmed is no longer linked to this patient.')
            if body.selected_value is not None and body.selected_value!=conf['confirmed_value']: fail(400,'value_must_come_from_evidence','The value is taken from the patient confirmation; it cannot be chosen by the resolver.',confirmed_value=conf['confirmed_value'])
            chosen=conf['confirmed_value']; conf_id=conf['confirmation_id']; write=True
        elif res=='accept_incoming': chosen=c['value_b']; write=True
        elif res=='custom':
            if not note or len(note)<3: fail(422,'note_required','Explain the value you are recording (note).')
            chosen=body.selected_value; write=True
        if write: check_value(field,chosen)
        write = write and chosen!=current
    now=UTC(); new_fv=fv; new_version=patient.get('version',1)
    if write:
        new_version+=1; new_fv=new_version
        r=db.patients.update_one({'patient_id':c['patient_id'],'version':patient.get('version',1),f'field_versions.{field}':fv},{'$set':{field:chosen,f'field_versions.{field}':new_fv,f'field_writers.{field}':'resolution:'+conflict_id,'version':new_version,'updated_at':now}},session=s)
        if r.matched_count!=1: fail(409,'stale_conflict','The record changed while you were resolving. Nothing was changed - review and decide again.',current_field_version=fv)
        db.changes.insert_one({'seq':next_seq(s),'patient_id':c['patient_id'],'version':new_version,'changes':{field:chosen},'operation_id':'resolution:'+conflict_id,'created_at':now},session=s)
    outcome='rejected' if res=='reject_incoming' else 'resolved'
    r=db.conflicts.update_one({'conflict_id':conflict_id,'status':'pending'},{'$set':{'status':outcome,'resolution':res,'selected_value':chosen,'resolved_by':str(user['_id']),'resolved_role':user['role'],'resolved_at':now,'evidence_reference':ev_ref,'patient_confirmation_id':conf_id,'resolution_note':note,'field_version_reviewed':fv,'field_version_after':new_fv},'$push':{'history':history_entry(user,outcome,resolution=res,old_value=current,new_value=chosen,evidence_reference=ev_ref,patient_confirmation_id=conf_id,note=note)}},session=s)
    if r.matched_count!=1: fail(409,'conflict_not_pending','This conflict was resolved by someone else.')
    db.audit.insert_one({'patient_id':c['patient_id'],'actor_id':str(user['_id']),'actor_role':user['role'],'event_type':'CONFLICT_REJECTED' if res=='reject_incoming' else 'CONFLICT_RESOLVED','description':f'{outcome.capitalize()} {conflict_id}','metadata':{'conflict_id':conflict_id,'field':field,'category':cat,'resolution':res,'old_value':current,'new_value':chosen,'field_version_reviewed':fv,'field_version_after':new_fv,'evidence_reference':ev_ref,'patient_confirmation_id':conf_id,'note':note},'created_at':now},session=s)
    return {'resolved':res!='reject_incoming','status':outcome,'conflict_id':conflict_id,'resolution':res,'selected_value':chosen,'patient_version':new_version,'field_version':new_fv}

@app.post('/api/conflicts/{conflict_id}/resolve')
def resolve_conflict(conflict_id:str,body:ResolveIn,user=Depends(require_roles('supervisor','clinician'))):
    if body.resolution not in ALL_RESOLUTIONS: fail(400,'unknown_resolution',f"'{body.resolution}' is not a resolution.",allowed=sorted(ALL_RESOLUTIONS))
    try: return run_tx(lambda s:_resolve(conflict_id,body,user,s))
    except HTTPException as exc:
        d=exc.detail if isinstance(exc.detail,dict) else {}
        if d.get('code') in AUDITED_REFUSALS:   # accountability for refused attempts (outside the rolled-back transaction)
            try:
                c=db.conflicts.find_one({'conflict_id':conflict_id}) or {}
                audit(c.get('patient_id'),user,'CONFLICT_ACTION_DENIED',f"Refused: {d['code']}",{'conflict_id':conflict_id,'code':d['code'],'attempted_resolution':body.resolution})
            except PyMongoError: pass
        raise

@app.post('/api/admin/patients/{patient_id}/link-account')
def link_patient_account(patient_id:str,body:LinkAccountIn,user=Depends(require_roles('supervisor'))):
    """Link a patient's login to a patient record. Only after the supervisor attests an out-of-band identity check (recorded)."""
    if not body.identity_verified: fail(422,'identity_check_required','Confirm that you verified the person\'s identity before linking.')
    email=body.email.lower()
    account=db.users.find_one({'email':email,'role':'patient','active':True})
    if not account: fail(404,'patient_account_not_found','No active patient account with that email.')
    if not db.patients.find_one({'patient_id':patient_id}): fail(404,'patient_not_found','Patient record not found.')
    cur=account.get('linked_patient_id')
    if cur==patient_id: return {'linked':True,'already_linked':True,'patient_id':patient_id}
    if cur: fail(409,'account_already_linked','This account is already linked to another patient record. Links are not silently overwritten.')
    if db.users.find_one({'linked_patient_id':patient_id}): fail(409,'patient_already_linked','This patient record is already linked to another account.')
    check={'verified':True,'method':body.identity_check_method,'note':body.identity_check_note.strip(),'checked_by':str(user['_id']),'checked_at':UTC()}
    try: r=db.users.update_one({'_id':account['_id'],'linked_patient_id':{'$exists':False}},{'$set':{'linked_patient_id':patient_id,'linked_by':str(user['_id']),'linked_at':UTC(),'identity_check':check}})
    except DuplicateKeyError: fail(409,'patient_already_linked','This patient record is already linked to another account.')
    if r.matched_count!=1: fail(409,'account_already_linked','This account is already linked to another patient record.')
    audit(patient_id,user,'PATIENT_ACCOUNT_LINKED','Patient account linked after a recorded identity check',{'account_id':str(account['_id']),'identity_check_method':body.identity_check_method})
    return {'linked':True,'already_linked':False,'patient_id':patient_id}

@app.get('/api/sync/conflicts')
def my_sync_conflicts(status:str='all',user=Depends(require_roles('worker','supervisor','clinician'))):
    """Conflict notifications: conflicts that came from THIS user's own synchronised changes (or evidence they submitted), with the outcome."""
    if status not in {'pending','resolved','rejected','all'}: fail(400,'invalid_status',"status must be pending, resolved, rejected or all")
    uid=str(user['_id'])
    q={'$or':[{'actor_id':uid},{'history':{'$elemMatch':{'actor_id':uid,'action':{'$in':['created','proposed_again','evidence_submitted']}}}}]}
    if status!='all': q['status']=status
    rows=list(db.conflicts.find(q).sort('created_at',-1).limit(200))
    ev={}
    for e in db.evidence.find({'conflict_id':{'$in':[r['conflict_id'] for r in rows]}}): ev.setdefault(e['conflict_id'],[]).append(e)
    pats={p['patient_id']:p for p in db.patients.find({'patient_id':{'$in':list({r['patient_id'] for r in rows})}})}
    out=[]
    for c in rows:
        cur,fv=field_state(pats.get(c['patient_id']) or {},c['field'])
        evidence=[{'evidence_reference':e['evidence_reference'],'status':e['status'],'claimed_value':e.get('claimed_value'),'approved_value':e.get('approved_value'),'review_note':e.get('review_note'),'submitted_by_me':e.get('submitted_by')==uid} for e in ev.get(c['conflict_id'],[])]
        item={'conflict_id':c['conflict_id'],'patient_id':c['patient_id'],'field':c['field'],'category':c['category'],'status':c['status'],'value_a':c['value_a'],'value_b':c['value_b'],'current_value':cur,'created_at':c['created_at'],'evidence':evidence,'can_submit_evidence':c['category']=='DOCUMENT_VERIFIABLE' and c['status']=='pending' and not c.get('synthetic')}
        if c['status']!='pending': item.update(resolution=c.get('resolution'),selected_value=c.get('selected_value'),resolved_at=c.get('resolved_at'))
        out.append(item)
    return {'open_count':sum(1 for x in out if x['status']=='pending'),'conflicts':out}

@app.get('/api/evidence')
def evidence_queue(status:str='pending_review',user=Depends(require_roles('clinician','supervisor'))):
    """Evidence waiting for (or already given) clinician review, with the conflict it belongs to. Reviewing stays clinician-only."""
    if status not in {'pending_review','approved','rejected','all'}: fail(400,'invalid_status',"status must be pending_review, approved, rejected or all")
    rows=list(db.evidence.find({} if status=='all' else {'status':status}).sort('created_at',-1).limit(200))
    cons={c['conflict_id']:c for c in db.conflicts.find({'conflict_id':{'$in':[r['conflict_id'] for r in rows]}})}
    return [{'evidence_reference':r['evidence_reference'],'conflict_id':r['conflict_id'],'patient_id':r['patient_id'],'field':(cons.get(r['conflict_id']) or {}).get('field'),'conflict_status':(cons.get(r['conflict_id']) or {}).get('status'),'status':r['status'],'claimed_value':r.get('claimed_value'),'approved_value':r.get('approved_value'),'description':r.get('description'),'submitted_role':r.get('submitted_role'),'created_at':r['created_at'],'submitted_by_me':r.get('submitted_by')==str(user['_id'])} for r in rows]

@app.get('/api/admin/patient-accounts')
def patient_accounts(user=Depends(require_roles('supervisor'))):
    """Patient logins and whether each is linked to a record (for the linking screen). No password data."""
    out=[]
    for u in db.users.find({'role':'patient','active':True}).sort('created_at',-1).limit(500):
        out.append({'email':u['email'],'name':u['name'],'linked_patient_id':u.get('linked_patient_id'),'linked_at':u.get('linked_at'),'identity_check_method':(u.get('identity_check') or {}).get('method'),'created_at':u.get('created_at')})
    return out

@app.post('/api/identity/{patient_id}/attempt')
def identity_attempt(patient_id:str,body:IdentityAttemptIn,user=Depends(require_roles('worker','supervisor'))):
    if not body.consent: raise HTTPException(400,'Recorded consent is required')
    if not db.patients.find_one({'patient_id':patient_id}): raise HTTPException(404,'Patient not found')
    # Do not accept/store raw QR payloads, XML files, Aadhaar numbers or share phrases.
    safe={'patient_id':patient_id,'method':body.method,'status':'PENDING_AUTHENTIC_VERIFICATION','consent_recorded':True,'created_by':str(user['_id']),'created_at':UTC(),'note':'Credential authenticity and holder match are not verified by this endpoint.'}
    db.identity_attempts.insert_one(safe); audit(patient_id,user,'IDENTITY_ATTEMPT_RECORDED','Identity attempt recorded',{'method':safe['method']})
    return {'status':safe['status'],'note':safe['note']}

_EVENT_RE=re.compile(r'^[A-Z][A-Z_]{2,39}$')
def _day(value,name):
    try: return datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError: fail(422,'invalid_date',f"{name} must be a date like 2026-11-02")

@app.get('/api/audit')
def audit_list(page:int=Query(default=1,ge=1,le=100000),page_size:int=Query(default=50,ge=1,le=100),event_type:str|None=Query(default=None,max_length=300),actor_role:str|None=Query(default=None,max_length=20),
               patient_id:str|None=Query(default=None,max_length=40),since:str|None=Query(default=None,max_length=10),until:str|None=Query(default=None,max_length=10),user=Depends(require_roles('supervisor','clinician'))):
    """Filterable, paginated audit history, newest first. Supervisors see everything; clinicians do not see security events (sign-ins, IP addresses)."""
    q={}
    types=[t for t in (event_type or '').split(',') if t]
    for t in types:
        if not _EVENT_RE.match(t): fail(422,'invalid_event_type','event_type must be one or more upper-case event names, comma separated')
    if user['role']!='supervisor':
        types=[t for t in types if t not in SECURITY_EVENTS] if types else types
        q['event_type']={'$in':types} if types else {'$nin':sorted(SECURITY_EVENTS)}
        if event_type and not types: q['event_type']={'$in':['__none__']}
    elif types: q['event_type']={'$in':types}
    if actor_role:
        if not re.match(r'^[a-z]{3,20}$',actor_role): fail(422,'invalid_actor_role','actor_role must be a role name')
        q['actor_role']=actor_role
    if patient_id: q['patient_id']=patient_id
    when={}
    if since: when['$gte']=_day(since,'since').isoformat()
    if until: when['$lt']=(_day(until,'until')+timedelta(days=1)).isoformat()
    if when: q['created_at']=when
    total=db.audit.count_documents(q)
    rows=list(db.audit.find(q).sort([('created_at',-1),('_id',-1)]).skip((page-1)*page_size).limit(page_size))
    ids=[]
    for r in rows:
        try: ids.append(ObjectId(r['actor_id']))
        except Exception: pass
    names={str(u['_id']):u.get('name') for u in db.users.find({'_id':{'$in':ids}})} if ids else {}
    items=[{**clean(r),'actor_name':names.get(r.get('actor_id'))} for r in rows]
    return {'items':items,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}

@app.get('/api/sync/summary')
def sync_summary(user=Depends(require_roles('supervisor','clinician'))):
    """Server-side view of synchronisation for monitoring. Rejected operations are not stored on the server (they stay on the device)."""
    ops=list(db.operations.find({}).sort('created_at',-1).limit(5000))
    by_status={}; devices={}
    for o in ops:
        by_status[o.get('status','?')]=by_status.get(o.get('status','?'),0)+1
        d=devices.setdefault(o.get('device_id','?'),{'device_id':o.get('device_id','?'),'operations':0,'last_seen':'','people':set()})
        d['operations']+=1; d['last_seen']=max(d['last_seen'],o.get('created_at',''));
        if o.get('actor_id'): d['people'].add(o['actor_id'])
    dev=sorted(({**d,'people':len(d['people'])} for d in devices.values()),key=lambda d:d['last_seen'],reverse=True)[:50]
    return {'operations_total':db.operations.count_documents({}),'sampled':len(ops),'by_status':by_status,'devices':dev,'changes_total':db.changes.count_documents({}),'pending_conflicts':db.conflicts.count_documents({'status':'pending'}),'patients':db.patients.count_documents({})}

@app.get('/api/dashboard/summary')
def dashboard(user=Depends(require_roles('worker','supervisor','clinician'))):
    return {'patients':db.patients.count_documents({}),'pending_conflicts':db.conflicts.count_documents({'status':'pending'}),'operations_total':db.operations.count_documents({}),'identity_pending':db.identity_attempts.count_documents({'status':'PENDING_AUTHENTIC_VERIFICATION'}),'recent_audit':db.audit.count_documents({})}

# =====================================================================================================================
# AI ASSISTANCE (Milestone 5). Logic and safety rules: app/ai.py. The AI only produces TEXT: nothing here can approve, reject or resolve
# a conflict, and no user input or AI output is stored. Details sent to the external service are minimal (see ai.py).
# =====================================================================================================================
def ai_fail(e):
    headers={'Retry-After':str(e.extra['retry_after_seconds'])} if e.extra.get('retry_after_seconds') else None
    raise HTTPException(e.status,{'code':e.code,'message':e.message,**e.extra},headers=headers)
def ai_language(lang):
    if lang not in ai.LANGUAGES: fail(422,'unsupported_language','That language is not supported.',supported=sorted(ai.LANGUAGES))
    return lang
def ai_ready(user):
    """Configured + within rate limits, checked BEFORE any data is prepared for sending."""
    try:
        if not ai.configured(): raise ai.AiError(503,'ai_not_configured','The AI assistant is not set up on this server yet.')
        ai.check_rate(user['_id'])
    except ai.AiError as e: ai_fail(e)
def ai_call(messages,max_tokens=500):
    try: return ai.groq_chat(messages,max_tokens=max_tokens)
    except ai.AiError as e: ai_fail(e)

@app.get('/api/ai/status')
def ai_status(user=Depends(current_user)):
    return {'configured':ai.configured(),'languages':{k:v for k,v in ai.LANGUAGES.items() if k!='auto'},'rate_limit_per_minute':settings.ai_rate_per_minute,'disclaimer':ai.DISCLAIMER}

@app.post('/api/chatbot/message')
def chatbot(body:ChatIn,user=Depends(current_user)):
    """General health information for any signed-in user. No patient data is ever attached. Identifiers the user types are removed
    before sending; emergencies get a fixed message without calling the AI; nothing is stored."""
    lang=ai_language(body.language)
    clean,counts=ai.redact(body.message)
    if ai.is_emergency(body.message): return {'answer':ai.EMERGENCY_TEXT,'language':lang,'source':'safety','emergency':True,'redactions':counts,'guarded':False,'disclaimer':ai.DISCLAIMER}
    ai_ready(user)
    history=[{'role':t.role,'text':t.text} for t in body.history]
    answer=ai_call(ai.build_chat_messages(clean,history,lang))
    answer,flagged=ai.guard_output(answer)
    return {'answer':answer,'language':lang,'source':'ai','emergency':False,'redactions':counts,'guarded':flagged,'disclaimer':ai.DISCLAIMER}

@app.post('/api/ai/patient-summary')
def ai_patient_summary(body:SummaryIn,user=Depends(require_roles('patient','clinician'))):
    """Plain-language summary of one patient's record. Patients: their OWN record only. Clinicians: a record they name. Requires explicit
    consent; sends only an age range and clinical fields (never name, phone, village, ID); the sharing is audited before anything is sent."""
    lang=ai_language(body.language)
    if not body.acknowledge_external_ai: fail(422,'consent_required','Please confirm that you agree to send these details to an external AI service.')
    if user['role']=='patient':
        pid=user.get('linked_patient_id')
        if not pid: fail(409,'account_not_linked','Your account is not linked to a health record yet.')
        if body.patient_id and body.patient_id!=pid: fail(403,'not_your_record','You can only summarise your own record.')
    else:
        pid=body.patient_id
        if not pid: fail(422,'patient_id_required','Choose the patient record to summarise.')
    patient=db.patients.find_one({'patient_id':pid})
    if not patient: fail(404,'patient_not_found','Patient record not found.')
    facts=ai.minimal_record_facts(patient)
    if not facts['has_clinical']: return {'summary':None,'source':'none','message':'This record has no clinical details to summarise yet.','fields_shared':[],'language':lang}
    ai_ready(user)
    try: audit(pid,user,'AI_ASSIST','Record details shared with an external AI service for a summary',{'mode':'patient_summary','fields_shared':facts['fields_shared'],'language':lang})
    except PyMongoError: fail(503,'audit_unavailable','The sharing could not be recorded, so nothing was sent. Please try again.')
    aud='patient' if user['role']=='patient' else 'clinician'
    text=ai_call([{'role':'system','content':ai.SUMMARY_SYSTEM.format(audience=aud,audience_short=aud)+ai.language_line(lang)},
                  {'role':'user','content':'Facts from the record:\n'+'\n'.join(f'- {k}: {v}' for k,v in facts['items'])}],max_tokens=600)
    text,flagged=ai.guard_output(text,strict=False)
    return {'summary':text,'language':lang,'source':'ai','fields_shared':facts['fields_shared'],'redactions':facts['redactions'],'guarded':flagged,'disclaimer':ai.DISCLAIMER}

def can_see_conflict(user,c):
    """Same visibility the rest of the API gives each role (patients: own patient-verifiable; workers: their own; staff reviewers: all)."""
    role=user['role']
    if role in ('supervisor','clinician'): return True
    if role=='patient': return c['patient_id']==user.get('linked_patient_id') and c['category']=='PATIENT_VERIFIABLE'
    uid=str(user['_id'])
    return c.get('actor_id')==uid or any(h.get('actor_id')==uid and h.get('action') in ('created','proposed_again','evidence_submitted') for h in c.get('history',[]))

@app.post('/api/ai/conflict-explanation')
def ai_conflict_explanation(body:ExplainIn,user=Depends(require_roles('patient','worker','supervisor','clinician'))):
    """Explains a conflict in plain language. It is sent NO record values and NO identifiers, has no tools, and can only return text:
    it cannot approve, reject or resolve anything (those remain the M3 endpoints with their own role checks)."""
    lang=ai_language(body.language)
    c=db.conflicts.find_one({'conflict_id':body.conflict_id})
    if not c or not can_see_conflict(user,c): fail(404,'conflict_not_found','Conflict not found.')
    patient=db.patients.find_one({'patient_id':c['patient_id']}) or {}
    ev=[clean(e) for e in db.evidence.find({'conflict_id':c['conflict_id']})] if c['category']=='DOCUMENT_VERIFIABLE' else []
    cf=[clean(x) for x in db.patient_confirmations.find({'conflict_id':c['conflict_id']})] if c['category']=='PATIENT_VERIFIABLE' else []
    facts=ai.conflict_facts(user['role'],c,ev,cf,conflict_view(c,patient)['changed_since_raised'])
    ai_ready(user)
    try: audit(c['patient_id'],user,'AI_ASSIST','A conflict explanation was requested (no record values shared)',{'mode':'conflict_explanation','conflict_id':c['conflict_id'],'language':lang})
    except PyMongoError: fail(503,'audit_unavailable','The request could not be recorded, so nothing was sent. Please try again.')
    text=ai_call([{'role':'system','content':ai.EXPLAIN_SYSTEM+ai.language_line(lang)},{'role':'user','content':'Facts:\n'+'\n'.join(f'- {k}: {v}' for k,v in facts.items())}],max_tokens=450)
    text,flagged=ai.guard_output(text)
    return {'explanation':text,'language':lang,'source':'ai','facts_used':facts,'guarded':flagged,'note':'This explanation cannot approve, reject or resolve anything.','disclaimer':ai.DISCLAIMER}
