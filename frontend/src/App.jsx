import React,{useEffect,useMemo,useRef,useState}from'react';
import{Activity,AlertTriangle,ArrowDownUp,ArrowRight,ArrowUpRight,Bell,CalendarDays,Check,CheckCircle2,ChevronDown,ChevronRight,Cloud,CloudOff,FileCheck2,FileText,Fingerprint,HeartPulse,Home,Lock,LogOut,Menu,MessageCircle,Plus,RefreshCw,Search,Send,Settings,ShieldCheck,Sparkles,Stethoscope,Users,Wifi,WifiOff,X,ClipboardList,Camera,Upload,FileUp,ShieldAlert,Info,RotateCcw,ScanLine,UserRound,Clock3,CircleHelp,ExternalLink}from'lucide-react';
import{db,seed}from'./db';
import{api,loginApi,registerApi,getToken,isTokenExpired,SESSION_EXPIRED_EVENT}from'./api';
import ConflictReview from'./ConflictReview';
import PatientWorkspace from'./PatientWorkspace';
import AiAssistant from'./AiAssistant';
import WorkerConflicts from'./WorkerConflicts';
import SupervisorAccounts from'./SupervisorAccounts';
import ClinicianWorkspace from'./ClinicianWorkspace';
import{restoreSession,startSession,endSession,claimDevice,discardAndClaim,beginLogout,completeLogout,getSession}from'./session';
const live=()=>{const t=getToken();return !!t&&!isTokenExpired(t)};
import{queueOperation,synchronize,startAutoSync}from'./syncEngine';
const roles={patient:{label:'Patient',icon:HeartPulse},worker:{label:'Healthcare Worker',icon:Stethoscope},supervisor:{label:'Professional Supervisor',icon:ShieldCheck},clinician:{label:'Authorized Clinician',icon:Stethoscope}};
const title=p=>({'dashboard':'Dashboard','my-health':'My health','assistant':'AI assistant','identity':'Identity verification','patients':'Patient directory','sync':'Sync centre','conflicts':'Conflict review','audit':'Audit history','confirmations':'Confirm information','my-conflicts':'Sync conflicts','accounts':'Patient accounts','evidence':'Evidence review','history':'Resolution history'})[p]||p;
function Badge({children,tone='neutral'}){return <span className={'badge '+tone}>{children}</span>}
export default function App(){const[session,setSession]=useState(()=>restoreSession().session),[expired,setExpired]=useState(()=>restoreSession().expired),[dialog,setDialog]=useState(null),[alertCount,setAlertCount]=useState(0),[authNotice,setAuthNotice]=useState(''),[role,setRole]=useState(()=>restoreSession().session?.role||'patient'),[page,setPage]=useState('dashboard'),[patients,setPatients]=useState([]),[ops,setOps]=useState([]),[conflicts,setConflicts]=useState([]),[online,setOnline]=useState(navigator.onLine),[toast,setToast]=useState(''),[mobile,setMobile]=useState(false);
 async function refresh(){if(import.meta.env.VITE_SEED_DEMO_DATA==='true')await seed();if(live()&&getSession()?.role!=='patient'){try{const remote=await api('/api/patients');if(remote?.length)await db.patients.bulkPut(remote);if(['supervisor','clinician'].includes(getSession()?.role)){const remoteConflicts=await api('/api/conflicts?status=pending');await db.conflicts.where('status').equals('pending').delete();if(remoteConflicts)await db.conflicts.bulkPut(remoteConflicts);}}catch(e){console.warn('Remote refresh unavailable; retaining local data:',e.message)}}setPatients(await db.patients.toArray());setOps(await db.operations.toArray());setConflicts(await db.conflicts.toArray());loadAlerts()}useEffect(()=>{refresh()},[]);useEffect(()=>{let a=()=>setOnline(true),b=()=>setOnline(false);addEventListener('online',a);addEventListener('offline',b);return()=>{removeEventListener('online',a);removeEventListener('offline',b)}},[]);
 function notify(s){setToast(s);setTimeout(()=>setToast(''),3500)}async function registerPatient(n,e,pw){try{await finishLogin(await registerApi(n,e,pw))}catch(err){setAuthNotice(err.message||'Registration failed')}}async function login(r,n,e,pw){try{const res=await loginApi(e,pw);if(res.user.role!==r){setAuthNotice('This account does not have the selected role.');return}await finishLogin(res)}catch(err){setAuthNotice(err.message||'Sign in failed')}}
 async function finishLogin(res){const claim=await claimDevice(res.user.id);if(!claim.ok){setDialog({type:'switch',pending:claim.pending,res});return}openSession(res)}
 function openSession(res){const profile=startSession(res.user,res.access_token);setSession(profile);setRole(profile.role);setExpired(false);setAuthNotice('');setDialog(null);setPage('dashboard');refresh()}
 async function requestLogout(){const{pending}=await beginLogout();if(pending>0){setDialog({type:'logout',pending});return}await doLogout(false)}
 async function doLogout(discard){try{await completeLogout({discard})}catch(e){notify('Sign-out cancelled: '+(e.message==='UNSYNCED_OPERATIONS'?'unsynced changes exist.':e.message));return}setSession(null);setExpired(false);setDialog(null);setPage('dashboard');setPatients([]);setOps([]);setConflicts([]);setAuthNotice('')}
 function signInAgain(){const email=session?.email;endSession();setSession(null);setExpired(false);setAuthNotice('Your session expired. Any unsynced changes are kept on this device. Sign in again'+(email?' as '+email:'')+' to continue.')}
 useEffect(()=>{if(!session)return;const mark=()=>setExpired(true);addEventListener(SESSION_EXPIRED_EVENT,mark);const t=setInterval(()=>{if(!live())setExpired(true)},30000);return()=>{removeEventListener(SESSION_EXPIRED_EVENT,mark);clearInterval(t)}},[session?.id]);
 async function loadAlerts(){const r=getSession()?.role;if(!r||!live())return;try{
  if(r==='worker'){const x=await api('/api/sync/conflicts?status=pending');setAlertCount(x.open_count||0)}
  else if(r==='patient'){const x=await api('/api/me/conflicts');setAlertCount((x.conflicts||[]).filter(c=>!c.my_confirmation).length)}
  else if(r==='clinician'){const[c,e]=await Promise.all([api('/api/conflicts?status=pending'),api('/api/evidence?status=pending_review')]);setAlertCount(c.length+e.length)}
  else if(r==='supervisor'){const c=await api('/api/conflicts?status=pending');setAlertCount(c.length)}
 }catch{/* offline or signed out: keep the last count */}}
 async function sync(){if(!online){notify('Offline: changes remain safely queued on this device.');return}try{const result=await synchronize();if(result.authExpired){setExpired(true);notify('Session expired: your changes stay queued. Sign in again to sync.');return}await refresh();notify(`Sync finished: ${result.uploaded} acknowledged, ${result.conflicts} conflicts, ${result.errors} errors.`)}catch(e){notify(`Sync failed; operations remain queued: ${e.message}`)}}
 useEffect(()=>{if(!session||expired||!live())return;if(role==='patient'){loadAlerts();const t=setInterval(loadAlerts,30000);return()=>clearInterval(t)}return startAutoSync(()=>refresh())},[session?.id,expired]);
 if(!session)return <><Login login={login} register={registerPatient} notice={authNotice}/>{dialog?.type==='switch'&&<UnsyncedDialog kind="switch" pending={dialog.pending} onCancel={()=>setDialog(null)} onDiscard={async()=>{await discardAndClaim(dialog.res.user.id);openSession(dialog.res)}}/>}</>;
 let nav=role==='patient'?[['dashboard','Overview',Home],['my-health','My health',HeartPulse],['confirmations','Confirm information',ShieldCheck],['assistant','SAHAAY AI assistant',Sparkles],['identity','Identity & consent',Fingerprint]]:role==='worker'?[['dashboard','Overview',Home],['patients','Patient directory',Users],['my-conflicts','Sync conflicts',AlertTriangle],['identity','Identity verification',Fingerprint],['sync','Sync centre',RefreshCw]]:role==='clinician'?[['dashboard','Overview',Home],['conflicts','Conflict review',AlertTriangle],['evidence','Evidence review',FileCheck2],['history','Resolution history',ClipboardList]]:[['dashboard','Overview',Home],['conflicts','Conflict review',AlertTriangle],['accounts','Patient accounts',UserRound],['identity','Identity monitoring',Fingerprint],['sync','Sync monitoring',ArrowDownUp],['audit','Audit history',ClipboardList]];
 return <div className="app-shell"><aside className={'sidebar '+(mobile?'mobile-open':'')}><div className="brand"><div className="brand-mark"><HeartPulse/></div><div><b>SAHAAY</b><small>COMMUNITY HEALTH</small></div><button className="mobile-close" onClick={()=>setMobile(false)}><X/></button></div><div className="side-caption">WORKSPACE</div><nav>{nav.map(([id,label,I])=><button key={id} className={'nav-item '+(page===id?'selected':'')} onClick={()=>{setPage(id);setMobile(false)}}><I size={17}/><span>{label}</span>{page===id&&<i className="nav-glow"/>}</button>)}</nav><div className="side-spacer"/><div className="sync-mini"><div className="sync-mini-top"><i className={'live-dot '+(!online?'off':'')}/><b>{online?'Connection available':'Offline mode'}</b></div><p>{ops.filter(x=>x.status==='pending').length} changes waiting to sync</p><button onClick={sync}><RefreshCw size={13}/> Sync now</button></div><button className="nav-item subdued" onClick={()=>notify('Settings will be connected in a later milestone.')}><Settings size={17}/>Settings</button><button className="nav-item subdued" onClick={()=>notify('Support centre coming soon.')}><MessageCircle size={17}/>Support</button><div className="profile-mini"><div className="profile-avatar">{session.name[0]?.toUpperCase()}</div><div className="profile-meta"><b>{session.name}</b><small>{roles[role].label}</small></div><button onClick={requestLogout} title="Sign out"><LogOut size={16}/></button></div></aside>
 <main className="main-area">{expired&&<div className="disclaimer" role="alert" style={{display:'flex',gap:12,alignItems:'center',justifyContent:'space-between',flexWrap:'wrap'}}><span><b>Session expired.</b> You can keep working offline; changes stay queued and nothing syncs until you sign in again.</span><button className="outline-btn" onClick={signInAgain}>Sign in again</button></div>}<header className="topbar"><button className="hamburger" onClick={()=>setMobile(true)}><Menu/></button><div className="crumb">SAHAAY <ChevronRight size={13}/><span>{title(page)}</span></div><div className="top-actions"><div className="connection-pill">{online?<Wifi size={14}/>:<WifiOff size={14}/>}<span>{online?'Online':'Offline'}</span></div><button className="round-action" style={{position:'relative'}} aria-label={alertCount?alertCount+' items need attention':'Notifications'} onClick={()=>{if(alertCount>0)setPage({patient:'confirmations',worker:'my-conflicts',clinician:'conflicts',supervisor:'conflicts'}[role]);else notify('No new notifications.')}}><Bell size={17}/>{alertCount>0&&<span style={{position:'absolute',top:-4,right:-4,minWidth:16,height:16,borderRadius:8,background:'#e0709b',color:'#fff',fontSize:10,display:'grid',placeItems:'center',padding:'0 4px'}}>{alertCount}</span>}</button><button className="round-action" onClick={()=>notify('Preferences coming soon.')}><Settings size={17}/></button><div className="top-profile"><div className="profile-avatar">{session.name[0]?.toUpperCase()}</div><div><b>{session.name}</b><small>{roles[role].label}</small></div><ChevronDown size={14}/></div></div></header>
 {role==='patient'&&<Patient page={page} setPage={setPage} notify={notify} onChanged={loadAlerts}/>}{role==='worker'&&<Worker page={page} setPage={setPage} patients={patients} ops={ops} refresh={refresh} notify={notify} sync={sync} alerts={alertCount}/>}{role==='supervisor'&&<Supervisor page={page} role={role} patients={patients} ops={ops} conflicts={conflicts} notify={notify} refresh={refresh}/>}{role==='clinician'&&<ClinicianWorkspace page={page} role={role} conflicts={conflicts} refresh={refresh} notify={notify} ui={UI}/>}
 <footer className="app-footer"><span>SAHAAY · Offline-first community healthcare</span><span>Demo environment · Synthetic data only</span></footer></main>{dialog?.type==='logout'&&<UnsyncedDialog kind="logout" pending={dialog.pending} canSync={online&&live()} onSync={async()=>{setDialog(null);await sync();requestLogout()}} onCancel={()=>setDialog(null)} onDiscard={()=>doLogout(true)}/>}{toast&&<div className="toast"><CheckCircle2 size={17}/>{toast}<button onClick={()=>setToast('')}><X size={14}/></button></div>}</div>
}
function Login({login,register,notice}){const[r,setR]=useState('patient'),[name,setName]=useState(''),[email,setEmail]=useState(''),[pw,setPw]=useState(''),[show,setShow]=useState(false);return <div className="login-screen"><div className="login-glow one"/><div className="login-glow two"/><div className="login-brand"><div className="brand-mark"><HeartPulse/></div><div><b>SAHAAY</b><small>COMMUNITY HEALTH PLATFORM</small></div></div><div className="login-layout"><section className="login-copy"><Badge tone="purple">CONNECTED CARE, EVEN OFFLINE</Badge><h1>Healthcare that<br/><span>stays with you.</span></h1><p>A trusted space for patients, healthcare workers and clinical teams — built to keep care moving, even when the internet doesn't.</p><div className="login-points"><div><ShieldCheck/><span><b>Privacy-led access</b><small>Role-specific workspaces</small></span></div><div><CloudOff/><span><b>Offline-first by design</b><small>Your work stays available without a signal</small></span></div><div><Sparkles/><span><b>AI-assisted guidance</b><small>Clear, supportive health information</small></span></div></div><div className="login-quote">“Care should never depend on a signal bar.”</div></section><section className="login-card">{notice&&<div className="disclaimer" role="alert" style={{marginBottom:12}}>{notice}</div>}<div className="login-card-head"><div><small>WELCOME TO SAHAAY</small><h2>Sign in to continue</h2><p>Select your access type to open the right workspace.</p></div><div className="secure-icon"><Lock/></div></div><div className="role-select">{Object.entries(roles).map(([id,v])=><button key={id} className={'role-option '+(r===id?'active':'')} onClick={()=>setR(id)}><v.icon size={18}/><span>{v.label}</span>{r===id&&<Check size={15}/>}</button>)}</div><label className="input-label">Full name<input value={name} onChange={e=>setName(e.target.value)} placeholder="Enter your name"/></label><label className="input-label">Email or registered ID<input value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com"/></label><label className="input-label">Password<div className="password-wrap"><input type={show?'text':'password'} value={pw} onChange={e=>setPw(e.target.value)} placeholder="Enter password"/><button type="button" onClick={()=>setShow(!show)}>{show?'Hide':'Show'}</button></div></label><div className="login-options"><label><input type="checkbox"/> Remember this device</label><button onClick={()=>alert('Password recovery will be connected to backend authentication.')}>Forgot password?</button></div><button className="sign-in" onClick={()=>login(r,name,email,pw)}><span>Sign in as {roles[r].label}</span><ArrowRight size={17}/></button>{r==='patient'&&<button className="text-btn" style={{marginTop:10,width:'100%'}} onClick={()=>register(name,email,pw)}>Create patient account</button>}<div className="login-foot"><ShieldCheck size={14}/> Secure backend sign-in · Patient accounts self-register; staff accounts are provisioned by a supervisor</div></section></div><div className="login-bottom"><span>© 2026 SAHAAY Health Systems</span><span>Privacy-first · Consent-led · Accessible care</span></div></div>}
function Head({eyebrow,title,sub,action}){return <div className="page-head"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{sub}</p></div>{action}</div>}
function Metric({label,value,change,I,tone=''}){return <div className="metric-card"><div className={'metric-icon '+tone}><I size={18}/></div><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-foot"><span className="positive">{change}</span></div></div>}
function Panel({title,sub,action,children}){return <section className="panel"><div className="panel-heading"><div><h3>{title}</h3>{sub&&<p>{sub}</p>}</div>{action}</div>{children}</section>}
function Timeline({items}){return <div className="timeline">{items.map(([d,t,s])=><div className="timeline-item" key={t}><div className="timeline-dot"/><div><small>{d}</small><b>{t}</b><p>{s}</p></div></div>)}</div>}
function Patient({page,setPage,notify,onChanged}){
 if(page==='assistant')return <AiAssistant notify={notify} ui={UI}/>;
 if(page==='my-health'||page==='confirmations')return <PatientWorkspace mode={page==='my-health'?'record':'confirmations'} setPage={setPage} notify={notify} onChanged={onChanged} ui={UI}/>;
 if(page==='identity')return <div className="content"><Head eyebrow="ACCOUNT & TRUST" title="Your identity and consent" sub="See what has been verified and manage your permissions."/><div className="info-card"><Fingerprint/><p>Aadhaar use must be voluntary and consent-led. A QR scan alone does not prove credential authenticity or confirm that the presenter is its rightful holder. This demo does not perform Aadhaar verification.</p></div></div>;
 return <PatientWorkspace mode="overview" setPage={setPage} notify={notify} onChanged={onChanged} ui={UI}/>}
function Worker({page,setPage,patients,ops,refresh,notify,sync,alerts}){const[q,setQ]=useState(''),[open,setOpen]=useState(false),[n,setN]=useState(''),[phone,setPhone]=useState(''),[village,setVillage]=useState('');let list=patients.filter(p=>(p.name+p.patient_id+p.village).toLowerCase().includes(q.toLowerCase()));async function add(e){e.preventDefault();let id='SH-'+Math.floor(3000+Math.random()*6000),p={patient_id:id,name:n,phone:phone||'Not added',village:village||'Not specified',condition:'New registration',identity_status:'UNVERIFIED'};await db.patients.add(p);await queueOperation({patient_id:id,base_version:0,operation_type:'create',changes:{name:n,phone:phone||'Not added',village:village||'Not specified',condition:'New registration',identity_status:'UNVERIFIED'}});await db.audit.add({patient_id:id,event_type:'PATIENT_REGISTERED',description:'Registered locally',created_at:new Date().toISOString()});await refresh();setOpen(false);setN('');setPhone('');setVillage('');notify('Patient registered locally and queued for sync.')}
 if(page==='my-conflicts')return <WorkerConflicts notify={notify} onChanged={refresh} ui={UI}/>;
 if(page==='patients')return <div className="content"><Head eyebrow="CARE OPERATIONS" title="Patient directory" sub="Find a patient, update their record or start a new registration." action={<button className="gradient-btn" onClick={()=>setOpen(true)}><Plus size={16}/> Register patient</button>}/><div className="toolbar"><div className="searchbox"><Search size={16}/><input value={q} onChange={e=>setQ(e.target.value)} placeholder="Search name, patient ID or village"/></div><Badge>{patients.length} records</Badge></div><Panel title="Community patient records" sub="Locally available · synthetic demo data"><div className="table-wrap"><table><thead><tr><th>Patient</th><th>Patient ID</th><th>Care note</th><th>Identity</th><th>Last visit</th></tr></thead><tbody>{list.map(p=><tr key={p.patient_id}><td><b>{p.name}</b><small>{p.village}</small></td><td>{p.patient_id}</td><td>{p.condition}</td><td><Badge tone={p.identity_status==='VERIFIED'?'green':'amber'}>{p.identity_status}</Badge></td><td>{p.last_visit||'Just now'}</td></tr>)}</tbody></table></div></Panel>{open&&<Modal title="Register a patient" close={()=>setOpen(false)}><form className="modal-form" onSubmit={add}><label>Patient name<input required value={n} onChange={e=>setN(e.target.value)} placeholder="Full name"/></label><label>Phone number<input value={phone} onChange={e=>setPhone(e.target.value)} placeholder="Contact number"/></label><label>Village / locality<input value={village} onChange={e=>setVillage(e.target.value)} placeholder="Village name"/></label><div className="modal-note">New patient identity status starts as UNVERIFIED. Verify identity separately with consent.</div><button className="gradient-btn">Save patient locally</button></form></Modal>}</div>;
 if(page==='identity')return <IdentityWorker patients={patients} refresh={refresh} notify={notify}/>;
 if(page==='sync')return <div className="content"><Head eyebrow="OFFLINE-FIRST ENGINE" title="Synchronization centre" sub="Review locally queued changes and resume when connectivity returns." action={<button className="gradient-btn" onClick={sync}><RefreshCw size={15}/> Sync now</button>}/><div className="metric-grid"><Metric label="Pending operations" value={ops.filter(o=>o.status==='pending').length} change="Stored locally" I={ArrowDownUp}/><Metric label="Network" value={navigator.onLine?'Available':'Offline'} change="Backend health not checked" I={Cloud} tone="blue"/><Metric label="Retry strategy" value="Queued" change="Backoff integration pending" I={RefreshCw} tone="violet"/><Metric label="Data safety" value="IndexedDB" change="Prototype storage" I={ShieldCheck} tone="green"/></div><Panel title="Operation queue" sub="Server acknowledgement is required before local deletion"><div className="table-wrap"><table><thead><tr><th>Operation</th><th>Patient</th><th>Created</th><th>Status</th></tr></thead><tbody>{ops.map(o=><tr key={o.operation_id}><td>{o.operation_id.slice(0,14)}…</td><td>{o.patient_id}</td><td>{new Date(o.created_at).toLocaleString()}</td><td><Badge tone={o.status==='pending'?'amber':'green'}>{o.status}</Badge></td></tr>)}</tbody></table></div></Panel><div className="disclaimer">Sync currently simulates acknowledgements locally. It is not connected to FastAPI.</div></div>;
 return <div className="content"><Head eyebrow="HEALTHCARE WORKER WORKSPACE" title="Good evening. Here's your care overview." sub="Your field activity, patient updates and offline sync status." action={<button className="gradient-btn" onClick={()=>setPage('patients')}><Plus size={16}/> New patient</button>}/><div className="metric-grid"><Metric label="Total patients" value={patients.length} change="Local patient list" I={Users}/><Metric label="Pending sync" value={ops.filter(o=>o.status==='pending').length} change="Waiting for connection" I={CloudOff} tone="violet"/><Metric label="Open conflicts" value={alerts} change="From your synced changes" I={AlertTriangle} tone="blue"/><Metric label="Synced changes" value={ops.filter(o=>o.status==='acknowledged'||o.status==='acknowledged_with_conflict').length} change="Acknowledged by the server" I={CheckCircle2} tone="green"/></div><div className="grid-two"><Panel title="Recent patient activity" sub="Latest updates" action={<button className="text-btn" onClick={()=>setPage('patients')}>All patients <ArrowUpRight size={14}/></button>}><div className="activity-list">{patients.slice(0,4).map(p=><div className="activity-row" key={p.patient_id}><div className="person-dot">{p.name[0]}</div><div className="activity-main"><b>{p.name}</b><small>{p.patient_id} · {p.condition}</small></div><Badge tone={p.identity_status==='VERIFIED'?'green':'amber'}>{p.identity_status}</Badge></div>)}</div></Panel><Panel title="Quick actions" sub="Common field tasks"><div className="action-list"><button onClick={()=>setPage('patients')}><Users/><span><b>Open patient directory</b><small>Find and update records</small></span><ChevronRight/></button><button onClick={()=>setPage('identity')}><Fingerprint/><span><b>Start identity verification</b><small>Consent-led credential workflow</small></span><ChevronRight/></button><button onClick={sync}><RefreshCw/><span><b>Sync local changes</b><small>Resume queued operations</small></span><ChevronRight/></button></div></Panel></div></div>}
function Supervisor({page,role,patients,ops,conflicts,notify,refresh}){const[acct,setAcct]=useState(null);useEffect(()=>{if(role==='supervisor'&&(page==='dashboard'))api('/api/admin/patient-accounts').then(setAcct).catch(()=>setAcct(null))},[page,role]);
 if(page==='accounts')return <SupervisorAccounts patients={patients} notify={notify} ui={UI}/>;
 if(page==='conflicts')return <ConflictReview role={role} conflicts={conflicts} refresh={refresh} notify={notify} Head={Head} Badge={Badge} Modal={Modal}/>;
 if(page==='identity')return <div className="content"><Head eyebrow="IDENTITY GOVERNANCE" title="Identity verification monitoring" sub="Review verification states without exposing raw Aadhaar credentials."/><Panel title="Patient identity states" sub="Synthetic demo records"><div className="table-wrap"><table><thead><tr><th>Patient</th><th>ID</th><th>Status</th><th>Action</th></tr></thead><tbody>{patients.map(p=><tr key={p.patient_id}><td>{p.name}</td><td>{p.patient_id}</td><td><Badge tone={p.identity_status==='VERIFIED'?'green':'amber'}>{p.identity_status}</Badge></td><td><button className="text-btn" onClick={()=>notify('Audit evidence viewer will connect to backend.')}>View audit</button></td></tr>)}</tbody></table></div></Panel></div>;
 if(page==='sync')return <div className="content"><Head eyebrow="SYSTEM OPERATIONS" title="Synchronization monitoring" sub="Monitor queue state and device activity."/><div className="metric-grid"><Metric label="Patient records" value={patients.length} change="Demo data" I={Users}/><Metric label="Queued operations" value={ops.filter(o=>o.status==='pending').length} change="Local browser" I={RefreshCw}/><Metric label="Conflicts" value={conflicts.length} change="Synthetic examples" I={AlertTriangle}/><Metric label="Sync state" value="Prototype" change="Backend pending" I={Cloud}/></div></div>;
 if(page==='audit')return <div className="content"><Head eyebrow="ACCOUNTABILITY" title="Audit history" sub="A traceable record of actions is essential for safe healthcare data handling."/><div className="info-card"><ClipboardList/><p>Full audit history will be loaded from the backend audit API. This screen is a placeholder for the integrated audit timeline.</p></div></div>;
 return <div className="content"><Head eyebrow="PROFESSIONAL SUPERVISOR" title="Clinical oversight at a glance." sub="Monitor data integrity, identity checks and field operations."/><div className="metric-grid"><Metric label="Patients" value={patients.length} change="Local registry" I={Users}/><Metric label="Open conflicts" value={conflicts.length} change="Need policy routing" I={AlertTriangle} tone="violet"/><Metric label="Pending sync" value={ops.filter(o=>o.status==='pending').length} change="Device queues" I={CloudOff} tone="blue"/><Metric label="Unlinked patient accounts" value={acct?acct.filter(a=>!a.linked_patient_id).length:'—'} change="Waiting for an identity check" I={UserRound} tone="green"/></div><div className="grid-two"><Panel title="Needs attention" sub="Items requiring review"><div className="attention-list">{conflicts.filter(c=>c.status==='pending').slice(0,4).map(c=><div key={c.conflict_id}><AlertTriangle/><span><b>{c.field} · {c.patient_id}</b><small>{c.category}</small></span><Badge tone={c.category==='CRITICAL_CLINICAL'?'amber':'purple'}>{c.category==='CRITICAL_CLINICAL'?'Clinician':'Review'}</Badge></div>)}{acct&&acct.filter(a=>!a.linked_patient_id).length>0&&<div><Fingerprint/><span><b>{acct.filter(a=>!a.linked_patient_id).length} patient account(s) not linked</b><small>Link after checking identity</small></span><Badge tone="purple">Review</Badge></div>}{conflicts.filter(c=>c.status==='pending').length===0&&!(acct&&acct.filter(a=>!a.linked_patient_id).length>0)&&<p style={{fontSize:12}}>Nothing needs attention.</p>}</div></Panel><Panel title="Governance principles" sub="Safe by design"><div className="principle"><ShieldCheck/><div><b>Least-privilege access</b><small>Permissions enforced by backend, not UI alone.</small></div></div><div className="principle"><FileCheck2/><div><b>Auditable decisions</b><small>Preserve original submissions and evidence.</small></div></div><div className="principle"><Lock/><div><b>Data minimization</b><small>Never expose raw Aadhaar payloads.</small></div></div></Panel></div></div>}

function IdentityWorker({patients,refresh,notify}){
 const [patientId,setPatientId]=useState(patients[0]?.patient_id||'');
 const [consent,setConsent]=useState(false);
 const [method,setMethod]=useState('qr');
 const [cameraOn,setCameraOn]=useState(false);
 const [cameraError,setCameraError]=useState('');
 const [scanState,setScanState]=useState('idle');
 const [qrValue,setQrValue]=useState('');
 const [xmlFile,setXmlFile]=useState(null);
 const [xmlState,setXmlState]=useState('');
 const [attempts,setAttempts]=useState([]);
 const [showConsent,setShowConsent]=useState(false);
 const [busy,setBusy]=useState(false);
 const videoRef=useRef(null);
 const streamRef=useRef(null);
 const fileRef=useRef(null);
 const patient=patients.find(p=>p.patient_id===patientId)||patients[0]||null;

 useEffect(()=>{if(!patientId&&patients[0]?.patient_id)setPatientId(patients[0].patient_id)},[patients,patientId]);
 useEffect(()=>{
   if(cameraOn&&videoRef.current&&streamRef.current){
     videoRef.current.srcObject=streamRef.current;
     videoRef.current.play().catch(()=>{});
   }
 },[cameraOn]);
 useEffect(()=>()=>stopCamera(),[]);
 function stopCamera(){
   if(streamRef.current){streamRef.current.getTracks().forEach(t=>t.stop());streamRef.current=null;}
   if(videoRef.current)videoRef.current.srcObject=null;
   setCameraOn(false);
 }
 async function startCamera(){
   setCameraError('');
   if(!navigator.mediaDevices?.getUserMedia){setCameraError('Camera access is unavailable in this browser. Use localhost or HTTPS, or try the synthetic demo scan.');return;}
   try{
     const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
     streamRef.current=stream;
     if(videoRef.current){videoRef.current.srcObject=stream;await videoRef.current.play();}
     setCameraOn(true);
   }catch(e){setCameraError(e?.name==='NotAllowedError'?'Camera permission was denied. Allow camera access in your browser settings.':'Could not open the camera. Check that another app is not using it.');}
 }
 async function scanCamera(){
   if(!videoRef.current)return;
   if(!('BarcodeDetector' in window)){setCameraError('This browser does not provide the BarcodeDetector API. Use the synthetic demo scan for the UI walkthrough.');return;}
   try{
     const detector=new window.BarcodeDetector({formats:['qr_code']});
     const codes=await detector.detect(videoRef.current);
     if(codes.length){
       setQrValue(codes[0].rawValue||'QR code detected');
       setScanState('decoded');
       stopCamera();
       notify('QR decoded locally. Credential authenticity has NOT been verified.');
     }else setCameraError('No QR code detected yet. Adjust the camera and try again.');
   }catch{setCameraError('Could not read a QR code from this frame. Try again.');}
 }
 function demoScan(){
   setQrValue('SYNTHETIC-DEMO-CREDENTIAL-ONLY');
   setScanState('demo');
   setCameraError('');
   stopCamera();
   notify('Synthetic demo QR loaded. This is not an Aadhaar credential.');
 }
 async function chooseXml(file){
   setXmlFile(null);setXmlState('');
   if(!file)return;
   if(!/\.xml$/i.test(file.name)){setXmlState('Please choose an .xml file.');return;}
   if(file.size>5*1024*1024){setXmlState('File is larger than 5 MB. Choose a smaller test file.');return;}
   try{
     const text=await file.text();
     const doc=new DOMParser().parseFromString(text,'application/xml');
     if(doc.querySelector('parsererror')){setXmlState('The selected file is not well-formed XML.');return;}
     const root=doc.documentElement?.nodeName||'XML document';
     setXmlFile({name:file.name,size:file.size,root});
     setXmlState('XML syntax parsed locally. Digital signature and certificate validity have NOT been checked.');
   }catch{setXmlState('The file could not be read.');}
 }
 async function recordAttempt(){
   if(!patient){notify('Select a patient first.');return;}
   if(!consent){setShowConsent(true);return;}
   if(method==='qr'&&!['decoded','demo'].includes(scanState)){notify('Scan a QR code or load the synthetic demo QR first.');return;}
   if(method==='xml'&&!xmlFile){notify('Select a well-formed XML file first.');return;}
   setBusy(true);
   const attempt={
     id:'IDV-'+Date.now().toString().slice(-7),
     patient_id:patient.patient_id,
     method:method==='qr'?'AADHAAR_SECURE_QR':'OFFLINE_EKYC_XML',
     status:'PENDING_AUTHENTIC_VERIFICATION',
     note:method==='qr'?(scanState==='demo'?'Synthetic QR fixture selected; no credential validation.':'QR decoded; signature verification not performed.'):'XML syntax parsed; signature verification not performed.',
     created_at:new Date().toISOString()
   };
   try{
     await db.audit.add({patient_id:patient.patient_id,event_type:'IDENTITY_ATTEMPT_RECORDED',description:`${attempt.method}: ${attempt.note}`,created_at:attempt.created_at});
     await db.patients.update(patient.patient_id,{identity_status:'PENDING'});
     if(getToken())await api('/api/identity/'+encodeURIComponent(patient.patient_id)+'/attempt',{method:'POST',body:JSON.stringify({consent:true,method:attempt.method})});
     setAttempts(a=>[attempt,...a]);
     await refresh();
     notify('Attempt saved locally as pending. No identity has been verified.');
   }catch(e){notify('Could not save the local demo attempt. Check the browser storage.');}
   setBusy(false);
 }
 const identityCss=`
 .idv-wrap{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(245px,.75fr);gap:15px;align-items:start}
 .idv-main{display:grid;gap:13px;min-width:0}
 .idv-step{background:linear-gradient(145deg,#211d25,#17151b);border:1px solid #ffffff14;border-radius:14px;padding:17px}
 .idv-step-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
 .idv-step-no{width:27px;height:27px;border-radius:9px;background:#b783bd22;color:#e0b0df;display:grid;place-items:center;font-size:10px;font-weight:700}
 .idv-step-head h3{font:600 12px Manrope;margin:0}
 .idv-step-head p{font-size:9px;color:#8e8492;margin:4px 0 0}
 .idv-select{width:100%;background:#141218;color:#eee;border:1px solid #ffffff1b;border-radius:9px;padding:11px;font-size:10px;outline:none}
 .idv-patient{display:flex;align-items:center;gap:10px;margin-top:10px;padding:10px;background:#ffffff06;border-radius:10px}
 .idv-patient-avatar{height:34px;width:34px;border-radius:10px;background:#49364e;color:#e8b9e1;display:grid;place-items:center;font-weight:700}
 .idv-patient-meta{flex:1;min-width:0}.idv-patient-meta b,.idv-patient-meta small{display:block}.idv-patient-meta b{font-size:10px}.idv-patient-meta small{font-size:8px;color:#8d8491;margin-top:3px}
 .idv-consent{display:flex;gap:10px;align-items:flex-start;padding:12px;border:1px solid #ffffff12;background:#ffffff05;border-radius:10px}
 .idv-consent input{accent-color:#bd8dbd;margin-top:2px}.idv-consent span{font-size:9px;color:#bcb1c0;line-height:1.6}
 .idv-methods{display:grid;grid-template-columns:1fr 1fr;gap:9px}
 .idv-method{border:1px solid #ffffff17;background:#151319;border-radius:11px;padding:13px;text-align:left;display:flex;gap:10px;align-items:flex-start;color:#a99eac}
 .idv-method.active{border-color:#c18bc455;background:#8d5c911c;color:#f1d8ef}
 .idv-method svg{color:#d5a0d4;flex-shrink:0}.idv-method b,.idv-method small{display:block}.idv-method b{font-size:10px}.idv-method small{font-size:8px;color:#918796;line-height:1.45;margin-top:4px}
 .idv-camera{background:#100e13;border:1px dashed #ffffff24;border-radius:11px;padding:12px;margin-top:12px}
 .idv-video{width:100%;max-height:260px;object-fit:cover;border-radius:8px;background:#050505}
 .idv-camera-placeholder{min-height:145px;display:grid;place-items:center;text-align:center;color:#887d8e;gap:8px;font-size:9px}
 .idv-camera-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
 .idv-small-btn{display:inline-flex;align-items:center;gap:7px;border:1px solid #ffffff1b;background:#ffffff08;color:#d6c9d9;border-radius:8px;padding:8px 10px;font-size:9px}
 .idv-small-btn.primary{background:linear-gradient(100deg,#c99bc9,#957ab6);color:#211726;border:0;font-weight:700}
 .idv-hint{font-size:9px;color:#d5b47f;line-height:1.5;margin-top:9px}
 .idv-status{display:flex;align-items:center;gap:8px;padding:10px 11px;border-radius:9px;background:#d6a04c12;border:1px solid #d6a04c28;color:#e8c184;font-size:9px;line-height:1.5;margin-top:11px}
 .idv-file{border:1px dashed #ffffff25;border-radius:10px;padding:16px;text-align:center;background:#ffffff04}
 .idv-file input{display:none}.idv-file b{display:block;font-size:10px;margin:7px 0 4px}.idv-file small{font-size:8px;color:#8e8492}
 .idv-side{display:grid;gap:12px}
 .idv-side-card{background:linear-gradient(145deg,#29212f,#19161d);border:1px solid #ffffff14;border-radius:14px;padding:16px}
 .idv-side-card h3{font:600 12px Manrope;margin:0 0 10px}
 .idv-check{display:flex;align-items:flex-start;gap:8px;padding:8px 0;border-top:1px solid #ffffff0d}
 .idv-check svg{color:#c79bc9;flex-shrink:0;margin-top:1px}.idv-check b,.idv-check small{display:block}.idv-check b{font-size:9px}.idv-check small{font-size:8px;color:#8f8592;line-height:1.45;margin-top:3px}
 .idv-attempt{display:flex;justify-content:space-between;gap:8px;padding:10px 0;border-top:1px solid #ffffff10}.idv-attempt b,.idv-attempt small{display:block}.idv-attempt b{font-size:9px}.idv-attempt small{font-size:8px;color:#8e8492;margin-top:4px}
 .idv-modal-copy{font-size:10px;color:#bcb1c0;line-height:1.65}.idv-modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:15px}
 @media(max-width:850px){.idv-wrap{grid-template-columns:1fr}.idv-side{grid-template-columns:1fr 1fr}}
 @media(max-width:540px){.idv-methods,.idv-side{grid-template-columns:1fr}}
 `;
 return <div className="content">
  <style>{identityCss}</style>
  <Head eyebrow="IDENTITY & TRUST" title="Patient identity verification" sub="Consent-led Aadhaar credential workflow. Scanning is not the same as verification." action={<Badge tone="amber">Demo workflow</Badge>}/>
  <div className="info-card"><Fingerprint/><p>Use synthetic test data in this prototype. QR decoding and XML syntax checks are local previews only; neither proves Aadhaar authenticity or confirms the credential holder. Real verification requires an authorized, compliant verification service.</p></div>
  <div className="idv-wrap" style={{marginTop:14}}>
   <div className="idv-main">
    <section className="idv-step">
     <div className="idv-step-head"><div className="idv-step-no">01</div><div><h3>Select patient</h3><p>Choose the patient whose identity workflow you are recording.</p></div></div>
     {patients.length?<select className="idv-select" value={patientId} onChange={e=>{setPatientId(e.target.value);setConsent(false);setScanState('idle');setQrValue('');setXmlFile(null);setXmlState('')}}>{patients.map(p=><option key={p.patient_id} value={p.patient_id}>{p.name} · {p.patient_id}</option>)}</select>:<div className="idv-status">No local patients found. Register a patient in the Patient Directory first.</div>}
     {patient&&<div className="idv-patient"><div className="idv-patient-avatar">{patient.name?.[0]?.toUpperCase()||'P'}</div><div className="idv-patient-meta"><b>{patient.name}</b><small>{patient.patient_id} · {patient.village||'Local record'}</small></div><Badge tone={patient.identity_status==='VERIFIED'?'green':'amber'}>{patient.identity_status||'UNVERIFIED'}</Badge></div>}
    </section>
    <section className="idv-step">
     <div className="idv-step-head"><div className="idv-step-no">02</div><div><h3>Consent and disclosure</h3><p>Explain why identity information is being checked and how it will be used.</p></div></div>
     <label className="idv-consent"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/><span>I confirm that the patient has been informed about the purpose, information used, intended use and available alternative, and has voluntarily consented to this verification workflow.</span></label>
     <div style={{marginTop:10}}><button className="text-btn" onClick={()=>setShowConsent(true)}>View consent information <ArrowUpRight size={13}/></button></div>
    </section>
    <section className="idv-step">
     <div className="idv-step-head"><div className="idv-step-no">03</div><div><h3>Choose verification method</h3><p>Select a credential type to preview the local workflow.</p></div></div>
     <div className="idv-methods">
      <button className={'idv-method '+(method==='qr'?'active':'')} onClick={()=>{setMethod('qr');setXmlFile(null);setXmlState('')}}><ScanLine size={20}/><span><b>Aadhaar Secure QR</b><small>Use device camera to decode a QR code.</small></span></button>
      <button className={'idv-method '+(method==='xml'?'active':'')} onClick={()=>{setMethod('xml');stopCamera();setScanState('idle')}}><FileUp size={20}/><span><b>Offline e-KYC XML</b><small>Select an XML file and check basic syntax.</small></span></button>
     </div>
     {method==='qr'&&<div className="idv-camera">
       {cameraOn?<video ref={videoRef} className="idv-video" playsInline muted/>:<div className="idv-camera-placeholder"><Camera size={30}/><span>Camera preview appears here after permission is granted.</span></div>}
       <div className="idv-camera-actions">
        {!cameraOn?<button className="idv-small-btn primary" onClick={startCamera}><Camera size={14}/> Start camera</button>:<><button className="idv-small-btn primary" onClick={scanCamera}><ScanLine size={14}/> Scan QR in frame</button><button className="idv-small-btn" onClick={stopCamera}>Stop camera</button></>}
        <button className="idv-small-btn" onClick={demoScan}><Sparkles size={13}/> Use synthetic demo QR</button>
       </div>
       {cameraError&&<div className="idv-hint">{cameraError}</div>}
       {scanState!=='idle'&&<div className="idv-status"><CheckCircle2 size={15}/><span>{scanState==='demo'?'Synthetic demo QR selected.':'QR data decoded in browser.'} Credential signature, certificate chain and holder identity remain unchecked.</span></div>}
       {qrValue&&<div style={{marginTop:9}}><small style={{color:'#8e8492',fontSize:8}}>LOCAL DECODE PREVIEW</small><div style={{fontSize:9,color:'#cfc3d2',background:'#ffffff08',padding:9,borderRadius:7,marginTop:5,overflowWrap:'anywhere'}}>{qrValue}</div></div>}
      </div>}
     {method==='xml'&&<div className="idv-camera">
       <label className="idv-file"><input ref={fileRef} type="file" accept=".xml,application/xml,text/xml" onChange={e=>chooseXml(e.target.files?.[0])}/><Upload size={24} color="#d6a3d4"/><b>{xmlFile?xmlFile.name:'Choose Offline e-KYC XML file'}</b><small>{xmlFile?`${(xmlFile.size/1024).toFixed(1)} KB · Root element: ${xmlFile.root}`:'XML only · Maximum 5 MB · File is inspected locally'}</small><span className="idv-small-btn" style={{marginTop:10}}>Browse files</span></label>
       {xmlState&&<div className="idv-status"><Info size={15}/><span>{xmlState}</span></div>}
      </div>}
    </section>
    <section className="idv-step">
     <div className="idv-step-head"><div className="idv-step-no">04</div><div><h3>Record verification attempt</h3><p>Save a local audit entry. The result remains pending until a trusted verifier responds.</p></div></div>
     <button className="gradient-btn" disabled={!patient||busy} onClick={recordAttempt}>{busy?'Saving…':'Save attempt as pending'} <ArrowRight size={15}/></button>
     <div className="idv-status"><ShieldAlert size={15}/><span>This action will not mark the patient as verified. It only records that a local verification attempt was made.</span></div>
    </section>
   </div>
   <aside className="idv-side">
    <section className="idv-side-card"><h3>What this prototype checks</h3>
     <div className="idv-check"><CheckCircle2 size={15}/><span><b>File / QR input</b><small>Can read a QR payload in supported browsers or parse well-formed XML.</small></span></div>
     <div className="idv-check"><Clock3 size={15}/><span><b>Signature validation</b><small>Not implemented. Requires official specifications and trusted verification material.</small></span></div>
     <div className="idv-check"><Clock3 size={15}/><span><b>Holder match</b><small>Not implemented. Credential validity alone does not prove presenter identity.</small></span></div>
     <div className="idv-check"><Clock3 size={15}/><span><b>Final identity decision</b><small>Must come from the authorized verification process.</small></span></div>
    </section>
    <section className="idv-side-card"><h3>Recent local attempts</h3>
     {attempts.length?attempts.map(a=><div className="idv-attempt" key={a.id}><span><b>{a.id}</b><small>{a.patient_id} · {a.method==='AADHAAR_SECURE_QR'?'Secure QR':'Offline XML'}<br/>{new Date(a.created_at).toLocaleString()}</small></span><Badge tone="amber">Pending</Badge></div>):<div style={{fontSize:9,color:'#8e8492',lineHeight:1.6}}>No attempts in this page session yet. Saved attempts are also written to the local audit store.</div>}
    </section>
    <section className="idv-side-card"><h3>Privacy reminders</h3>
     <div className="idv-check"><Lock size={15}/><span><b>Data minimization</b><small>Do not store raw Aadhaar numbers, QR payloads, XML files or share phrases in demo records.</small></span></div>
     <div className="idv-check"><ShieldCheck size={15}/><span><b>Alternative route</b><small>Offer a non-Aadhaar identity route. Healthcare access must not depend solely on Aadhaar.</small></span></div>
    </section>
   </aside>
  </div>
  {showConsent&&<Modal title="Consent information" close={()=>setShowConsent(false)}><div className="idv-modal-copy">Before continuing, explain to the patient: why identity verification is requested; what credential information may be processed; who will access the result; how long relevant records are retained; and what alternative verification method is available. Obtain voluntary, informed consent in the manner required for the applicable authorized process. Do not photograph or retain Aadhaar credentials in this demo.</div><div className="idv-modal-actions"><button className="outline-btn" onClick={()=>setShowConsent(false)}>Close</button><button className="gradient-btn" onClick={()=>{setConsent(true);setShowConsent(false)}}>Confirm consent recorded</button></div></Modal>}
 </div>;
}

function UnsyncedDialog({kind,pending,canSync,onSync,onCancel,onDiscard}){const[ok,setOk]=useState(false);const sw=kind==='switch';return <Modal title={sw?"Another user's changes are on this device":'Sign out with unsynced changes?'} close={onCancel}><div className="modal-form"><div className="modal-note"><b>{pending} change{pending===1?'':'s'}</b> {sw?'made by the previous user have not been synced to the server and exist only on this device.':'have not been synced to the server and exist only on this device.'} Clearing local data will <b>permanently remove</b> {pending===1?'it':'them'}.</div>{sw&&<p style={{fontSize:12}}>To keep those changes, cancel and ask the previous user to sign in and sync first.</p>}<label style={{display:'flex',gap:8,alignItems:'flex-start',fontSize:12}}><input type="checkbox" checked={ok} onChange={e=>setOk(e.target.checked)}/> I understand these {pending} unsynced change{pending===1?'':'s'} will be lost.</label><div style={{display:'flex',gap:8,justifyContent:'flex-end',flexWrap:'wrap'}}><button className="outline-btn" onClick={onCancel}>Cancel</button>{!sw&&canSync&&<button className="outline-btn" onClick={onSync}>Sync now</button>}<button className="gradient-btn" disabled={!ok} onClick={onDiscard}>{sw?'Discard and continue':'Discard changes and sign out'}</button></div></div></Modal>}
function Modal({title,close,children}){return <div className="modal-backdrop"><div className="modal"><div className="modal-title"><h2>{title}</h2><button onClick={close}><X/></button></div>{children}</div></div>}
const UI={Head,Metric,Panel,Timeline,Badge,Modal};
