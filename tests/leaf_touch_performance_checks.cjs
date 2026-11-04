const fs=require('node:fs'),assert=require('node:assert/strict'),{performance}=require('node:perf_hooks');
// Real production pointer handlers with deterministic display frames and canvas IO instrumentation.
// No elapsed-time threshold: scan/allocation counts are portable; timings are diagnostic only.
let nextFrame=0,frames=new Map(),scheduled=0,maxPending=0;
globalThis.requestAnimationFrame=callback=>{const id=++nextFrame;frames.set(id,callback);scheduled++;maxPending=Math.max(maxPending,frames.size);return id;};
globalThis.cancelAnimationFrame=id=>frames.delete(id);
function flushFrame(){const batch=[...frames.values()];frames.clear();for(const callback of batch)callback(performance.now());}
function settle(){for(let i=0;frames.size&&i<10;i++)flushFrame();assert.equal(frames.size,0,'visual updates must settle');}
const scanCounts={instanceColors:0};globalThis.__touchScanCounts=scanCounts;
function element(parent=null){const listeners={};return {style:{},parentElement:parent,clientWidth:320,clientHeight:240,listeners,
 addEventListener(name,callback){(listeners[name]??=[]).push(callback);},setPointerCapture(){},releasePointerCapture(){},
 dispatch(name,event){for(const callback of listeners[name]||[])callback(event);},
 getBoundingClientRect(){if(!parent)return {left:20,top:30,width:this.clientWidth,height:this.clientHeight};const t=(this.style.transform||'').match(/translate\(([-.\d]+)px,\s*([-.\d]+)px\)/);return {left:20+(t?+t[1]:0),top:30+(t?+t[2]:0),width:parseFloat(this.style.width)||320,height:parseFloat(this.style.height)||240};}};}
function fixture(MaskEditor,{width=128,height=96,mode='paint',large=false}={}){
 const wrap=element(),stage=element(wrap),canvas=element(stage),io={allocations:[],writes:[],stats:0,includes:0};
 canvas.width=width;canvas.height=height;canvas.getBoundingClientRect=()=>stage.getBoundingClientRect();
 const ctx={createImageData(w,h){io.allocations.push({w,h});return {width:w,height:h,data:new Uint8ClampedArray(w*h*4)};},
  putImageData(image,x,y,sx=0,sy=0,w=image.width,h=image.height){io.writes.push({x,y,sx,sy,w,h,imageWidth:image.width,imageHeight:image.height});this.image=image;},
  beginPath(){},arc(){},fill(){},moveTo(){},lineTo(){},stroke(){},fillText(){},strokeText(){}};
 canvas.getContext=()=>ctx;let commits=0,splits=[];
 const editor=new MaskEditor(stage,canvas,{style:{}},()=>commits++,points=>splits.push(points));
 const pixels=Buffer.alloc(width*height,mode==='paint'?0:1);pixels[0]=1;
 const labels=new Uint16Array(width*height);if(mode!=='paint')labels.fill(1);labels[0]=mode==='reassign'?2:1;
 editor.load(width,height,pixels.toString('base64'));editor.loadInstances(Buffer.from(labels.buffer).toString('base64'),'synthetic-touch');
 editor.editable=true;editor.radius=2;editor.mode=mode;editor.selectedLeaf=mode==='reassign'?2:1;if(mode==='add')editor.addLeaf();
 settle();
 const originalStats=editor.stats.bind(editor);editor.stats=(...args)=>(io.stats++,originalStats(...args));
 const nativeIncludes=editor.labels.includes.bind(editor.labels);editor.labels.includes=(...args)=>(io.includes++,nativeIncludes(...args));
 function event(point,samples){const r=canvas.getBoundingClientRect();const convert=([x,y])=>({clientX:r.left+x*r.width/width,clientY:r.top+y*r.height/height,button:0,pointerId:1,pointerType:'touch',preventDefault(){}});const e=convert(point);if(samples)e.getCoalescedEvents=()=>samples.map(convert);return e;}
 function send(name,point,samples){wrap.dispatch(name,event(point,samples));}
 function reset(){io.allocations=[];io.writes=[];io.stats=0;io.includes=0;scanCounts.instanceColors=0;scheduled=0;maxPending=frames.size;}
 return {editor,canvas,ctx,io,wrap,stage,send,reset,splits,get commits(){return commits;}};
}
function native(editor){return {pixels:Buffer.from(editor.pixels),labels:Buffer.from(new Uint8Array(editor.labels.buffer,editor.labels.byteOffset,editor.labels.byteLength))};}
function exact(actual,expected,message){assert.ok(actual.pixels.equals(expected.pixels),message+' mask');assert.ok(actual.labels.equals(expected.labels),message+' instance labels');}
function pixel(editor,x,y){return {mask:editor.pixels[y*editor.width+x],label:editor.labels[y*editor.width+x]};}

