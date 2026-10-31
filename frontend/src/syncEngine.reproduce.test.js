// Reproduction tests for audit bugs S2 and S3 (see documentation/BUGS_AND_GAPS.md).
// `it.fails` = the test asserts CORRECT behaviour and is expected to fail while the bug exists.
// When the bug is fixed, vitest reports these as failures -> remove `.fails` then.
// NOTE: fetch is replaced by a test double. This tests client logic only; it is NOT backend integration.
import {describe,it,expect,beforeEach,vi} from 'vitest';
import {db} from './db';
import {queueOperation,synchronize} from './syncEngine';

beforeEach(async()=>{await db.delete();await db.open();localStorage.clear();vi.restoreAllMocks();});

function mockServer(){
  const sentOrder=[];
  vi.stubGlobal('fetch',vi.fn(async(url,opts)=>{
    const u=String(url);
    if(u.includes('/api/sync/batch')){
      const ops=JSON.parse(opts.body).operations;ops.forEach(o=>sentOrder.push(o.operation_id));
      return {ok:true,json:async()=>({results:ops.map(o=>({operation_id:o.operation_id,patient_id:o.patient_id,status:'applied',server_version:1,conflicts:[]}))})};
    }
    return {ok:true,json:async()=>({changes:[],next_seq:0,has_more:false})};
  }));
  return sentOrder;
}

describe('offline queue (current behaviour vs. required behaviour)',()=>{
  it.fails('S2: operations are sent to the server in the order they were created',async()=>{
    const sentOrder=mockServer();
    const created=[];
    for(let i=0;i<20;i++){created.push(await queueOperation({patient_id:'SH-1',changes:{phone:String(i)},base_version:1}));}
    await synchronize();
    expect(sentOrder).toEqual(created);
  });

  it.fails('S3: a second local edit to the same patient does not reuse the first edit\'s stale base_version',async()=>{
    const first=await queueOperation({patient_id:'SH-1',changes:{phone:'111'},base_version:1});
    const second=await queueOperation({patient_id:'SH-1',changes:{phone:'222'},base_version:1});
    const a=await db.operations.get(first),b=await db.operations.get(second);
    // Two sequential edits from one device must not both claim the same base: the server would
    // report the second as a conflict with the first (field_version > base_version).
    expect(b.base_version).toBeGreaterThan(a.base_version);
  });

  it('control: queued operations persist as pending with unique ids (works today)',async()=>{
    const ids=new Set();
    for(let i=0;i<5;i++)ids.add(await queueOperation({patient_id:'SH-1',changes:{phone:String(i)},base_version:1}));
    expect(ids.size).toBe(5);
    expect(await db.operations.where('status').equals('pending').count()).toBe(5);
  });
});
