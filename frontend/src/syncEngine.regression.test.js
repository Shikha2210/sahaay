// Regression tests for audit bugs S2 (ordering) and S3 (stale base versions) - client side.
// `fetch` is a test double: this verifies CLIENT logic only. It is not backend integration.
import {describe,it,expect,beforeEach,vi} from 'vitest';
import {db} from './db';
import {queueOperation,synchronize,compareOps} from './syncEngine';
import {startSession} from './session';
import {setToken} from './api';
import {makeToken} from './test/helpers';

beforeEach(async()=>{await db.delete();await db.open();localStorage.clear();sessionStorage.clear();setToken(null);vi.restoreAllMocks();vi.unstubAllGlobals();
  startSession({id:'user-a',role:'worker',name:'A',email:'a@example.com'},makeToken(600));});

// Fake server. `decide(op, indexInBatch)` may return a custom result; default is "applied".
function mockServer(decide=()=>null){
  const batches=[];
  vi.stubGlobal('fetch',vi.fn(async(url,opts)=>{
    const u=String(url);
    if(u.includes('/api/sync/batch')){
      const ops=JSON.parse(opts.body).operations;batches.push(ops);
      return {ok:true,status:200,json:async()=>({results:ops.map((o,i)=>decide(o,i)||{operation_id:o.operation_id,patient_id:o.patient_id,status:'applied',server_version:1,conflicts:[]})})};
    }
    return {ok:true,status:200,json:async()=>({changes:[],next_seq:0,has_more:false})};
  }));
  return batches;
}
const sentIds=b=>b.flat().map(o=>o.operation_id);

describe('S2 - operations are sent in the order they were created',()=>{
  it('20 operations with random UUIDs are sent in creation order',async()=>{
    const batches=mockServer();const created=[];
    for(let i=0;i<20;i++)created.push(await queueOperation({patient_id:'SH-1',changes:{phone:String(i)},base_version:1}));
    await synchronize();
    expect(sentIds(batches)).toEqual(created);
  });
  it('order is preserved across several batches (120 operations = 3 batches)',async()=>{
    const batches=mockServer();const created=[];
    for(let i=0;i<120;i++)created.push(await queueOperation({patient_id:'SH-'+(i%7),changes:{phone:String(i)},base_version:1}));
    await synchronize();
    expect(batches.length).toBe(3);expect(sentIds(batches)).toEqual(created);
  });
  it('patient creation is sent before the updates that depend on it, even if the update was saved first by a faulty clock',async()=>{
    const batches=mockServer();
    const create=await queueOperation({patient_id:'SH-9',operation_type:'create',changes:{name:'N',identity_status:'UNVERIFIED'}});
    const upd=await queueOperation({patient_id:'SH-9',changes:{village:'V'}});
    await db.operations.update(create,{created_at:'2999-01-01T00:00:00.000Z'}); // clock skew must not matter: seq decides
    await synchronize();
    expect(sentIds(batches)).toEqual([create,upd]);
  });
  it('compareOps falls back to created_at for operations from older app versions (no seq)',()=>{
    const a={operation_id:'b',created_at:'2026-01-01T00:00:00.000Z'},b={operation_id:'a',created_at:'2026-01-02T00:00:00.000Z'};
    expect([b,a].sort(compareOps)).toEqual([a,b]);
  });
  it('sequence numbers keep increasing and are independent of timestamps',async()=>{
    const ids=[];for(let i=0;i<5;i++)ids.push(await queueOperation({patient_id:'SH-1',changes:{phone:String(i)},base_version:1}));
    const seqs=(await Promise.all(ids.map(id=>db.operations.get(id)))).map(o=>o.seq);
    expect(seqs).toEqual([1,2,3,4,5]);
  });
  it('if the create fails, later operations for that patient are not sent in this or later batches; other patients still sync',async()=>{
    const first=[];
    const batches=mockServer((o)=>o.changes.__create__?{operation_id:o.operation_id,patient_id:o.patient_id,status:'retryable_error',reason:'database_transaction_failed'}
      :o.patient_id==='SH-1'?{operation_id:o.operation_id,patient_id:o.patient_id,status:'retryable_error',reason:'blocked_by_earlier_operation'}:null);
    first.push(await queueOperation({patient_id:'SH-1',operation_type:'create',changes:{name:'N'}}));
    for(let i=0;i<60;i++)first.push(await queueOperation({patient_id:'SH-1',changes:{phone:String(i)},base_version:1}));
    const other=await queueOperation({patient_id:'SH-2',changes:{phone:'1'},base_version:1});
    await synchronize();
    const sent=sentIds(batches);
    expect(sent.filter(id=>first.includes(id)).length).toBeLessThanOrEqual(50);       // the second batch never carried SH-1 ops
    expect(sent).toContain(other);
    expect((await db.operations.get(other)).status).toBe('acknowledged');
    for(const id of first)expect((await db.operations.get(id)).status).toBe('pending'); // nothing lost, nothing acknowledged
  });
  it('an operation held back by the server (blocked_by_earlier_operation) keeps pending with no back-off penalty',async()=>{
    mockServer(o=>({operation_id:o.operation_id,patient_id:o.patient_id,status:'retryable_error',reason:'blocked_by_earlier_operation'}));
    const id=await queueOperation({patient_id:'SH-1',changes:{phone:'1'},base_version:1});
    await synchronize();
    const op=await db.operations.get(id);
    expect(op.status).toBe('pending');expect(op.attempts).toBe(0);expect(op.next_retry_at).toBe(0);
  });
  it('a rejected create blocks later operations for that patient on the next sync, without discarding them',async()=>{
    const batches=mockServer(o=>o.changes.__create__?{operation_id:o.operation_id,patient_id:o.patient_id,status:'rejected',reason:'unsupported_fields'}:null);
    const c=await queueOperation({patient_id:'SH-5',operation_type:'create',changes:{name:'N'}});
    await synchronize();
    const u=await queueOperation({patient_id:'SH-5',changes:{phone:'1'},base_version:1});
    batches.length=0;await synchronize();
    expect(sentIds(batches)).not.toContain(u);
    expect((await db.operations.get(c)).status).toBe('rejected');
    expect((await db.operations.get(u)).status).toBe('pending');
  });
  it('an operation waiting for retry back-off holds back later operations of the SAME patient only',async()=>{
    const batches=mockServer();
    const a=await queueOperation({patient_id:'SH-1',changes:{phone:'1'},base_version:1});
    const b=await queueOperation({patient_id:'SH-1',changes:{phone:'2'},base_version:1});
    const c=await queueOperation({patient_id:'SH-2',changes:{phone:'3'},base_version:1});
    await db.operations.update(a,{next_retry_at:Date.now()+60000,attempts:1});
    await synchronize();
    expect(sentIds(batches)).toEqual([c]);
    expect((await db.operations.get(a)).status).toBe('pending');expect((await db.operations.get(b)).status).toBe('pending');
  });
  it('network failure keeps every operation pending and schedules a retry (retry handling preserved)',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('network down');}));
    const id=await queueOperation({patient_id:'SH-1',changes:{phone:'1'},base_version:1});
    await synchronize().catch(()=>{});
    const op=await db.operations.get(id);
    expect(op.status).toBe('pending');expect(op.attempts).toBe(1);expect(op.next_retry_at).toBeGreaterThan(Date.now());
  });
  it('resending after a lost acknowledgement is safe: the same operation_id is sent again unchanged',async()=>{
    let first=true;const seen=[];
    vi.stubGlobal('fetch',vi.fn(async(url,opts)=>{
      if(String(url).includes('/api/sync/batch')){
        const ops=JSON.parse(opts.body).operations;seen.push(...ops.map(o=>o.operation_id));
        if(first){first=false;throw new TypeError('connection dropped after the server applied it');}
        return {ok:true,status:200,json:async()=>({results:ops.map(o=>({operation_id:o.operation_id,patient_id:o.patient_id,status:'duplicate',conflicts:[]}))})};
      }
      return {ok:true,status:200,json:async()=>({changes:[],next_seq:0,has_more:false})};
    }));
    const id=await queueOperation({patient_id:'SH-1',changes:{phone:'1'},base_version:1});
    await synchronize().catch(()=>{});
    await db.operations.update(id,{next_retry_at:0});
    await synchronize();
    expect(seen).toEqual([id,id]);expect((await db.operations.get(id)).status).toBe('acknowledged');
  });
});