(async()=>{
 eval(fs.readFileSync('src/webapp/static/image-viewport.js','utf8'));
 const original=fs.readFileSync('src/webapp/static/mask-editor.js','utf8');
 const instrumented=original.replace(/(export function instanceColors\([^)]*\)\s*\{)/,'$1globalThis.__touchScanCounts.instanceColors++;');
 assert.notEqual(instrumented,original,'instance palette scan instrumentation must attach');
 const {MaskEditor}=await import('data:text/javascript;base64,'+Buffer.from(instrumented).toString('base64'));
 const results=[];
 for(const mode of ['paint','erase','reassign','add']){
  const regular=fixture(MaskEditor,{mode}),batched=fixture(MaskEditor,{mode});
  const initial=native(batched.editor),start=[24,24],curve=[[24,48],[48,48],[48,24]],end=[72,24];
  regular.send('pointerdown',start);for(const point of [...curve,end])regular.send('pointermove',point);regular.send('pointerup',end);settle();
  batched.send('pointerdown',start);batched.send('pointermove',curve.at(-1),curve);batched.send('pointerup',end);settle();
  exact(native(batched.editor),native(regular.editor),mode+': coalesced curved samples and final pointerup must equal individually delivered points');
  const target=mode==='erase'?0:mode==='reassign'||mode==='add'?2:1;
  assert.equal(pixel(batched.editor,24,48).label,target,mode+': curved path must follow the finger through its bend');
  assert.equal(pixel(batched.editor,...end).label,target,mode+': final pointerup location must not be dropped');
  assert.equal(pixel(batched.editor,35,35).label,mode==='paint'?0:1,mode+': curved samples must not become a diagonal shortcut');
  assert.equal(batched.editor.history.length,1,mode+': exactly one undo entry per stroke');assert.equal(batched.commits,1,mode+': exactly one scientific change callback');
  const finished=native(batched.editor);batched.editor.undo();settle();exact(native(batched.editor),initial,mode+': undo exact');batched.editor.redo();settle();exact(native(batched.editor),finished,mode+': redo exact');
  results.push({mode,curve_and_pointerup_exact:true,single_undo:true});
 }
 const split=fixture(MaskEditor,{mode:'split'}),splitBefore=native(split.editor);
 split.send('pointerdown',[24,24]);split.send('pointermove',[48,24],[[24,48],[48,48],[48,24]]);split.send('pointerup',[72,24]);settle();
 assert.equal(split.splits.length,1,'split submits one completed path');
 for(const [x,y]of [[24,24],[24,48],[48,48],[48,24],[72,24]])assert.ok(split.splits[0].some(p=>Math.abs(p[0]-x)<1e-6&&Math.abs(p[1]-y)<1e-6),'split retains each curved/final touch sample');
 exact(native(split.editor),splitBefore,'drawing a split request does not alter native mask before server result');
 // Cancel before a pending display frame, including a curved coalesced batch.
 for(const mode of ['paint','erase','reassign','add']){
  const f=fixture(MaskEditor,{mode}),before=native(f.editor);f.send('pointerdown',[24,24]);f.send('pointermove',[48,24],[[24,48],[48,48],[48,24]]);f.send('pointercancel',[48,24]);settle();
  exact(native(f.editor),before,mode+': cancellation restores all pixels despite queued frame');assert.equal(f.editor.history.length,0);assert.equal(f.commits,0);
 }
 const width=1536,height=2048,f=fixture(MaskEditor,{width,height,mode:'reassign',large:true});f.editor.radius=5;
 f.send('pointerdown',[600,700]);settle();f.reset();const t=performance.now();
 for(let i=1;i<=20;i++)f.send('pointermove',[600+i*2,700+(i%3)]);
 const inputMs=performance.now()-t,pending=frames.size;
 assert.equal(f.io.allocations.filter(x=>x.w===width&&x.h===height).length,0,'pointermove must not allocate a full native frame');
 assert.equal(f.io.writes.length,0,'display work must coalesce on requestAnimationFrame');
 assert.ok(pending===1&&scheduled===1&&maxPending===1,'20 pointer samples schedule at most one visual update in a display frame');
 assert.equal(f.io.stats,0,'pointermove must not scan all native pixels for stats');assert.equal(scanCounts.instanceColors,0,'pointermove must not rebuild the adjacency palette');assert.equal(f.io.includes,0,'selected identity validity must not scan native labels for every stamp');
 const ft=performance.now();flushFrame();const dirtyFrameMs=performance.now()-ft;
 assert.ok(f.io.writes.length>=1,'next display frame shows the actual stroke');
 assert.ok(f.io.writes.every(x=>x.w*x.h<=10000),'small brush path preview must upload only a bounded dirty rectangle on a 3M-pixel image');
 assert.ok(f.io.allocations.every(x=>x.w*x.h<=10000),'dirty preview must not allocate a native-sized image');
 assert.equal(f.io.stats,0,'dirty preview must not run full statistics');assert.equal(scanCounts.instanceColors,0,'dirty preview must use the stable gesture palette');
 const dirtyArea=f.io.writes.reduce((sum,x)=>sum+x.w*x.h,0);f.reset();f.send('pointerup',[650,702]);settle();
 assert.equal(pixel(f.editor,650,702).label,2,'large native stroke also consumes its final sample');
 assert.equal(f.editor.history.length,1);assert.equal(f.commits,1);
 assert.ok(f.io.allocations.filter(x=>x.w===width&&x.h===height).length<=1,'finishing a stroke rebuilds the complete outline once');
 assert.ok(f.io.writes.some(x=>x.w===width&&x.h===height),'finished view contains the canonical full-resolution overlay');
 const full=f.ctx.image;assert.equal(full.data[(702*width+650)*4+3],255,'selected leaf boundary remains an opaque outline');
 results.push({native_pixels:width*height,pointer_moves:20,input_ms:inputMs,dirty_frame_ms:dirtyFrameMs,dirty_uploaded_pixels:dirtyArea,pending_frames:pending});
 // Pointer coordinates continue to follow viewport zoom/pan geometry.
 const z=fixture(MaskEditor,{mode:'erase'});z.editor.zoom(4);z.editor.viewport.panBy(-37,19);z.send('pointerdown',[60,40]);z.send('pointerup',[60,40]);settle();assert.equal(pixel(z.editor,60,40).mask,0);assert.equal(pixel(z.editor,70,40).mask,1);
 const before=native(z.editor);z.editor.panMode=true;z.reset();const at=[60,40];z.send('pointerdown',at);z.send('pointermove',[65,40]);z.send('pointerup',[65,40]);settle();exact(native(z.editor),before,'pan only changes the viewport');assert.equal(z.io.allocations.length,0,'panning must not rebuild the native mask');assert.equal(z.io.stats,0,'panning must not rescan native stats');
 console.log(JSON.stringify({status:'PASS',checks:results},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
