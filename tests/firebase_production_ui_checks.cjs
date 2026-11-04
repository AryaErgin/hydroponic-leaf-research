// Simulated browser contract only: no real network, credentials or email.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'../src/firebase_preview/static/app.js'),'utf8').replace(/^import[\s\S]*?from '\.\/firebase-sdk\.js';\s*/,'');
const origin='https://hydroponic-research-242618885776.us-central1.run.app';
async function boot(changes={},place={}){
 const calls=[],nodes=new Map();
 function node(selector){if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',disabled:false,classList:{toggle(){}},addEventListener(name,fn){this[name]=fn;},querySelector(){return node('button');},append(){}});return nodes.get(selector);}
 const config={local_only:false,identity_mode:'firebase_production',origin,...changes};
 const context=vm.createContext({console,URLSearchParams,location:{hostname:new URL(origin).hostname,origin,protocol:'https:',pathname:'/signin',search:'',...place},
  document:{querySelector:node,querySelectorAll:()=>[],createElement:()=>({})},
  initializeApp:()=>{calls.push('initialize');return {};},getAuth:()=>({}),connectAuthEmulator:()=>calls.push('emulator'),
  setPersistence:async()=>{},inMemoryPersistence:{},signOut:async()=>{},sendPasswordResetEmail:async()=>calls.push('reset'),
  fetch:async(url)=>{calls.push(url);return {ok:true,json:async()=>url==='/api/firebase/config'?config:{user:null,csrf_token:'synthetic-csrf'}};},
  FormData:class{*[Symbol.iterator](){yield ['email','synthetic@example.test'];}}});
 await new vm.Script('(async()=>{'+code+'})()').runInContext(context);
 return {calls,nodes,reset:()=>nodes.get('#reset').submit({preventDefault(){},currentTarget:nodes.get('#reset')})};
}
(async()=>{
 const production=await boot();assert(production.calls.includes('initialize'));assert(!production.calls.includes('emulator'));
 await production.reset();assert(production.calls.includes('reset'));assert(!production.calls.includes('/api/firebase/test-intent'));
 assert.match(production.nodes.get('#message').textContent,/Google will send/);
 for(const [config,location] of [[{local_only:true},{}],[{emulator:'http://127.0.0.1:9099'},{}],[{},{protocol:'http:'}],[{},{origin:'https://untrusted.example'}]]){
  await assert.rejects(boot(config,location),/Secure Firebase origin required/);
 }
 await assert.rejects(boot({identity_mode:'emulator',local_only:true}),/Local evaluation only/);
 console.log(JSON.stringify({passed:true,checks:7,scope:'Production bootstrap/origin and reset source handlers; simulated DOM only'}));
})().catch(()=>{console.error('Firebase production UI contract failed');process.exitCode=1;});
