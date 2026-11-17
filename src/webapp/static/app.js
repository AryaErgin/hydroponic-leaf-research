import {calibrationGuidance} from './calibration-guidance.js';
import {historyView} from './history-view.js';
import {photoDiagnostic} from './image-diagnostics.js';
import {workflowState} from './workflow-state.js';
import {MaskEditor,leafColor} from './mask-editor.js';
import {approvalState,median,graphSeries,calibrationIssue,reviewScale} from './review-state.js';
import {PotCalibration} from './pot-calibration.js';
import {userMessage} from './user-message.js';
const $=id=>document.getElementById(id);
let profiles=[],selected=null,file=null,analysis=null,editor=null,working=false,dirty=false,config={},calibrations=[],setupMode='new';
let savingApproval=false,mergeSelections=new Set(),historyMeasurements=[];
let selectionRevision=0,resumeId=null,setupDefault=null,potPicker=null,setupCalibration=null,setupTarget=null;
let setupRequestId=null,setupSaving=false,calibrationSaving=false,photoSerial=0,calibrationSerial=0,pendingPhoto=null,imageProvenance=null,uploadRequestId=null,uploadedReview=null;
let workflowPhase='',calibrationDeferred=false,calibrationSaved=false,reviewVisited=false,guidance=null,captureStep=null,approvalConflict='';
const reviewKeys=['visible_target_only','visible_tissue_complete','moss_excluded','growing_medium_excluded','dead_tissue_excluded','pot_and_background_excluded','scale_approximation_accepted'];
function say(text,error=false){$('status').textContent=error?userMessage(text):text;$('status').classList.toggle('error',error);if(error)$('debug-details').textContent=String(text);}
async function api(url,options={}){
 // A cold promoted-model analysis can exceed60s. Its deadline includes the
 // Cloud Run300s processing window plus transport overhead; approval stays60s.
 const analyzing=/^\/api\/analyses\/[^/?]+\/analyze(?:\?|$)/.test(url),timeoutMs=analyzing?330000:60000;
 const controller=new AbortController(),message=analyzing?'Analysis timed out after 330 seconds. Resume the unfinished review to check whether it completed.':'Request timed out after 60 seconds. A completed approval will be recovered; otherwise retry when the connection is available.';let timeout;
 const deadline=new Promise((_,reject)=>{timeout=setTimeout(()=>{controller.abort();reject(Error(message));},timeoutMs);});
 try{return await Promise.race([deadline,(async()=>{
  const session=globalThis.leafScienceSession,headers=new Headers(options.headers||{});
  // A second-tab sign-in rotates CSRF. Refresh only this non-mutating session
  // proof, retaining the selected file; never upload it under a different actor.
  if(url==='/api/images/prepare'&&session?.integrated&&session.user?.id){
   const latest=await(await api('/api/session')).json();
   if(!latest.authenticated||!latest.user){globalThis.leafSessionRecovery?.();throw Error('Your sign-in session expired. Open Sign in in another tab, then return here and try again; keep this page open to retain your photo.');}
   if(latest.user.id!==session.user.id||latest.user.org_id!==session.user.org_id)throw Error('The signed-in account or institution changed. Return to the original account in another tab before trying this photo again.');
   Object.assign(session,latest);
  }
  if(session?.csrf_token&&!['GET','HEAD','OPTIONS'].includes((options.method||'GET').toUpperCase()))headers.set('X-CSRF-Token',session.csrf_token);
  const response=await fetch(url,{...options,headers,credentials:'same-origin',signal:controller.signal});
  // Consume the network body inside the deadline; callers read only buffered JSON/blob data.
  const bytes=await response.arrayBuffer(),ready=new Response([204,205,304].includes(response.status)?null:bytes,{status:response.status,statusText:response.statusText,headers:response.headers});
  if(!ready.ok){let data={};try{data=await ready.json();}catch{}if(ready.status===401&&url==='/api/images/prepare')throw Error('Your sign-in session expired. Open Sign in in another tab, then return here and try again; keep this page open to retain your photo.');if(ready.status===401&&session?.integrated){globalThis.leafSessionRecovery?.();const expired=Error('Your sign-in session expired. Keep this page open to retain your photo and edits. Sign in in another tab, then reconnect here.');expired.status=401;throw expired;}const failed=Error(typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(d=>d.msg).join('; '):'Request failed: HTTP '+ready.status+' '+ready.statusText);failed.status=ready.status;if(typeof data.code==='string')failed.code=data.code;if(data.diagnostic&&typeof data.diagnostic==='object')failed.diagnostic=data.diagnostic;throw failed;}return ready;
 })()]);}catch(error){if(error.name==='AbortError')throw Error(message);throw error;}finally{clearTimeout(timeout);}
}
async function json(url,data,method='POST'){return (await api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})).json();}
function step(name){document.querySelectorAll('[data-step]').forEach(n=>n.classList.toggle('active',n.dataset.step===name));renderGuidance();}
function currentApprovalState(confirmed=$('review-confirm').checked){return approvalState({analysis,selected,stats:editor?.drawing?null:editor?.stats(),confirmed,working,saving:savingApproval,drawing:editor?.drawing,geometryUnchanged:$('geometry').checked});}
function confirmationIsOnlyBlocker(){return !$('review-confirm').checked&&!approvalConflict&&currentApprovalState(true).enabled;}
function buttons(){
 $('analyze').disabled=working||!selected||!file;
 const approval=currentApprovalState();$('approve').disabled=!approval.enabled||Boolean(approvalConflict);$('approval-reason').textContent=approvalConflict?'Approval blocked: a measurement already exists for this date.':approval.reason;$('approve').textContent=savingApproval?'Saving…':'Approve measurement';
 $('save-draft').disabled=working||!analysis||analysis.status==='APPROVED'||editor?.drawing;
 if(editor)editor.busy=working||analysis?.status==='APPROVED';
 for(const id of ['plant','photo','geometry','edit','review-confirm','setup-open','calibration-open','manage-open'])$(id).disabled=working||Boolean(editor?.drawing);
 $('review-recalibrate').disabled=working||Boolean(editor?.drawing);
 $('photo-date').disabled=working||analysis?.status==='APPROVED'||Boolean(editor?.drawing);
 for(const id of ['leaf-select','leaf-pick','leaf-split','leaf-merge','leaf-reassign','leaf-delete','leaf-add','leaf-undo','leaf-redo','leaf-pan','paint','erase','undo','clear','tool-done'])$(id).disabled=working||analysis?.status==='APPROVED'||Boolean(editor?.drawing);
 if(!editor?.future?.length)$('leaf-redo').disabled=true;
 renderGuidance();
 if(editor?.mode==='add')$('leaf-select').disabled=true;
 $('merge-confirm').disabled=working||editor?.mode!=='merge'||mergeSelections.size<2;
 $('confirm-photo-calibration').disabled=working||Boolean(editor?.drawing)||!$('confirm-photo-setup').checked;
}
function setPhase(value){workflowPhase=value;renderGuidance();}
function showPhotoDiagnostic(error,source,stage,started){
 if(!$('photo-diagnostic'))return;
 const report=photoDiagnostic(error,source,stage,Date.now()-started,config.max_upload_bytes);
 $('photo-diagnostic').hidden=false;if($('capture-details'))$('capture-details').open=true;$('photo-diagnostic-text').textContent=JSON.stringify(report,null,2);$('photo-diagnostic-code').textContent='Error code: '+report.code;
}
async function reconnectSession(){
 const button=$('session-reconnect');button.disabled=true;
 try{
  const latest=await(await api('/api/session')).json(),original=globalThis.leafScienceSession;
  if(!latest.authenticated||!latest.user)throw Error('Sign in in the other tab first, then reconnect here.');
  if(latest.user.id!==original?.user?.id||latest.user.org_id!==original?.user?.org_id||latest.user.status!=='ACTIVE')throw Error('Return to the original account and institution before reconnecting.');
  Object.assign(original,latest);$('session-recovery').hidden=true;buttons();say('Reconnected. Your photo and edits are still here. Continue with the action you were using.');
 }catch(error){$('session-recovery-status').textContent=error.message;}
 finally{button.disabled=false;}
}
function tipsKey(){const u=globalThis.leafScienceSession?.user;return u?.id?'hydroponic.guidance.v1:'+u.id+':'+(u.org_id||'local'):null;}
function syncTips(){let hidden=false;try{const key=tipsKey();hidden=key&&localStorage.getItem(key)==='hidden';}catch{}$('workspace').classList.toggle('tips-hidden',Boolean(hidden));if($('workflow-tips')){$('workflow-tips').textContent=hidden?'Show tips':'Hide tips';$('workflow-tips').setAttribute('aria-expanded',String(!hidden));$('workflow-tips').setAttribute('aria-pressed',String(!hidden));if(analysis&&editor?.labels&&!editor.drawing)renderLeafStats();}}
function renderGuidance(){
 if(!$('workflow-heading'))return;
 const stats=analysis&&editor&&!editor.drawing?editor.stats():null;
 guidance=workflowState({selected,file,analysis,stats,confirmed:$('review-confirm').checked,working,saving:savingApproval,drawing:editor?.drawing,geometryUnchanged:$('geometry').checked,calibrationOpen:!$('calibration-card').hidden,calibrationDeferred,calibrationSaved,reviewVisited,phase:workflowPhase,approvalConflict,resumeId,prepareFailed:!$('photo-retry').hidden});
 $('workspace').dataset.guidedStep=guidance.step;
 $('workflow-heading').textContent=guidance.title;$('workflow-position').textContent='Step '+guidance.index+' of 7';
 $('workflow-hint').textContent=guidance.instruction;
 const next=$('workflow-action');next.textContent=guidance.label;next.disabled=guidance.action==='wait';next.hidden=true;
 if(guidance.step==='plant')next.textContent=profiles.length?'Choose plant':'Add plant';
 if(guidance.step==='calibration'&&!$('calibration-card').hidden){const hint=calibrationGuidance({diameter:Number($('pot-diameter').value),distance:potPicker?.distance(),points:potPicker?.points.length||0,previewReady:Boolean(potPicker?.image),saving:calibrationSaving});next.textContent=hint.ready?'Save calibration':hint.target==='pot-diameter'?'Enter pot diameter':'Mark pot edges';$('workflow-hint').textContent=hint.message;}
 for(const n of document.querySelectorAll('[data-workflow-step]')){const w=guidance.steps.find(x=>x.key===n.dataset.workflowStep);n.classList.toggle('current',w.current);n.classList.toggle('complete',w.complete);n.classList.toggle('optional',w.optional);if(w.current)n.setAttribute('aria-current','step');else n.removeAttribute('aria-current');n.disabled=working||Boolean(editor?.drawing)||!w.available;n.setAttribute('aria-label',w.label+(w.current?', current step':w.complete?', complete':w.optional?', optional / checked per photo':''));}
 const phases={preparing:'Preparing image...',uploading:'Uploading prepared image...',analyzing:'Analyzing leaves. The service may need to start; the first run can take several minutes.', 'review-loading':'Preparing the colored leaf review...'};
 $('analysis-phase').textContent=phases[workflowPhase]||'';$('analysis-phase').hidden=!phases[workflowPhase];if(phases[workflowPhase]){$('workflow-heading').textContent={preparing:'Preparing your photo',uploading:'Uploading your photo',analyzing:'Analyzing the leaves','review-loading':'Opening your review'}[workflowPhase];$('workflow-hint').textContent=workflowPhase==='analyzing'?'Keep this page open. The first analysis can take a few minutes while the service starts.':'Your photo is kept while this finishes.';}
 for(const id of ['another-photo','view-growth','review-continue','view-original','view-overlay'])$(id).disabled=working||Boolean(editor?.drawing);
 const primary={plant:profiles.length?null:'setup-open',photo:guidance.action==='resume'?'resume':guidance.action==='retry'?'photo-retry':'choose-photo',calibration:calibrationGuidance({diameter:Number($('pot-diameter').value),distance:potPicker?.distance(),points:potPicker?.points.length||0,previewReady:Boolean(potPicker?.image),saving:calibrationSaving}).target==='pot-diameter'?'pot-fix':!$('pot-save').disabled?'pot-save':null,analyze:working?null:'analyze',review:'review-continue',approve:guidance.action==='approve'?'approve':null,results:'view-growth'}[guidance.step];
  for(const id of ['setup-open','choose-photo','photo-retry','resume','analyze','pot-save','pot-fix','review-continue','approve','view-growth'])$(id).classList.toggle('primary',id===primary);
  $('choose-photo').textContent=file?'Choose another photo':'Choose photo';$('analyze').hidden=guidance.step!=='analyze';$('ready-photo').hidden=guidance.step!=='analyze'||!file||working;
  $('review-continue').hidden=guidance.step!=='review';
  if(file&&$('prepared-photo').__sourceFile!==file){if($('prepared-photo').__objectURL)URL.revokeObjectURL($('prepared-photo').__objectURL);const url=URL.createObjectURL(file);$('prepared-photo').src=url;$('prepared-photo').__objectURL=url;$('prepared-photo').__sourceFile=file;}
  $('ready-photo-context').textContent=(selected?.display_name||selected?.plant_id||'')+' - '+$('photo-date').value;
  const capture=$('capture-details');if(capture&&captureStep!==guidance.step){capture.open=['plant','photo'].includes(guidance.step);captureStep=guidance.step;}
 $('approval-checklist').replaceChildren();for(const item of guidance.checks){const li=document.createElement('li');li.textContent=(item.complete?'Done: ':item.optional?'Optional: ':'Missing: ')+item.label;li.className=item.complete?'complete':item.optional?'optional':'incomplete';$('approval-checklist').append(li);}
 $('photo-date-help').hidden=!approvalConflict;$('photo-date-help').textContent=approvalConflict;
 const confirmationOnly=confirmationIsOnlyBlocker();$('approve-blocker').hidden=guidance.approval.enabled||analysis?.status==='APPROVED'||confirmationOnly&&!$('approval-error').textContent;
 const brief=working?(savingApproval?'Saving measurement...':'Please wait for the current action.'):{date:'A measurement is already saved for this date.',confirm:'Confirm that you checked the leaves.',scale:'Check this photo scale before saving.',editor:stats?.unassigned?'Some leaf areas still need to be assigned to a leaf.':'Check the leaf assignments before saving.'}[guidance.action];
 if(brief)$('approval-reason').textContent=brief;
 const fix=$('approval-fix');fix.hidden=guidance.approval.enabled||analysis?.status==='APPROVED'||confirmationOnly;fix.disabled=working||Boolean(editor?.drawing);fix.textContent={date:'Check photo date',editor:'Return to leaf editor',scale:'Review calibration',confirm:'Review confirmation'}[guidance.action]||'Review missing items';
 const targets={plant:'plant',photo:'choose-photo',retry:'photo-retry',calibrate:'pot-canvas',analyze:'analyze',review:'editor-viewport',editor:'editor-viewport',scale:'photo-calibration',confirm:'review-confirm',date:'photo-date',approve:'approve',growth:'history-card'};
 for(const n of document.querySelectorAll('.guidance-target'))n.classList.remove('guidance-target');let target=$(targets[guidance.action]);if(guidance.step==='calibration'){const hint=calibrationGuidance({diameter:Number($('pot-diameter').value),distance:potPicker?.distance(),points:potPicker?.points.length||0,previewReady:Boolean(potPicker?.image),saving:calibrationSaving});target=hint.target?$(hint.target):null;}if(target)target.classList.add('guidance-target');
}
function focusStep(id){const n=$(id);if(!n)return;if(n.closest?.('#capture-details'))$('capture-details').open=true;n.scrollIntoView({behavior:'smooth',block:'center'});if(n.matches?.('button,input,select,[tabindex]'))n.focus({preventScroll:true});}
async function workflowAction(action=guidance?.action){
 if(working||editor?.drawing)return;
 if(action==='plant'){if(!profiles.length)return openSetup('new');return focusStep('plant');}
 if(action==='photo')return $('photo').click();
 if(action==='retry')return chooseFile(pendingPhoto);
 if(action==='resume')return $('resume').click();
 if(action==='calibrate'){if($('calibration-card').hidden||!potPicker?.image)await startCalibration();else if(!$('pot-save').disabled)return savePot();focusStep(Number($('pot-diameter').value)>0?'pot-canvas':'pot-diameter');return;}
 if(action==='analyze')return analyze();
 if(action==='review'){reviewVisited=true;renderGuidance();focusStep('approval-guidance');return;}
 if(action==='editor'){if($('tool-drawer').hidden)toggleEditor();leafMode('select');if(editor?.stats().unassigned)$('tool-message').textContent='Some leaf pixels are unassigned. Use Reassign to an existing leaf, Add leaf for a missing leaf, or Erase confirmed non-leaf tissue.';focusStep('editor-viewport');return;}
 if(action==='scale'){await startCalibration();focusStep('calibration-card');return;}
 if(action==='confirm'){focusStep('review-confirm');return;}
 if(action==='date'){focusStep('photo-date');return;}
 if(action==='approve')return approve();
 if(action==='growth')return focusStep('history-card');
}
function viewPhoto(original){if(!editor||editor.drawing)return;if(original)leafMode('pan');$('show-mask').checked=!original;editor.show(!original);editor.editable=!original&&!working&&analysis?.status!=='APPROVED';$('view-original').setAttribute('aria-pressed',String(original));$('view-overlay').setAttribute('aria-pressed',String(!original));$('tool-message').textContent=original?'Original photograph. Switch to colored leaves before correcting.':'Colored leaf overlay. Check each leaf and correct mistakes.';}
function cameraKey(profile){return 'hydroponic.camera-changed:'+profile.id+':'+profile.calibration.id;}
function cameraActive(profile){if(!profile?.calibration)return false;try{return localStorage.getItem(cameraKey(profile))!=='true';}catch{return false;}}
function calibrationStatus(){
 $('setup-label').textContent=!selected?'Select a plant.':!selected.calibration?'Set the pot scale on your first photo.':!$('geometry').checked?'Mark pot edges in this photo to check its scale.':selected.calibration.id===setupDefault?.id?'Calibration active \u00b7 default '+selected.calibration.pot_diameter_cm+' cm reference':'Calibration active \u00b7 custom pot reference';
 $('setup-label').classList.toggle('warning',Boolean(selected&&(!selected.calibration||!$('geometry').checked)));
 if(analysis?.profile_id===selected?.id&&calibrationIssue(analysis,$('geometry').checked)){$('setup-label').textContent=calibrationIssue(analysis,$('geometry').checked);$('setup-label').classList.add('warning');}
 renderCalibrationReview();
}
function renderCalibrationReview(){
 const active=Boolean(analysis&&selected&&analysis.profile_id===selected.id&&analysis.status!=='APPROVED');
 const r=active&&analysis.calibration&&!$('geometry').checked?{status:'RECALIBRATION_REQUIRED',reason:'Mark the pot edges in this photo.'}:analysis?.calibration_reuse;
 $('photo-calibration').hidden=!active||!r||['VERIFIED','CONFIRMED'].includes(r.status);
 if(!active||!r)return;
 const pending=r.status==='CONFIRMATION_REQUIRED';
 $('photo-calibration-message').textContent=r.status==='PIXELS_ONLY'?'Leaf review is available in pixels. Mark the pot edges to measure cm².':pending?'We could not verify this photo’s scale automatically. Mark the pot edges, or confirm an unchanged setup below.':'This photo needs a fresh pot reference. Your leaf edits will stay here.';
 $('photo-calibration-confirmation').hidden=!pending;
 const reference=analysis.calibration_reference_url;
 $('photo-reference').hidden=!pending||!reference;
 if(reference)$('photo-reference').src=reference;
 $('photo-calibration').classList.toggle('warning',!['VERIFIED','CONFIRMED'].includes(r.status));
}
async function confirmPhotoCalibration(){
 if(working||editor?.drawing||!analysis||!$('confirm-photo-setup').checked)return;
 const active=analysis,profileId=selected.id,revision=selectionRevision;
 working=true;buttons();$('photo-calibration-error').textContent='';
 try{
  const saved=await json('/api/analyses/'+active.id+'/confirm-calibration',{
   image_sha256:active.image_sha256,calibration_id:active.calibration.id,
   same_physical_pot:true,same_camera_geometry:true,full_frame_resize_only:true});
  if(analysis?.id!==active.id||selected?.id!==profileId||revision!==selectionRevision)return;
  // Only calibration fields change. Native edits and undo/redo stay in memory.
  analysis={...analysis,calibration_reuse:saved.calibration_reuse,effective_cm_per_pixel:saved.effective_cm_per_pixel,
   calibration_valid:saved.calibration_valid,calibration_issue:saved.calibration_issue,calibration_revision:saved.calibration_revision};
  $('review-confirm').checked=false;calibrationStatus();renderLeafStats();
  say('Photo scale confirmed. Review the leaf mask and identities before approval.');
 }catch(error){$('photo-calibration-error').textContent=error.message;say(error.message,true);}
 finally{working=false;buttons();}
}
async function updateCaptureDate(){
 const previousConflict=approvalConflict;
 if(!analysis){approvalConflict='';uploadedReview=null;uploadRequestId=crypto.randomUUID();buttons();return;}
 if(working||editor?.drawing||analysis.status==='APPROVED')return;
 const date=$('photo-date').value,prior=analysis,revision=selectionRevision;
 if(date===prior.date)return;
 if(!date){$('photo-date').value=prior.date;return;}
 working=true;buttons();say('Updating the photo date...');
 try{
  const saved=await json('/api/analyses/'+prior.id+'/capture-date',{date,expected_date:prior.date});
  if(analysis?.id!==prior.id||selectionRevision!==revision)throw Error('Date saved. Return to this plant to review it.');
  // Keep the exact local pixels, leaf identities, undo/redo and calibration.
  // Only an unapproved date is editable; confirmation is explicitly renewed.
  analysis={...analysis,date:saved.date,capture_date_edits:saved.capture_date_edits};
  if(uploadedReview?.id===prior.id)uploadedReview={...uploadedReview,date:saved.date};
  uploadRequestId=crypto.randomUUID();approvalConflict='';$('review-confirm').checked=false;$('approval-error').textContent='';
  say('Photo date updated. Your leaf corrections are unchanged. Confirm the review again before approval.');
 }catch(error){analysis=prior;approvalConflict=previousConflict;$('photo-date').value=prior.date;say(error.message,true);}
 finally{working=false;buttons();}
}
function resetReview(){$('approval-error').textContent='';dirty=true;$('review-confirm').checked=false;if(editor?.labels){renderLeafStats();const changed=editor.stats();$('tool-message').textContent='Edit applied. '+(changed.unassigned?changed.unassigned.toLocaleString()+' leaf pixels need assignment.':'Leaf counts and areas updated; review the boundaries.');}buttons();}
function plantName(p){return p.species==='lettuce'?'lettuce':'Rumex acetosa';}
async function refreshProfiles(selectId){
 profiles=await (await api('/api/profiles')).json();$('plant').replaceChildren(new Option('Choose a plant',''));
 for(const p of profiles)$('plant').add(new Option(p.display_name||p.plant_id,p.id));
 if(selectId&&profiles.some(p=>p.id===selectId))$('plant').value=selectId;
 if(!selectId&&profiles.length===1)$('plant').value=profiles[0].id;
 await selectPlant();
}
async function selectPlant(){
 approvalConflict='';const revision=++selectionRevision;calibrationDeferred=false;calibrationSaved=false;reviewVisited=false;workflowPhase='';
 selected=profiles.find(p=>p.id===$('plant').value)||null;analysis=null;dirty=false;$('review-card').hidden=true;$('result-card').hidden=true;
 const profileId=selected?.id;$('resume-row').hidden=true;resumeId=null;
 $('calibration-card').hidden=true;uploadedReview=null;uploadRequestId=null;pendingPhoto=null;imageProvenance=null;++photoSerial;
 $('geometry').checked=cameraActive(selected);$('geometry-label').hidden=!selected?.calibration;$('calibration-open').hidden=!selected;$('manage-open').hidden=!selected;file=null;$('photo').value='';$('file-label').textContent='Take a photo or choose JPEG, PNG or HEIC';$('photo-progress').textContent='';$('photo-retry').hidden=true;
 $('history-card').hidden=true;calibrationStatus();$('species-label').textContent=selected?plantName(selected):'';
 buttons();step('plant');
 if(selected)await Promise.all([history(profileId,revision),pending(profileId,revision)]);
}
async function pending(profileId=selected?.id,revision=selectionRevision){
 if(!profileId)return;
 const rows=await(await api('/api/profiles/'+profileId+'/analyses'+(analysis?.profile_id===profileId?'?active_id='+encodeURIComponent(analysis.id):''))).json();
 if(selected?.id!==profileId||revision!==selectionRevision)return;
 resumeId=null;
 resumeId=rows[0]?.id||null;$('resume-row').hidden=!resumeId;$('resume-context').textContent=resumeId?'For '+(selected?.display_name||selected?.plant_id||'this plant')+' - '+(rows[0].date||'Saved review'):'';renderGuidance();
}
async function chooseFile(chosen){
 if(!chosen)return;const photoStarted=Date.now();$('photo-diagnostic').hidden=true;approvalConflict='';calibrationDeferred=false;calibrationSaved=false;reviewVisited=false;
 const serial=++photoSerial,profileId=selected?.id;pendingPhoto=chosen;file=null;uploadedReview=null;uploadRequestId=null;imageProvenance=null;analysis=null;dirty=false;$('review-card').hidden=true;$('result-card').hidden=true;
 $('calibration-card').hidden=true;
 if(chosen.size>config.max_upload_bytes){const message='This image exceeds the '+Math.round(config.max_upload_bytes/1048576)+' MB limit. Export a smaller JPEG or choose another photo; your plant and calibration are saved.';showPhotoDiagnostic({code:'image.input_too_large'},chosen,'prepare',photoStarted);$('photo-progress').textContent=message;$('photo-retry').hidden=true;say(message,true);buttons();return;}
 working=true;setPhase('preparing');buttons();$('photo-progress').textContent='Preparing your photo…';$('photo-retry').hidden=true;
 try{
  const form=new FormData();form.append('image',chosen);
  const response=await api('/api/images/prepare',{method:'POST',body:form}),blob=await response.blob();
  if(serial!==photoSerial||profileId!==selected?.id)return;
  const type=blob.type||'image/png';file=new File([blob],chosen.name.replace(/\.[^.]+$/,'')+(type==='image/jpeg'?'.jpg':'.png'),{type});
  imageProvenance=JSON.parse(response.headers.get('X-Image-Provenance')||'null');
  uploadedReview=null;uploadRequestId=crypto.randomUUID();
  const label=$('file-label');label.replaceChildren();const strong=document.createElement('strong');strong.textContent=chosen.name;label.append(strong,document.createElement('span'));label.lastChild.textContent='Photo ready · '+(file.size/1048576).toFixed(1)+' MB';
  analysis=null;dirty=false;$('review-card').hidden=true;$('result-card').hidden=true;$('photo-progress').textContent='Ready to analyze. Your selected photo is retained.';step('upload');
  const frame=imageProvenance;
  if(frame?.resized)$('photo-progress').textContent+=' Full frame resized from '+frame.oriented_size_px.join(' x ')+' to '+frame.normalized_size_px.join(' x ')+' pixels; no crop. Review boundaries and set scale on this prepared photo.';
  if(selected&&!selected.calibration){
   try{await startCalibration();}catch(error){if(serial!==photoSerial)return;potPicker?.clear?.();showPhotoDiagnostic({code:'calibration_preview.decode_failed',message:error.message},file,'calibration_preview',photoStarted);$('calibration-card').hidden=false;$('debug-details').textContent='Calibration preview: '+error.name+': '+error.message;$('pot-error').textContent='Photo ready; the scale preview could not open. Tap Recalibrate to retry, or Later then Analyze photo for a pixels-only leaf review.';say('Photo ready. Calibration can be completed separately.');}
  }else say('Photo ready. Tap Analyze photo.');
 }catch(error){if(serial===photoSerial){showPhotoDiagnostic(error,chosen,'prepare',photoStarted);$('photo-progress').textContent=userMessage(error.message)+' Your photo is still selected. Try again, or choose another photo.';$('photo-retry').hidden=false;say(error.message,true);}}
 finally{if(serial===photoSerial){working=false;setPhase('');buttons();}}
}
async function analyze(){
 if(working||!selected||!file)return;if(!confirmDiscard())return;const analysisStarted=Date.now();$('photo-diagnostic').hidden=true;
 if(uploadedReview&&(uploadedReview.profile_id!==selected.id||uploadedReview.date!==$('photo-date').value||uploadedReview.geometry_unchanged!==$('geometry').checked)){uploadedReview=null;uploadRequestId=crypto.randomUUID();}
 working=true;setPhase('uploading');buttons();step('analyze');say('Analyzing your photo…');$('photo-progress').textContent='Finding visible leaves. The first analysis may take a few minutes.';
 try{
  if(!uploadedReview){const form=new FormData();form.append('profile_id',selected.id);form.append('date',$('photo-date').value);form.append('geometry_unchanged',String($('geometry').checked));form.append('image',file);if(imageProvenance)form.append('image_provenance',JSON.stringify(imageProvenance));form.append('request_id',uploadRequestId||=crypto.randomUUID());
   uploadedReview=await(await api('/api/uploads',{method:'POST',body:form})).json();}
  setPhase('analyzing');const data=await json('/api/analyses/'+uploadedReview.id+'/analyze',{});setPhase('review-loading');await showAnalysis(data);await pending();$('photo-progress').textContent='Analysis ready for your review.';say('Review the colored leaf blades. Correct any mistakes, then approve.');
 }catch(error){showPhotoDiagnostic(error,file,workflowPhase==='uploading'?'upload':'analysis',analysisStarted);$('photo-progress').textContent='Analysis did not finish. Your photo is still here. Tap Analyze photo to retry.';say(error.message,true);}finally{working=false;setPhase('');buttons();}
}
async function showAnalysis(data){
 if(!selected||data.profile_id!==selected.id)throw Error('This review belongs to another plant. Select its plant before reviewing.');
 if(!data.mask_base64||!data.pipeline)throw Error('Analysis is not ready. Resume it to retry; interrupted work becomes retryable after 5 minutes.');
 analysis=data;dirty=false;reviewVisited=false;mergeSelections.clear();$('approval-error').textContent='';$('tool-message').textContent='';$('photo-date').value=data.date;$('review-card').hidden=false;$('review-confirm').checked=false;
 $('confirm-photo-setup').checked=false;$('photo-calibration-error').textContent='';
 $('review-card').classList.remove('editing');$('editor-tools').hidden=true;$('edit').textContent='Edit lamina mask';$('leaf-tools').open=true;$('tool-drawer').hidden=true;$('zoom').value='fit';$('show-mask').checked=true;
 $('review-card').dataset.measurementTarget=data.measurement_target||'visible_plant_foreground';$('original').src=data.assets.original;$('candidate').src=data.assets.candidate_mask;$('backdrop').src=data.assets.original;
 if(!editor)editor=new MaskEditor($('stage'),$('mask'),$('cursor'),resetReview,splitLeaf,(state={})=>{if(!editor.drawing){if(!state.rendered)editor.render();renderLeafStats();}renderToolState();buttons();},message=>{$('tool-message').textContent=message;});
 editor.editable=true;editor.visible=true;editor.show(true);editor.load(...data.image_size_px,data.mask_base64);
 if(data.instance_base64)editor.loadInstances(data.instance_base64,data.id,data.instance_metadata);
 editor.mode='select';$('view-original').setAttribute('aria-pressed','false');$('view-overlay').setAttribute('aria-pressed','true');renderLeafStats();renderToolState();calibrationStatus();
 $('review-badge').textContent=data.status==='DRAFT'?'Edited draft \u00b7 Not yet approved':'Automatic estimate \u00b7 Not validated';
 $('candidate-panel').querySelector('h3').textContent=data.automatic_mask_available?'Automatic candidate mask':'Manual mask · no automatic thresholds';
 
 $('review-statement').textContent=(data.calibration?'Same physical pot and camera distance/zoom/plane; full uncropped frame. ':'')+'For '+(selected.display_name||data.plant_id)+' on '+data.date+', I reviewed all visible '+plantName(data)+' leaf blades (lamina) and completeness, excluded petioles, stems, crown structures, moss, other vegetation, medium and pot/background. Pale, yellow, chlorotic and partially browned blades remain included. I accept '+(data.calibration?'the setup’s approximate projected-area calibration.':'pixels-only measurement without a physical scale.')+' I also reviewed the visible leaf-blade identities and their individual areas. No hidden tissue is inferred.';
 if(data.measurement_target!=='visible_leaf_lamina'){
  $('review-area').textContent='Legacy foreground review: this saved analysis does not measure lamina only. Start a new photo analysis for leaf-blade measurements.';
  $('review-statement').textContent='I reviewed this legacy visible foreground mask, tissue, exclusions, identities and calibration. This record does not claim lamina-only area.';
 }else $('review-area').textContent='Visible projected leaf-blade area. Exclude petiole, stem and crown with Edit lamina mask / Erase; uncertain boundaries need human review.';

 $('review-checklist').textContent=$('review-statement').textContent;
 $('review-statement').textContent='I checked the visible leaf blades, their boundaries, excluded tissue and '+(reviewScale(data,$('geometry').checked)===null?'pixel-only units.':'the approximate photo scale.');
 $('edit').textContent='Leaf corrections';
 $('calibration-card').hidden=true;
 $('overlay-title').textContent='Visible leaf-blade instances · one color per leaf';buttons();step('review');$('review-card').scrollIntoView({behavior:'smooth',block:'start'});
 if(!data.automatic_mask_available)toggleEditor();
}
function toggleEditor(){const open=$('tool-drawer').hidden;$('tool-drawer').hidden=!open;$('edit').setAttribute('aria-expanded',String(open));if(open){editor.editable=true;}else leafMode('select');}
async function draft(){if(working||!analysis)return;working=true;buttons();try{const saved=await json('/api/analyses/'+analysis.id+'/draft',editor.payload());if(saved.instance_metadata)editor.restoreIdentity(saved.instance_metadata);analysis=saved;dirty=false;editor.edits=[];$('approval-error').textContent='';await pending();say('Draft saved. Human approval is still pending.');}catch(error){say(error.message,true);}finally{working=false;buttons();}}
async function approve(){
 if(working)return;const state=currentApprovalState();if(!state.enabled){$('approval-reason').textContent=state.reason;return;}
 const active=analysis,profileId=selected.id,revision=selectionRevision,body={...editor.payload(),approval:'APPROVE',instances_reviewed:true,calibration_revision:analysis.calibration_revision||0,review:Object.fromEntries(reviewKeys.map(k=>[k,true]))};
 working=true;savingApproval=true;$('approval-error').textContent='';buttons();say('Saving the human-approved measurement…');
 try{
  let measurement;
  try{measurement=await json('/api/analyses/'+active.id+'/approve',body);}
  catch(error){
   // A response can fail after the atomic commit. Reconcile before offering a
   // retry; do not leave a saved measurement looking like a failed approval.
   try{const saved=await(await api('/api/analyses/'+active.id)).json();if(saved.status==='APPROVED')measurement=await(await api('/api/measurements/'+saved.measurement_id)).json();}catch{}
   if(!measurement)throw error;
  }
  if(analysis?.id!==active.id||selected?.id!==profileId||selectionRevision!==revision)throw Error('Measurement saved. Reopen its plant to view the approved record.');
  analysis.status='APPROVED';analysis.measurement_id=measurement.id;dirty=false;editor.editable=false;$('review-card').hidden=true;$('result-card').hidden=false;
  $('result-area').textContent=measurement.projected_area_cm2===null?measurement.pixel_area.toLocaleString()+' pixels':measurement.projected_area_cm2.toFixed(4)+' cm²';
  $('result-info').textContent=(selected.display_name||measurement.plant_id)+' · '+(measurement.captured_at||measurement.date)+' · '+measurement.pixel_area.toLocaleString()+' pixels · Approved and saved';
  renderApprovedLeaves(measurement);step('result');$('resume-row').hidden=true;resumeId=null;
  try{await history(profileId,revision);await pending(profileId,revision);say('Measurement approved and saved. Growth history and export updated.');}
  catch(error){say('Measurement approved and saved. History refresh failed: '+error.message+'. Refresh the page to load it.',true);}
  $('result-card').scrollIntoView({behavior:'smooth'});
 }catch(error){$('approval-error').textContent='Measurement not saved. '+userMessage(error);if(/final measurement already exists|measurement.*already.*plant.*date/i.test(error.message))approvalConflict='A measurement is already saved for this plant and date. Check the actual photo date; this review has not been saved.';say(approvalConflict||error.message,true);}
 finally{working=false;savingApproval=false;buttons();}
}
function renderLeafStats(){
 if(!editor?.labels)return;const s=editor.stats(),scale=reviewScale(analysis,$('geometry').checked);
 const area=v=>v===null?'—':scale===null?v.toLocaleString(undefined,{maximumFractionDigits:1})+' px':(v*scale).toFixed(4)+' cm²';
 $('leaf-count').textContent=String(s.ids.length);$('leaf-total').textContent=area(s.total);
 $('leaf-mean').textContent=area(s.ids.length&&!s.unassigned?s.total/s.ids.length:null);$('leaf-median').textContent=area(s.unassigned?null:median(s.areas));
 $('leaf-note').textContent=s.unassigned?s.unassigned.toLocaleString()+' lamina pixels are unassigned. Add creates a new leaf from painted lamina; Reassign transfers pixels to an existing leaf.':(scale===null?'Scale not verified: pixel areas only. ':'Approximate projected areas at the reference plane. ')+'Lamina only: inspect petiole, stem and crown exclusions; keep pale/yellow and uncertain lamina.';
 if(calibrationIssue(analysis,$('geometry').checked))$('leaf-note').textContent+=' '+calibrationIssue(analysis,$('geometry').checked)+' Preview uses pixels only.';
 if($('workspace').classList.contains('tips-hidden'))$('leaf-note').textContent=s.unassigned?s.unassigned.toLocaleString()+' unassigned leaf pixels. Use Add / Reassign, or erase confirmed non-leaf tissue.':scale===null?'Pixel units only; physical scale is not verified.':'Approximate projected leaf area at the pot-rim plane.';
 if(analysis?.status!=='APPROVED')$('review-statement').textContent='I checked the visible '+(analysis?.measurement_target==='visible_leaf_lamina'?'leaf blades':'plant tissue')+', their boundaries, excluded tissue and '+(scale===null?'pixel-only units.':'the approximate photo scale.');
 $('leaf-rows').replaceChildren();$('leaf-select').replaceChildren();const merging=editor.mode==='merge';$('merge-column').hidden=!merging;
 if(editor.mode!=='add'&&!s.ids.includes(editor.selectedLeaf))editor.selectedLeaf=s.ids[0]||1;
 const choices=s.ids.includes(editor.selectedLeaf)||editor.mode!=='add'?s.ids:[...s.ids,editor.selectedLeaf];for(const id of choices)$('leaf-select').add(new Option(s.ids.includes(id)?'Leaf '+editor.displayNumber(id):'New leaf (paint lamina)',id));$('leaf-select').value=String(editor.selectedLeaf);
 s.ids.forEach((id,i)=>{const number=editor.displayNumber(id),tr=document.createElement('tr');if(merging){const td=document.createElement('td'),box=document.createElement('input');box.type='checkbox';box.value=String(id);box.checked=mergeSelections.has(id);box.setAttribute('aria-label','Merge leaf '+number);box.onchange=()=>{box.checked?mergeSelections.add(id):mergeSelections.delete(id);buttons();};td.append(box);tr.append(td);}
  for(const text of ['Leaf '+number,s.areas[i].toLocaleString(),scale===null?'No calibration':(s.areas[i]*scale).toFixed(4)+' cm²','Review required']){const cell=document.createElement('td');cell.textContent=text;tr.append(cell);}tr.children[merging?1:0].style.borderLeft='6px solid rgb('+(editor.currentColors?.get(id)||leafColor(id)).join(',')+')';$('leaf-rows').append(tr);});
 buttons();
}
function renderApprovedLeaves(m){
 const area=(cm,px)=>cm!==null&&cm!==undefined?cm.toFixed(4)+' cm²':px!==null&&px!==undefined?px.toFixed(1)+' px':'—';
 $('result-leaf-summary').textContent=m.leaf_count===null?'Individual leaves were not reviewed.':m.leaf_count+' visible leaf blades · mean '+area(m.mean_leaf_area_cm2,m.mean_leaf_pixel_area)+' · median '+area(m.median_leaf_area_cm2,m.median_leaf_pixel_area);
 $('result-leaf-rows').replaceChildren();for(const [index,leaf] of (m.leaves||[]).entries()){const tr=document.createElement('tr');for(const text of ['Leaf '+(leaf.display_leaf_number??index+1),leaf.pixel_area.toLocaleString(),area(leaf.projected_area_cm2,leaf.pixel_area)]){const td=document.createElement('td');td.textContent=text;tr.append(td);}$('result-leaf-rows').append(tr);}
}
function renderToolState(){
 const mode=editor?.mode||'select';$('active-tool').textContent='Active tool: '+mode.toUpperCase();
 const map={'leaf-pick':'select','leaf-split':'split','leaf-reassign':'reassign','leaf-add':'add','leaf-merge':'merge','leaf-pan':'pan',paint:'paint',erase:'erase'};
 for(const[id,value]of Object.entries(map)){$(id).classList.toggle('selected',mode===value);$(id).setAttribute('aria-pressed',String(mode===value));}
 $('merge-confirm').hidden=mode!=='merge';$('editor-tools').hidden=['select','merge','pan'].includes(mode);if(mode==='merge')$('leaf-details').open=true;
 $('leaf-mode-note').textContent={pan:'Move the photo with one finger. Pinch with two fingers to zoom.',select:'Tap a colored leaf to select it. Use Paint / Erase for boundaries, or Leaf corrections for identities.',split:'Select one leaf, then draw a complete line across it to divide it. Failed cuts explain why.',merge:'Select two or more leaf checkboxes in Individual leaf areas, then Merge selected leaves.',reassign:'Choose the correct leaf in Selected leaf, then paint the area to transfer.',add:'Paint the missing visible leaf blade. Magenta previews the new leaf region; release to create it. Escape cancels.',paint:'Paint adds visible blade tissue. Assign new pixels with Add leaf or Reassign before approval.',erase:'Erase removes tissue from lamina area and all leaf assignments.'}[mode];
 if(editor)editor.canvas.style.cursor=['select','merge'].includes(mode)?'pointer':'crosshair';
}
function leafMode(mode){
 if(!editor||working||analysis?.status==='APPROVED'||editor.drawing)return;
 $('show-mask').checked=true;editor.show(true);$('view-original').setAttribute('aria-pressed','false');$('view-overlay').setAttribute('aria-pressed','true');
 if(editor.mode==='merge'&&mode!=='merge')mergeSelections.clear();
 editor.mode=mode;editor.editable=true;$('tool-message').textContent='';editor.render();renderLeafStats();renderToolState();buttons();
}
function undoEditor(){if(!editor||working)return;mergeSelections.clear();const restored=editor.undo();$('tool-message').textContent=restored?'Undo restored the exact previous mask and leaf assignments.':'Nothing earlier to undo.';renderToolState();buttons();}
function redoEditor(){if(!editor||working)return;mergeSelections.clear();const restored=editor.redo();$('tool-message').textContent=restored?'Redo restored the mask, identities and leaf numbering.':'Nothing to redo.';renderToolState();buttons();}

