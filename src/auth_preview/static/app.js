/* No role or organization from this UI is trusted by the server. */
(()=>{
'use strict';
const $=s=>document.querySelector(s),main=$('#main');
let state={},organizations=[],signupInstitutions=[];
const integrated=()=>Boolean(state.integrated||document.body?.dataset.integrated==='true');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const orgName=id=>organizations.find(o=>o.id===id)?.name||'Institution request';
const isAdmin=()=>['ORG_ADMIN','PLATFORM_ADMIN','PLATFORM_OWNER'].includes(state.user?.role);
const isPlatform=()=>['PLATFORM_ADMIN','PLATFORM_OWNER'].includes(state.user?.role);
const isOwner=()=>state.user?.role==='PLATFORM_OWNER';
function notice(message,error=false){const box=$('#notice');box.hidden=false;box.className='notice'+(error?' error':'');box.textContent=message;box.setAttribute?.('role',error?'alert':'status');if(error)box.focus?.();}
async function api(path,body){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':state.csrf_token||''},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json().catch(()=>({detail:'Unexpected server response.'}));
 if(!response.ok){const errors=data.errors?.map(e=>`${e.field}: ${e.message}`).join(' ');const error=new Error(`HTTP ${response.status} · ${data.detail||'Request failed'}${errors?' '+errors:''}`);error.status=response.status;throw error;}
 return data;
}
async function refresh(){state=await api('/api/session');organizations=(await api('/api/organizations')).organizations;signupInstitutions=(await api('/api/signup-institutions')).organizations;window.leafAuth.state=state;rendering++;chrome();window.dispatchEvent(new Event('auth-ready'));}
function chrome(){
 const u=state.user,org=state.organization;
 $('#identity').textContent=u?(u.status==='ACTIVE'&&u.role==='MEMBER'?(org?.name||''):`${u.display_name||u.username} · ${u.role.replaceAll('_',' ')} · ${org?.name||u.institution_request||'No institution'}${u.status!=='ACTIVE'?' · '+u.status:''}`):'Private research, shared within your verified institution.';
 $('#profile-link').hidden=!u;
 let links=u?[]:[['Sign in','/signin'],['Create account','/signup']];
 if(u&&u.status!=='ACTIVE')links=[['Account status','/pending']];
 if(u?.status==='ACTIVE'&&integrated())links.push(['Analyze','/analyze'],['My university','/institution']);
 if(u?.status==='ACTIVE'&&isAdmin())links.push(...(integrated()?[]:[['Analyze','/analyze']]),['Administration','/admin'],['Users','/users']);
 if(u?.status==='ACTIVE'&&isPlatform())links.push(['Organizations','/organizations']);
 if(integrated())links.push(['How to use','/help']);
 $('#navigation').hidden=links.length===0;
 $('#navigation').innerHTML=links.map(([text,path])=>`<a href="${path}"${path==='/help'?' target="_blank" rel="noopener" title="Open the user guide in a new tab"':''}${location.pathname===path?' aria-current="page"':''}>${text}</a>`).join('');
}
function go(path){if((integrated()&&path==='/analyze')||(document.body?.dataset.firebase==='true'&&['/','/signin','/signup','/profile','/pending'].includes(path))){window.location.assign(path);return;}history.pushState({},'',path);return render().then(()=>main.focus?.());}
const pageRoutes=new Set(['/','/signin','/signup','/pending','/institution','/analyze','/profile','/admin','/users','/organizations']);
document.addEventListener('click',e=>{const a=e.target.closest('a[href^="/"]');if(a&&!a.hasAttribute('download')&&!a.getAttribute('target')&&pageRoutes.has(a.getAttribute('href'))&&!e.ctrlKey&&!e.metaKey){e.preventDefault();go(a.getAttribute('href'));}});
window.addEventListener('popstate',render);
function form(id,handler){$(id).addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{await handler(new FormData(e.target));}catch(err){notice(err.message,true);}finally{button.disabled=false;}});}
function options(selected='',placeholder=false){return `${placeholder?'<option value="">Select institution</option>':''}${organizations.filter(o=>o.enabled).map(o=>`<option value="${esc(o.id)}"${o.id===selected?' selected':''}>${esc(o.name)}</option>`).join('')}`;}
function heading(kicker,title,lead){if(typeof document!=='undefined')document.title=title.replace(/<[^>]*>/g,'')+' · Hydroponic Leaf Research';return `<div class="page-heading"><h1>${title}</h1><p class="lead">${lead}</p></div>`;}
function signIn(){
 let recoveryEmail='';
 main.innerHTML=`<div class="split"><section class="intro">${heading('Institution access','Sign in to Leaf Research','A shared place for your institution’s plants, reviewed measurements, and growth history.')}<div class="card space"><h3>Access is reviewed by a person</h3><p class="muted">Create an account, request your institution, and wait for administrator approval.</p><a href="/signup">Create an account →</a></div></section><section class="card form-card"><h2>Sign in</h2><form id="signin-form"><label for="username">Username</label><input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required maxlength="40"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="128"><button type="submit">Sign in</button></form><p class="help">Pending accounts can sign in to see their access status.</p><details><summary>Forgot your password?</summary><p class="help">Enter your verified email address. We will email a code automatically.</p><form id="reset-request-form"><label for="reset-email">Email</label><input id="reset-email" name="email" type="email" autocomplete="email" maxlength="254" required><button type="submit">Email me a reset code</button></form><form id="reset-complete-form" hidden><p id="reset-destination" class="help"></p><label for="reset-code">Email code</label><input id="reset-code" name="code" inputmode="numeric" autocomplete="one-time-code" minlength="8" maxlength="8" required><label for="reset-password">New password</label><input id="reset-password" name="new_password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required><button type="submit">Reset password</button><button id="reset-back" type="button" class="secondary">Use a different email</button></form></details></section></div>`;
 form('#signin-form',async f=>{await api('/api/login',Object.fromEntries(f));await refresh();go(state.user.status==='ACTIVE'?'/analyze':'/pending');notice('Signed in successfully.');});
 form('#reset-request-form',async f=>{const data=Object.fromEntries(f);const r=await api('/api/recovery/request',data);recoveryEmail=data.email;$('#reset-request-form').hidden=true;$('#reset-complete-form').hidden=false;$('#reset-destination').textContent='Enter the code sent to '+recoveryEmail;notice(r.message);window.dispatchEvent(new Event('auth-ready'));});
 $('#reset-back').onclick=()=>{$('#reset-complete-form').hidden=true;$('#reset-request-form').hidden=false;};
 form('#reset-complete-form',async f=>{const r=await api('/api/recovery/complete',{...Object.fromEntries(f),email:recoveryEmail});await refresh();if(state.user)go(state.user.status==='ACTIVE'?'/analyze':'/pending');notice(state.user?'Password updated. You are signed in.':r.message+' Your account is '+r.account_status+'.');});
}
function signup(){
 main.innerHTML=`<div class="split"><section class="intro">${heading('Join the research','One account.<br>Your institution’s work.','Request access to your institution’s shared workspace. Registration is currently open to Bahçeşehir University. Administrator approval is required.')}<div class="card space"><h3>Account requirements</h3><p class="muted">Choose a username and a secure passphrase. Your email is used to verify ownership and recover your password. No GPS or precise location is collected.</p><p class="help">Institution means your university affiliation.</p></div></section><section class="card form-card"><h2>Create an account</h2><form id="signup-form"><label for="username">Username</label><input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required minlength="3" maxlength="40" pattern="[A-Za-z0-9][A-Za-z0-9_.\-]+"><p class="help">3–40 letters, numbers, dots, dashes, or underscores.</p><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" autocapitalize="none" spellcheck="false" maxlength="254" required><p class="help">Verify your address with the code we email you. Password recovery is automatic.</p><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required><p class="help">15–128 characters. A long passphrase works well.</p><label for="confirm-password">Confirm password</label><input id="confirm-password" name="confirm_password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required><label for="institution">University / Institution</label><select id="institution" name="requested_org_id" required>${signupInstitutions.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select><button type="submit">Create account & request access</button></form><p class="help">Already have an account? <a href="/signin">Sign in</a></p></section></div>`;
 form('#signup-form',async f=>{const data=Object.fromEntries(f);const r=await api('/api/signup',data);await refresh();go('/pending');notice(r.message);});
}
function emailSection(){
 const u=state.user,pending=u.pending_email,delivery=state.email_delivery;
 return `<section class="card form-card space"><h2>Recovery email</h2><p>${esc(u.email||'No email added')} <span class="pill ${u.email_verified_at?'':'pending'}">${u.email_verified_at?'VERIFIED':'NOT VERIFIED'}</span></p>${delivery?.pending_security_notifications?`<div class="notice info"><p>${esc(delivery.message||'An account security notification is waiting to be delivered. Your saved account change is retained.')}</p><button id="retry-email-delivery" class="secondary">Retry notification delivery</button></div>`:''}${pending?`<p class="notice info">Verification pending for <strong>${esc(pending)}</strong>. Your current address stays active until you verify the new address.</p>`:''}${pending||(u.email&&!u.email_verified_at)?`<form id="email-verify-form"><label for="email-code">${pending?'New email verification code':'Email verification code'}</label><input id="email-code" name="code" inputmode="numeric" autocomplete="one-time-code" minlength="8" maxlength="8" required><button type="submit">${pending?'Verify new email address':'Verify email'}</button></form><button id="resend-email" class="secondary">Resend verification email</button>`:''}<details><summary>Change email address</summary><form id="email-change-form"><label for="account-email">New email address</label><input id="account-email" name="email" type="email" autocomplete="email" maxlength="254" required><label for="email-current-password">Current password</label><input id="email-current-password" name="current_password" type="password" autocomplete="current-password" maxlength="128" required><button type="submit">Send verification code</button></form></details><p class="help space">Only a verified address can receive password reset codes. ${integrated()?'Verify your new address before it replaces the current address. Verification signs out previous sessions.':'Changing email revokes previous sessions.'}</p></section>`;
}
function bindEmail(){
 if($('#retry-email-delivery'))$('#retry-email-delivery').onclick=async()=>{const button=$('#retry-email-delivery');button.disabled=true;try{const r=await api('/api/email/retry-delivery',{});await refresh();await render();notice(r.message||'Notification delivery status refreshed.');}catch(e){notice(e.message,true);}finally{button.disabled=false;}};
 if($('#email-verify-form'))form('#email-verify-form',async f=>{const r=await api('/api/email/verify',Object.fromEntries(f));await refresh();await render();notice(r.message);});
 if($('#resend-email'))$('#resend-email').onclick=async()=>{try{const r=await api('/api/email/resend',{});notice(r.message);window.dispatchEvent(new Event('auth-ready'));}catch(e){notice(e.message,true);}};
 form('#email-change-form',async f=>{const r=await api('/api/email/change',Object.fromEntries(f));await refresh();await render();notice(r.message);});
}
function pending(){
 if(!state.user)return signIn();
 const u=state.user,pending=u.status==='PENDING';
 main.innerHTML=`${heading('Account access',pending?'Your request is with an administrator.':u.status==='ACTIVE'?'Your account is approved.':`Your account is ${esc(u.status.toLowerCase())}.`,pending?'Account created. An administrator must approve your access.':u.status==='ACTIVE'?'You can now open your institution’s workspace.':'Contact your institution administrator or the platform owner about the next step.')}<section class="card form-card space"><div class="row-top"><h2>${esc(u.username)}</h2><span class="pill ${u.status.toLowerCase()}">${esc(u.status)}</span></div><p><strong>${esc(orgName(u.org_id||u.requested_org_id))}</strong></p>${u.institution_request?`<p>Requested new institution: ${esc(u.institution_request)}</p>`:''}<p class="muted">${pending?'No institutional scientific data is available until your affiliation is verified.':'Access follows your current account status and verified membership.'}</p><button id="refresh-status">Check approval status</button></section>${emailSection()}`;
 bindEmail();
 $('#refresh-status').onclick=async()=>{await refresh();if(state.user?.status==='ACTIVE')go('/analyze');else{render();notice('Current account status refreshed.');}};
}
// Pagination changes presentation only; the server still supplies the verified institution scope.
let universityPage=0,universityPageOwner='';
async function university(version=rendering){
 const viewer=state.user?.id,owner=JSON.stringify([viewer,state.user?.org_id,state.organization?.id]);
 const isCurrent=()=>version===rendering&&owner===JSON.stringify([state.user?.id,state.user?.org_id,state.organization?.id]);
 if(!state.user)return signIn();if(state.user.status!=='ACTIVE')return pending();
 const plants=await api('/api/profiles');
 if(!isCurrent())return;
 if(owner!==universityPageOwner){universityPage=0;universityPageOwner=owner;}
 const pageSize=10,pageCount=Math.max(1,Math.ceil(plants.length/pageSize));
 universityPage=Math.min(Math.max(0,universityPage),pageCount-1);
 main.innerHTML=`${heading('My university','My university','Plants and reviewed measurements for '+esc(state.organization?.name||'your institution')+'.')}<section class="card space university-plants" aria-labelledby="university-plants-title"><div class="plant-list-heading"><div><h2 id="university-plants-title">Your university’s plants</h2><p id="plant-page-status" class="plant-page-status" role="status" aria-live="polite" aria-atomic="true"></p></div><a href="/analyze" class="plant-new-analysis">Analyze a plant</a></div><ul id="university-plant-list" class="university-plant-list" aria-label="Plants on this page"></ul><div id="plants-empty" class="plants-empty" hidden><h3>No plants yet</h3><p>Add your first plant in the analysis workspace. Its photos and reviewed measurements will stay together.</p><a href="/analyze">Add your first plant</a></div><nav class="plant-page-navigation" aria-label="Plant list pages"><p id="plant-page-position" class="plant-page-position"></p><div class="plant-page-buttons"><button id="plants-prev" type="button" class="secondary" aria-controls="university-plant-list">Previous</button><button id="plants-next" type="button" class="secondary" aria-controls="university-plant-list">Next</button></div></nav><p class="help plant-list-help">Open a plant to review its photos, calibration, approved measurements, growth history, and CSV export.</p></section>`;
 function draw(){
  if(!isCurrent())return;
  const start=universityPage*pageSize,visible=plants.slice(start,start+pageSize);
  $('#university-plant-list').innerHTML=visible.map(p=>{
   const name=p.display_name||p.plant_id||'Unnamed plant';
   const species=p.species==='lettuce'?'Lettuce':p.species==='rumex_acetosa'?'Sorrel (Rumex acetosa)':p.species||'Species not recorded';
   const identifier=p.plant_id&&p.plant_id!==name?' · ID '+p.plant_id:'';
   return `<li class="plant-row"><article class="plant-row-info"><h3>${esc(name)}</h3><p>${esc(species+identifier)}</p></article><a class="plant-open-analysis" href="/analyze?plant=${encodeURIComponent(String(p.id||''))}" aria-label="${esc('Analyze '+name)}">Analyze</a></li>`;
  }).join('');
  $('#plant-page-status').textContent=plants.length?`${start+1}–${Math.min(start+pageSize,plants.length)} of ${plants.length} plant${plants.length===1?'':'s'}`:'0 plants';
  $('#plant-page-position').textContent=plants.length?`Page ${universityPage+1} of ${pageCount}`:'No pages to show';
  $('#plants-empty').hidden=plants.length>0;
  $('#plants-prev').disabled=universityPage===0;
  $('#plants-next').disabled=universityPage>=pageCount-1;
 }
 $('#plants-prev').onclick=()=>{if(!isCurrent())return;if(universityPage>0){universityPage--;draw();}};
 $('#plants-next').onclick=()=>{if(!isCurrent())return;if(universityPage<pageCount-1){universityPage++;draw();}};
 draw();
}

