import {initializeApp,getAuth,connectAuthEmulator,setPersistence,inMemoryPersistence,
 createUserWithEmailAndPassword,signInWithEmailAndPassword,signOut,sendEmailVerification,
 sendPasswordResetEmail,verifyBeforeUpdateEmail,updatePassword} from './firebase-sdk.js';

const content=document.querySelector('#content'),message=document.querySelector('#message');
const config=await (await fetch('/api/firebase/config')).json();
const production=config.identity_mode==='firebase_production'||config.identity_mode==='independent_cloud';
if(production){
 if(config.local_only!==false||location.protocol!=='https:'||location.origin!==config.origin||config.emulator)throw Error('Secure Firebase origin required');
}else if(!config.local_only||!['localhost','127.0.0.1'].includes(location.hostname))throw Error('Local evaluation only');
const live=config.identity_mode==='google_live_test';
const managed=live||production;
const auth=getAuth(initializeApp(config));
if(config.emulator)connectAuthEmulator(auth,config.emulator,{disableWarnings:true});
else if(!managed)throw Error('Explicit Firebase identity mode required');
await setPersistence(auth,inMemoryPersistence);
let state=await (await fetch('/api/session')).json();
function say(text,error=false){
 message.textContent=text;message.classList.toggle('error',error);
 message.setAttribute?.('role',error?'alert':'status');
 if(error)message.focus?.();
}
function heading(title,lead){
 document.title=title+' · Hydroponic Leaf Research';
 const titleNode=document.querySelector('#page-title'),leadNode=document.querySelector('#page-lead');
 if(titleNode)titleNode.textContent=title;if(leadNode)leadNode.textContent=lead;
}
function navigation(){
 const node=document.querySelector('#account-navigation');if(!node)return;
 const active=state.user?.status==='ACTIVE'&&state.user?.email_verified_at;
 const links=active? [['Analyze a plant','/analyze'],['My university','/institution'],['Profile','/profile']]:state.user?[['Account status','/pending'],['Profile','/profile']]:[['Sign in','/signin'],['Create account','/signup']];
 if(active&&['ORG_ADMIN','PLATFORM_ADMIN','PLATFORM_OWNER'].includes(state.user.role))links.push(['Administration','/admin']);
 links.push(['How to use','/help']);
 node.innerHTML=links.map(([text,path])=>`<a href="${path}"${location.pathname===path?' aria-current="page"':''}${path==='/help'?' target="_blank" rel="noopener"':''}>${text}</a>`).join('');
}
async function api(path,data){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf_token},body:JSON.stringify(data)});
 const result=await response.json();if(!response.ok){const error=new Error(result.detail||'Please try again.');error.status=response.status;throw error;}
 state=await (await fetch('/api/session')).json();return result;
}
async function receivingInbox(email){if(live)await api('/api/firebase/test-intent',{email});}
const verificationMessage=()=>production?'Check your inbox to verify your email, then sign in again.':live?'Verification requested from Google. Check the receiving inbox; delivery has not yet been confirmed. After verification, administrator approval is still required.':'Emulator verification action generated; no email was sent. After verification, administrator approval is required.';
const messages={'auth/user-not-found':'Email or password is incorrect. Check both entries and try again.','auth/wrong-password':'Email or password is incorrect. Check both entries and try again.','auth/invalid-credential':'Email or password is incorrect. Check both entries and try again.','auth/invalid-login-credentials':'Email or password is incorrect. Check both entries and try again.',
 'auth/too-many-requests':'Google has temporarily limited sign-in attempts. Wait before retrying, or use Forgot password to recover access.','auth/network-request-failed':'Could not connect. Your entries are still here. Try again.',
 'auth/requires-recent-login':'Sign in again before changing account details.','auth/email-already-in-use':'This email cannot be used. Try signing in or resetting your password.'};
