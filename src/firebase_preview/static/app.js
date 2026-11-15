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
function say(text,error=false){message.textContent=text;message.classList.toggle('error',error);}
async function api(path,data){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf_token},body:JSON.stringify(data)});
 const result=await response.json();if(!response.ok)throw Error(result.detail||'Please try again.');
 state=await (await fetch('/api/session')).json();return result;
}
async function receivingInbox(email){if(live)await api('/api/firebase/test-intent',{email});}
const verificationMessage=()=>production?'Check your inbox to verify your email, then sign in again.':live?'Verification requested from Google. Check the receiving inbox; delivery has not yet been confirmed. After verification, administrator approval is still required.':'Emulator verification action generated; no email was sent. After verification, administrator approval is required.';
const messages={'auth/invalid-credential':'Email or password is incorrect.','auth/invalid-login-credentials':'Email or password is incorrect.',
 'auth/too-many-requests':'Google has temporarily limited sign-in attempts. Wait before retrying, or use Forgot password to recover access.','auth/network-request-failed':'Could not connect. Your entries are still here. Try again.',
 'auth/requires-recent-login':'Sign in again before changing account details.','auth/email-already-in-use':'This email cannot be used. Try signing in or resetting your password.'};
function form(id,work){document.querySelector(id).addEventListener('submit',async event=>{
 event.preventDefault();const node=event.currentTarget,button=node.querySelector('button[type=submit]');button.disabled=true;say('Working…');
 try{await work(Object.fromEntries(new FormData(node)));}catch(error){say(messages[error.code]||(error.code?'Please check your details and try again.':error.message),true);}
 finally{button.disabled=false;await signOut(auth);}
});}
const email='<label>Email<input type="email" name="email" required autocomplete="email" autocapitalize="none"></label>';
const password='<label>Password<input type="password" name="password" required minlength="15" maxlength="128" autocomplete="current-password"></label>';
async function exchange(user){await api('/api/firebase/session',{id_token:await user.getIdToken(true)});await signOut(auth);location.href=!state.user?.email_verified_at?'/verify-email':state.user?.status==='ACTIVE'?'/institution':'/pending';}
if(location.pathname==='/signup'){
 content.innerHTML=`<h2>Create an account</h2><form id="signup"><label>Username<input name="username" required minlength="3" maxlength="40" pattern="[A-Za-z0-9][A-Za-z0-9_.-]+" autocomplete="username"></label>${email}${password}<label>University<select name="requested_org_id" id="universities" required></select></label><button type="submit">Create account</button></form><p><a href="/signin">Already registered? Sign in</a></p>`;
 const organizations=(await (await fetch('/api/signup-institutions')).json()).organizations;
 for(const org of organizations){const option=document.createElement('option');option.value=org.id;option.textContent=org.name;document.querySelector('#universities').append(option);}
 form('#signup',async data=>{
   await receivingInbox(data.email);
   // Reuse a successfully created identity after a metadata/network retry.
   let credential;
   try{credential=await createUserWithEmailAndPassword(auth,data.email,data.password);}
   catch(error){if(error.code!=='auth/email-already-in-use')throw error;credential=await signInWithEmailAndPassword(auth,data.email,data.password);}
   await api('/api/firebase/register',{id_token:await credential.user.getIdToken(true),username:data.username,requested_org_id:data.requested_org_id});
   await sendEmailVerification(credential.user);
   say('Account created. '+verificationMessage());
 });
}else if(['/pending','/profile','/verify-email'].includes(location.pathname)){
 if(!state.user){location.replace('/signin');}else{
 content.innerHTML='<h2 id="account-title"></h2><p id="account-state"></p><p id="account-email"></p>'+(!state.user.email_verified_at?'<form id="verify-email-form"><p>Enter your password to request a verification email. After opening Google’s link, sign in again.</p>'+password+'<button type="submit">Send verification email</button></form><p><a href="/signin">I have verified my email — sign in</a></p>':'')+'<div class="actions"><button id="logout">Sign out</button></div><details><summary>Change email address</summary><form id="change-email">'+password+'<label>New email<input type="email" name="new_email" autocomplete="email" required></label><button type="submit">Verify new email</button></form></details><details><summary>Change password</summary><form id="change-password">'+password+'<label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="15" maxlength="128" required></label><button type="submit">Change password</button></form></details><p><a href="/institution">Open my university</a></p>';
 document.querySelector('#account-title').textContent=state.user.email_verified_at?state.user.username:'Verify your email';
 document.querySelector('#account-state').textContent=!state.user.email_verified_at?(state.user.status==='ACTIVE'?'Your administrator access is already approved. Verify your email to open your university workspace.':'Verify your email first. Your university must then approve access.'):state.user.status==='PENDING'?'Your email is verified. Your university approval is pending.':'Email verified. Access: '+state.user.status;
 document.querySelector('#account-email').textContent=state.user.email;
 document.querySelector('#logout').onclick=async()=>{try{await api('/api/logout',{});location.href='/signin';}catch{say('Could not sign out. Please retry.',true);}};
 if(!state.user.email_verified_at)form('#verify-email-form',async data=>{await receivingInbox(state.user.email);const {user}=await signInWithEmailAndPassword(auth,state.user.email,data.password);await sendEmailVerification(user);say('Verification email requested. Open Google’s email link, then sign in again.');});
 form('#change-email',async data=>{await receivingInbox(state.user.email);await receivingInbox(data.new_email);const {user}=await signInWithEmailAndPassword(auth,state.user.email,data.password);await verifyBeforeUpdateEmail(user,data.new_email);say(managed?'Check your new inbox to verify the change. Your current email stays active until confirmation.':'Emulator action generated. Your current email stays active until the new address is verified. No email was sent.');});
 form('#change-password',async data=>{await receivingInbox(state.user.email);const {user}=await signInWithEmailAndPassword(auth,state.user.email,data.password);await updatePassword(user,data.new_password);state=await (await fetch('/api/session')).json();if(state.user)await api('/api/logout',{});say('Password changed. Sign in again with your new password.');});
 }
}else{
 content.innerHTML=`<h2>Sign in</h2><form id="signin">${email}${password}<button type="submit">Sign in</button></form><p><a href="/signup">Create an account</a></p><details><summary>Forgot password?</summary><form id="reset">${email}<button type="submit">Send reset link</button></form></details>`;
 form('#signin',async data=>{await receivingInbox(data.email);const {user}=await signInWithEmailAndPassword(auth,data.email,data.password);
 if(new URLSearchParams(location.search).has('verify')&&!user.emailVerified){await sendEmailVerification(user);say(verificationMessage());}
 else await exchange(user);});
 form('#reset',async data=>{await receivingInbox(data.email);try{await sendPasswordResetEmail(auth,data.email);}catch(error){if(error.code!=='auth/user-not-found')throw error;}say(production?'If this address is registered, Google will send a password-reset message.':live?'If this address is registered, Google will send a password-reset message. Check the receiving inbox; delivery has not yet been confirmed.':'If this address is registered, a reset action is available in the emulator. No email was sent.');});
}
if(live&&!config.test_inboxes_configured){
 say('The Google email test is waiting for a controlled receiving inbox. A second address is needed only for email-change receipt testing. No real email has been requested.');
 document.querySelectorAll('input,select,button').forEach(node=>{node.disabled=true;});
}