async function splitLeaf(points){
 if(working||!analysis||!points?.length)return;const active=analysis.id,leafId=editor.selectedLeaf,version=editor.revision;working=true;buttons();
 try{const result=await json('/api/analyses/'+active+'/split-leaf',{...editor.payload(),leaf_id:leafId,points});if(analysis?.id!==active||editor.selectedLeaf!==leafId||editor.revision!==version)throw Error('Review changed; retry the split');editor.busy=false;editor.applySplit(result.instance_base64,result.new_leaf_id,result.instance_metadata);$('tool-message').textContent='Split complete: one new leaf ID, unchanged lamina area.';say('Leaf split. Review both visible portions.');}catch(error){$('tool-message').textContent=error.message;say(error.message,true);}finally{working=false;buttons();}
}
function formatArea(value){return value===null||value===undefined?'—':value.toFixed(3)+' cm²';}
function formatLeafArea(cm,pixels){return Number.isFinite(cm)?cm.toFixed(3)+' cm²':Number.isFinite(pixels)?pixels.toLocaleString(undefined,{maximumFractionDigits:1})+' px':'—';}
async function history(profileId=selected?.id,revision=selectionRevision){
 if(!profileId)return;
 const data=await(await api('/api/profiles/'+profileId+'/history')).json(),g=data.growth;
 if(selected?.id!==profileId||revision!==selectionRevision)return;
 historyMeasurements=data.measurements;$('history-card').hidden=false;$('history-empty').hidden=Boolean(data.measurements.length);$('history-single').hidden=data.measurements.length!==1;$('history-context').textContent=(selected?.display_name||selected?.plant_id||'This plant')+' - '+data.measurements.length+' approved measurement'+(data.measurements.length===1?'':'s');$('export').hidden=!data.measurements.length;$('graph-metric').closest('label').hidden=!data.measurements.length;$('chart').hidden=!data.measurements.length;
 $('current').textContent=formatArea(g.current_area_cm2);$('previous').textContent=formatArea(g.previous_area_cm2);
 $('daily-growth').textContent=g.daily_growth_cm2===null?'—':(g.daily_growth_cm2>=0?'+':'')+g.daily_growth_cm2.toFixed(3)+' cm²/day';
 $('percentage').textContent=g.percentage_growth===null?'—':(g.percentage_growth>=0?'+':'')+g.percentage_growth.toFixed(1)+'%';
 $('rgr').textContent=g.relative_growth_rate_per_day===null?'—':g.relative_growth_rate_per_day.toFixed(4)+' day⁻¹';
 $('growth-note').textContent=g.reason==='measurement_target_changed'?'Measurement target changed from foreground to leaf lamina. Growth comparison is withheld.':g.reason==='calibration_changed'?'Calibration changed. Growth comparison is withheld until measurements use the same setup.':g.reason==='unscaled_or_invalid_interval'?'Physical growth needs positive scaled areas and a valid time interval.':g.reason?'Growth appears after two approved, scaled measurements.':g.delta_days===1?'Change since the previous day. Approximate projected area.':'Previous measurement was '+g.delta_days+' days earlier. Total change: '+g.absolute_growth_cm2.toFixed(3)+' cm²; daily growth uses the actual interval.';
 $('export').href='/api/profiles/'+profileId+'/export.csv';$('history').replaceChildren();
 for(const m of [...data.measurements].reverse()){const tr=document.createElement('tr');for(const text of [m.captured_at||m.date,m.leaf_count??'—',formatLeafArea(m.total_projected_area_cm2??m.projected_area_cm2,m.total_pixel_area??m.pixel_area),formatLeafArea(m.mean_leaf_area_cm2,m.mean_leaf_pixel_area),formatLeafArea(m.median_leaf_area_cm2,m.median_leaf_pixel_area),(m.measurement_target==='visible_leaf_lamina'?'Lamina · ':'Legacy foreground · ')+(m.correction_required?'Corrected & approved':'Reviewed & approved')]){const td=document.createElement('td');td.dataset.label=['Captured','Leaves','Total','Mean','Median','Review'][tr.children.length];td.textContent=text;tr.append(td);}const td=document.createElement('td'),link=document.createElement('a');link.textContent='View mask';link.href=m.assets.overlay;link.target='_blank';link.rel='noopener';td.append(link);tr.append(td);$('history').append(tr);}
 graph(data.measurements);
}
function graph(rows){
 const view=historyView(rows,$('history-target').value);$('history-target').value=view.target;$('history-target-label').hidden=!view.hasLegacy;const series=graphSeries(view.rows,$('graph-metric').value||'total'),points=series.points;if(view.target==='visible_plant_foreground')series.label={total:'Legacy foreground area',count:'Legacy region count',mean:'Mean legacy region area',median:'Median legacy region area'}[$('graph-metric').value||'total'];$('chart').replaceChildren();
 if(!points.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No approved values for this metric.';$('chart').append(empty);return;}
 const chartWidth=Math.max(250,$('chart').clientWidth||1000),ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 '+chartWidth+' 220');svg.setAttribute('role','img');svg.setAttribute('aria-label',series.label+' in '+series.unit);
 const add=(name,attrs,text)=>{const node=document.createElementNS(ns,name);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,String(v));if(text!==undefined)node.textContent=text;svg.append(node);return node;};
 const first=points[0].time,last=points.at(-1).time,rawTop=Math.max(...points.map(p=>p.value))*1.15||1,top=series.unit==='leaves'?Math.max(1,Math.ceil(rawTop)):rawTop,xy=p=>[55+(p.time-first)/(last-first||86400000)*(chartWidth-75),170-p.value/top*140];
 const countStep=Math.max(1,Math.ceil(top/3)),ticks=series.unit==='leaves'?Array.from({length:Math.floor(top/countStep)+1},(_,i)=>i*countStep):[0,top/3,2*top/3,top];
 for(const v of ticks){const y=170-v/top*140;add('line',{x1:50,y1:y,x2:chartWidth-20,y2:y,stroke:'#e1e8df'});add('text',{x:45,y:y+4,'text-anchor':'end',fill:'#50645b','font-size':12},series.unit==='leaves'?String(v):v.toFixed(2));}
 add('text',{x:10,y:17,fill:'#50645b','font-size':13},series.label+' · '+series.unit);
 points.forEach((p,i)=>{const[x,y]=xy(p),r=p.record,previous=points[i-1]?.record;if(i&&previous.calibration_id===r.calibration_id&&(previous.measurement_target||'visible_plant_foreground')===(r.measurement_target||'visible_plant_foreground')){const[px,py]=xy(points[i-1]);add('line',{x1:px,y1:py,x2:x,y2:y,stroke:'#387d52','stroke-width':2});}const circle=add('circle',{cx:x,cy:y,r:5,fill:r.measurement_target==='visible_leaf_lamina'?'#216d4d':'#718477','data-measurement-id':r.id,'data-captured-at':p.label,'data-value':p.value});const title=document.createElementNS(ns,'title');title.textContent=p.label+': '+p.value.toFixed(series.unit==='leaves'?0:4)+' '+series.unit+' · '+(r.measurement_target==='visible_leaf_lamina'?'leaf lamina':'legacy foreground');circle.append(title);});
 add('text',{x:55,y:198,fill:'#50645b','font-size':12},points[0].label.slice(0,10));if(points.length>1)add('text',{x:chartWidth-20,y:198,fill:'#50645b','font-size':12,'text-anchor':'end'},points.at(-1).label.slice(0,10));$('chart').append(svg);
}
function confirmDiscard(){return !dirty||window.confirm('Discard unsaved mask corrections? Save a draft first to keep them.');}
function calibrationFields(){/* Current-photo calibration is guided inline. */}
function potChanged(){
 setupCalibration=null;const d=potPicker?.distance(),cm=Number($('pot-diameter').value);
 $('pot-canvas').hidden=!potPicker?.image;
 $('pot-step').textContent=!potPicker?.points.length?'1 · Tap the first outer edge':potPicker.points.length===1?'2 · Tap the opposite outer edge':'3 · Save calibration';
 $('pot-summary').textContent=d===null?'Your photo stays here while you mark the rim.':d<1?'Choose two different points on opposite sides.':cm>0?'Diameter '+d.toFixed(1)+' px · approximate scale '+(cm/d).toFixed(6)+' cm per pixel':'Enter the physical outer pot diameter above.';
 potButtons();$('pot-preview-retry').hidden=Boolean(potPicker?.image);const hint=calibrationGuidance({diameter:cm,distance:d,points:potPicker?.points.length||0,previewReady:Boolean(potPicker?.image),saving:calibrationSaving});$('pot-action-hint').textContent=hint.message;$('pot-fix').hidden=hint.ready||calibrationSaving;$('pot-fix').textContent=hint.target==='pot-diameter'?'Enter pot diameter':hint.target==='pot-preview-retry'?'Retry scale preview':'Return to the rim points';if(hint.target==='pot-diameter')$('pot-step').textContent='Pot diameter needed';renderGuidance();
}
function potButtons(){const d=potPicker?.distance(),cm=Number($('pot-diameter').value);$('pot-save').disabled=calibrationSaving||!(d>=1&&Number.isFinite(cm)&&cm>0&&cm<=1000);$('pot-undo').disabled=calibrationSaving||!potPicker?.points.length;$('pot-redo').disabled=calibrationSaving;}
async function openSetup(mode){
 if(working)return;if(mode==='new'&&analysis&&dirty&&!confirmDiscard())return;if(mode==='calibration')return startCalibration();
 setupMode=mode;setupTarget=mode==='new'?null:selected;setupCalibration=null;setupRequestId=crypto.randomUUID();$('setup-error').textContent='';
 $('plant-id').value=mode==='new'?'':setupTarget.display_name||setupTarget.plant_id;$('species').value=mode==='new'?'lettuce':setupTarget.species;
 $('identity-fields').hidden=false;$('plant-id').disabled=false;$('species').disabled=false;
 $('setup-title').textContent=mode==='edit'?'Manage plant':'Add plant';$('setup-save').hidden=false;$('setup-save').textContent=mode==='new'?'Add plant':'Save plant';$('setup-dialog').showModal();
}
async function startCalibration(){
 if(!selected)return;calibrationDeferred=false;const calibrationTarget=selected,revision=selectionRevision,sourceSerial=photoSerial,openSerial=++calibrationSerial,reviewId=analysis?.id;
 const stillCurrent=()=>selected?.id===calibrationTarget.id&&selectionRevision===revision&&photoSerial===sourceSerial&&calibrationSerial===openSerial&&analysis?.id===reviewId;
 setupTarget=selected;setupMode='calibration';$('pot-error').textContent='';
 if(!potPicker)potPicker=new PotCalibration($('pot-canvas'),potChanged,$('pot-viewport'));
 let image=file;
 if(analysis?.profile_id===selected.id&&analysis.status!=='APPROVED'){
  const photoReview=analysis,r=await api(photoReview.assets.original),b=await r.blob();if(!stillCurrent())return;image=new File([b],photoReview.original_filename,{type:b.type});
 }
 if(!stillCurrent())return;
 if(!image)potPicker.clear();potPicker.panMode=false;$('pot-pan').classList.remove('selected');$('pot-pan').setAttribute('aria-pressed','false');
 $('pot-diameter').value=selected.pot_diameter_cm??selected.calibration?.pot_diameter_cm??'';
 $('diameter-hint').textContent=$('pot-diameter').value?'Remembered for this plant. Change only if the physical pot has changed.':'Measure the OUTSIDE rim once with a ruler, in cm.';
 $('calibration-title').textContent='Set scale · '+(selected.display_name||selected.plant_id);$('calibration-card').hidden=false;$('pot-photo-label').hidden=Boolean(image);
 if(image&&potPicker.file!==image)await potPicker.load(image);
 if(!stillCurrent())return;
 potChanged();renderGuidance();$('calibration-card').scrollIntoView({behavior:'smooth',block:'start'});requestAnimationFrame(()=>potPicker.fit());
}
async function savePot(){
 if(calibrationSaving||potPicker?.pointDraft||$('pot-save').disabled||!setupTarget||!potPicker?.file)return;
 const target=setupTarget,revision=selectionRevision;
 const activeReview=analysis&&analysis.profile_id===target.id&&analysis.status!=='APPROVED'?analysis:null;
 calibrationSaving=true;potPicker.locked=true;working=true;potButtons();buttons();$('pot-save').textContent='Saving…';$('pot-error').textContent='';
 try{
  const form=new FormData();form.append('diameter_cm',$('pot-diameter').value);form.append('points',JSON.stringify(potPicker.points));form.append('reviewed','true');form.append('image',potPicker.file);if(!activeReview&&imageProvenance)form.append('image_provenance',JSON.stringify(imageProvenance));
  if(activeReview){form.append('analysis_id',activeReview.id);form.append('image_sha256',activeReview.image_sha256);}
  const saved=await(await api('/api/plants/'+target.id+'/calibration/two-point',{method:'POST',body:form})).json();
  if(selected?.id!==target.id||selectionRevision!==revision)throw Error('Calibration saved. Select its plant to continue.');
  selected=saved.plant;profiles=profiles.map(p=>p.id===selected.id?selected:p);setupTarget=selected;$('geometry').checked=true;
  // A failed or response-lost initial analysis has no editable mask to rebind.
  // Its old upload may be ERROR/ANALYZING. The next upload must snapshot this
  // newly saved plant scale rather than reuse that failed request's snapshot.
  if(!activeReview){uploadedReview=null;uploadRequestId=crypto.randomUUID();}
  try{localStorage.removeItem(cameraKey(selected));}catch{}
  if(saved.analysis&&uploadedReview?.id===saved.analysis.id)uploadedReview=saved.analysis;
  if(activeReview&&analysis?.id===activeReview.id){const review=saved.analysis;analysis={...analysis,calibration:review.calibration,geometry_unchanged:true,calibration_reuse:review.calibration_reuse,effective_cm_per_pixel:review.effective_cm_per_pixel,calibration_valid:review.calibration_valid,calibration_issue:review.calibration_issue,calibration_reference_url:review.calibration_reference_url,calibration_revision:review.calibration_revision};$('review-confirm').checked=false;renderLeafStats();}
  $('geometry').checked=true;calibrationSaved=true;calibrationDeferred=false;$('calibration-card').hidden=true;calibrationStatus();say('Calibration saved for '+(selected.display_name||selected.plant_id)+'. '+(activeReview?'Your leaf edits are unchanged.':'Tap Analyze photo to continue.'));
 }catch(error){$('pot-error').textContent=userMessage(error,'calibration');$('debug-details').textContent=String(error.message);$('pot-save').textContent='Retry save';}
 finally{calibrationSaving=false;potPicker.locked=false;working=false;if(!$('pot-error').textContent)$('pot-save').textContent='Save calibration';potButtons();buttons();}
}
async function saveSetup(event){
 event.preventDefault();if(setupSaving)return;setupSaving=true;$('setup-save').disabled=true;$('setup-save').textContent='Saving...';$('setup-error').textContent='';
 try{
  const retainedPhoto=setupMode==='new'&&!analysis?pendingPhoto:null;
  const retainedPrepared=retainedPhoto&&file&&imageProvenance?.normalized_sha256?{file,provenance:imageProvenance}:null;
  const identity={name:$('plant-id').value.trim(),species:$('species').value};
  const speciesChanged=setupMode==='edit'&&setupTarget?.species!==identity.species;
  if(speciesChanged&&analysis&&!window.confirm('Changing species requires fresh analysis. Keep the photo and discard this unsaved leaf review?'))return;
  let retainedReviewPhoto=null;
  if(speciesChanged&&analysis&&!file){const response=await api(analysis.assets.original),blob=await response.blob();retainedReviewPhoto=new File([blob],analysis.original_filename,{type:blob.type});}

  const saved=setupMode==='edit'?await json('/api/plants/'+setupTarget.id,identity,'PUT'):await json('/api/plants',{...identity,calibration_choice:'none',request_id:setupRequestId});
  $('setup-dialog').close();
  if(setupMode==='edit'){
   profiles=profiles.map(p=>p.id===saved.id?saved:p);
   if(selected?.id===saved.id){
    if(speciesChanged){file=file||retainedReviewPhoto;pendingPhoto=pendingPhoto||file;analysis=null;dirty=false;uploadedReview=null;uploadRequestId=crypto.randomUUID();reviewVisited=false;calibrationSaved=false;calibrationDeferred=true;$('review-confirm').checked=false;$('review-card').hidden=true;$('result-card').hidden=true;$('calibration-card').hidden=true;$('photo-progress').textContent='Photo retained. Analyze again for the new species; the previous leaf mask cannot be reused.';}
    selected=saved;setupTarget=saved;const option=[...$('plant').options].find(o=>o.value===saved.id);if(option)option.textContent=saved.display_name||saved.plant_id;$('species-label').textContent=plantName(saved);calibrationStatus();buttons();say(speciesChanged?'Species changed. Your photo is retained; fresh analysis is required.':'Plant details saved. Your photo and leaf review are unchanged.');}
   return;
  }
  await refreshProfiles(saved.id);say('Plant saved. Take or choose its first photo.');
  if(retainedPrepared){file=retainedPrepared.file;imageProvenance=retainedPrepared.provenance;pendingPhoto=retainedPhoto;uploadRequestId=crypto.randomUUID();$('file-label').textContent='Selected photo retained - ready to analyze';$('photo-progress').textContent='Ready to analyze. Your selected photo was kept while adding the plant.';await startCalibration();buttons();}else if(retainedPhoto)await chooseFile(retainedPhoto);
 }catch(error){$('setup-error').textContent=userMessage(error);$('debug-details').textContent=String(error.message);}finally{setupSaving=false;$('setup-save').disabled=false;$('setup-save').textContent=setupMode==='new'?'Add plant':'Save plant';}
}
$('setup-open').onclick=()=>openSetup('new').catch(e=>say(e.message,true));$('manage-open').onclick=()=>openSetup('edit').catch(e=>say(e.message,true));$('calibration-open').onclick=()=>startCalibration().catch(e=>say(e.message,true));$('review-recalibrate').onclick=$('calibration-open').onclick;$('setup-close').onclick=()=>$('setup-dialog').close();$('setup-form').onsubmit=saveSetup;
$('confirm-photo-calibration').onclick=confirmPhotoCalibration;$('confirm-photo-setup').onchange=buttons;
$('pot-preview-retry').onclick=()=>startCalibration().catch(e=>say(e.message,true));
$('pot-photo').onchange=async()=>{await chooseFile($('pot-photo').files[0]);if(file)await startCalibration();};
$('use-current-photo').onclick=()=>startCalibration().catch(e=>say(e.message,true));
$('pot-redo').onclick=()=>potPicker.redo();$('pot-undo').onclick=()=>potPicker.undoLastPoint();$('pot-diameter').oninput=potChanged;$('pot-save').onclick=savePot;
$('pot-pan').onclick=()=>{potPicker.panMode=!potPicker.panMode;$('pot-pan').classList.toggle('selected',potPicker.panMode);$('pot-pan').setAttribute('aria-pressed',String(potPicker.panMode));};$('pot-fit').onclick=()=>potPicker.fit();
$('calibration-close').onclick=()=>{calibrationDeferred=true;$('calibration-card').hidden=true;buttons();};
$('analyze-pixels').onclick=()=>{calibrationDeferred=true;$('calibration-card').hidden=true;analyze();};
$('photo-retry').onclick=()=>chooseFile(pendingPhoto);
$('plant').onchange=async()=>{if(!confirmDiscard()){if(selected)$('plant').value=selected.id;return;}try{await selectPlant();}catch(e){say(e.message,true);}};
$('photo').onchange=()=>{const chosen=$('photo').files[0];$('photo').value='';if(confirmDiscard())chooseFile(chosen);};
async function changePhotoGeometry(){
 if(working||editor?.drawing)return;
 // A changed setup is a new upload claim, including when the previous upload
 // response was lost. Never retry an immutable request ID with a stale scale.
 if(!analysis||analysis.status==='APPROVED'){uploadedReview=null;uploadRequestId=crypto.randomUUID();}
 if(selected?.calibration){try{localStorage.setItem(cameraKey(selected),String(!$('geometry').checked));}catch{say('Camera-change warning cannot persist in this browser.',true);}}
 if(!$('geometry').checked&&analysis&&analysis.status!=='APPROVED'){
  const active=analysis;
  analysis={...analysis,geometry_unchanged:false,effective_cm_per_pixel:null,
   calibration_reuse:{...analysis.calibration_reuse,status:'RECALIBRATION_REQUIRED',reason:'Camera/setup marked changed. Recalibrate this photo.'}};
  $('review-confirm').checked=false;working=true;calibrationStatus();renderLeafStats();buttons();
  try{
   const saved=await json('/api/analyses/'+active.id+'/geometry-changed',{});
   if(analysis?.id===active.id){analysis={...analysis,calibration_revision:saved.calibration_revision,calibration_reuse:saved.calibration_reuse};}
  }catch(error){say('Camera change could not be saved: '+error.message+'. Physical approval remains blocked. Retry or recalibrate.',true);}
  finally{working=false;}
 }
 calibrationStatus();buttons();
}
$('geometry').onchange=changePhotoGeometry;$('photo-date').onchange=()=>updateCaptureDate();$('review-confirm').onchange=buttons;
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.add('over');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.remove('over');});
$('dropzone').addEventListener('drop',e=>{if(working||!confirmDiscard())return;chooseFile(e.dataTransfer.files[0]);});
$('analyze').onclick=analyze;$('edit').onclick=toggleEditor;$('save-draft').onclick=draft;$('approve').onclick=approve;
for(const mode of ['paint','erase'])$(mode).onclick=()=>leafMode(mode);
$('radius').oninput=()=>{if(editor)editor.radius=Math.max(1,Math.min(80,Number($('radius').value)||1));};$('undo').onclick=undoEditor;$('clear').onclick=()=>{if(window.confirm('Clear this draft mask? Undo restores it.'))editor.clear();};$('show-mask').onchange=()=>editor.show($('show-mask').checked);$('zoom').onchange=()=>{$('zoom').value==='fit'?editor.fit():editor.zoom(Number($('zoom').value));};
$('leaf-undo').onclick=undoEditor;
$('leaf-redo').onclick=redoEditor;
$('leaf-select').onchange=()=>{editor.selectedLeaf=Number($('leaf-select').value);editor.render();};
$('leaf-pan').onclick=()=>leafMode('pan');$('leaf-pick').onclick=()=>leafMode('select');$('zoom-in').onclick=()=>editor.zoom(Math.min(8,editor.scale*1.4));$('zoom-out').onclick=()=>editor.zoom(Math.max(.05,editor.scale/1.4));$('leaf-split').onclick=()=>leafMode('split');$('leaf-reassign').onclick=()=>leafMode('reassign');
$('leaf-merge').onclick=()=>leafMode('merge');
$('merge-confirm').onclick=()=>{try{editor.mergeLeaves([...mergeSelections]);mergeSelections.clear();leafMode('select');$('tool-message').textContent='Leaves merged; lamina area unchanged.';}catch(e){$('tool-message').textContent=e.message;}};
$('tool-done').onclick=()=>{editor.cancelDrawing();leafMode('select');$('tool-drawer').hidden=true;};
$('overlay-opacity').oninput=()=>{editor.opacity=Number($('overlay-opacity').value)/100;editor.render();};
$('graph-metric').onchange=()=>graph(historyMeasurements);
$('history-target').onchange=()=>graph(historyMeasurements);
window.addEventListener('resize',()=>{if(historyMeasurements.length)graph(historyMeasurements);});
$('leaf-delete').onclick=()=>{try{editor.deleteLeaf(editor.selectedLeaf);leafMode('select');$('tool-message').textContent='Leaf removed; its lamina pixels are unassigned. Use Add / Reassign or erase confirmed non-lamina tissue.';say('Leaf removed; its tissue remains unassigned until you reassign it or edit the lamina mask.');}catch(e){say(e.message,true);}};
$('leaf-add').onclick=()=>{try{editor.addLeaf();leafMode('add');renderLeafStats();}catch(e){say(e.message,true);}};
$('resume').onclick=async()=>{if(working||!selected||!resumeId||!confirmDiscard())return;working=true;buttons();try{let data=await(await api('/api/analyses/'+resumeId)).json();if(data.profile_id!==selected.id)throw Error('This saved review belongs to another plant. Refresh its plant selection.');if(['UPLOADED','ERROR','ANALYZING'].includes(data.status)){setPhase('analyzing');data=await json('/api/analyses/'+data.id+'/analyze',{});}setPhase('review-loading');await showAnalysis(data);await pending();say('Saved review restored. Human approval is still pending.');}catch(error){say(error.message,true);}finally{working=false;setPhase('');buttons();}};
window.addEventListener('keydown',e=>{if(e.key==='Escape'&&editor&&!working){editor.cancelDrawing();leafMode('select');}if((e.ctrlKey||e.metaKey)&&['z','y'].includes(e.key.toLowerCase())&&editor&&!working&&!editor.drawing&&($('review-card').classList.contains('editing')||!$('tool-drawer').hidden)&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)){e.preventDefault();e.key.toLowerCase()==='y'||e.shiftKey?redoEditor():undoEditor();}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
if($('login-form'))$('login-form').onsubmit=async e=>{e.preventDefault();try{await json('/api/login',{token:$('access-key').value});$('access-key').value='';await init();}catch(error){say(error.message,true);}};
$('logout').onclick=async()=>{if(!confirmDiscard())return;await json('/api/logout',{});if(globalThis.leafScienceSession?.integrated)location.assign('/signin');else location.reload();};
$('copy-photo-diagnostic').onclick=async()=>{const text=$('photo-diagnostic-text').textContent;try{await navigator.clipboard.writeText(text);$('photo-copy-status').textContent='Error details copied. No photo, filename or account information is included.';}catch{const range=document.createRange();range.selectNodeContents($('photo-diagnostic-text'));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);$('photo-copy-status').textContent='Select and copy these error details.';}};
globalThis.leafSessionRecovery=()=>{$('session-recovery').hidden=false;};
$('session-reconnect').onclick=reconnectSession;
$('pot-fix').onclick=()=>{if(working)return;const hint=calibrationGuidance({diameter:Number($('pot-diameter').value),distance:potPicker?.distance(),points:potPicker?.points.length||0,previewReady:Boolean(potPicker?.image),saving:calibrationSaving});if(hint.target==='pot-preview-retry')$('pot-preview-retry').click();else focusStep(hint.target);};
$('review-change-photo').onclick=()=>$('photo').click();$('calibration-change-photo').onclick=()=>$('photo').click();
$('choose-photo').onclick=()=>$('photo').click();$('change-photo').onclick=()=>{focusStep('dropzone');$('photo').click();};
$('progress-details').ontoggle=()=>{$('progress-toggle-label').textContent=$('progress-details').open?'Hide steps':'View steps';};
$('workflow-action').onclick=()=>workflowAction().catch(e=>say(e.message,true));
$('workflow-tips').onclick=()=>{const hidden=!$('workspace').classList.contains('tips-hidden');try{const key=tipsKey();if(key)localStorage.setItem(key,hidden?'hidden':'visible');}catch{}syncTips();};
$('review-continue').onclick=()=>workflowAction('review');
$('approval-fix').onclick=()=>workflowAction(guidance?.action).catch(e=>say(e.message,true));
$('view-original').onclick=()=>viewPhoto(true);$('view-overlay').onclick=()=>viewPhoto(false);
$('view-growth').onclick=()=>focusStep('history-card');
$('another-photo').onclick=()=>{if(working)return;approvalConflict='';analysis=null;file=null;pendingPhoto=null;imageProvenance=null;uploadedReview=null;uploadRequestId=null;dirty=false;calibrationDeferred=false;calibrationSaved=false;reviewVisited=false;$('review-card').hidden=true;$('result-card').hidden=true;$('calibration-card').hidden=true;$('photo-progress').textContent='';$('file-label').textContent='Take a photo or choose JPEG, PNG or HEIC';buttons();focusStep('dropzone');};
for(const n of document.querySelectorAll('[data-workflow-step]'))n.onclick=()=>{const target={plant:'plant',photo:'choose-photo',calibration:'calibration-open',analyze:'analyze',review:'editor-viewport',approve:'approval-guidance',results:'result-card'}[n.dataset.workflowStep];focusStep(target);};

