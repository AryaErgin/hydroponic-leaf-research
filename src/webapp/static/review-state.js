// Shared deterministic UI policy. Missing historical metrics remain missing.
export function calibrationIssue(analysis,geometryUnchanged=true){
 const c=analysis?.calibration;if(!c)return '';
 if(geometryUnchanged===false)return 'Cannot approve: the camera setup was marked changed. Mark the pot edges in this photo.';
 if(analysis.calibration_reuse){
  const r=analysis.calibration_reuse;
  if(!['VERIFIED','CONFIRMED'].includes(r.status))return r.status==='CONFIRMATION_REQUIRED'?'Cannot approve: this photo’s scale needs checking. Mark the pot edges, or confirm an unchanged setup.':'Cannot approve: mark the pot edges in this photo to set its scale.';
  if(!Number.isFinite(analysis.effective_cm_per_pixel)||analysis.effective_cm_per_pixel<=0)return 'Cannot approve: photo scale is invalid. Recalibrate.';
  return '';
 }
 if(analysis.geometry_unchanged===false)return 'Cannot approve: camera geometry changed. Recalibrate this photo.';
 if(c.reference_size_px&&analysis.image_size_px&&c.reference_size_px.some((v,i)=>v!==analysis.image_size_px[i]))return 'Cannot approve: check the scale on this photo. Tap Recalibrate and mark the pot edges.';
 if(!Number.isFinite(c.cm_per_pixel)||c.cm_per_pixel<=0)return 'Cannot approve: the saved physical scale is invalid. Recalibrate.';
 return '';
}
export function reviewScale(analysis,geometryUnchanged=true){
 if(!analysis?.calibration||calibrationIssue(analysis,geometryUnchanged))return null;
 const scale=analysis.calibration_reuse?analysis.effective_cm_per_pixel:analysis.calibration.cm_per_pixel;
 return Number.isFinite(scale)&&scale>0?scale**2:null;
}
export function approvalState({analysis,selected,stats,confirmed,working=false,saving=false,drawing=false,geometryUnchanged=true}){
 let reason='';
 if(saving)reason='Saving measurement… Please wait.';
 else if(working)reason='Cannot approve: a request is currently running.';
 else if(!analysis)reason='Cannot approve: load an analyzed photo first.';
 else if(analysis.status==='APPROVED')reason='Measurement saved and approved.';
 else if(!['ANALYZED','DRAFT'].includes(analysis.status))reason='Cannot approve: this analysis is not ready for review.';
 else if(analysis.profile_id!==selected?.id)reason='Cannot approve: select the plant that owns this review.';
 else if(drawing)reason='Cannot approve: finish or cancel the current stroke.';
 else if(stats?.unassigned)reason=`Cannot approve: ${stats.unassigned.toLocaleString()} lamina pixels are unassigned.`;
 else if(stats?.invalid)reason='Cannot approve: invalid leaf assignments extend outside the lamina mask.';
 else if(!stats?.total||!stats?.ids?.length)reason='Cannot approve: the lamina mask has no assigned leaves.';
 else if(calibrationIssue(analysis,geometryUnchanged))reason=calibrationIssue(analysis,geometryUnchanged);
 else if(analysis.requires_calibration&&!analysis.calibration)reason='Cannot approve: a reviewed calibration is required.';
 else if(!confirmed)reason='Cannot approve: confirm the leaf lamina, identities, exclusions and scale review.';
 return {enabled:!reason,reason:reason||'Ready to approve the current mask and leaf assignments.'};
}
export function median(values){if(!values.length)return null;const a=[...values].sort((a,b)=>a-b),i=Math.floor(a.length/2);return a.length%2?a[i]:(a[i-1]+a[i])/2;}
export function captureTime(row){const value=row.captured_at||row.date;return {label:value,time:Date.parse(value?.length===10?value+'T00:00:00Z':value)};}
export function graphSeries(rows,key='total'){
 const physical={total:r=>r.total_projected_area_cm2??r.projected_area_cm2,mean:r=>r.mean_leaf_area_cm2,median:r=>r.median_leaf_area_cm2};
 const pixels={total:r=>r.total_pixel_area??r.pixel_area,mean:r=>r.mean_leaf_pixel_area,median:r=>r.median_leaf_pixel_area};
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const scaled=key!=='count'&&rows.some(r=>finite(physical[key](r)));
 const unit=key==='count'?'leaves':scaled?'cm²':'px';
 const read=key==='count'?r=>r.leaf_count:scaled?physical[key]:pixels[key];
 return {unit,label:{total:'Total visible projected lamina area',count:'Visible leaf count',mean:'Mean leaf lamina area',median:'Median leaf lamina area'}[key],
  points:rows.map(record=>({...captureTime(record),record,value:read(record)})).filter(p=>finite(p.value)&&Number.isFinite(p.time)).sort((a,b)=>a.time-b.time)};
}
