// Browser-code contract only: no real mail, credentials or remote calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'../src/firebase_preview/static/app.js'),'utf8').replace(/^import[\s\S]*?from '\.\/firebase-sdk\.js';\s*/,'');
async function boot(live,configured){
 const calls=[],nodes=new Map();
 function node(selector){if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',disabled:false,classList:{toggle(){}},addEventListener(name,fn){this[name]=fn;},querySelector(){return node('button');},append(){}});return nodes.get(selector);}
 const config=live?{local_only:true,identity_mode:'google_live_test',projectId:'hydroponic-leaf-research-aryae',test_inboxes_configured:configured}:{local_only:true,emulator:'http://127.0.0.1:9099',identity_mode:'emulator'};
 const context=vm.createContext({console,URLSearchParams,location:{hostname:'localhost',pathname:'/signup',search:''},
  document:{querySelector:node,querySelectorAll:()=>[node('input'),node('button')],createElement:()=>({})},
  initializeApp:()=>({}),getAuth:()=>({}),connectAuthEmulator:()=>calls.push('emulator'),setPersistence:async()=>{},inMemoryPersistence:{},signOut:async()=>{},
  createUserWithEmailAndPassword:async()=>{calls.push('create');return {user:{getIdToken:async()=> 'unit-token'}};},
  sendEmailVerification:async()=>calls.push('send'),
  fetch:async(url,options)=>{calls.push(url);let value={};let ok=true;
   if(url==='/api/firebase/config')value=config;
   if(url==='/api/session')value={user:null,csrf_token:'unit-csrf'};
   if(url==='/api/signup-institutions')value={organizations:[]};
   if(url==='/api/firebase/test-intent'){ok=false;value={detail:'Controlled inbox required'};}
   return {ok,json:async()=>value};
  },FormData:class{*[Symbol.iterator](){yield ['email','not-controlled@example.test'];yield ['password','not-a-real-credential'];yield ['username','test'];yield ['requested_org_id','org-bau'];}}});
 await new vm.Script('(async()=>{'+code+'})()').runInContext(context);
 return {calls,nodes,submit:()=>nodes.get('#signup').submit({preventDefault(){},currentTarget:nodes.get('#signup')})};
}
(async()=>{
 const pending=await boot(true,false);assert(!pending.calls.includes('emulator'));
 assert.equal(pending.nodes.get('button').disabled,true);assert.match(pending.nodes.get('#message').textContent,/receiving inbox/i);
 const live=await boot(true,true);await live.submit();
 assert(live.calls.includes('/api/firebase/test-intent'));assert(!live.calls.includes('create'));assert(!live.calls.includes('send'));
 const emulator=await boot(false,true);assert(emulator.calls.includes('emulator'));
 console.log(JSON.stringify({passed:true,checks:['no emulator in live mode','unconfigured inbox UI disabled','inbox intent precedes identity creation','blocked intent sends no email','emulator mode retained']}));
})().catch(()=>{console.error('Firebase live UI safety contract failed');process.exitCode=1;});
