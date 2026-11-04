// Invoke the production handlers with a native unsaved editor state.
const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/webapp/static/app.js','utf8');
let selected={id:'p',calibration:{id:'c'}},selectionRevision=2,working=false;
let analysis={id:'a',profile_id:'p',status:'DRAFT',image_sha256:'image-sha',calibration:{id:'c',cm_per_pixel:.1},calibration_reuse:{status:'CONFIRMATION_REQUIRED'},effective_cm_per_pixel:null};
const editor={drawing:false,pixels:new Uint8Array([1,1,0]),labels:new Uint16Array([1,2,0]),history:[{identity:'unchanged'}],future:[{identity:'redo'}]};
const preserved={pixels:editor.pixels,labels:editor.labels,history:editor.history,future:editor.future};
const ui={};const $=id=>ui[id]||(ui[id]={checked:true,textContent:''});
const calls=[];let fail=false;
async function json(url,payload){calls.push({url,payload});if(fail)throw Error('offline');return{calibration_revision:3,calibration_reuse:{status:url.endsWith('geometry-changed')?'RECALIBRATION_REQUIRED':'CONFIRMED',reason:'reviewed'},effective_cm_per_pixel:.2,calibration_valid:true,calibration_issue:''};}
function buttons(){}function calibrationStatus(){}function renderLeafStats(){}function say(){}function cameraKey(){return'camera';}const localStorage={setItem(){}};
eval(source.slice(source.indexOf('async function confirmPhotoCalibration('),source.indexOf('async function updateCaptureDate(')));
eval(source.slice(source.indexOf('async function changePhotoGeometry('),source.indexOf("$('geometry').onchange=changePhotoGeometry")));
(async()=>{
 const savedAnalysis=analysis,savedSelected=selected;
 eval(source.slice(source.indexOf('function renderCalibrationReview('),source.indexOf('async function confirmPhotoCalibration(')));
 analysis=null;selected=null;
 assert.doesNotThrow(()=>renderCalibrationReview(),'Initial empty plant selection must not dereference a null review');
 assert.equal($('photo-calibration').hidden,true);
 analysis=savedAnalysis;selected=savedSelected;
 await confirmPhotoCalibration();assert.equal(working,false);assert.equal(analysis.calibration_revision,3);assert.equal(analysis.effective_cm_per_pixel,.2);assert.equal($('review-confirm').checked,false);
 assert.equal(calls[0].payload.image_sha256,'image-sha');assert.equal(calls[0].payload.calibration_id,'c');
 for(const key of Object.keys(preserved))assert.equal(editor[key],preserved[key],key+' was reset');
 $('geometry').checked=false;await changePhotoGeometry();assert.equal(analysis.effective_cm_per_pixel,null);assert.equal(analysis.geometry_unchanged,false);assert.equal(analysis.calibration_reuse.status,'RECALIBRATION_REQUIRED');assert.match(calls[1].url,/geometry-changed$/);
 analysis.effective_cm_per_pixel=.2;analysis.geometry_unchanged=true;fail=true;await changePhotoGeometry();assert.equal(analysis.effective_cm_per_pixel,null);assert.equal(working,false);
 for(const key of Object.keys(preserved))assert.equal(editor[key],preserved[key]);
 assert.match(source,/calibration_revision:analysis.calibration_revision\|\|0/,'Approval must bind the reviewed scale revision');
 console.log('PASS actual confirmation/geometry handlers: image-bound claims, unsaved mask/identity/history preservation, scale revocation, failure recovery');
})().catch(error=>{console.error(error);process.exitCode=1;});
