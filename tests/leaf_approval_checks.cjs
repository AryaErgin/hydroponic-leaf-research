const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2]||'src/webapp/static/app.js','utf8');
let working=false,savingApproval=false,selectionRevision=1,resumeId='a',approvalState,postMode='success',dirty=true,analysis={id:'a',profile_id:'p',date:'2026-10-14',status:'DRAFT',calibration:{cm_per_pixel:.1}},selected={id:'p',plant_id:'Synthetic'},requests=0,rendered=null,bodyDeadline,deadlineCleared=false;
const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('async function json('));
const boundedApi=new Function('fetch','setTimeout','clearTimeout','return '+apiSource)(async()=>new Response(new ReadableStream({start(){}}),{status:201}),fn=>{bodyDeadline=fn;return 1;},()=>{deadlineCleared=true;});
const nodes={'review-confirm':{checked:true},'review-card':{hidden:false},'result-card':{hidden:true,scrollIntoView(){}}};
const $=id=>nodes[id]||(nodes[id]={});
const reviewKeys=['visible_target_only'];
const editor={drawing:false,editable:true,payload(){return {width:2,height:2,mask_base64:'AQEAAA==',instance_base64:'AQACAAAAAAA=',instance_analysis_id:'a'};},stats(){return {total:2,ids:[1,2],unassigned:0,invalid:0};}};
const window={confirm(){return true;}};function plantName(){return 'lettuce';}function buttons(){}function userMessage(e){return e?.message||String(e);}
function say(){}function step(){}async function history(){}async function pending(){}function renderApprovedLeaves(m){rendered=m;}
function measurement(){return {id:'m',pixel_area:2,projected_area_cm2:.02,date:'2026-10-14',plant_id:'Synthetic',correction_required:false,leaf_count:2};}
async function api(url){return {json:async()=>url.includes('/measurements/')?measurement():{status:postMode==='late'?'APPROVED':'DRAFT',measurement_id:'m'}};}
async function json(url,body){requests++;assert.equal(url,'/api/analyses/a/approve');assert.equal(body.approval,'APPROVE');assert.equal(body.instances_reviewed,true);assert.equal(body.instance_analysis_id,'a');assert.equal(body.instance_base64,'AQACAAAAAAA=');if(postMode==='fail')throw Error('Synthetic API failure');if(postMode==='late')throw Error('Response lost after commit');if(postMode==='stalled')return(await boundedApi(url)).json();return measurement();}
const start=source.indexOf('async function approve(){'),end=source.indexOf('function renderLeafStats(){',start);
eval(source.slice(start,end<0?source.indexOf('function formatArea(',start):end));
eval(source.slice(source.indexOf('function currentApprovalState(){'),source.indexOf('function buttons(){')));
(async()=>{
 ({approvalState}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/review-state.js','utf8')).toString('base64')));
 const first=approve();await approve();await first;assert.equal(requests,1);assert.equal(analysis.status,'APPROVED');assert.equal(editor.editable,false);assert.equal(nodes['review-card'].hidden,true);assert.equal(nodes['result-card'].hidden,false);assert.equal(rendered.leaf_count,2);assert.equal(dirty,false);assert.equal(savingApproval,false);
 analysis.status='DRAFT';editor.stats=()=>({unassigned:1});await approve();assert.equal(requests,1);
 editor.stats=()=>({total:2,ids:[1,2],unassigned:0});selected.id='other-plant';await approve();assert.equal(requests,1);
 selected.id='p';postMode='fail';await approve();assert.equal(requests,2);assert.equal(analysis.status,'DRAFT');assert.equal(nodes['approval-error'].textContent,'Synthetic API failure');assert.equal(working,false);assert.equal(savingApproval,false);assert.equal(currentApprovalState().enabled,true);
 postMode='stalled';const stalled=approve();await Promise.resolve();await Promise.resolve();assert.equal(savingApproval,true);assert.equal(deadlineCleared,false,'Timeout must cover the body after headers arrive');bodyDeadline();await stalled;assert.equal(requests,3);assert.match(nodes['approval-error'].textContent,/timed out/);assert.equal(working,false);assert.equal(savingApproval,false);assert.equal(currentApprovalState().enabled,true);
 postMode='late';await approve();assert.equal(requests,4);assert.equal(analysis.status,'APPROVED');assert.equal(analysis.measurement_id,'m');assert.equal(working,false);
 console.log('PASS: actual professor APPROVE handler sends reviewed labels, displays final result and rejects unassigned/cross-plant edits');
})().catch(e=>{console.error(e);process.exitCode=1;});
