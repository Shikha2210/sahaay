import Dexie from 'dexie';
export const db = new Dexie('SAHAAY_DARK_LOCAL');
db.version(1).stores({
  patients:'patient_id,name,identity_status',
  operations:'operation_id,patient_id,status,created_at',
  conflicts:'conflict_id,patient_id,category,status',
  audit:'++id,patient_id,event_type,created_at'
});
// Version 2 adds a durable sync cursor and metadata without deleting version-1 records.
db.version(2).stores({
  patients:'patient_id,name,identity_status',
  operations:'operation_id,patient_id,status,created_at,device_id',
  conflicts:'conflict_id,patient_id,category,status',
  audit:'++id,patient_id,event_type,created_at',
  sync_state:'key'
}).upgrade(async tx=>{
  // Earlier demo code marked operations demo_acknowledged without contacting a server.
  // Re-queue those operations so they are not silently lost during migration.
  await tx.table('operations').toCollection().modify(op=>{
    if(op.status==='demo_acknowledged') op.status='pending';
    if(!op.device_id) op.device_id='legacy-device';
    if(typeof op.base_version!=='number') op.base_version=1;
    if(typeof op.attempts!=='number') op.attempts=0;
  });
});
// Version 3: operations are tagged with the user who created them (owner_id) so one user's queued
// changes can never be sent under another user's session. Existing rows keep their data (untagged rows are
// claimed by the first user to sign in, see session.js claimDevice).
db.version(3).stores({
  patients:'patient_id,name,identity_status',
  operations:'operation_id,patient_id,status,created_at,device_id,owner_id',
  conflicts:'conflict_id,patient_id,category,status',
  audit:'++id,patient_id,event_type,created_at',
  sync_state:'key'
});
// Version 4: a small per-user cache of the last successful server reads (so read-only screens still show the last-known information
// offline). Tagged with owner_id and wiped on logout / user switch together with all other local data.
db.version(4).stores({
  patients:'patient_id,name,identity_status',
  operations:'operation_id,patient_id,status,created_at,device_id,owner_id',
  conflicts:'conflict_id,patient_id,category,status',
  audit:'++id,patient_id,event_type,created_at',
  sync_state:'key',
  cache:'key,owner_id'
});
export async function seed(){
  // Seed each table independently: existing patients must not prevent missing demo conflicts from being seeded.
  if(await db.patients.count()===0) await db.patients.bulkAdd([
    {patient_id:'SH-2041',name:'Asha Devi',age:38,phone:'•••• 4821',village:'Rampur',condition:'Hypertension',identity_status:'VERIFIED',last_visit:'Today, 10:40 AM',version:1},
    {patient_id:'SH-2042',name:'Ravi Kumar',age:51,phone:'•••• 9012',village:'Khera',condition:'Follow-up due',identity_status:'PENDING',last_visit:'Yesterday',version:1},
    {patient_id:'SH-2043',name:'Meena Sharma',age:25,phone:'•••• 3350',village:'Ward 4',condition:'Routine care',identity_status:'UNVERIFIED',last_visit:'24 Oct 2026',version:1}
  ]);
  if(await db.conflicts.count()===0) await db.conflicts.bulkAdd([
    {conflict_id:'CF-101',patient_id:'SH-2041',field:'Date of birth',category:'DOCUMENT_VERIFIABLE',value_a:'12 Apr 1988',value_b:'21 Apr 1988',status:'pending',synthetic:true},
    {conflict_id:'CF-102',patient_id:'SH-2042',field:'Phone number',category:'PATIENT_VERIFIABLE',value_a:'•••• 9012',value_b:'•••• 9099',status:'pending',synthetic:true},
    {conflict_id:'CF-103',patient_id:'SH-2043',field:'Medication dosage',category:'CRITICAL_CLINICAL',value_a:'10 mg',value_b:'20 mg',status:'pending',synthetic:true}
  ]);
}
