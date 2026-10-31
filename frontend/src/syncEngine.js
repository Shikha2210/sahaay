import {db} from './db';
import {api} from './api';

const DEVICE_KEY='sahaay-device-id';
export function getDeviceId(){let id=localStorage.getItem(DEVICE_KEY);if(!id){id=crypto.randomUUID();localStorage.setItem(DEVICE_KEY,id);}return id;}
export async function queueOperation({patient_id,changes,base_version=1,operation_type='update'}){
  const operation_id=crypto.randomUUID();
  await db.operations.add({operation_id,patient_id,device_id:getDeviceId(),base_version,changes:operation_type==='create'?{...changes,__create__:true}:changes,operation_type,status:'pending',attempts:0,created_at:new Date().toISOString(),next_retry_at:0});
  return operation_id;
}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export async function synchronize({onProgress=()=>{}}={}){
  if(!navigator.onLine) return {offline:true,uploaded:0,conflicts:0};
  let uploaded=0,conflictCount=0,errors=0;
  const queue=await db.operations.where('status').equals('pending').toArray();
  for(let offset=0;offset<queue.length;offset+=50){
    const batch=queue.slice(offset,offset+50).filter(op=>(op.next_retry_at||0)<=Date.now());
    if(!batch.length)continue;
    try{
      const response=await api('/api/sync/batch',{method:'POST',body:JSON.stringify({operations:batch.map(({operation_id,patient_id,device_id,base_version,changes,created_at})=>({operation_id,patient_id,device_id:device_id||getDeviceId(),base_version:Number.isInteger(base_version)?base_version:1,changes,created_at,schema_version:1}))})});
      for(const result of response.results||[]){
        const local=batch.find(x=>x.operation_id===result.operation_id);if(!local)continue;
        if(['applied','duplicate','conflict'].includes(result.status)){
          await db.operations.update(result.operation_id,{status:result.status==='conflict'?'acknowledged_with_conflict':'acknowledged',server_response:result,acknowledged_at:new Date().toISOString()});
          uploaded++;conflictCount+=(result.conflicts||[]).length;
          for(const c of result.conflicts||[]) await db.conflicts.put({...c,patient_id:local.patient_id,status:'pending',operation_id:result.operation_id});
        }else if(result.status==='rejected'){
          await db.operations.update(result.operation_id,{status:'rejected',server_response:result,last_error:result.reason});errors++;
        }else{
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