describe('S3 - base_version comes from what this device last saw',()=>{
  it('an update takes the version of the local patient record',async()=>{
    await db.patients.put({patient_id:'SH-1',name:'N',version:4});
    const id=await queueOperation({patient_id:'SH-1',changes:{phone:'1'}});
    expect((await db.operations.get(id)).base_version).toBe(4);
  });
  it('a new patient is based on version 0',async()=>{
    const id=await queueOperation({patient_id:'SH-9',operation_type:'create',changes:{name:'N'}});
    expect((await db.operations.get(id)).base_version).toBe(0);
  });
  it('after a pull advances the local version, the next edit is based on the newer version',async()=>{
    await db.patients.put({patient_id:'SH-1',name:'N',version:2});
    const a=await queueOperation({patient_id:'SH-1',changes:{phone:'1'}});
    await db.patients.put({patient_id:'SH-1',name:'N',version:5}); // what a pull does
    const b=await queueOperation({patient_id:'SH-1',changes:{phone:'2'}});
    expect([(await db.operations.get(a)).base_version,(await db.operations.get(b)).base_version]).toEqual([2,5]);
  });
  it('an explicit base_version is respected; an unknown patient falls back to 1',async()=>{
    const a=await queueOperation({patient_id:'SH-1',changes:{phone:'1'},base_version:3});
    const b=await queueOperation({patient_id:'SH-404',changes:{phone:'1'}});
    expect([(await db.operations.get(a)).base_version,(await db.operations.get(b)).base_version]).toEqual([3,1]);
  });
  // The server half of S3 (same device's earlier write is not a conflict; another device's newer write is) is tested in
  // backend/tests/test_sync_fixes.py. Two sequential offline edits share one base_version by design.
});

describe('controls (behaviour that already worked and must keep working)',()=>{
  it('queued operations persist as pending with unique ids',async()=>{
    const ids=new Set();
    for(let i=0;i<5;i++)ids.add(await queueOperation({patient_id:'SH-1',changes:{phone:String(i)},base_version:1}));
    expect(ids.size).toBe(5);expect(await db.operations.where('status').equals('pending').count()).toBe(5);
  });
  it('an operation is marked acknowledged only after the server says so',async()=>{
    mockServer();
    const id=await queueOperation({patient_id:'SH-1',changes:{phone:'1'},base_version:1});
    expect((await db.operations.get(id)).status).toBe('pending');
    await synchronize();
    const op=await db.operations.get(id);
    expect(op.status).toBe('acknowledged');expect(op.server_response.status).toBe('applied');
  });
});
