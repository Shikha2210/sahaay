import {db} from './db';
import {api,getToken,isTokenExpired} from './api';
import {getSession} from './session';

const DEVICE_KEY='sahaay-device-id';
const BATCH_SIZE=50;
export function getDeviceId(){let id=localStorage.getItem(DEVICE_KEY);if(!id){id=crypto.randomUUID();localStorage.setItem(DEVICE_KEY,id);}return id;}

// ---- other tabs of the same browser share this IndexedDB; tell them when something changed so their screens refresh ----------------
const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('sahaay-sync'):null;
const tellOtherTabs=what=>{try{channel&&channel.postMessage(what)}catch{/* channel closed */}};
export function onOtherTabChange(callback){
  if(!channel) return ()=>{};
  const handler=()=>callback();channel.addEventListener('message',handler);
  return ()=>channel.removeEventListener('message',handler);
}

// Operations are replayed in the order they were created. `seq` is a per-device counter (immune to clock changes and
// to two operations sharing one millisecond); operations from older app versions have no seq, so fall back to time.
export function compareOps(a,b){
  if(Number.isFinite(a.seq)&&Number.isFinite(b.seq)&&a.seq!==b.seq) return a.seq-b.seq;
  if(a.created_at!==b.created_at) return a.created_at<b.created_at?-1:1;
  return a.operation_id<b.operation_id?-1:a.operation_id>b.operation_id?1:0;
}

// `base_version` = the patient version this device last saw (so the server can tell a stale edit from a fresh one).
// It defaults to the local patient record's version (0 for a brand-new patient), never to a hard-coded number.
export async function queueOperation({patient_id,changes,base_version,operation_type='update'}){
  const me=getSession();
  if(!me?.id) throw new Error('Sign in before creating changes.');
  const operation_id=crypto.randomUUID();
  await db.transaction('rw',db.operations,db.sync_state,db.patients,async()=>{
    if(base_version===undefined){
      const local=operation_type==='create'?null:await db.patients.get(patient_id);
      base_version=operation_type==='create'?0:(Number.isInteger(local?.version)?local.version:1);
    }
    const counter=await db.sync_state.get('op_seq');const seq=(counter?.value||0)+1;
    await db.sync_state.put({key:'op_seq',value:seq});
    await db.operations.add({operation_id,seq,patient_id,owner_id:me.id,device_id:getDeviceId(),base_version,changes:operation_type==='create'?{...changes,__create__:true}:changes,operation_type,status:'pending',attempts:0,created_at:new Date().toISOString(),next_retry_at:0});
  });
  tellOtherTabs('changed');
  return operation_id;
}

// ---- unsynced local edits survive server data arriving (a refresh or the pull feed must never overwrite them) --------------------
export function applyPending(patient,pendingOps){
  const out={...patient};
  for(const o of pendingOps){if(o.patient_id!==patient.patient_id) continue;const {__create__,...fields}=o.changes||{};Object.assign(out,fields);}
  return out;
}
const pendingFor=async userId=>(await db.operations.toArray()).filter(o=>o.owner_id===userId&&o.status==='pending').sort(compareOps);
export async function mergeRemotePatients(remote){
  const me=getSession();const pending=me?.id?await pendingFor(me.id):[];
  await db.patients.bulkPut(remote.map(r=>applyPending(r,pending)));
}

// ---- what the Sync centre shows for each operation -----------------------------------------------------------------------------
export function describeOperationError(op){
  const raw=op.last_error||op.server_response?.reason||'';
  if(/^invalid_value/.test(raw)) return 'A value is not allowed: '+raw.replace(/^invalid_value:\s*/,'');
  const known={unsupported_fields:'One of the fields cannot be changed from the app.',identity_status_not_allowed:'Identity status can only start as UNVERIFIED; it cannot be set from here.',
    patient_not_found:'The server does not know this patient yet (their registration may not have synced).',invalid_patient_id:'The patient ID is not valid.',invalid_operation_id:'The change identifier is not valid.',
    invalid_device_id:'The device identifier is not valid.',empty_changes:'The change was empty.',blocked_by_earlier_operation:'Waiting for an earlier change for the same patient.',
    database_transaction_failed:'The server could not save it just now; it will be retried.','Cannot reach the server.':'No connection to the server. It will be retried automatically.','The server took too long to respond.':'The server took too long to respond. It will be retried.'};
  return known[raw]||raw||'';
}
export function operationInfo(op,now=Date.now()){
  if(op.status==='acknowledged') return {label:'Synced',tone:'green',kind:'synced'};
  if(op.status==='acknowledged_with_conflict') return {label:'Synced · conflict raised',tone:'purple',kind:'synced'};
  if(op.status==='rejected') return {label:'Rejected by the server',tone:'red',kind:'failed'};
  if((op.attempts||0)>0&&op.last_error) return {label:(op.next_retry_at||0)>now?'Retrying':'Waiting to retry',tone:'amber',kind:'retrying'};
  return {label:'Waiting to sync',tone:'amber',kind:'waiting'};
}

