const categorical=[[0,114,178],[230,159,0],[0,158,115],[204,51,102],[86,180,233],[213,94,0],[153,102,204],[240,228,66],[0,170,170],[180,80,0],[190,110,210],[90,150,20]];
export function leafColor(id){return categorical[(id-1+categorical.length)%categorical.length];}
export function instanceColors(labels,width,height){
 const adjacent=new Map();for(let i=0;i<labels.length;i++){const id=labels[i];if(!id)continue;if(!adjacent.has(id))adjacent.set(id,new Set());for(const j of [i%width<width-1?i+1:-1,i+width<labels.length?i+width:-1]){const other=j>=0?labels[j]:0;if(other&&other!==id){if(!adjacent.has(other))adjacent.set(other,new Set());adjacent.get(id).add(other);adjacent.get(other).add(id);}}}
 const colors=new Map(),distance=(a,b)=>a.reduce((s,v,i)=>s+(v-b[i])**2,0);
 for(const id of [...adjacent.keys()].sort((a,b)=>a-b)){const near=[...adjacent.get(id)].filter(i=>colors.has(i)).map(i=>colors.get(i));let c=leafColor(id);if(near.some(n=>distance(c,n)<100**2))c=categorical.reduce((best,c)=>Math.min(...near.map(n=>distance(c,n)))>Math.min(...near.map(n=>distance(best,n)))?c:best,categorical[0]);colors.set(id,c);}
 return colors;
}
// Adapted from target_review.html: native binary pixels, disc/line brush,
// nearest-neighbor display, per-stroke undo, and review reset on every mutation.
export class MaskEditor {
 constructor(stage,canvas,cursor,onChange,onSplit=()=>{},onSelect=()=>{},onMessage=()=>{}){
  this.onMessage=onMessage;this.opacity=.65;this.revision=0;
  this.onSplit=onSplit;this.onSelect=onSelect;this.selectedLeaf=1;this.edits=[];this.palette=Array.from({length:4097},(_,i)=>i?leafColor(i):[255,160,40]);this.totalColor=[0,220,255];
  this.stage=stage;this.canvas=canvas;this.cursor=cursor;this.onChange=onChange;
  this.ctx=canvas.getContext('2d');this.history=[];this.future=[];this.mode='paint';this.radius=6;
  this.editable=false;this.busy=false;this.drawing=false;this.visible=true;
  this.strokeFrame=null;this.dirtyRegion=null;
  const down=e=>{
   if(!this.pixels||!this.editable||this.busy||!this.visible||e.button!==0)return;
   const point=this.point(e);if(!point)return;e.preventDefault();
   if(this.mode==='select'){const id=this.labels?.[Math.floor(point[1])*this.width+Math.floor(point[0])];if(id){this.selectedLeaf=id;this.onSelect();}return;}
   if(['select','merge'].includes(this.mode)){return;}
   if(this.mode==='add'&&this.selectedLeaf!==this.addLeafId){this.mode='select';this.addLeafId=null;this.onMessage('Add target was cancelled. Select Add leaf to start a new instance.');this.onSelect();return;}
   if(this.mode==='reassign'&&!this.labels?.includes(this.selectedLeaf)){this.onMessage('Select an existing leaf, or use Add leaf for unassigned lamina.');this.onSelect();return;}
   if(this.mode==='paint'&&!this.labels?.includes(this.selectedLeaf)&&this.nextLabelId>4096){this.onMessage('Leaf identity limit reached; this stroke cannot create another instance.');this.onSelect();return;}
   this.beginGesture();
   if(this.mode==='paint'&&this.labels&&!this.labels.includes(this.selectedLeaf)){this.selectedLeaf=this.nextLabelId;this.registerInstance(this.selectedLeaf);}
   if(this.mode==='split'){this.splitPoints=[point];this.splitDrawn=1;}else if(this.mode==='add')this.beginAdd();else this.saveUndo();
   this.drawing=true;this.last=point;if(!this.viewport)canvas.setPointerCapture(e.pointerId);if(this.mode!=='split')this.stroke(point,point);
   this.onSelect();
  };
  const move=e=>{
   if(!this.pixels)return;const point=this.point(e);this.showCursor(point);
   if(!this.drawing)return;
   const samples=e.getCoalescedEvents?.()||[];
   for(const sample of [...samples,e]){const p=this.point(sample);if(!p){this.last=null;continue;}
    if(this.mode==='split'){if(this.splitPoints.length<2048)this.splitPoints.push(p);this.requestStrokeRender();}
    else this.stroke(this.last||p,p);this.last=p;
   }
  };
  const finish=e=>{if(this.drawing){if(e){const p=this.point(e);if(p&&this.mode==='split')this.splitPoints.push(p);else if(p)this.stroke(this.last||p,p);}this.drawing=false;this.last=null;this.gestureState=null;if(this.mode==='split'){const points=this.splitPoints;this.splitPoints=null;this.render();this.onSplit(points);}else if(this.mode==='add')this.commitAdd();else{this.record(this.mode==='reassign'?'reassign':'total_mask',[this.selectedLeaf]);this.render();this.onChange();}this.onSelect({rendered:true});}this.gestureState=null;};
  if(globalThis.ImageViewport&&stage.parentElement?.addEventListener){
   this.viewport=new globalThis.ImageViewport(stage.parentElement,stage,{canInteract:()=>!!this.pixels&&!this.busy,panMode:()=>this.panMode||this.mode==='pan'||!this.editable||!this.visible,onChange:v=>{this.scale=v.scale;if(!v.pointers.size)this.render();},onGestureEnd:()=>{if(this.renderScale!==this.scale)this.render();},onGestureStart:()=>this.cancelDrawing(),onPointerDown:down,onPointerMove:move,onPointerUp:finish,onPointerCancel:()=>this.cancelDrawing(),onHover:move});
  }else{canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',finish);canvas.addEventListener('pointercancel',()=>this.cancelDrawing());canvas.addEventListener('lostpointercapture',()=>this.cancelDrawing());}
  canvas.addEventListener('pointerleave',()=>cursor.hidden=true);
 }
 load(width,height,encoded){
  this.viewport?.abort();
  this.width=width;this.height=height;this.pixels=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
  if(this.pixels.length!==width*height)throw Error('Invalid native mask');
  this.history=[];this.future=[];this.labels=null;this.edits=[];this.splitPoints=null;this.selectedLeaf=1;this.mode='select';this.addLeafId=null;this.addCandidate=null;this.addArmState=null;this.instanceRegistry=new Map();this.instanceOrder=[];this.nextLabelId=1;this.canvas.width=width;this.canvas.height=height;this.render();this.fit();
 }
 point(e){if(this.viewport)return this.viewport.point(e,this.canvas);const r=this.canvas.getBoundingClientRect(),x=(e.clientX-r.left)*this.width/r.width,y=(e.clientY-r.top)*this.height/r.height;return x<0||y<0||x>=this.width||y>=this.height?null:[x,y];}
 disc([x,y]){
  if(this.mode==='add'&&this.selectedLeaf!==this.addLeafId)throw Error('Add must target its new leaf; use Reassign for an existing leaf');
  if(!this.drawing&&this.labels&&this.mode==='reassign'&&!this.labels.includes(this.selectedLeaf)){this.onMessage('Select an existing leaf, or use Add leaf for unassigned lamina.');return;}
  if(!this.drawing&&this.labels&&this.mode==='paint'&&!this.labels.includes(this.selectedLeaf)){if(this.nextLabelId>4096)throw Error('Leaf identity limit reached');this.selectedLeaf=this.nextLabelId;this.registerInstance(this.selectedLeaf);}
  const r=Math.max(1,Math.min(80,this.radius)),value=this.mode==='paint'?1:0;
  const margin=r+Math.max(2,Math.ceil(2/(this.scale||1))),box=[Math.max(0,Math.floor(x-margin)),Math.max(0,Math.floor(y-margin)),Math.min(this.width,Math.ceil(x+margin+1)),Math.min(this.height,Math.ceil(y+margin+1))];
  this.dirtyRegion=this.dirtyRegion?[Math.min(this.dirtyRegion[0],box[0]),Math.min(this.dirtyRegion[1],box[1]),Math.max(this.dirtyRegion[2],box[2]),Math.max(this.dirtyRegion[3],box[3])]:box;
  for(let py=Math.max(0,Math.ceil(y-r));py<=Math.min(this.height-1,Math.floor(y+r));py++)
   for(let px=Math.max(0,Math.ceil(x-r));px<=Math.min(this.width-1,Math.floor(x+r));px++)
    if((px-x)**2+(py-y)**2<=r*r){const i=py*this.width+px;if(this.mode==='add'){if(this.addCandidate&&!this.addCandidate.pixels[i]){this.addCandidate.pixels[i]=1;this.addCandidate.count++;}}else if(this.mode==='reassign'){if(this.pixels[i]&&this.labels)this.labels[i]=this.selectedLeaf;}else if(['paint','erase'].includes(this.mode)){this.pixels[i]=value;if(this.labels)this.labels[i]=value?(this.labels[i]||this.selectedLeaf):0;}}
  if(this.mode!=='add')this.revision++;
 }
 stroke(a,b){const steps=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/Math.max(1,this.radius/2)));for(let i=0;i<=steps;i++)this.disc([a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps]);if(this.drawing)this.requestStrokeRender();else this.render();if(this.mode==='add'){this.onMessage('Selecting '+(this.addCandidate?.count||0)+' image pixels for a new leaf blade, including missed areas. Release to add lamina and create the leaf; Escape cancels.');}else if(!this.gestureState)this.onChange();}
 requestStrokeRender(){
  if(this.strokeFrame!==null)return;
  if(!globalThis.requestAnimationFrame){this.renderStroke();return;}
  this.strokeFrame=globalThis.requestAnimationFrame(()=>{this.strokeFrame=null;this.renderStroke();});
 }
 renderStroke(){
  // Only the brush's changed rectangle is rasterized while a finger is down.
  // Metrics, adjacency colors, outlines and label centers are recomputed once
  // on release, never by scanning the whole photograph for each move event.
  if(this.mode==='split'&&this.splitPoints?.length){const points=this.splitPoints,start=Math.max(0,(this.splitDrawn||1)-1);this.ctx.beginPath();this.ctx.strokeStyle='#ff3050';this.ctx.lineWidth=2/(this.scale||1);this.ctx.moveTo(...points[start]);for(let i=start+1;i<points.length;i++)this.ctx.lineTo(...points[i]);this.ctx.stroke();this.splitDrawn=points.length;return;}
  const box=this.dirtyRegion;this.dirtyRegion=null;if(!box)return;
  const [left,top,right,bottom]=box,w=right-left,h=bottom-top;if(w<=0||h<=0)return;
  const image=this.ctx.createImageData(w,h),colors=this.currentColors,thickness=Math.max(1,Math.ceil(1/(this.scale||1)));
  for(let y=top;y<bottom;y++)for(let x=left;x<right;x++){const i=y*this.width+x,preview=this.addCandidate?.pixels[i];if(!this.pixels[i]&&!preview)continue;
   const id=this.labels?.[i]||0,j=((y-top)*w+x-left)*4;let c=this.labels?(colors?.get(id)||this.palette[id]||[255,160,40]):this.totalColor,alpha=Math.round(this.opacity*255);
   if(id){const k=id===this.selectedLeaf?thickness*2:thickness;if(x<k||y<k||x>=this.width-k||y>=this.height-k||this.labels[i-k]!==id||this.labels[i+k]!==id||this.labels[i-k*this.width]!==id||this.labels[i+k*this.width]!==id){c=id===this.selectedLeaf?[255,255,255]:[15,15,15];alpha=255;}}
   if(preview){c=[255,0,220];alpha=235;}image.data[j]=c[0];image.data[j+1]=c[1];image.data[j+2]=c[2];image.data[j+3]=alpha;
  }
  this.ctx.putImageData(image,left,top);
 }
 render(){if(this.strokeFrame!==null){globalThis.cancelAnimationFrame?.(this.strokeFrame);this.strokeFrame=null;}this.dirtyRegion=null;if(!this.pixels)return;this.renderScale=this.scale;if(this.labels)this.stats();const image=this.ctx.createImageData(this.width,this.height),colors=this.labels?instanceColors(this.labels,this.width,this.height):null,centers=new Map(),thickness=Math.max(1,Math.ceil(1/(this.scale||1)));this.currentColors=colors;
  const edge=(x,y,id,k)=>x<k||y<k||x>=this.width-k||y>=this.height-k||this.labels[y*this.width+x-k]!==id||this.labels[y*this.width+x+k]!==id||this.labels[(y-k)*this.width+x]!==id||this.labels[(y+k)*this.width+x]!==id;
  for(let i=0;i<this.pixels.length;i++)if(this.pixels[i]||this.addCandidate?.pixels[i]){const id=this.labels?.[i]||0,j=i*4,x=i%this.width,y=Math.floor(i/this.width);let c=this.labels?(colors.get(id)||[255,160,40]):this.totalColor,alpha=Math.round(this.opacity*255);
   if(id){if(!centers.has(id))centers.set(id,{x:0,y:0,n:0});const a=centers.get(id);a.x+=x;a.y+=y;a.n++;if(edge(x,y,id,id===this.selectedLeaf?thickness*2:thickness)){c=id===this.selectedLeaf?[255,255,255]:[15,15,15];alpha=255;}}
   if(this.addCandidate?.pixels[i]){c=[255,0,220];alpha=235;}
   image.data[j]=c[0];image.data[j+1]=c[1];image.data[j+2]=c[2];image.data[j+3]=alpha;}
  this.ctx.putImageData(image,0,0);
  if(this.ctx.fillText&&this.labels){for(const [id,a] of centers){a.x/=a.n;a.y/=a.n;a.best=Infinity;}for(let i=0;i<this.labels.length;i++){const a=centers.get(this.labels[i]);if(!a)continue;const x=i%this.width,y=Math.floor(i/this.width),d=(x-a.x)**2+(y-a.y)**2;if(d<a.best){a.best=d;a.at=[x,y];}}this.ctx.font=`bold ${14/(this.scale||1)}px system-ui`;this.ctx.textAlign='center';this.ctx.textBaseline='middle';this.ctx.lineWidth=3/(this.scale||1);this.ctx.strokeStyle='#101010';this.ctx.fillStyle='white';for(const[id,a]of centers){const number=String(this.displayNumber(id));this.ctx.strokeText?.(number,...a.at);this.ctx.fillText(number,...a.at);}}
  if(this.splitPoints?.length){this.ctx.beginPath();this.ctx.strokeStyle='#ff3050';this.ctx.lineWidth=2/(this.scale||1);this.ctx.moveTo(...this.splitPoints[0]);for(const p of this.splitPoints.slice(1))this.ctx.lineTo(...p);this.ctx.stroke();}}
 beginGesture(){this.gestureState={state:this.snapshot(),registry:new Map(this.instanceRegistry),order:[...this.instanceOrder],next:this.nextLabelId,revision:this.revision,history:[...this.history],future:[...this.future],edits:[...this.edits],addArmState:this.addArmState};}
 cancelDrawing(){
  if(this.gestureState){const g=this.gestureState,s=g.state;this.gestureState=null;this.pixels=s.pixels;this.labels=s.labels;this.selectedLeaf=s.selectedLeaf;this.mode=s.mode;this.addLeafId=s.addLeafId;this.instanceRegistry=g.registry;this.instanceOrder=g.order;this.nextLabelId=g.next;this.revision=g.revision;this.history=g.history;this.future=g.future;this.edits=g.edits;this.addArmState=g.addArmState;this.drawing=false;this.last=null;this.splitPoints=null;this.addCandidate=null;this.render();this.onSelect();return;}
  if(!this.drawing)return;const split=this.mode==='split',add=this.mode==='add';this.drawing=false;this.last=null;this.splitPoints=null;if(add){this.addCandidate=null;this.render();this.onMessage('New leaf selection cancelled; pixel ownership is unchanged.');}else if(!split)this.undo();else this.render();this.onSelect();
 }
 restoreState(state){this.pixels=state.pixels;this.labels=state.labels;this.selectedLeaf=state.selectedLeaf;this.mode=state.mode;this.addLeafId=state.addLeafId;this.addCandidate=null;this.addArmState=null;this.restoreIdentity(state.instance_metadata);this.revision++;this.render();this.onChange();}
 undo(){if(this.busy)return false;if(this.mode==='add'&&this.addArmState&&!this.drawing){Object.assign(this,this.addArmState);this.addArmState=null;this.addCandidate=null;this.render();this.onSelect();return true;}if(!this.history.length)return false;this.future.push(this.snapshot());const state=this.history.pop();this.record('undo',[]);this.restoreState(state);return true;}
 redo(){if(this.busy||!this.future.length)return false;this.history.push(this.snapshot());const state=this.future.pop();this.record('redo',[]);this.restoreState(state);return true;}
 clear(){if(this.busy)return;this.saveUndo();this.pixels.fill(0);this.labels?.fill(0);this.record('total_mask',[]);this.render();this.onChange();}
 zoom(scale){if(this.viewport){this.viewport.zoom(scale);return;}this.scale=scale;this.stage.style.width=Math.round(this.width*scale)+'px';this.stage.style.height=Math.round(this.height*scale)+'px';this.render();}
 fit(){if(this.viewport){this.viewport.width=this.width;this.viewport.height=this.height;this.viewport.fit();return;}const view=this.stage.parentElement;this.zoom(Math.max(.1,Math.min(1,(view.clientWidth-16)/this.width,(view.clientHeight-16)/this.height)));}
 show(value){this.visible=value;this.canvas.style.opacity=value?'1':'0';}
 showCursor(point){if(!point||!this.editable){this.cursor.hidden=true;return;}const r=2*this.radius*this.scale;Object.assign(this.cursor.style,{width:r+'px',height:r+'px',left:point[0]*this.scale-r/2+'px',top:point[1]*this.scale-r/2+'px'});this.cursor.hidden=false;}
 loadInstances(encoded,analysisId,metadata=null){
  const raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));if(raw.length!==this.pixels.length*2)throw Error('Invalid native leaf labels');
  const view=new DataView(raw.buffer);this.labels=new Uint16Array(this.pixels.length);for(let i=0;i<this.labels.length;i++){this.labels[i]=view.getUint16(i*2,true);if(this.labels[i]>4096||(!this.pixels[i]&&this.labels[i]))throw Error('Invalid leaf assignments');}
  this.analysisId=analysisId;this.history=[];this.future=[];this.instanceRegistry=new Map();this.instanceOrder=[];this.nextLabelId=1;
  if(metadata)this.restoreIdentity(metadata);
  for(const id of new Set(this.labels))if(id&&!this.instanceRegistry.has(id))this.registerInstance(id,analysisId+':instance:'+id);
  this.selectedLeaf=this.stats().ids[0]||1;this.edits=[];this.render();
 }
 registerInstance(label,identity=globalThis.crypto.randomUUID()){if(this.instanceRegistry.has(label)&&this.instanceRegistry.get(label)!==identity)throw Error('Existing internal instance identity must remain stable');this.instanceRegistry.set(label,identity);this.nextLabelId=Math.max(this.nextLabelId,label+1);}
 restoreIdentity(metadata){for(const row of metadata.instances)this.registerInstance(row.label_id,row.internal_instance_id);this.instanceOrder=[...metadata.display_order];this.nextLabelId=Math.max(this.nextLabelId||1,metadata.next_label_id);}
 instanceMetadata(){this.stats();return {version:1,instances:[...this.instanceRegistry].sort((a,b)=>a[0]-b[0]).map(([label_id,internal_instance_id])=>({label_id,internal_instance_id,active:this.instanceOrder.includes(label_id)})),display_order:[...this.instanceOrder],next_label_id:this.nextLabelId};}
 displayNumber(id){const at=this.instanceOrder.indexOf(id);return at<0?null:at+1;}
 internalId(id){return this.instanceRegistry.get(id);}
 snapshot(){return {pixels:this.pixels.slice(),labels:this.labels?.slice(),selectedLeaf:this.selectedLeaf,mode:this.mode,addLeafId:this.addLeafId,instance_metadata:this.instanceMetadata()};}
 saveUndo(){this.history.push(this.gestureState?.state||this.snapshot());this.future=[];if(this.history.length>15)this.history.shift();}
 record(operation,leaf_ids){leaf_ids=leaf_ids.filter(i=>i>0&&i<=4096);this.edits.push({operation,leaf_ids,internal_instance_ids:leaf_ids.map(i=>this.internalId(i)),display_leaf_numbers:leaf_ids.map(i=>this.displayNumber(i))});if(this.edits.length>200)this.edits.shift();}
 stats(){const counts=new Map();let total=0,unassigned=0,invalid=0;for(let i=0;i<this.pixels.length;i++){const id=this.labels?.[i]||0;if(id>4096||(!this.pixels[i]&&id))invalid++;if(this.pixels[i]){total++;if(!id)unassigned++;else counts.set(id,(counts.get(id)||0)+1);}}this.instanceOrder=this.instanceOrder.filter(i=>counts.has(i));for(const id of [...counts.keys()].sort((a,b)=>a-b)){if(!this.instanceRegistry.has(id))this.registerInstance(id);if(!this.instanceOrder.includes(id))this.instanceOrder.push(id);}const ids=[...this.instanceOrder];return{ids,areas:ids.map(i=>counts.get(i)),total,unassigned,invalid};}
 mergeLeaves(ids){if(this.busy)return;ids=[...new Set(ids)];if(ids.length<2)throw Error('Select at least two leaves in the table');const available=this.stats().ids;if(ids.some(i=>!available.includes(i)))throw Error('Select existing leaves');this.saveUndo();const target=available.find(i=>ids.includes(i)),set=new Set(ids);for(let i=0;i<this.labels.length;i++)if(set.has(this.labels[i]))this.labels[i]=target;this.selectedLeaf=target;this.record('merge',ids);this.render();this.onChange();}
 deleteLeaf(id){if(this.busy)return;if(!this.stats().ids.includes(id))throw Error('Select an existing leaf');this.saveUndo();for(let i=0;i<this.labels.length;i++)if(this.labels[i]===id)this.labels[i]=0;this.record('delete',[id]);this.render();this.onChange();}
 addLeaf(){if(this.busy)return;const id=this.nextLabelId;if(id>4096)throw Error('Leaf identity limit reached for this review');if(this.mode!=='add'||!this.addArmState)this.addArmState={selectedLeaf:this.selectedLeaf,mode:this.mode,addLeafId:this.addLeafId};this.selectedLeaf=id;this.addLeafId=id;this.mode='add';this.render();return id;}
 beginAdd(){const before={...(this.gestureState?.state||this.snapshot())};Object.assign(before,this.addArmState||{mode:'select',addLeafId:null});this.addCandidate={pixels:new Uint8Array(this.pixels.length),count:0,before};}
 commitAdd(){
  const candidate=this.addCandidate;if(!candidate)return false;this.addCandidate=null;
  if(!candidate.count){this.render();this.onMessage('No image pixels selected. Paint the visible leaf blade you want to add.');return false;}
  const id=this.addLeafId,before=candidate.before,sourceSet=new Set(),runs=[];let unassigned=0,addedLamina=0;
  for(let i=0;i<candidate.pixels.length;i++)if(candidate.pixels[i]){const source=before.labels[i];if(source)sourceSet.add(source);else if(before.pixels[i])unassigned++;if(!before.pixels[i])addedLamina++;if(runs.length&&runs.at(-1)[0]+runs.at(-1)[1]===i)runs.at(-1)[1]++;else runs.push([i,1]);}
  const sourceIds=before.instance_metadata.display_order.filter(i=>sourceSet.has(i)),numbers=sourceIds.map(i=>before.instance_metadata.display_order.indexOf(i)+1);
  this.history.push(before);if(this.history.length>15)this.history.shift();this.future=[];this.registerInstance(id);
  for(const [start,length]of runs){this.pixels.fill(1,start,start+length);this.labels.fill(id,start,start+length);}
  this.selectedLeaf=id;this.addLeafId=null;this.addArmState=null;this.mode='select';this.revision++;this.stats();this.record('add',[id]);
  Object.assign(this.edits.at(-1),{painted_runs:runs,selected_pixel_count:candidate.count,added_lamina_pixel_count:addedLamina,source_leaf_ids:sourceIds,source_internal_instance_ids:sourceIds.map(i=>this.internalId(i)),source_display_leaf_numbers:numbers});
  this.render();this.onChange();
  const from=numbers.length===1?'Leaf '+numbers[0]:numbers.length?'Leaves '+numbers.slice(0,-1).join(', ')+' and '+numbers.at(-1):'';
  const emptied=sourceIds.filter(i=>!this.instanceOrder.includes(i)).length;
  this.onMessage('New leaf created from '+candidate.count+' painted pixels'+(from?' previously assigned to '+from:'')+(unassigned?(from?' and ':' from ')+'unassigned lamina':'')+'. '+addedLamina+' new lamina pixels added.'+(emptied?' '+emptied+' emptied source '+(emptied===1?'leaf was':'leaves were')+' removed; numbering compacted.':''));return true;
 }
 applySplit(encoded,newId,metadata=null){if(this.busy)return;const old=this.selectedLeaf,raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)),view=new DataView(raw.buffer),next=new Uint16Array(this.labels.length),before=new Set(this.stats().ids),after=new Set();if(raw.length!==next.length*2||this.instanceRegistry.has(newId)||!Number.isInteger(newId)||newId<1||newId>4096)throw Error('Invalid split result: expected one new leaf ID');for(let i=0;i<next.length;i++){next[i]=view.getUint16(i*2,true);if(this.labels[i]!==old&&next[i]!==this.labels[i])throw Error('Invalid split result: another leaf was modified');if(this.labels[i]===old&&![old,newId].includes(next[i]))throw Error('Invalid split result: selected leaf coverage changed');if(next[i])after.add(next[i]);}if(after.size!==before.size+1)throw Error('Invalid split result: count must increase by exactly one');this.saveUndo();this.registerInstance(newId,metadata?.instances.find(r=>r.label_id===newId)?.internal_instance_id);this.instanceOrder.splice(this.instanceOrder.indexOf(old)+1,0,newId);this.labels=next;this.selectedLeaf=newId;this.revision++;this.record('split',[old,newId]);this.render();this.onChange();}
 payload(){const chunks=[];for(let i=0;i<this.pixels.length;i+=16384)chunks.push(String.fromCharCode(...this.pixels.subarray(i,i+16384)));const payload={width:this.width,height:this.height,mask_base64:btoa(chunks.join(''))};if(this.labels){const bytes=new Uint8Array(this.labels.length*2),view=new DataView(bytes.buffer);for(let i=0;i<this.labels.length;i++)view.setUint16(i*2,this.labels[i],true);const encoded=[];for(let i=0;i<bytes.length;i+=16384)encoded.push(String.fromCharCode(...bytes.subarray(i,i+16384)));Object.assign(payload,{instance_base64:btoa(encoded.join('')),instance_analysis_id:this.analysisId,instance_edits:this.edits,instance_metadata:this.instanceMetadata()});}return payload;}
}
