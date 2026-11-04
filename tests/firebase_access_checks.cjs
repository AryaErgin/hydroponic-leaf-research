// Actual source handlers in a simulated DOM; no credentials, mail or network.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'../src/firebase_preview/static/app.js'),'utf8').replace(/^import[\s\S]*?from '\.\/firebase-sdk\.js';\s*/,'');
const origin='https://hydroponic-research-242618885776.us-central1.run.app';
async function boot(user,pathname='/signin',signinError=null,search=''){
 const nodes=new Map(),calls=[];
 function node(key){if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',disabled:false,classList:{toggle(){}},addEventListener(k,f){this[k]=f;},querySelector(){return node('button');},append(){}});return nodes.get(key);}
 const location={origin,protocol:'https:',hostname:new URL(origin).hostname,pathname,search};
 const context=vm.createContext({URLSearchParams,location,console,document:{querySelector:node,querySelectorAll:()=>[],createElement:()=>({})},
  initializeApp:()=>({}),getAuth:()=>({}),setPersistence:async()=>{},inMemoryPersistence:{},signOut:async()=>{},
  signInWithEmailAndPassword:async()=>{if(signinError)throw {code:signinError};return {user:{emailVerified:Boolean(user.email_verified_at),getIdToken:async()=> 'synthetic-token'}};},
  sendEmailVerification:async()=>calls.push('verification-requested'),
  fetch:async(url)=>({ok:true,json:async()=>url==='/api/firebase/config'?{identity_mode:'firebase_production',local_only:false,origin}:{user,csrf_token:'synthetic-csrf'}}),
  FormData:class{*[Symbol.iterator](){yield ['email','synthetic@example.test'];yield ['password','Synthetic fixture passphrase 47!'];}}});
 await new vm.Script('(async()=>{'+code+'})()').runInContext(context);
 return {nodes,calls,location,submit:async key=>node(key).submit({preventDefault(){},currentTarget:node(key)})};
}
(async()=>{
 const approved={id:'fixture',username:'professor',email:'synthetic@example.test',status:'ACTIVE',role:'ORG_ADMIN',email_verified_at:null};
 const login=await boot(approved);await login.submit('#signin');assert.equal(login.location.href,'/verify-email');
 const verify=await boot(approved,'/verify-email');assert.match(verify.nodes.get('#account-state').textContent,/already approved/i);
 assert.match(verify.nodes.get('#content').innerHTML,/Send verification email/);assert.equal(verify.calls.length,0);
 await verify.submit('#verify-email-form');assert.deepEqual(verify.calls,['verification-requested']);
 const active=await boot({...approved,email_verified_at:'synthetic-verified'});await active.submit('#signin');assert.equal(active.location.href,'/institution');
 const oldLink=await boot({...approved,email_verified_at:'synthetic-verified'},'/signin',null,'?verify=1');
 await oldLink.submit('#signin');assert.equal(oldLink.location.href,'/institution');assert.equal(oldLink.calls.length,0);
 const unverifiedLink=await boot(approved,'/signin',null,'?verify=1');await unverifiedLink.submit('#signin');
 assert.deepEqual(unverifiedLink.calls,['verification-requested']);assert.doesNotMatch(unverifiedLink.nodes.get('#message').textContent,/approval.*required/i);
 const pending=await boot({...approved,status:'PENDING',role:'PENDING',email_verified_at:'synthetic-verified'});await pending.submit('#signin');assert.equal(pending.location.href,'/pending');
 const blocked=await boot(approved,'/signin','auth/too-many-requests');await blocked.submit('#signin');
 assert.match(blocked.nodes.get('#message').textContent,/Google/);assert.match(blocked.nodes.get('#message').textContent,/Forgot password/i);
 console.log(JSON.stringify({passed:true,checks:12,scope:'Verified routing, stale verification URL, approved-unverified explanation, explicit verification and provider throttle message'}));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
