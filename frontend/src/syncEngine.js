import {db} from './db';
import {api,getToken,isTokenExpired} from './api';
import {getSession} from './session';

const DEVICE_KEY='sahaay-device-id';
const BATCH_SIZE=50;
export function getDeviceId(){let id=localStorage.getItem(DEVICE_KEY);if(!id){id=crypto.randomUUID();localStorage.setItem(DEVICE_KEY,id);}return id;}

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
  return operation_id;
}

export async function synchronize({onProgress=()=>{}}={}){
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
  // Pull is cursor-based and idempotently applies field updates to local records.
  let cursorRow=await db.sync_state.get('global_cursor');let cursor=cursorRow?.value||0;let more=true;
  while(more){
    const pulled=await api(`/api/sync/pull?since_seq=${cursor}&limit=200`);
    await db.transaction('rw',db.patients,db.sync_state,async()=>{
      for(const change of pulled.changes||[]){
        const patient=await db.patients.get(change.patient_id);
        if(patient){await db.patients.put({...patient,...change.changes,version:Math.max(patient.version||1,change.version||1)});}
      }
      cursor=pulled.next_seq??cursor;await db.sync_state.put({key:'global_cursor',value:cursor,updated_at:new Date().toISOString()});
    });
    more=!!pulled.has_more;
  }
  return {offline:false,uploaded,conflicts:conflictCount,errors,cursor};
}
export function startAutoSync(onResult=()=>{}){
  let running=false;
  const run=async()=>{if(running||!navigator.onLine)return;running=true;try{const result=await synchronize();onResult(result);}catch(e){onResult({errors:1,message:e.message});}finally{running=false;}};
  const online=()=>run();window.addEventListener('online',online);const timer=window.setInterval(run,15000);run();
  return ()=>{window.removeEventListener('online',online);window.clearInterval(timer);};
}