// ---- retry / discard (never silent: discarding needs an explicit confirmation in the screen that calls this) -------------------
async function ownOperation(id){
  const me=getSession();const op=await db.operations.get(id);
  if(!me?.id||!op||op.owner_id!==me.id) throw new Error('That change does not belong to the signed-in user.');
  return op;
}
export async function retryOperation(id){
  const op=await ownOperation(id);
  if(!['rejected','pending'].includes(op.status)) throw new Error('Only waiting or rejected changes can be retried.');
  await db.operations.update(id,{status:'pending',next_retry_at:0,retry_count:(op.retry_count||0)+1,last_retry_at:new Date().toISOString()});
  tellOtherTabs('changed');return true;
}
export async function retryAllFailed(){
  const me=getSession();if(!me?.id) return 0;
  const todo=(await db.operations.toArray()).filter(o=>o.owner_id===me.id&&(o.status==='rejected'||(o.status==='pending'&&(o.attempts||0)>0)));
  for(const o of todo) await retryOperation(o.operation_id);
  return todo.length;
}
export async function discardOperation(id){
  const op=await ownOperation(id);
  if(op.status!=='rejected') throw new Error('Only rejected changes can be discarded. Changes that are still waiting are never deleted.');
  await db.transaction('rw',db.operations,db.patients,db.audit,async()=>{
    await db.operations.delete(id);
    if(op.operation_type==='create'){   // a registration the server refused: drop the never-synced local copy unless other changes still depend on it
      const p=await db.patients.get(op.patient_id);
      const others=(await db.operations.toArray()).some(o=>o.patient_id===op.patient_id);
      if(p&&p.version===undefined&&!others) await db.patients.delete(op.patient_id);
    }
    await db.audit.add({patient_id:op.patient_id,event_type:'OPERATION_DISCARDED',description:`Discarded a rejected change (${describeOperationError(op)||'rejected'})`,created_at:new Date().toISOString()});
  });
  tellOtherTabs('changed');return true;
}

// ---- synchronisation ---------------------------------------------------------------------------------------------------------
const skipped=()=>({skipped:true,reason:'already_running',offline:false,uploaded:0,conflicts:0,errors:0});
let runningHere=false;   // fallback where the Web Locks API does not exist
// Only ONE sync runs at a time on this device, across all tabs (Web Locks). A second request (manual click, auto-sync timer, another tab)
// is told "already running" instead of sending the same operations again.
export async function synchronize(options={}){
  const guarded=async()=>{
    try{
      const result=await runSync(options);
      if(!result.offline&&!result.authExpired&&!result.skipped){
        await db.sync_state.put({key:'last_sync',value:{at:new Date().toISOString(),uploaded:result.uploaded,conflicts:result.conflicts,errors:result.errors,ok:result.errors===0}});
        tellOtherTabs('synced');
      }
      return result;
    }catch(error){
      await db.sync_state.put({key:'last_sync',value:{at:new Date().toISOString(),ok:false,message:error.message}}).catch(()=>{});
      throw error;
    }
  };
  if(typeof navigator!=='undefined'&&navigator.locks&&navigator.locks.request){
    return navigator.locks.request('sahaay-sync',{ifAvailable:true},lock=>lock?guarded():skipped());
  }
  if(runningHere) return skipped();
  runningHere=true;try{return await guarded();}finally{runningHere=false;}
}