function accountChrome(session){
 const header=document.querySelector('header');header.classList.add('integrated-header');
 document.querySelector('header h1').textContent='Analyze a plant.';
 document.title='Analyze a plant · Leaf Research';
 let identity=$('science-identity'),nav=$('science-navigation');
 if(!identity){identity=document.createElement('p');identity.id='science-identity';header.append(identity);}
 identity.textContent=session.organization?.name||'University workspace';
 if(!nav){nav=document.createElement('nav');nav.id='science-navigation';nav.setAttribute('aria-label','Account navigation');header.append(nav);}
 nav.replaceChildren();
 const links=[['Analyze','/analyze'],['My university','/institution'],['Profile','/profile']];
 if(['ORG_ADMIN','PLATFORM_ADMIN','PLATFORM_OWNER'].includes(session.user?.role))links.push(['Admin','/admin']);
 for(const [label,url] of links){const link=document.createElement('a');link.textContent=label;link.href=url;if(url==='/help'){link.target='_blank';link.rel='noopener';link.title='Open the user guide in a new tab';}if(url==='/analyze')link.setAttribute('aria-current','page');nav.append(link);}
}
async function init(){working=true;buttons();try{
  const session=await(await api('/api/session')).json();
  session.integrated=Boolean(session.integrated||document.body?.dataset.integrated==='true');globalThis.leafScienceSession=session;
  if($('login-panel'))$('login-panel').hidden=session.authenticated||session.integrated;
  $('workspace').hidden=!session.authenticated;$('setup-open').hidden=!session.authenticated;$('logout').hidden=!session.authenticated||!session.auth_required;
  if(session.integrated){
   accountChrome(session);
   if(!session.authenticated){location.assign('/signin');return;}
   if(session.user?.status!=='ACTIVE') {location.assign('/pending');return;}
   if(!session.user.email_verified_at&&!session.email_verification_deferred){location.assign('/profile');return;}
  }
  if(!session.authenticated)return;$('progress-details').open=Boolean(globalThis.matchMedia?.('(min-width:600px)').matches);syncTips();
  if(globalThis.matchMedia){const wide=matchMedia('(min-width:600px)');wide.addEventListener('change',e=>{$('progress-details').open=e.matches;});}
   config=await(await api('/api/config')).json();setupDefault=(await(await api('/api/setup')).json()).default_calibration;
  const now=new Date();$('photo-date').value=[now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
  await refreshProfiles(new URLSearchParams(location.search).get('plant'));working=false;buttons();say(profiles.length?'Select a plant and today’s photo.':'Create a plant profile to begin.');renderGuidance();
 }catch(error){say(error.message,true);}finally{working=false;buttons();}
}


init();
