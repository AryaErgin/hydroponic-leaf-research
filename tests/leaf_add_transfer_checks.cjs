const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const {MaskEditor}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/mask-editor.js','utf8')).toString('base64'));
 function fixture(unassigned=false){const events={},ctx={createImageData:(w,h)=>({width:w,height:h,data:new Uint8ClampedArray(w*h*4)}),putImageData(im,x=0,y=0){
  // Match canvas partial uploads: a dirty rectangle does not replace the
  // native-sized backing surface or move its origin to (0,0).
  if(!this.image||this.image.width!==canvas.width||this.image.height!==canvas.height)this.image=this.createImageData(canvas.width,canvas.height);
  for(let row=0;row<im.height;row++)this.image.data.set(im.data.subarray(row*im.width*4,(row+1)*im.width*4),((y+row)*canvas.width+x)*4);
 }};const canvas={style:{},getContext:()=>ctx,addEventListener(n,f){events[n]=f;},getBoundingClientRect:()=>({left:0,top:0,width:12,height:6}),setPointerCapture(){}};const messages=[],e=new MaskEditor({style:{},parentElement:{clientWidth:400,clientHeight:300}},canvas,{style:{}},()=>{},()=>{},()=>{},m=>messages.push(m));const mask=new Uint8Array(72),labels=new Uint16Array(72);for(let y=1;y<5;y++)for(let x=1;x<11;x++){mask[y*12+x]=1;labels[y*12+x]=x<6?1:2;}if(unassigned)labels[2*12+5]=0;e.load(12,6,Buffer.from(mask).toString('base64'));e.loadInstances(Buffer.from(labels.buffer).toString('base64'),'add-photo');e.radius=1;e.editable=true;return {e,events,ctx,messages};}
 const pointer=(x,y)=>({clientX:x,clientY:y,button:0,pointerId:1,preventDefault(){}});
 for(const [name,x,y,mixed] of [['new lamina only',0,0,false],['one source',3,2,false],['two sources',5.5,2,false],['assigned and unassigned',5,2,true],['mixed new and old lamina',1,1,false]]){
  const {e,events,ctx,messages}=fixture(mixed),before=e.snapshot(),old=Array.from(e.labels),oldCount=e.stats().ids.length;e.addLeaf();events.pointerdown(pointer(x,y));
  assert.deepEqual(Array.from(e.labels),old,name+': preview must not commit ownership');assert.deepEqual(Array.from(e.pixels),Array.from(before.pixels));
  const selected=[];for(let i=0;i<72;i++)if((i%12-x)**2+(Math.floor(i/12)-y)**2<=1)selected.push(i);
  if(selected.length){const i=selected[0]*4;assert.deepEqual(Array.from(ctx.image.data.slice(i,i+3)),[255,0,220],'distinct candidate highlight');}
  events.pointerup(pointer(x,y));assert.equal(e.drawing,false);for(let i=0;i<72;i++)assert.equal(e.pixels[i],before.pixels[i]||Number(selected.includes(i)),name+': only painted pixels may expand lamina');
  if(!selected.length){assert.deepEqual(Array.from(e.labels),old);assert.equal(e.stats().ids.length,oldCount);assert.match(messages.at(-1),/No image pixels/);continue;}
  const id=e.selectedLeaf,identity=e.internalId(id),after=e.snapshot();assert.equal(e.stats().ids.length,oldCount+1);assert.equal(e.displayNumber(id),oldCount+1);
  for(let i=0;i<72;i++)assert.equal(e.labels[i],selected.includes(i)?id:old[i],name+': unpainted ownership changed');
  assert.equal(e.stats().unassigned,0);assert.equal(e.stats().areas.reduce((a,b)=>a+b,0),e.stats().total);assert.match(messages.at(-1),/New leaf created/);
  if(name==='two sources')assert.match(messages.at(-1),/Leaves 1 and 2/);
  const event=e.edits.at(-1);assert.equal(event.operation,'add');assert.equal(event.selected_pixel_count,selected.length);assert.ok(event.painted_runs.length);assert.equal(event.added_lamina_pixel_count,selected.filter(i=>!before.pixels[i]).length);if(name==='new lamina only')assert.ok(e.stats().total>before.pixels.reduce((a,b)=>a+b,0));
  e.undo();assert.deepEqual(Array.from(e.labels),old);assert.deepEqual(Array.from(e.pixels),Array.from(before.pixels),'Undo must restore the previous lamina mask');assert.deepEqual(e.instanceOrder,before.instance_metadata.display_order);assert.equal(e.stats().ids.length,oldCount);
  assert.equal(e.internalId(id),identity,'Undo retains the new leaf in historical registry');assert.equal(e.instanceMetadata().instances.find(r=>r.label_id===id).active,false);
  e.redo();assert.deepEqual(Array.from(e.labels),Array.from(after.labels));assert.deepEqual(Array.from(e.pixels),Array.from(after.pixels));assert.equal(e.internalId(id),identity);assert.equal(e.displayNumber(id),oldCount+1);
 }
 const {e,events}=fixture();e.radius=80;e.addLeaf();events.pointerdown(pointer(5,2));events.pointerup(pointer(5,2));assert.equal(e.stats().ids.length,1,'fully emptied source leaves must be removed');assert.equal(e.displayNumber(e.selectedLeaf),1);assert.equal(e.stats().unassigned,0);e.undo();assert.equal(e.stats().ids.length,2);e.redo();assert.equal(e.stats().ids.length,1);
 const cancelled=fixture();cancelled.e.addLeaf();const start=Array.from(cancelled.e.labels);const cancelMask=Array.from(cancelled.e.pixels);cancelled.events.pointerdown(pointer(0,0));cancelled.events.pointercancel();assert.deepEqual(Array.from(cancelled.e.labels),start);assert.deepEqual(Array.from(cancelled.e.pixels),cancelMask,'cancelled outside-mask preview must not add tissue');assert.equal(cancelled.e.history.length,0,'cancelled preview is not an edit');
 assert.equal(cancelled.e.mode,'add','cancelled pointer gesture keeps the active Add tool');assert.equal(cancelled.e.selectedLeaf,cancelled.e.addLeafId,'cancel keeps the same uncommitted Add target');
 const incomplete=fixture();incomplete.e.labels[2*12+9]=0;incomplete.e.addLeaf();incomplete.events.pointerdown(pointer(3,2));incomplete.events.pointerup(pointer(3,2));assert.equal(incomplete.e.labels[2*12+9],0,'pre-existing unassigned pixels outside selection are untouched');assert.equal(incomplete.e.stats().unassigned,1);
 console.log('PASS Add preview/release, one/two/mixed sources, image-bound clipping, explicit new lamina, exact painted-region transfer, count/area, empty-source compaction, Undo/Redo identities and cancellation');
})().catch(error=>{console.error(error);process.exitCode=1;});