const progress={'#signin':'Signing in…','#signup':'Creating account…','#reset':'Requesting reset link…','#verify-email-form':'Requesting verification…','#change-email':'Requesting email change…','#change-password':'Changing password…'};
function errorText(error){
 if(error.status===403&&/restricted/i.test(error.message))return 'Account access is restricted. Contact your institution administrator to restore access before trying again.';
 return messages[error.code]||(error.code?'Please check your details and try again.':error.message);
}
function form(id,work){document.querySelector(id).addEventListener('submit',async event=>{
 event.preventDefault();const node=event.currentTarget,button=node.querySelector('button[type=submit]'),label=button.textContent,inline=document.querySelector(id+'-error');
 button.disabled=true;button.textContent=progress[id]||'Saving…';if(inline)inline.hidden=true;const priorDetails=document.querySelector('#account-error-details');if(priorDetails)priorDetails.hidden=true;say(progress[id]||'Saving…');
 try{await work(Object.fromEntries(new FormData(node)));}catch(error){
  const text=errorText(error);if(inline){inline.hidden=false;inline.textContent=text;}say(text,true);
  const detail=document.querySelector('#account-error-details'),report=document.querySelector('#account-error-report');
  if(detail&&report){detail.hidden=false;report.textContent=JSON.stringify({stage:id.slice(1),http_status:error.status||null,provider_code:/^auth\/[a-z-]+$/.test(error.code||'')?error.code:null});}
 }finally{button.disabled=false;button.textContent=label;await signOut(auth);}
});}
const email='<label>Email<input type="email" name="email" required autocomplete="email" autocapitalize="none" spellcheck="false"></label>';
const password='<label>Password<input type="password" name="password" required minlength="15" maxlength="128" autocomplete="current-password"></label>';
const inlineError=id=>`<p class="form-error" id="${id}-error" role="alert" hidden></p>`;
async function exchange(user){await api('/api/firebase/session',{id_token:await user.getIdToken(true)});await signOut(auth);location.href=!state.user?.email_verified_at?'/verify-email':state.user?.status==='ACTIVE'?'/institution':'/pending';}
navigation();
if(location.pathname==='/signup'){
 heading('Create an account','Verify your email, then wait for your institution to approve research access.');
 content.innerHTML=`<form id="signup" aria-labelledby="page-title"><label>Username<input name="username" required minlength="3" maxlength="40" pattern="[A-Za-z0-9][A-Za-z0-9_.-]+" autocomplete="username" autocapitalize="none" spellcheck="false" aria-describedby="username-help"></label><p class="help" id="username-help">3–40 letters, numbers, dots, dashes or underscores.</p>${email}${password.replace('current-password','new-password')}<p class="help">Use a passphrase of 15–128 characters.</p><label>Institution<select name="requested_org_id" id="universities" required></select></label><p class="help">Choose the institution where you will conduct this research.</p>${inlineError('signup')}<button type="submit">Create account</button></form><p class="account-alternative">Already registered? <a href="/signin">Sign in</a></p>`;
 const organizations=(await (await fetch('/api/signup-institutions')).json()).organizations;
 for(const org of organizations){const option=document.createElement('option');option.value=org.id;option.textContent=org.name;document.querySelector('#universities').append(option);}
 form('#signup',async data=>{
   await receivingInbox(data.email);
   // Preserve identity reuse after an account metadata/network retry.
   let credential;
   try{credential=await createUserWithEmailAndPassword(auth,data.email,data.password);}
   catch(error){if(error.code!=='auth/email-already-in-use')throw error;credential=await signInWithEmailAndPassword(auth,data.email,data.password);}
   await api('/api/firebase/register',{id_token:await credential.user.getIdToken(true),username:data.username,requested_org_id:data.requested_org_id});
   navigation();
   try{await sendEmailVerification(credential.user);}catch(error){error.message='Your account was created, but the verification request failed. Open Account status to request the email again.';error.code=null;throw error;}
   location.href='/verify-email';
 });
}else if(['/pending','/profile','/verify-email'].includes(location.pathname)){
 if(!state.user){location.replace('/signin');}else{
 const verified=Boolean(state.user.email_verified_at),active=state.user.status==='ACTIVE',waiting=state.user.status==='PENDING';
 heading(!verified?'Verify your email':waiting?'Waiting for institution approval':location.pathname==='/profile'?'Your profile':'Account status',!verified?'Confirm your email address before opening the research workspace.':waiting?'Your account is registered. Your institution administrator needs to approve access.':'Manage your account and open your institution workspace.');
 content.innerHTML='<section class="account-summary" aria-labelledby="account-title"><h2 id="account-title"></h2><p id="account-state" class="account-status"></p><p id="account-email"></p><dl class="access-checklist"><div><dt>Email</dt><dd>'+(verified?'Verified':'Verification required')+'</dd></div><div><dt>Institution access</dt><dd>'+(active?'Approved':waiting?'Awaiting administrator approval':'Restricted')+'</dd></div></dl></section>'+(!verified?'<form id="verify-email-form"><p>Enter your password to request a verification email. Open its link, then sign in again.</p>'+password+inlineError('verify-email-form')+'<button type="submit">Send verification email</button></form><p><a href="/signin">I have verified my email — sign in</a></p>':waiting?'<p class="help">Approval is separate from email verification. You do not need to create another account.</p><a class="primary-link" id="check-status" href="/signin">Sign in to check approval</a>':active?'<a class="primary-link" href="/institution">Open my university</a>':'<p>Contact your institution administrator about restoring access.</p>')+'<details><summary>Change email address</summary><form id="change-email">'+password+'<label>New email<input type="email" name="new_email" autocomplete="email" autocapitalize="none" spellcheck="false" required></label>'+inlineError('change-email')+'<button type="submit">Verify new email</button></form></details><details><summary>Change password</summary><form id="change-password">'+password+'<label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required></label>'+inlineError('change-password')+'<button type="submit">Change password</button></form></details><div class="account-signout"><button id="logout" class="secondary">Sign out</button></div>';
 document.querySelector('#account-title').textContent=state.user.username;
 document.querySelector('#account-state').textContent=!verified?(active?'Your administrator access is already approved. Verify your email to open your university workspace.':'Verify your email first. Your institution administrator must then approve access.'):waiting?'Your email is verified. Waiting for your institution administrator to approve access.':active?'Your email and institution access are approved.':'Your institutional access is restricted. Contact your institution administrator.';
 document.querySelector('#account-email').textContent=state.user.email;
 document.querySelector('#logout').onclick=async()=>{try{await api('/api/logout',{});location.href='/signin';}catch{say('Could not sign out. Please retry.',true);}};
 if(!verified)form('#verify-email-form',async data=>{await receivingInbox(state.user.email);const {user}=await signInWithEmailAndPassword(auth,state.user.email,data.password);await sendEmailVerification(user);say('Verification email requested. Open Google’s email link, then sign in again.');});
 form('#change-email',async data=>{await receivingInbox(state.user.email);await receivingInbox(data.new_email);const {user}=await signInWithEmailAndPassword(auth,state.user.email,data.password);await verifyBeforeUpdateEmail(user,data.new_email);say(managed?'Check your new inbox to verify the change. Your current email stays active until confirmation.':'Emulator action generated. Your current email stays active until the new address is verified. No email was sent.');});
 form('#change-password',async data=>{await receivingInbox(state.user.email);const {user}=await signInWithEmailAndPassword(auth,state.user.email,data.password);await updatePassword(user,data.new_password);state=await (await fetch('/api/session')).json();if(state.user)await api('/api/logout',{});location.href='/signin?password-changed=1';});
 }
}else{
 heading('Sign in','Open your institution’s plants, reviewed measurements and growth history.');
 if(new URLSearchParams(location.search).has('password-changed'))say('Password changed. Sign in again with your new password.');
 content.innerHTML=`<form id="signin" aria-labelledby="page-title">${email}${password}${inlineError('signin')}<button type="submit">Sign in</button></form><p class="account-alternative">New to Leaf Research? <a href="/signup">Create an account</a></p><details><summary>Forgot password?</summary><form id="reset"><p class="help">Request a reset link for your registered email address.</p>${email}${inlineError('reset')}<button type="submit">Send reset link</button></form></details>`;
 form('#signin',async data=>{await receivingInbox(data.email);const {user}=await signInWithEmailAndPassword(auth,data.email,data.password);
 if(new URLSearchParams(location.search).has('verify')&&!user.emailVerified){await sendEmailVerification(user);say(verificationMessage());}
 else await exchange(user);});
 form('#reset',async data=>{await receivingInbox(data.email);try{await sendPasswordResetEmail(auth,data.email);}catch(error){if(error.code!=='auth/user-not-found')throw error;}say(production?'If this address is registered, Google will send a password-reset message.':live?'If this address is registered, Google will send a password-reset message. Check the receiving inbox; delivery has not yet been confirmed.':'If this address is registered, a reset action is available in the emulator. No email was sent.');});
}
if(live&&!config.test_inboxes_configured){
 say('The Google email test is waiting for a controlled receiving inbox. A second address is needed only for email-change receipt testing. No real email has been requested.');
 document.querySelectorAll('input,select,button').forEach(node=>{node.disabled=true;});
}
