// Exercise the shared-session boundary without replacing scientific editor tests.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const auth=fs.readFileSync('src/auth_preview/static/app.js','utf8');
const science=fs.readFileSync('src/webapp/static/app.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const nodes={},handlers={},assigned=[],pushed=[],requested=[];
 const element=()=>({hidden:false,innerHTML:'',textContent:'',setAttribute(){},removeAttribute(){},addEventListener(){}});
 const context={document:{body:{dataset:{integrated:'true'}},querySelector(s){return nodes[s]||(nodes[s]=element());},addEventListener(k,fn){handlers[k]=fn;}},window:{location:{assign:p=>assigned.push(p)},addEventListener(){},dispatchEvent(){}},location:{pathname:'/institution'},history:{pushState(_,__,p){pushed.push(p);context.location.pathname=p;}},fetch:async path=>{requested.push(path);if(path==='/api/session')return new Promise(()=>{});assert.equal(path,'/api/profiles','Real institution page must request the scientific profile API');return {ok:true,json:async()=>[{id:'real-profile',display_name:'University lettuce',species:'lettuce'}]};},Event:class {},console};
 vm.runInNewContext(auth,context);
 Object.assign(context.window.leafAuth.state,{integrated:true,csrf_token:'shared',organization:{name:'Verified University'},user:{id:'member',username:'member',status:'ACTIVE',role:'MEMBER',email:'old@example.test',email_verified_at:'2026-10-27',pending_email:'new@example.test'}});
 context.window.leafAuth.go('/analyze');
 assert.deepEqual(assigned,['/analyze']);assert.equal(pushed.length,0,'Science must load its own HTML and scripts');
 context.window.leafAuth.go('/institution');await flush();
 const institutionHTML=nodes['#main'].innerHTML+(nodes['#university-plant-list']?.innerHTML||'');assert(institutionHTML.includes('University lettuce'));assert(!institutionHTML.includes('Synthetic'));assert(!requested.some(p=>p.includes('/workspace/')));
 assert(nodes['#navigation'].innerHTML.includes('My university'),'Members can reach their institution');
 context.window.leafAuth.go('/profile');await flush();
 assert(nodes['#main'].innerHTML.includes('Change email address'));
 assert(nodes['#main'].innerHTML.includes('old@example.test'));assert(nodes['#main'].innerHTML.includes('new@example.test'));
 assert(nodes['#main'].innerHTML.includes('current address stays active'));
 assert(!auth.includes('id="complete-email"'),'Password recovery keeps the email entered at the first step');

 const body=science.slice(science.indexOf('async function api('),science.indexOf('async function json('));
 const originalSession=globalThis.leafScienceSession,originalLocation=globalThis.location;
 try{
  globalThis.leafScienceSession={integrated:true,csrf_token:'shared-session-token'};
  const calls=[],redirects=[];globalThis.location={assign:path=>redirects.push(path)};
  const api=new Function('fetch','setTimeout','clearTimeout','return '+body)(async(url,options)=>{calls.push({url,...options});return new Response('{}',{status:url.includes('expired')?401:200});},()=>1,()=>{});
  for(const [url,method] of [['/api/profiles','GET'],['/api/profiles','POST'],['/api/profiles/id','PATCH'],['/api/analyses/id/draft','POST'],['/api/images/prepare','POST'],['/api/uploads','POST'],['/api/plants/id/calibration/two-point','POST'],['/api/profiles/id','DELETE']]){
   const data=method==='GET'?undefined:new FormData();if(data)data.set('fixture','synthetic');
   await api(url,{method,body:data,headers:{'X-Existing':'kept'}});
   const call=calls.at(-1);assert.equal(call.headers.get('X-CSRF-Token'),method==='GET'?null:'shared-session-token');assert.equal(call.headers.get('X-Existing'),'kept');assert.equal(call.body,data);assert.equal(call.credentials,'same-origin');
  }
  let recoveryShown=0;globalThis.leafSessionRecovery=()=>recoveryShown++;await assert.rejects(api('/api/expired'),/session expired.*Keep this page open/i);assert.deepEqual(redirects,[],'Expired science requests must retain unsaved work in the current tab');assert.equal(recoveryShown,1);delete globalThis.leafSessionRecovery;
  // Standalone mode still works without an auth session or a CSRF token.
  delete globalThis.leafScienceSession;await api('/api/profiles');assert.equal(calls.at(-1).headers.has('X-CSRF-Token'),false);
 }finally{if(originalSession===undefined)delete globalThis.leafScienceSession;else globalThis.leafScienceSession=originalSession;if(originalLocation===undefined)delete globalThis.location;else globalThis.location=originalLocation;}

 const initSource=science.slice(science.indexOf('async function init(){'),science.lastIndexOf('\ninit();'));
 for(const [session,destination] of [[{authenticated:false},'/signin'],[{authenticated:true,user:{status:'PENDING'}},'/pending'],[{authenticated:true,user:{status:'ACTIVE',email_verified_at:null}},'/profile']]){
  const redirects=[],ui={};const sandbox={globalThis:{},location:{assign:p=>redirects.push(p)},document:{body:{dataset:{integrated:'true'}}},buttons(){},say(){},accountChrome(){},working:false,$:id=>ui[id]||(ui[id]={}),api:async url=>{assert.equal(url,'/api/session','Blocked account must not fetch scientific data');return {json:async()=>session};}};
  vm.runInNewContext(initSource+'\ninit();',sandbox);await flush();assert.deepEqual(redirects,[destination]);assert.equal(ui['login-panel'].hidden,true,'The legacy access-key panel must stay hidden');
 }
 console.log('PASS integrated frontend: full scientific navigation, real institution data, pending email, eight CSRF methods/uploads, expired-session retained-work recovery, three account gates, standalone compatibility');
})().catch(error=>{console.error(error);process.exitCode=1;});
