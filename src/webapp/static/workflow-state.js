import {approvalState,calibrationIssue,reviewScale} from './review-state.js';
export const workflowSteps=['plant','photo','calibration','analyze','review','approve','results'];
const names=['Plant','Photo','Calibration','Analyze','Review','Approve','Results'];
export function workflowState(c={}){
 const {selected,file,analysis,stats,confirmed=false,working=false,saving=false,drawing=false,geometryUnchanged=true}=c;
 const saved=analysis?.status==='APPROVED',ready=Boolean(analysis?.mask_base64&&['ANALYZED','DRAFT','APPROVED'].includes(analysis.status));
 const photo=Boolean(file||ready),assignments=Boolean(stats?.total&&stats?.ids?.length&&!stats.unassigned&&!stats.invalid&&!drawing);
 const scale=Boolean(analysis&&reviewScale(analysis,geometryUnchanged)!==null),pixelOnly=Boolean(ready&&!analysis.calibration&&!analysis.requires_calibration);
 const validatedApproval=approvalState({analysis,selected,stats,confirmed,working,saving,drawing,geometryUnchanged});
 const approval=c.approvalConflict?{enabled:false,reason:c.approvalConflict}:validatedApproval;
 let step=!selected?'plant':!photo?'photo':'analyze';
 if(c.phase==='preparing')step='photo';
 else if(saved)step='results';
 else if(photo&&c.calibrationOpen)step='calibration';
 else if(ready)step=c.reviewVisited||confirmed?'approve':'review';
 else if(photo&&(c.calibrationOpen||(!selected?.calibration&&!c.calibrationDeferred&&!c.calibrationSaved)))step='calibration';
 if(['uploading','analyzing','review-loading'].includes(c.phase))step='analyze';
 const complete={plant:Boolean(selected),photo,calibration:scale||Boolean(c.calibrationSaved),analyze:ready,review:confirmed&&assignments,approve:saved,results:saved};
 const steps=workflowSteps.map((key,i)=>({key,label:names[i],complete:complete[key],current:key===step,optional:key==='calibration'&&(pixelOnly||Boolean(c.calibrationDeferred&&!analysis)||Boolean(selected?.calibration&&!analysis&&!c.calibrationOpen)),available:key==='plant'||key==='photo'||key==='calibration'&&photo&&!saved||key==='analyze'&&photo||['review','approve'].includes(key)&&ready&&!saved||key==='results'&&saved}));
 let action={plant:'plant',photo:c.prepareFailed?'retry':c.resumeId&&!c.phase?'resume':'photo',calibration:'calibrate',analyze:'analyze',review:'review',approve:'approve',results:'growth'}[step];
 if(step==='approve')action=!assignments?'editor':calibrationIssue(analysis,geometryUnchanged)||analysis?.requires_calibration&&!analysis.calibration?'scale':!confirmed?'confirm':'approve';
 if(c.approvalConflict&&ready&&!saved)action='date';
 if(working||drawing)action='wait';
 const text={plant:['Choose your plant','Choose your plant or create a new one.','Choose plant'],photo:['Choose a photo','Take a clear photo showing the whole plant and outer pot rim.','Take or choose photo'],calibration:['Set the photo scale','Tap two opposite points on the outer pot rim, then save. You can review in pixels first.','Set photo scale'],analyze:['Analyze the photo',selected?.calibration?'Press Analyze to identify visible leaves. The saved scale will be checked against this photo.':'Press Analyze to identify visible leaves. Without a verified scale, areas stay in pixels.','Analyze photo'],review:['Check the colored leaves','Check that each visible leaf has its own color. Correct mistakes, then review the measurement.','Review measurement'],approve:['Approve your measurement','Check the items below. Your review and approval are required before saving.','Approve measurement'],results:['Measurement saved','Your measurement is saved. View growth or analyze another photo.','View growth']}[step];
 const labels={date:'Check photo date',resume:'Resume unfinished review',retry:'Try photo again',editor:'Return to leaf editor',scale:'Review calibration',confirm:'Review confirmation',wait:working?'Please wait…':'Finish the current stroke'};
 return {step,index:workflowSteps.indexOf(step)+1,steps,title:text[0],instruction:c.approvalConflict||text[1],action,label:labels[action]||text[2],approval,
 checks:[...(c.approvalConflict?[{key:'date',label:'A measurement is already saved for this date',complete:false}]:[]),{key:'photo',label:'Photo uploaded',complete:photo},{key:'scale',label:scale?'Valid calibration':pixelOnly?'Pixels only — cm² unavailable':'Calibration needs checking',complete:scale,optional:pixelOnly},{key:'assignments',label:assignments?'Leaf assignments complete':stats?.unassigned?'Some leaf pixels are unassigned':'Check leaf assignments',complete:assignments},{key:'confirmed',label:'Review confirmed',complete:confirmed}]};
}
