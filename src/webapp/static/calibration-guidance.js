// Explain existing readiness checks; physical validation remains server-authoritative.
export function calibrationGuidance({diameter,distance,points=0,previewReady=false,saving=false}={}){
 if(saving)return {ready:false,target:null,message:'Saving calibration. Keep this photo open.'};
 if(!previewReady)return {ready:false,target:'pot-preview-retry',message:'Your photo is kept. Retry the scale preview to mark the rim.'};
 if(!Number.isFinite(diameter)||diameter<=0||diameter>1000)return {ready:false,target:'pot-diameter',message:'Enter the measured outside pot diameter in cm.'+(points===2?' Your two points are kept.':'')};
 if(points<2)return {ready:false,target:'pot-canvas',message:points?'Tap the opposite outside edge.':'Tap the first outside edge.'};
 if(!(distance>=1))return {ready:false,target:'pot-canvas',message:'Choose two different points on opposite outside edges.'};
 return {ready:true,target:'pot-save',message:'Both rim points and the pot diameter are ready. Save calibration to estimate area in cm\u00b2.'};
}
