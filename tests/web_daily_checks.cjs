const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/webapp/static/app.js','utf8');
let savingApproval=false,mergeSelections=new Set(),approvalState;
let selected={id:'plant',calibration:{id:'reviewed'}},file={},analysis={id:'old',profile_id:'plant',date:'2026-10-08',status:'DRAFT',geometry_unchanged:true,assets:{original:'/original'},original_filename:'lettuce.jpeg',candidate_model:{requested_engine:'auto'}},working=false,editor={drawing:false,payload(){return{width:2,height:2,mask_base64:'AQABAQ=='};}};
const ui={};const $=id=>ui[id]||(ui[id]={checked:true,value:'2026-10-09',disabled:false});
const calls=[];
function say(){}async function pending(){}
async function api(url,options){if(url==='/original')return{blob:async()=>new Blob(['original'],{type:'image/jpeg'})};calls.push({url,date:options.body.get('date'),geometry:options.body.get('geometry_unchanged')});return{json:async()=>({id:'new'})};}
async function json(url,payload){calls.push({url,payload});return{...analysis,id:'new',date:'2026-10-09',status:'DRAFT'};}
async function showAnalysis(data){analysis=data;$('review-confirm').checked=false;}
editor.stats=()=>({total:3,ids:[1],unassigned:0,invalid:0});
eval(source.slice(source.indexOf('function currentApprovalState(){'),source.indexOf('function calibrationStatus(){')));
eval(source.slice(source.indexOf('async function updateCaptureDate(){'),source.indexOf('function resetReview(){')));
(async()=>{
 ({approvalState}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/review-state.js','utf8')).toString('base64')));
 buttons();assert.equal($('photo-date').disabled,false);assert.equal($('analyze').disabled,false);
 await updateCaptureDate();assert.equal(analysis.date,'2026-10-09');assert.equal(calls[0].date,'2026-10-09');
 assert.equal(calls[0].geometry,'true');assert.equal(calls.at(-1).payload.mask_base64,'AQABAQ==');
 assert.equal($('review-confirm').checked,false);assert.equal($('approve').disabled,true);
 assert.ok(calls.every(c=>!c.url.includes('/approve')));assert.equal(working,false);
 analysis.status='APPROVED';buttons();assert.equal($('photo-date').disabled,true);
 console.log('PASS: capture date remains editable before approval; native draft preserved; review reset; approved date locked');
})().catch(e=>{console.error(e);process.exitCode=1;});