async function runSync({onProgress=()=>{}}={}){
  if(!navigator.onLine) return {offline:true,uploaded:0,conflicts:0};
  const me=getSession(); const token=getToken();
  // No usable session: nothing leaves the device. Local changes stay queued until the user signs in again.
  if(!me?.id||!token||isTokenExpired(token)) return {authExpired:true,offline:false,uploaded:0,conflicts:0,errors:0};
  let uploaded=0,conflictCount=0,errors=0;
  // Only operations created by the signed-in user are ever sent; other users' operations are left untouched.
  const mine=(await db.operations.toArray()).filter(op=>op.owner_id===me.id);
  // Per-patient ordering rule: an operation is never sent ahead of an earlier operation for the same patient that
  // is still waiting (retry back-off) or whose patient-creation was rejected. Those patients are "blocked".
  const blocked=new Set(mine.filter(op=>op.status==='rejected'&&op.operation_type==='create').map(op=>op.patient_id));
  const queue=[];
  for(const op of mine.filter(o=>o.status==='pending').sort(compareOps)){
    if(blocked.has(op.patient_id)) continue;
    if((op.next_retry_at||0)>Date.now()){blocked.add(op.patient_id);continue;}
    queue.push(op);
  }
  for(let offset=0;offset<queue.length;offset+=BATCH_SIZE){
    // `blocked` can grow while earlier batches are processed (a failure there must hold back later batches too).
    const batch=queue.slice(offset,offset+BATCH_SIZE).filter(op=>!blocked.has(op.patient_id));
    if(!batch.length)continue;
    try{
      const response=await api('/api/sync/batch',{method:'POST',body:JSON.stringify({operations:batch.map(({operation_id,patient_id,device_id,base_version,changes,created_at})=>({operation_id,patient_id,device_id:device_id||getDeviceId(),base_version:Number.isInteger(base_version)?base_version:1,changes,created_at,schema_version:1}))})});
      for(const result of response.results||[]){
        const local=batch.find(x=>x.operation_id===result.operation_id);if(!local)continue;
        // Only an explicit server acknowledgement (the server reports one only after its transaction committed) marks an operation done.
        if(['applied','duplicate','conflict'].includes(result.status)){
          await db.operations.update(result.operation_id,{status:result.status==='conflict'?'acknowledged_with_conflict':'acknowledged',server_response:result,acknowledged_at:new Date().toISOString()});
          uploaded++;conflictCount+=(result.conflicts||[]).length;
          for(const c of result.conflicts||[]) await db.conflicts.put({...c,patient_id:local.patient_id,status:'pending',operation_id:result.operation_id});
        }else if(result.status==='rejected'){
          await db.operations.update(result.operation_id,{status:'rejected',server_response:result,last_error:result.reason});errors++;
          if(local.operation_type==='create'||result.reason==='patient_not_found') blocked.add(local.patient_id);
        }else{
          blocked.add(local.patient_id);
          if(result.reason==='blocked_by_earlier_operation') continue; // not this operation's fault: stay pending, no back-off
          const attempts=(local.attempts||0)+1;await db.operations.update(result.operation_id,{attempts,next_retry_at:Date.now()+Math.min(60000,1000*(2**Math.min(attempts,6))),last_error:result.reason||'Retryable server error'});errors++;
        }
      }
      onProgress({uploaded,conflicts:conflictCount,errors});
    }catch(error){
      for(const op of batch){const attempts=(op.attempts||0)+1;await db.operations.update(op.operation_id,{attempts,next_retry_at:Date.now()+Math.min(300000,1000*(2**Math.min(attempts,8))),last_error:error.message});}
      errors+=batch.length;break;
    }
  }
  // Pull is cursor-based and idempotently applies field updates to local records - without overwriting this user's unsynced edits.
  let cursorRow=await db.sync_state.get('global_cursor');let cursor=cursorRow?.value||0;let more=true;
  while(more){
    const pulled=await api(`/api/sync/pull?since_seq=${cursor}&limit=200`);
    const pending=await pendingFor(me.id);
    await db.transaction('rw',db.patients,db.sync_state,async()=>{
      for(const change of pulled.changes||[]){
        const patient=await db.patients.get(change.patient_id);
        if(patient){await db.patients.put(applyPending({...patient,...change.changes,version:Math.max(patient.version||1,change.version||1)},pending));}
      }
      cursor=pulled.next_seq??cursor;await db.sync_state.put({key:'global_cursor',value:cursor,updated_at:new Date().toISOString()});
    });
    more=!!pulled.has_more;
  }
  return {offline:false,uploaded,conflicts:conflictCount,errors,cursor};
}
export function startAutoSync(onResult=()=>{}){
  const run=async()=>{if(!navigator.onLine)return;try{const result=await synchronize();onResult(result);}catch(e){onResult({errors:1,message:e.message});}};
  const online=()=>run();window.addEventListener('online',online);const timer=window.setInterval(run,15000);run();
  return ()=>{window.removeEventListener('online',online);window.clearInterval(timer);};
}
