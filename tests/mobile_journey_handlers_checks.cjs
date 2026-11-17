// Invoke actual current production handlers; API/DOM are controlled boundaries.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {File}=require('node:buffer');
const source=fs.readFileSync('src/webapp/static/app.js','utf8');
function slice(start,end){const a=source.indexOf(start),b=source.indexOf(end,a+start.length);assert(a>=0&&b>a,start);return source.slice(a,b);}
const handlerSource=[
 slice('function currentApprovalState(', 'function buttons('),
 slice('function cameraKey(', 'function calibrationStatus('),
 slice('async function chooseFile(', 'async function analyze('),
 slice('async function analyze(', 'async function showAnalysis('),
 slice('async function draft(', 'async function approve('),
 slice('function potChanged(', 'async function openSetup('),
 slice('async function openSetup(', "$('setup-open').onclick="),
 slice('async function changePhotoGeometry(', "$('geometry').onchange=changePhotoGeometry"),
].join('\n');
let policy={};
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject};}
function element(){return{disabled:false,hidden:false,checked:false,value:'',textContent:'',children:[],classList:{toggle(){},add(){},remove(){}},replaceChildren(...nodes){this.children=nodes;this.lastChild=nodes.at(-1);},append(...nodes){this.children.push(...nodes);this.lastChild=nodes.at(-1);},scrollIntoView(){},showModal(){this.open=true;},close(){this.open=false;},setAttribute(){}};}
function fixture(){
 const ui={},calls=[],messages=[],storage=new Map();
 const env={...policy,selected:{id:'p',species:'lettuce',display_name:'Plant P',calibration:{id:'old',pot_diameter_cm:5.82}},profiles:[],file:new File(['old'],'old.png',{type:'image/png'}),analysis:null,editor:null,savingApproval:false,
  showPhotoDiagnostic(){},setPhase(){},renderGuidance(){},workflowPhase:'',approvalConflict:'',calibrationDeferred:false,calibrationSaved:false,reviewVisited:false,working:false,dirty:false,setupMode:'new',setupTarget:null,setupCalibration:null,setupRequestId:'plant-request',setupSaving:false,calibrationSaving:false,selectionRevision:1,
  photoSerial:0,calibrationSerial:0,pendingPhoto:null,imageProvenance:null,uploadRequestId:'upload-request',uploadedReview:null,config:{max_upload_bytes:100000},
  potPicker:{file:new File(['calibration'],'calibration.png',{type:'image/png'}),image:{},points:[[10,10],[90,10]],distance(){return 80;},async load(file){this.file=file;this.points=[];},fit(){}},
  window:{confirm:()=>true},$:id=>ui[id]||(ui[id]=element()),document:{createElement:element},File,Blob,FormData,crypto:require('node:crypto').webcrypto,requestAnimationFrame:fn=>fn(),
  localStorage:{removeItem:k=>storage.delete(k),setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)??null},userMessage:e=>String(e.message||e),buttons(){},calibrationStatus(){},renderLeafStats(){},step(){},confirmDiscard:()=>true,
  say:(...args)=>messages.push(args),refreshProfiles:async()=>{},pending:async()=>{},showAnalysis:async data=>{env.analysis=data;},
  api:async(url,options)=>{calls.push({url,options});throw Error('Unexpected API request '+url);},json:async()=>{throw Error('Unexpected JSON request');},
 };
 const handlers=new Function('env','with(env){'+handlerSource+';return{chooseFile,analyze,potChanged,potButtons,openSetup,startCalibration,savePot,saveSetup,changePhotoGeometry,currentApprovalState,draft,cameraActive};}')(env);
 env.profiles=[env.selected];env.setupTarget=env.selected;env.$('geometry').checked=true;env.$('pot-diameter').value='5.82';env.$('photo-date').value='2026-10-27';env.$('plant-id').value='Plant P';env.$('species').value='lettuce';
 return{env,ui,calls,messages,...handlers};
}
function prepared(name){return{blob:async()=>new Blob([name],{type:'image/png'}),headers:new Headers({'X-Image-Provenance':JSON.stringify({original_filename:name})})};}
function savedCalibration(){return{plant:{id:'p',display_name:'Plant P',calibration:{id:'new'}},analysis:{calibration:{id:'new'},calibration_revision:2,calibration_valid:true,effective_cm_per_pixel:.2,calibration_reuse:{status:'VERIFIED'}}};}
const results=[];
async function check(name,fn){try{await fn();results.push({name,status:'PASS'});}catch(e){results.push({name,status:'FAIL',detail:e.message});}}
(async()=>{
 policy=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/review-state.js','utf8')).toString('base64'));
 await check('Prepared photo survives calibration preview decode failure and can analyze pixels',async()=>{
  const f=fixture();f.env.selected.calibration=null;f.env.api=async()=>prepared('ready');f.env.potPicker.load=async()=>{throw Error('EncodingError');};
  await f.chooseFile(new File(['photo'],'photo.jpg',{type:'image/jpeg'}));
  assert(f.env.file);assert.equal(f.env.$('photo-retry').hidden,true);assert.match(f.env.$('pot-error').textContent,/scale|calibration/i);assert.match(f.env.$('photo-progress').textContent,/Ready to analyze/);
 });
 await check('Preparation failure clears previous review and offers actual reason and same-file retry',async()=>{
  const f=fixture();const photo=new File(['photo'],'photo.jpg',{type:'image/jpeg'});f.env.analysis={id:'old'};f.env.api=async()=>{throw Error('Photo format is unsupported');};
  await f.chooseFile(photo);assert.equal(f.env.pendingPhoto,photo);assert.equal(f.env.analysis,null);assert.equal(f.env.$('review-card').hidden,true);assert.equal(f.env.$('photo-retry').hidden,false);assert.match(f.env.$('photo-progress').textContent,/unsupported/);assert.equal(f.env.working,false);
 });
 await check('Adding plant keeps prepared frame/provenance without preparing original twice',async()=>{
  const f=fixture(),original=new File(['original'],'raw.heic',{type:'image/heic'}),ready=new File(['ready'],'ready.png',{type:'image/png'}),provenance={normalized_sha256:'bound-frame'};
  f.env.selected=null;f.env.setupMode='new';f.env.analysis=null;f.env.pendingPhoto=original;f.env.file=ready;f.env.imageProvenance=provenance;
  f.env.json=async()=>({id:'new'});f.env.refreshProfiles=async()=>{f.env.selected={id:'new',display_name:'New',calibration:null};f.env.file=null;f.env.pendingPhoto=null;f.env.imageProvenance=null;};
  await f.saveSetup({preventDefault(){}});assert.equal(f.calls.length,0);assert.equal(f.env.file,ready);assert.equal(f.env.imageProvenance,provenance);assert.equal(f.env.pendingPhoto,original);assert.equal(f.env.potPicker.file,ready);
 });
 await check('Species change keeps photo but invalidates previous species review and confirmation',async()=>{
  const f=fixture(),photo=f.env.file;f.env.setupMode='edit';f.env.analysis={id:'a',profile_id:'p',species:'lettuce'};f.env.dirty=true;f.env.$('species').value='rumex_acetosa';f.env.$('review-confirm').checked=true;f.env.$('plant').options=[{value:'p'}];f.env.json=async()=>({...f.env.selected,species:'rumex_acetosa'});
  await f.saveSetup({preventDefault(){}});assert.equal(f.env.file,photo);assert.equal(f.env.analysis,null);assert.equal(f.env.$('review-confirm').checked,false);assert.equal(f.env.$('review-card').hidden,true);assert.equal(f.env.selected.species,'rumex_acetosa');
 });
 await check('Initial empty selection calibration save returns safely',async()=>{const f=fixture();f.env.setupTarget=null;f.env.selected=null;await f.savePot();assert.equal(f.calls.length,0);});
 await check('Plant creation double submit blocked; lost response retries same request identity',async()=>{
  const f=fixture(),d=deferred(),requests=[];f.env.setupMode='new';f.env.json=async(url,payload)=>{requests.push({url,payload});return requests.length===1?d.promise:{id:'p'};};
  const first=f.saveSetup({preventDefault(){}});await f.saveSetup({preventDefault(){}});assert.equal(requests.length,1);d.reject(Error('lost response after commit'));await first;
  assert.equal(f.env.setupRequestId,'plant-request');assert.equal(f.env.setupSaving,false);await f.saveSetup({preventDefault(){}});
  assert.equal(requests.length,2);assert.equal(requests[0].payload.request_id,requests[1].payload.request_id);assert.equal(requests[1].payload.calibration_choice,'none');
 });
 await check('Calibration failure retains file, exact points, diameter; double save blocked and retry succeeds',async()=>{
  const f=fixture(),d=deferred(),requests=[],points=f.env.potPicker.points,file=f.env.potPicker.file;
  f.env.api=async(url,options)=>{requests.push({url,options});return requests.length===1?d.promise:{json:async()=>savedCalibration()};};
  const first=f.savePot();await f.savePot();assert.equal(requests.length,1);d.reject(Error('offline'));await first;
  assert.equal(f.env.potPicker.points,points);assert.equal(f.env.potPicker.file,file);assert.equal(f.env.$('pot-diameter').value,'5.82');assert.equal(f.env.calibrationSaving,false);assert.equal(f.env.working,false);
  assert.equal(f.env.$('pot-save').disabled,false);await f.savePot();assert.equal(requests.length,2);assert.equal(requests[1].url,'/api/plants/p/calibration/two-point');assert.equal(requests[0].options.body.get('points'),requests[1].options.body.get('points'));assert.equal(f.env.selected.calibration.id,'new');
 });
 await check('Photo selected before Add plant is retained and guided on the new plant',async()=>{
  const f=fixture(),photo=new File(['retained'],'before-add.png',{type:'image/png'});f.env.selected=null;f.env.setupMode='new';f.env.pendingPhoto=photo;f.env.analysis=null;
  f.env.json=async()=>({id:'new',display_name:'New plant',calibration:null});
  f.env.refreshProfiles=async()=>{f.env.selected={id:'new',display_name:'New plant',calibration:null};f.env.profiles=[f.env.selected];f.env.file=null;f.env.pendingPhoto=null;f.env.selectionRevision++;};
  f.env.api=async()=>prepared('retained');await f.saveSetup({preventDefault(){}});
  assert.equal(f.env.file?.name,'before-add.png');assert.equal(f.env.pendingPhoto,photo);assert.equal(f.env.potPicker.file,f.env.file);assert.equal(f.env.$('calibration-card').hidden,false);
 });
 await check('Unfinished point drag cannot be saved as completed calibration',async()=>{
  const f=fixture();f.env.potPicker.pointDraft={at:1,before:[[10,10],[90,10]]};await f.savePot();assert.equal(f.calls.length,0);
 });
 await check('Active edited review calibration binds correct image and preserves editor/undo arrays',async()=>{
  const f=fixture();f.env.analysis={id:'review',profile_id:'p',status:'DRAFT',image_sha256:'sha',mask_base64:'unsaved-parent',instance_base64:'labels',calibration_revision:1};
  const editor={pixels:new Uint8Array([1,0,1]),labels:new Uint16Array([1,0,2]),history:[{label:'undo'}],future:[{label:'redo'}]};f.env.editor=editor;const refs={...editor};let form;
  f.env.api=async(url,options)=>{assert.equal(url,'/api/plants/p/calibration/two-point');form=options.body;return{json:async()=>savedCalibration()};};await f.savePot();
  assert.equal(form.get('analysis_id'),'review');assert.equal(form.get('image_sha256'),'sha');for(const k of Object.keys(refs))assert.equal(editor[k],refs[k]);assert.equal(f.env.analysis.mask_base64,'unsaved-parent');assert.equal(f.env.analysis.instance_base64,'labels');assert.equal(f.env.analysis.calibration_revision,2);assert.equal(f.env.$('review-confirm').checked,false);
 });
 await check('Calibration after failed analysis creates a fresh upload with newly saved scale',async()=>{
  const f=fixture();f.env.uploadedReview={id:'uploaded',profile_id:'p',status:'UPLOADED',image_sha256:'sha',calibration:{id:'old'}};let form,upload;
  f.env.api=async(url,options)=>{assert.equal(url,'/api/plants/p/calibration/two-point');form=options.body;return{json:async()=>({...savedCalibration(),analysis:null})};};await f.savePot();
  assert.equal(form.get('analysis_id'),null,'Failed upload must not use editable-review calibration binding');assert.equal(form.get('image_sha256'),null);assert.equal(f.env.uploadedReview,null);assert.notEqual(f.env.uploadRequestId,'upload-request');
  f.env.api=async(url,options)=>{assert.equal(url,'/api/uploads');upload=options.body;return{json:async()=>({id:'new-upload',profile_id:'p',date:upload.get('date'),geometry_unchanged:true,calibration:f.env.selected.calibration})};};
  f.env.json=async()=>({...f.env.uploadedReview});await f.analyze();assert.equal(f.env.analysis.id,'new-upload');assert.equal(f.env.analysis.calibration.id,'new');assert.equal(upload.get('geometry_unchanged'),'true');
 });
 await check('Stale calibration response never replaces another selected plant',async()=>{
  const f=fixture(),d=deferred();f.env.api=()=>d.promise;const saving=f.savePot();f.env.selected={id:'q',calibration:{id:'q-cal'}};f.env.selectionRevision++;d.resolve({json:async()=>savedCalibration()});await saving;
  assert.equal(f.env.selected.id,'q');assert.equal(f.env.selected.calibration.id,'q-cal');assert.match(f.env.$('pot-error').textContent,/Select its plant/);
 });
 await check('First prepared photo opens inline calibration even while preparation is working',async()=>{
  const f=fixture();f.env.selected.calibration=null;f.env.selected.pot_diameter_cm=null;f.env.api=async()=>prepared('first');await f.chooseFile(new File(['first'],'first.heic',{type:'image/heic'}));
  assert.equal(f.env.$('calibration-card').hidden,false);assert.equal(f.env.potPicker.file,f.env.file);assert.equal(f.env.$('pot-diameter').value,'');assert.equal(f.env.working,false);
 });
 await check('Calibration source fetch cannot reopen stale photo after switching plants',async()=>{
  const f=fixture(),d=deferred();f.env.analysis={id:'old-review',profile_id:'p',status:'DRAFT',assets:{original:'/old-original'},original_filename:'old.png'};
  f.env.api=()=>d.promise;f.env.$('calibration-card').hidden=true;const opening=f.startCalibration();
  f.env.selected={id:'q',display_name:'Plant Q',pot_diameter_cm:12};f.env.analysis=null;f.env.file=null;f.env.selectionRevision++;
  d.resolve({blob:async()=>new Blob(['old-p-photo'],{type:'image/png'})});await opening;
  assert.equal(f.env.$('calibration-card').hidden,true,'Old plant photo reopened under new plant title/diameter');
 });
 await check('Calibration source fetch cannot replace a newer prepared photo on the same plant',async()=>{
  const f=fixture(),d=deferred();f.env.analysis={id:'old-review',profile_id:'p',status:'DRAFT',assets:{original:'/old-original'},original_filename:'old.png'};
  f.env.api=async(url)=>url==='/old-original'?d.promise:prepared('new');const opening=f.startCalibration();
  await f.chooseFile(new File(['new'],'new.png',{type:'image/png'}));await f.startCalibration();const latest=f.env.potPicker.file;
  d.resolve({blob:async()=>new Blob(['old-p-photo'],{type:'image/png'})});await opening;
  assert.equal(f.env.file.name,'new.png');assert.equal(f.env.potPicker.file,latest);assert.equal(f.env.potPicker.file.name,'new.png','Old same-plant image replaced current photo calibration');
 });
 await check('Calibration picker does not change points while save is locked',async()=>{
  const pickerSource=fs.readFileSync('src/webapp/static/pot-calibration.js','utf8').replace('export class PotCalibration','class PotCalibration');
  const Picker=new Function(pickerSource+';return PotCalibration;')();
  const canvas={parentElement:null,addEventListener(){},width:120,height:100,getBoundingClientRect:()=>({left:0,top:0,width:120,height:100}),getContext:()=>({drawImage(){},beginPath(){},arc(){},fill(){},stroke(){},moveTo(){},lineTo(){}})};
  const picker=new Picker(canvas,()=>{});picker.image={};picker.points=[[10,10]];picker.locked=true;picker.click({clientX:90,clientY:10});
  assert.deepEqual(picker.points,[[10,10]],'Canvas accepted point edits during in-flight calibration save');
 });
 await check('Out-of-order photo preparation keeps latest photo and provenance',async()=>{
  const f=fixture(),a=deferred(),b=deferred();let n=0;f.env.api=()=>++n===1?a.promise:b.promise;
  const pa=f.chooseFile(new File(['a'],'a.png',{type:'image/png'})),pb=f.chooseFile(new File(['b'],'b.png',{type:'image/png'}));
  b.resolve(prepared('b'));await pb;a.resolve(prepared('a'));await pa;assert.equal(f.env.file.name,'b.png');assert.equal(f.env.imageProvenance.original_filename,'b');
 });
 await check('Stale photo completion does not unlock interface while latest prepare remains pending',async()=>{
  const f=fixture(),a=deferred(),b=deferred();let n=0;f.env.api=()=>++n===1?a.promise:b.promise;
  const pa=f.chooseFile(new File(['a'],'a.png')),pb=f.chooseFile(new File(['b'],'b.png'));a.resolve(prepared('a'));await pa;const busy=f.env.working;b.resolve(prepared('b'));await pb;
  assert.equal(busy,true,'Older preparation cleared working while current photo is still preparing');
 });
 await check('Failed new preparation cannot analyze a previous photo under new selection',async()=>{
  const f=fixture();f.env.uploadedReview={id:'old-review'};f.env.api=async()=>{throw Error('decode failed');};await f.chooseFile(new File(['new'],'new.heic',{type:'image/heic'}));
  let analyzed=false;f.env.json=async()=>{analyzed=true;return{id:'old-review'};};await f.analyze();assert.equal(analyzed,false,'Previous retained file/review remained eligible after new photo failed preparation');
 });
 await check('Analysis retry reuses uploaded review and request identity',async()=>{
  const f=fixture();let uploads=0,analyses=0;f.env.api=async(url,options)=>{assert.equal(url,'/api/uploads');uploads++;assert.equal(options.body.get('request_id'),'upload-request');return{json:async()=>({id:'uploaded',profile_id:'p',date:options.body.get('date'),geometry_unchanged:options.body.get('geometry_unchanged')==='true'})};};
  f.env.json=async(url)=>{assert.equal(url,'/api/analyses/uploaded/analyze');analyses++;if(analyses===1)throw Error('analysis transport failure');return{id:'uploaded'};};
  await f.analyze();assert.equal(f.env.uploadedReview.id,'uploaded');await f.analyze();assert.equal(uploads,1);assert.equal(analyses,2);
 });
 await check('Lost upload response retries same upload identity and original selected file',async()=>{
  const f=fixture(),uploads=[],file=f.env.file;f.env.api=async(url,options)=>{uploads.push(options.body);if(uploads.length===1)throw Error('lost upload response');return{json:async()=>({id:'same-upload',profile_id:'p',date:options.body.get('date')})};};f.env.json=async()=>({id:'same-upload'});
  await f.analyze();await f.analyze();assert.equal(uploads.length,2);assert.equal(uploads[0].get('request_id'),uploads[1].get('request_id'));assert.equal(uploads[0].get('profile_id'),uploads[1].get('profile_id'));assert.equal(uploads[0].get('date'),uploads[1].get('date'));assert.equal(f.env.file,file);
 });
 for(const responseLost of [false,true])await check('Changed camera setup after '+(responseLost?'lost upload response':'failed analysis')+' cannot reuse trusted upload scale',async()=>{
  const f=fixture(),uploads=[];let attempts=0;
  f.env.api=async(url,options)=>{assert.equal(url,'/api/uploads');const form=options.body;uploads.push({request:form.get('request_id'),geometry:form.get('geometry_unchanged')});if(responseLost&&uploads.length===1)throw Error('lost response');return{json:async()=>({id:'upload-'+uploads.length,profile_id:'p',date:form.get('date'),geometry_unchanged:form.get('geometry_unchanged')==='true'})};};
  f.env.json=async()=>{if(!responseLost&&++attempts===1)throw Error('transient analysis failure');const a=f.env.uploadedReview;return{...a,calibration_reuse:{status:a.geometry_unchanged?'VERIFIED':'RECALIBRATION_REQUIRED'}};};
  await f.analyze();f.env.$('geometry').checked=false;await f.changePhotoGeometry();await f.analyze();
  assert.equal(uploads.length,2);assert.notEqual(uploads[0].request,uploads[1].request);assert.equal(uploads[1].geometry,'false');assert.equal(f.env.analysis.geometry_unchanged,false);assert.equal(f.env.analysis.calibration_reuse.status,'RECALIBRATION_REQUIRED');
 });
 await check('Failed camera-change save stays pixels-only and blocks approval after draft and reopen',async()=>{
  const f=fixture(),trusted={id:'a',profile_id:'p',status:'DRAFT',geometry_unchanged:true,calibration:{id:'old',cm_per_pixel:.1},effective_cm_per_pixel:.1,calibration_reuse:{status:'VERIFIED'}};
  f.env.analysis=structuredClone(trusted);f.env.editor={drawing:false,edits:[],payload:()=>({}),stats:()=>({total:1,ids:[1],unassigned:0,invalid:0})};
  f.env.$('review-confirm').checked=true;assert.equal(f.currentApprovalState().enabled,true);
  f.env.json=async(url)=>{if(url.endsWith('/geometry-changed'))throw Error('offline before commit');assert(url.endsWith('/draft'));return structuredClone(trusted);};
  f.env.$('geometry').checked=false;await f.changePhotoGeometry();await f.draft();
  assert.equal(f.env.analysis.geometry_unchanged,true,'Fixture must retain server scale after failed revoke');assert.equal(f.env.analysis.calibration_reuse.status,'VERIFIED');
  f.env.$('review-confirm').checked=true;assert.equal(f.currentApprovalState().enabled,false,'Draft response restored physical approval');assert.equal(policy.reviewScale(f.env.analysis,f.env.$('geometry').checked),null,'Draft response restored cm² preview');
  // Reopening derives the checkbox from the real persisted camera-warning policy.
  f.env.$('geometry').checked=f.cameraActive(f.env.selected);f.env.analysis=structuredClone(trusted);f.env.$('review-confirm').checked=true;
  assert.equal(f.env.$('geometry').checked,false);assert.equal(f.currentApprovalState().enabled,false,'Reopening discarded unsaved camera-change warning');assert.equal(policy.reviewScale(f.env.analysis,f.env.$('geometry').checked),null);
 });
 await check('Date changed after analysis failure never reuses upload from previous date',async()=>{
  const f=fixture(),uploads=[];let count=0;f.env.api=async(url,options)=>{uploads.push({date:options.body.get('date'),request:options.body.get('request_id')});return{json:async()=>({id:'uploaded-'+uploads.length,profile_id:'p',date:uploads.at(-1).date})};};
  f.env.json=async()=>{if(++count===1)throw Error('analysis failure');return{id:'review',date:uploads.at(-1).date};};
  await f.analyze();f.env.$('photo-date').value='2026-10-28';await f.analyze();
  assert.equal(f.env.analysis.date,'2026-10-28','Cached upload silently kept previous capture date');assert.equal(uploads.at(-1).date,'2026-10-28');
 });
 console.log(JSON.stringify({scope:'actual production handlers with controlled UI/API boundaries',results},null,2));
 if(results.some(r=>r.status==='FAIL'))process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});
