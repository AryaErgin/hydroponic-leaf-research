const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync(process.argv[2]||'src/webapp/static/app.js','utf8');
let savingApproval=false,mergeSelections=new Set(),approvalState,approvalConflict='',selectionRevision=1,uploadRequestId='original-request';
const calibration={id:'reviewed',cm_per_pixel:.1,reference_size_px:[2,2]};
let selected={id:'plant',calibration},file={synthetic:true},analysis={id:'old',profile_id:'plant',date:'2026-10-08',status:'DRAFT',geometry_unchanged:true,calibration,image_size_px:[2,2],assets:{original:'/original'},original_filename:'synthetic.jpeg',candidate_model:{requested_engine:'auto'},pipeline:{checkpoint_loaded:true}},working=false;
const native={width:2,height:2,mask_base64:'AQABAQ=='};
let editor={drawing:false,pixels:Uint8Array.from([1,0,1,1]),labels:Uint16Array.from([0,1,0,1]),history:[{mask:'prior native mask',ids:'prior leaf identities'}],future:[{mask:'redo native mask',ids:'redo leaf identities'}],revision:7,selectedLeaf:1,payload(){return structuredClone(native);},stats(){return{total:3,ids:[1],unassigned:0,invalid:0};}};
let uploadedReview={...analysis};const ui={};const $=id=>ui[id]||(ui[id]={checked:true,value:'2026-10-09',disabled:false});
const calls=[];let reloads=0,unexpectedApiCalls=0,failDate=false;
function say(){}function renderGuidance(){}async function pending(){}
async function api(){unexpectedApiCalls++;throw Error('Date correction must not fetch/reupload the original photo');}
async function json(url,payload){calls.push({url,payload});assert.equal(url,'/api/analyses/old/capture-date');if(failDate)throw Error('Synthetic date service interruption');return{...analysis,date:payload.date,capture_date_edits:[{from:payload.expected_date,to:payload.date}]};}
async function showAnalysis(){reloads++;throw Error('Date correction must not reload/rebind the native editor');}
eval(source.slice(source.indexOf('function currentApprovalState('),source.indexOf('function setPhase(')));
eval(source.slice(source.indexOf('async function updateCaptureDate(){'),source.indexOf('function resetReview(){')));
(async()=>{
 ({approvalState}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/review-state.js','utf8')).toString('base64')));
 const originalEditor=editor,originalFile=file,pixels=editor.pixels,labels=editor.labels,undo=editor.history,redo=editor.future,payloadBefore=structuredClone(editor.payload()),calibrationBefore=analysis.calibration,assetsBefore=analysis.assets,pipelineBefore=analysis.pipeline;
 buttons();assert.equal($('photo-date').disabled,false);assert.equal($('analyze').disabled,false);assert.equal($('approve').disabled,false);
 await updateCaptureDate();assert.equal(analysis.date,'2026-10-09');assert.equal(analysis.id,'old');assert.equal(analysis.profile_id,'plant');assert.equal(analysis.geometry_unchanged,true);
 assert.deepEqual(calls,[{url:'/api/analyses/old/capture-date',payload:{date:'2026-10-09',expected_date:'2026-10-08'}}]);
 assert.equal(editor,originalEditor);assert.equal(file,originalFile);assert.equal(editor.pixels,pixels);assert.equal(editor.labels,labels);assert.equal(editor.history,undo);assert.equal(editor.future,redo);assert.equal(editor.revision,7);assert.equal(editor.selectedLeaf,1);assert.deepEqual(editor.payload(),payloadBefore);
 assert.equal(analysis.calibration,calibrationBefore);assert.equal(analysis.assets,assetsBefore);assert.equal(analysis.pipeline,pipelineBefore);assert.equal(uploadedReview.id,'old');assert.equal(uploadedReview.date,'2026-10-09');assert.equal(uploadedReview.calibration,calibrationBefore);
 assert.equal(editor.payload().mask_base64,'AQABAQ==');assert.equal('instance_analysis_id' in editor.payload(),false,'Legacy payload remains legacy; date correction must not invent an identity binding');
 assert.equal($('review-confirm').checked,false);assert.equal($('approve').disabled,true);assert.equal(working,false);assert.equal(unexpectedApiCalls,0);assert.equal(reloads,0);assert.ok(calls.every(c=>!c.url.includes('/approve')&&!c.url.includes('/analyze')&&!c.url.includes('/draft')));
 $('review-confirm').checked=true;buttons();assert.equal($('approve').disabled,false,'Explicit review reconfirmation is still mandatory after correcting the date');
 // A recoverable failed correction restores the prior date and conflict without losing native work.
 const priorAnalysis=analysis,priorReview=uploadedReview;approvalConflict='Existing approved-date conflict';$('photo-date').value='2026-10-10';failDate=true;await updateCaptureDate();assert.equal(analysis,priorAnalysis);assert.equal(uploadedReview,priorReview);assert.equal(analysis.date,'2026-10-09');assert.equal($('photo-date').value,'2026-10-09');assert.equal(approvalConflict,'Existing approved-date conflict');assert.deepEqual(editor.payload(),payloadBefore);assert.equal(editor.history,undo);assert.equal(editor.future,redo);assert.equal(file,originalFile);assert.equal(working,false);
 analysis.status='APPROVED';buttons();assert.equal($('photo-date').disabled,true);const count=calls.length;await updateCaptureDate();assert.equal(calls.length,count);assert.equal(analysis.date,'2026-10-09');assert.equal(unexpectedApiCalls,0);assert.equal(reloads,0);
 console.log('PASS: same-analysis capture-date correction preserves exact native mask/identities/undo/calibration/photo, makes no upload/inference, requires reconfirmation, recovers failure and locks approved dates');
})().catch(e=>{console.error(e);process.exitCode=1;});