async function institution(version=rendering){
 if(integrated())return university(version);
 const viewer=state.user?.id;
 if(!state.user)return signIn();if(state.user.status!=='ACTIVE')return pending();
 const [plants,measurements,growth]=await Promise.all(['plants','measurements','growth'].map(k=>api('/api/workspace/'+k)));
 if(version!==rendering||viewer!==state.user?.id)return;
 main.innerHTML=`${heading('My institution',esc(state.organization?.name||'Institution workspace'),'Plants and research history shared with your verified institution.')}<div class="notice info">Synthetic test workspace. The existing scientific pipeline is separate; no real photos, measurements, or approvals are changed here.</div><div class="grid"><section class="card"><div class="stat">${plants.items.length}</div><p>Test plants</p></section><section class="card"><div class="stat">${measurements.items.length}</div><p>Synthetic measurement records</p></section><section class="card"><div class="stat">Isolated</div><p>Organization-scoped backend access</p></section></div><div class="grid two"><section class="card"><h2>Plants</h2><div id="plants">${plants.items.map(p=>`<p><strong>${esc(p.data.name)}</strong><br><small>${esc(p.id)} · ${esc(p.data.species)}</small></p>`).join('')||'<p>No plants in this test institution yet.</p>'}</div><form id="plant-form"><label for="plant-name">Add a test plant</label><input id="plant-name" name="name" maxlength="120" required placeholder="Plant name"><label for="species">Species</label><select id="species" name="species"><option value="lettuce">Lettuce</option><option value="rumex_acetosa">Sorrel (Rumex acetosa)</option></select><button type="submit" class="space">Add test plant</button></form></section><section class="card"><h2>Growth history</h2><p class="help">Illustrative projected pixel area · synthetic series</p>${growth.items.length?'<svg class="chart" viewBox="0 0 360 160" role="img" aria-label="Synthetic area grows from 800 to 1200 pixels over three days"><line x1="30" y1="130" x2="330" y2="130"/><polyline points="40,115 180,80 320,35"/><text x="30" y="152">Day 1</text><text x="165" y="152">Day 2</text><text x="300" y="152">Day 3</text></svg>':'<p>No test growth series yet.</p>'}<div class="actions"><a href="/api/workspace/export.csv" download>Download institution CSV</a></div></section></div><details class="card"><summary>Account security · change password</summary><form id="password-form" class="form-card"><label for="old-password">Current password</label><input id="old-password" name="current_password" type="password" autocomplete="current-password" maxlength="128" required><label for="new-password">New password</label><input id="new-password" name="new_password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required><button type="submit">Change password & revoke previous sessions</button></form></details>${emailSection()}`;
 bindEmail();
 form('#plant-form',async f=>{await api('/api/workspace/plants',Object.fromEntries(f));await institution();notice('Test plant added to your verified institution.');});
 form('#password-form',async f=>{const r=await api('/api/password',Object.fromEntries(f));await refresh();notice(r.message);});

}
async function analyze(version=rendering){
 if(integrated()){go('/analyze');return;}
 const viewer=state.user?.id;
 if(!state.user)return signIn();if(state.user.status!=='ACTIVE')return pending();
 const [plants,photos,analyses]=await Promise.all(['plants','photos','analyses'].map(k=>api('/api/workspace/'+k)));
 if(version!==rendering||viewer!==state.user?.id)return;
 main.innerHTML=`${heading('Leaf research','Analyze a plant.','Choose a plant and photo to review its visible leaf area.')}<section class="card analysis-card space"><form id="analysis-form"><label for="analysis-plant">Plant</label><select id="analysis-plant" required>${plants.items.map(p=>`<option value="${esc(p.id)}">${esc(p.data.name)}</option>`).join('')}</select><label for="analysis-photo">Photo</label><select id="analysis-photo" required></select><p class="help">Development preview uses synthetic test photos and analyses.</p><button type="submit"${plants.items.length?'':' disabled'}>View analysis</button></form><div id="analysis-result" aria-live="polite"></div></section>`;
 const choose=()=>{const plant=$('#analysis-plant').value;$('#analysis-photo').innerHTML=photos.items.filter(p=>p.plant_id===plant).map(p=>`<option value="${esc(p.id)}">${esc(p.data.name)}</option>`).join('');};
 $('#analysis-plant').onchange=choose;choose();
 form('#analysis-form',async()=>{
  const plant=$('#analysis-plant').value,photo=$('#analysis-photo').value;
  const candidate=analyses.items.find(a=>a.plant_id===plant);
  if(!candidate){notice('No test analysis is available for this plant.',true);return;}
  const {item}=await api('/api/workspace/analyses/'+encodeURIComponent(candidate.id));
  if(version!==rendering||viewer!==state.user?.id)return;
  $('#analysis-result').innerHTML=`<div class="space"><span class="pill pending">SYNTHETIC PREVIEW</span><h2 class="space">Visible leaf area</h2><img class="test-photo" src="/api/artifacts/${encodeURIComponent(photo)}" alt="Synthetic test plant photo"><p><strong>${esc(item.data.projected_pixel_area)} pixels</strong></p><p class="help">Test result only. Real scientific measurements require human review and approval.</p></div>`;
 });
}
function profile(){
 if(!state.user)return signIn();const u=state.user;
 main.innerHTML=`${heading('Your account','Your profile','Manage your account and security.')}<div class="actions"><a href="${u.status==='ACTIVE'?'/analyze':'/pending'}">← Back to ${u.status==='ACTIVE'?'analysis':'account status'}</a></div><section class="card form-card space"><h2>${esc(u.display_name||u.username)}</h2><p class="user-meta">Username: ${esc(u.username)}<br>${esc(state.organization?.name||'No verified institution')}<br>Account: ${esc(u.status)}</p></section>${emailSection()}<section class="card form-card space"><h2>Change password</h2><form id="password-form"><label for="old-password">Current password</label><input id="old-password" name="current_password" type="password" autocomplete="current-password" maxlength="128" required><label for="new-password">New password</label><input id="new-password" name="new_password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required><button type="submit">Change password</button></form><p class="help">Changing your password signs out previous sessions.</p></section><div class="actions"><button id="profile-signout" class="secondary">Sign out</button></div>`;
 bindEmail();
 form('#password-form',async f=>{const r=await api('/api/password',Object.fromEntries(f));await refresh();await render();notice(r.message);});
 $('#profile-signout').onclick=signOut;
}
function adminGuard(){if(!state.user||state.user.status!=='ACTIVE'||!isAdmin()){main.innerHTML=heading('Restricted','Administrator access required.','Ask your institution administrator if you need to manage accounts.')+'<a href="/institution" class="primary-link">Return to my university</a>'; return false;}return true;}
let auditPage=0,auditPageOwner='';
async function dashboard(version=rendering){
 const owner=JSON.stringify([state.user?.id,state.user?.org_id,state.organization?.id,state.user?.role,state.user?.status]);
 const isCurrent=()=>version===rendering&&owner===JSON.stringify([state.user?.id,state.user?.org_id,state.organization?.id,state.user?.role,state.user?.status]);
 if(!adminGuard())return;const {users}=await api('/api/admin/users');if(!isCurrent())return;
 main.innerHTML=`${heading('Administration','Administration','Review account requests and keep research access within verified institutions.')}<dl class="account-counts">${[['Pending requests',users.filter(u=>u.status==='PENDING').length],['Active accounts',users.filter(u=>u.status==='ACTIVE').length],['Suspended accounts',users.filter(u=>u.status==='SUSPENDED').length]].map(([title,n])=>`<div><dt>${title}</dt><dd>${n}</dd></div>`).join('')}</dl><div class="actions"><button id="pending-users">Review pending requests</button><a href="/users">Manage users</a>${isPlatform()?'<a href="/organizations">Manage institutions</a>':''}</div><section class="space audit-section" aria-labelledby="audit-title"><h2 id="audit-title">Audit log</h2><p class="help audit-scope-note">${isPlatform()?'The server returns at most the latest 300 account and organization events, not the full audit history. Passwords, session tokens, and recovery codes are excluded.':'Platform administrators and the owner can inspect the audit log.'}</p><div id="audit-pagination-panel" class="audit-pagination-panel"${isPlatform()?'':' hidden'}><p id="audit-page-status" class="audit-page-status" role="status" aria-live="polite" aria-atomic="true"></p><ul class="audit audit-log-list" id="audit" aria-label="Audit events on this page"></ul><p id="audit-empty" class="audit-empty" hidden>No audit events were returned.</p><nav class="audit-page-navigation" aria-label="Audit log pages"><p id="audit-page-position"></p><div class="audit-page-buttons"><button id="audit-prev" type="button" class="secondary" aria-controls="audit">Previous</button><button id="audit-next" type="button" class="secondary" aria-controls="audit">Next</button></div></nav></div></section>`;
 $('#pending-users').onclick=async()=>{await go('/users');const filter=$('#status-filter');if(filter){filter.value='PENDING';filter.dispatchEvent(new Event('change'));}};
 if(!isPlatform())return;
 const {events}=await api('/api/admin/audit');if(!isCurrent())return;
 if(owner!==auditPageOwner){auditPage=0;auditPageOwner=owner;}
 const pageSize=10,pageCount=Math.max(1,Math.ceil(events.length/pageSize));auditPage=Math.min(Math.max(0,auditPage),pageCount-1);
 function draw(){
  if(!isCurrent()||!isPlatform())return;
  const start=auditPage*pageSize,visible=events.slice(start,start+pageSize);
  $('#audit').innerHTML=visible.map(e=>{
   const date=new Date(e.at),valid=!Number.isNaN(date.getTime()),when=valid?date.toLocaleString():String(e.at||'Time not recorded');
   const details=typeof e.details==='string'?e.details:e.details==null?'':JSON.stringify(e.details);
   return `<li class="audit-row"><article><div class="audit-row-heading"><h3>${esc(String(e.action||'Action not recorded').replaceAll('_',' '))}</h3><time${valid?' datetime="'+esc(date.toISOString())+'"':''}>${esc(when)}</time></div><dl class="audit-event-meta"><div><dt>Actor</dt><dd>${esc(e.actor_id||'Not recorded')}</dd></div><div><dt>Account / institution</dt><dd>${esc(e.target_id||'Not recorded')}</dd></div></dl>${details?`<details class="audit-event-details"><summary>Event details</summary><p>${esc(details)}</p></details>`:''}</article></li>`;
  }).join('');
  $('#audit-page-status').textContent=events.length?`${start+1}–${Math.min(start+pageSize,events.length)} of ${events.length} fetched events`:'0 fetched events';
  $('#audit-page-position').textContent=events.length?`Page ${auditPage+1} of ${pageCount}`:'No pages to show';
  $('#audit-empty').hidden=events.length>0;$('#audit-prev').disabled=auditPage===0;$('#audit-next').disabled=auditPage>=pageCount-1;
 }
 $('#audit-prev').onclick=()=>{if(!isCurrent()||!isPlatform())return;if(auditPage>0){auditPage--;draw();}};
 $('#audit-next').onclick=()=>{if(!isCurrent()||!isPlatform())return;if(auditPage<pageCount-1){auditPage++;draw();}};
 draw();
}
async function usersPage(version=rendering){
 const viewer=state.user?.id;
 if(!adminGuard())return;const {users}=await api('/api/admin/users');if(version!==rendering||viewer!==state.user?.id)return;
 main.innerHTML=`${heading('User management','Manage accounts','Approve requests after verifying affiliation. Role changes revoke all of that account’s existing sessions.')}<section class="card space"><div class="control-grid"><div><label for="search">Search username</label><input id="search" type="search" placeholder="Find an account"></div><div><label for="status-filter">Account status</label><select id="status-filter"><option value="">All states</option>${['PENDING','ACTIVE','REJECTED','SUSPENDED'].map(s=>`<option value="${s}">${s[0]+s.slice(1).toLowerCase()}</option>`).join('')}</select></div><div><label for="org-filter">Institution</label><select id="org-filter"><option value="">All permitted institutions</option>${options()}</select></div></div></section><div class="list" id="user-list"></div>`;
 function draw(){
  const needle=$('#search').value.toLowerCase(),status=$('#status-filter').value,org=$('#org-filter').value;
  const list=[...users].sort((a,b)=>(a.status==='PENDING'?0:1)-(b.status==='PENDING'?0:1)).filter(u=>(u.username.toLowerCase().includes(needle)||u.display_name.toLowerCase().includes(needle))&&(!status||u.status===status)&&(!org||(u.org_id||u.requested_org_id)===org));
  $('#user-list').innerHTML='';
  for(const u of list){
   const card=document.createElement('article');card.className='card account-row';
   const self=u.id===state.user.id,protectedUser=u.role==='PLATFORM_OWNER',canManage=!self&&!protectedUser&&(isOwner()||['PENDING','MEMBER'].includes(u.role));
   card.innerHTML=`<div class="row-top"><div><h2>${esc(u.username)}</h2><span class="muted">${esc(u.display_name||u.role.replaceAll('_',' '))}</span></div><span class="pill ${u.status.toLowerCase()}">${esc(u.status)}</span></div><p class="user-meta">${esc(orgName(u.org_id||u.requested_org_id))}${u.institution_request?' · Requested: '+esc(u.institution_request):''}<br>Registered ${esc(new Date(u.created_at).toLocaleString())}</p>${canManage&&u.status==='PENDING'?`<div><label>Confirm institution <select class="verified">${options(u.requested_org_id||'',true)}</select></label>${(!integrated()||u.email_verified_at)?'':'<p class="help approval-wait">Email verification is still needed. Ask this person to verify their email and sign in again before approval.</p>'}</div>`:''}<div class="actions decisions"></div>${isOwner()&&canManage&&u.status==='ACTIVE'?`<div class="row-controls space"><div><label>Change role <select class="role">${['MEMBER','ORG_ADMIN','PLATFORM_ADMIN'].map(r=>`<option${r===u.role?' selected':''}>${r}</option>`).join('')}</select></label></div><button class="role-save">Update role</button></div>`:''}${self||protectedUser?'<p class="help">Protected account. Self-administration and owner removal are unavailable.</p>':''}`;
   if(canManage){
    const actions=u.status==='PENDING'?['approve','reject']:u.status==='ACTIVE'?['suspend']:u.status==='SUSPENDED'?['reactivate']:[];
    for(const action of actions){const button=document.createElement('button');button.textContent=action[0].toUpperCase()+action.slice(1);button.className=['reject','suspend'].includes(action)?'danger':'';button.onclick=async()=>{if(['reject','suspend'].includes(action)&&!confirm(`${action==='suspend'?'Suspend access for':'Reject the request from'} ${u.username}? ${action==='suspend'?'This signs out their current sessions.':'They will need administrator help to regain access.'}`))return;button.disabled=true;try{const verified=card.querySelector('.verified')?.value;await api(`/api/admin/users/${encodeURIComponent(u.id)}/decision`,{action,version:u.version,...(action==='approve'?{verified_org_id:verified||null}:{})});await refresh();await usersPage();notice(`${u.username}: ${action} successful.`);}catch(e){notice(e.message,true);button.disabled=false;}};if(action==='approve'&&integrated()&&!u.email_verified_at){button.disabled=true;button.setAttribute('aria-describedby','email-required-'+u.id);const hint=card.querySelector('.approval-wait');if(hint)hint.id='email-required-'+u.id;}card.querySelector('.decisions').append(button);}
   }
   const roleButton=card.querySelector('.role-save');if(roleButton)roleButton.onclick=async()=>{const next=card.querySelector('.role').value;if(next===u.role){notice('This account already has that role.');return;}if(!confirm(`Change ${u.username} to ${next.replaceAll('_',' ').toLowerCase()}? Their existing sessions will be signed out.`))return;roleButton.disabled=true;try{await api(`/api/admin/users/${encodeURIComponent(u.id)}/role`,{role:card.querySelector('.role').value,version:u.version});await refresh();await usersPage();notice(`${u.username}: role updated. Previous sessions revoked.`);}catch(e){notice(e.message,true);roleButton.disabled=false;}};
   $('#user-list').append(card);
  }
  if(!list.length)$('#user-list').innerHTML='<p class="card">No matching accounts.</p>';
 }
 for(const id of ['#search','#status-filter','#org-filter'])$(id).addEventListener(id==='#search'?'input':'change',draw);draw();
}
async function organizationsPage(version=rendering){
 const viewer=state.user?.id;
 if(!adminGuard())return;if(!isPlatform())throw new Error('Institution registry management requires platform authorization.');
 main.innerHTML=`${heading('Organization registry','Manage institutions','Bahçeşehir is the only signup institution. Additional institution onboarding is reserved for a later release. Names can change while ownership IDs remain stable.')}<div class="list" id="org-list"></div><p class="help space">Assign organization administrators in <a href="/users">Users → Select account → Change role</a>. Only the owner can promote or demote administrators.</p>`;

 if(state.independent_cloud){
  main.innerHTML=`${heading('Organization registry','Manage institutions','Enabled institutions accept registration requests. Verified accounts require administrator approval before research access. Institution IDs preserve ownership when names change.')}<div class="list" id="org-list"></div>${isOwner()?'<form id="create-institution" class="card space"><h2>Add an institution</h2><label>Institution name<input name="name" required minlength="2" maxlength="120"></label><button type="submit" class="space">Create institution</button></form>':''}<p class="help space">Assign institution administrators in Users after verifying membership.</p>`;
  const create=$('#create-institution');if(create)create.onsubmit=async event=>{event.preventDefault();const button=create.querySelector('button');button.disabled=true;try{await api('/api/admin/organizations',{name:create.elements.name.value.trim()});await refresh();await organizationsPage();notice('Institution created. New registrations remain pending until approved.');}catch(error){notice(error.message,true);button.disabled=false;}};
 }
 for(const org of organizations){const card=document.createElement('form');card.className='card account-row';card.innerHTML=`<div class="row-top"><h2>${esc(org.name)}${state.development&&org.id!=='org-bau'?' ? TEST ONLY':''}</h2><span class="pill">${org.enabled?'Enabled':'Disabled'}</span></div><p class="help">Immutable ID: <code>${esc(org.id)}</code></p><label>Institution name <input name="name" value="${esc(org.name)}" required minlength="2" maxlength="120"></label><label class="check-label"><input name="enabled" type="checkbox"${org.enabled?' checked':''}${!isOwner()?' disabled':''}>Institution enabled</label><label class="check-label"><input name="approvals_enabled" type="checkbox"${org.approvals_enabled?' checked':''}${!isOwner()?' disabled':''}>Organization admins may approve their institution’s members</label><button type="submit" class="secondary space">Save institution</button>`;card.onsubmit=async e=>{e.preventDefault();const button=card.querySelector('button');button.disabled=true;try{await api('/api/admin/organizations/'+encodeURIComponent(org.id),{name:card.elements.name.value,enabled:card.elements.enabled.checked,approvals_enabled:card.elements.approvals_enabled.checked,version:org.version});await refresh();await organizationsPage();notice('Institution updated. Scientific ownership IDs preserved.');}catch(err){notice(err.message,true);button.disabled=false;}};$('#org-list').append(card);}
}
let rendering=0;
async function render(){if(document.body?.dataset.firebase==='true'&&!state.user){window.location.assign('/signin');return;}const id=++rendering;chrome();main.setAttribute('aria-busy','true');try{const page=location.pathname;switch(page){case '/signup':signup();break;case '/pending':pending();break;case '/institution':await institution();break;case '/analyze':await analyze();break;case '/profile':profile();break;case '/admin':await dashboard();break;case '/users':await usersPage();break;case '/organizations':await organizationsPage();break;default:if(state.user?.status==='ACTIVE')await analyze();else signIn();}}catch(e){if(id===rendering){main.innerHTML=heading('Access check','This page could not open.',esc(e.message))+'<div class="actions"><button id="retry-page">Try again</button><a href="/institution">Return to my university</a></div>';$('#retry-page').onclick=()=>render();notice(e.message,true);}}finally{if(id===rendering)main.removeAttribute('aria-busy');}}
async function signOut(){try{const r=await api('/api/logout',{});await refresh();go('/signin');notice(r.message);}catch(e){notice(e.message,true);}}
window.leafAuth={api,refresh,notice,go,state};
refresh().then(render).catch(e=>notice(e.message,true));
})();
