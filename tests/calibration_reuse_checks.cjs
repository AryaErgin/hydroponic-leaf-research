const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const source=fs.readFileSync('src/webapp/static/review-state.js','utf8');
 const {reviewScale,calibrationIssue,approvalState}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
 const a={status:'DRAFT',profile_id:'p',geometry_unchanged:true,image_size_px:[1536,2048],calibration:{reference_size_px:[1200,1600],cm_per_pixel:.008},effective_cm_per_pixel:.008/1.28,
 calibration_reuse:{status:'VERIFIED',reason:'Verified image geometry'}};
 assert.equal(calibrationIssue(a),'');assert.equal(reviewScale(a),(.008/1.28)**2);
 const s={analysis:a,selected:{id:'p'},stats:{total:200,ids:[1],unassigned:0,invalid:0},confirmed:true};
 assert.equal(approvalState(s).enabled,true);
 assert.equal(approvalState({...s,geometryUnchanged:false}).enabled,false,'An unsaved camera-change warning must survive a draft response');
 assert.equal(reviewScale(a,false),null,'An explicitly changed setup must never show physical preview areas');
 for(const status of ['CONFIRMATION_REQUIRED','RECALIBRATION_REQUIRED']){a.calibration_reuse={status,reason:'Geometry uncertain'};assert.equal(reviewScale(a),null);assert.match(approvalState(s).reason,/pot edges|scale needs checking/);}
 a.calibration_reuse.status='CONFIRMED';assert.equal(approvalState(s).enabled,true);
 const app=fs.readFileSync('src/webapp/static/app.js','utf8'),section=app.slice(app.indexOf('async function confirmPhotoCalibration'),app.indexOf('async function updateCaptureDate'));
 assert.match(section,/image_sha256/);assert.match(section,/calibration_id/);assert.match(section,/same_physical_pot/);assert.match(section,/full_frame_resize_only/);
 assert.doesNotMatch(section,/showAnalysis|editor\.load|refreshProfiles/,'Confirmation must preserve unsaved native masks, identity archive and undo history');
 assert.match(app,/same physical pot/i);
 console.log('PASS transformed preview scale, strict approval states, image-bound confirmation preserves native editor');
})().catch(e=>{console.error(e);process.exitCode=1;});

