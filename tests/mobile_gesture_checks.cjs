const fs=require('node:fs'),assert=require('node:assert/strict');
// DOM geometry is simulated; the production pointer handlers and pixel operations run unchanged.
function element(parent=null){const listeners={};return {style:{},parentElement:parent,clientWidth:320,clientHeight:240,listeners,
 addEventListener(n,f){(listeners[n]??=[]).push(f);},setPointerCapture(){},releasePointerCapture(){},
 dispatch(n,id,x,y,type='touch'){const e={pointerId:id,clientX:x,clientY:y,pointerType:type,button:0,preventDefault(){this.prevented=true;}};for(const f of listeners[n]||[])f(e);return e;},
 getBoundingClientRect(){if(!parent)return {left:20,top:30,width:this.clientWidth+2*(this.clientLeft||0),height:this.clientHeight+2*(this.clientTop||0)};const t=(this.style.transform||'').match(/translate\(([-.\d]+)px,\s*([-.\d]+)px\)/);return {left:20+(parent.clientLeft||0)+(t?+t[1]:0),top:30+(parent.clientTop||0)+(t?+t[2]:0),width:parseFloat(this.style.width)||320,height:parseFloat(this.style.height)||240};}};}
function canvas(parent){const c=element(parent);c.width=640;c.height=480;c.getContext=()=>({drawImage(){},beginPath(){},arc(){},fill(){},moveTo(){},lineTo(){},stroke(){},createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}});return c;}
function near(actual,want){assert.ok(Math.abs(actual-want)<1e-6,`${actual} != ${want}`);}
(async()=>{
 const viewportFile='src/webapp/static/image-viewport.js';if(fs.existsSync(viewportFile))eval(fs.readFileSync(viewportFile,'utf8'));
 assert.equal(typeof globalThis.ImageViewport,'function','shared native-coordinate gesture controller is required');
 const outside=element(),wrap=element(),stage=element(wrap),v=new ImageViewport(wrap,stage);v.setSize(640,480);v.fit();
 let p=v.point({clientX:180,clientY:150});near(p[0],320);near(p[1],240);
 v.zoom(1,{clientX:180,clientY:150});p=v.point({clientX:180,clientY:150});near(p[0],320);near(p[1],240);
 const bw=element();bw.clientLeft=2;bw.clientTop=2;const bs=element(bw),bv=new ImageViewport(bw,bs);bv.setSize(640,480);bv.zoom(1,{clientX:182,clientY:152});p=bv.point({clientX:182,clientY:152});near(p[0],320);near(p[1],240);
 wrap.dispatch('pointerdown',1,140,150);wrap.dispatch('pointerdown',2,220,150);wrap.dispatch('pointermove',1,100,170);wrap.dispatch('pointermove',2,260,170);
 p=v.point({clientX:180,clientY:170});near(p[0],320);near(p[1],240);near(v.scale,2);
 wrap.dispatch('pointerup',1,100,170);wrap.dispatch('pointerup',2,260,170);assert.equal(outside.dispatch('pointermove',3,1,1).prevented,undefined,'page scroll outside editor remains native');
 eval(fs.readFileSync('src/webapp/static/pot-calibration.js','utf8').replace('export class PotCalibration','globalThis.PotCalibration=class PotCalibration'));
 const pw=element(),pc=canvas(pw);let changes=0;const picker=new PotCalibration(pc,()=>changes++,pw);picker.image={};picker.viewport.setSize(640,480);picker.fit();
 pw.dispatch('pointerdown',1,100,150);pw.dispatch('pointerup',1,100,150);pw.dispatch('pointerdown',1,260,150);pw.dispatch('pointerup',1,260,150);assert.deepEqual(picker.points,[[160,240],[480,240]]);
 pw.dispatch('pointerdown',1,100,150);pw.dispatch('pointermove',1,120,150);pw.dispatch('pointerup',1,120,150);assert.deepEqual(picker.points[0],[200,240],'endpoint can be corrected');
 pw.dispatch('pointerdown',1,120,150);pw.dispatch('pointermove',1,140,150);pw.dispatch('pointercancel',1,140,150);assert.deepEqual(picker.points[0],[200,240],'cancel restores endpoint');
 const before=JSON.stringify(picker.points);pw.dispatch('pointerdown',1,120,150);pw.dispatch('pointermove',1,140,150);pw.dispatch('pointerdown',2,220,150);pw.dispatch('pointermove',2,250,150);pw.dispatch('pointerup',2,250,150);pw.dispatch('pointerup',1,140,150);assert.equal(JSON.stringify(picker.points),before,'pinch never moves an endpoint');picker.undoLastPoint();assert.equal(picker.points.length,1);
 picker.locked=true;const locked=JSON.stringify(picker.points);pw.dispatch('pointerdown',1,180,150);pw.dispatch('pointerup',1,180,150);picker.redo();picker.undoLastPoint();assert.equal(JSON.stringify(picker.points),locked,'saving locks point edits and correction buttons');picker.locked=false;
 let resizeCallback;globalThis.ResizeObserver=class{constructor(callback){resizeCallback=callback;}observe(){}};
 const rw=element(),rs=element(rw),rv=new ImageViewport(rw,rs);rv.setSize(640,480);rv.zoom(1);rw.clientWidth=400;rw.clientHeight=300;resizeCallback?.();near(rv.x,-120);near(rv.y,-90);rv.fit();rw.clientWidth=240;rw.clientHeight=320;resizeCallback?.();near(rv.scale,.375);near(rv.x,0);near(rv.y,70);delete globalThis.ResizeObserver;
 const {MaskEditor}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/mask-editor.js','utf8')).toString('base64'));
 for(const mode of ['paint','erase','reassign','split','add']){
  const w=element(),s=element(w),c=canvas(s);c.getBoundingClientRect=()=>s.getBoundingClientRect();let committed=0,splits=0;const e=new MaskEditor(s,c,{style:{}},()=>committed++,()=>splits++);e.load(64,48,Buffer.alloc(64*48,1).toString('base64'));e.loadInstances(Buffer.from(new Uint16Array(64*48).fill(1).buffer).toString('base64'),'gesture');e.editable=true;e.radius=2;e.mode=mode;if(mode==='add')e.addLeaf();
  const initial=e.payload(),revision=e.revision;
  w.dispatch('pointerdown',1,100,120);assert.equal(e.drawing,true,mode+': first pointer begins a real tool gesture');w.dispatch('pointermove',1,110,120);w.dispatch('pointerdown',2,220,120);w.dispatch('pointermove',2,240,120);w.dispatch('pointerup',2,240,120);w.dispatch('pointerup',1,110,120);
  assert.deepEqual(e.payload(),initial,mode+': delayed second touch must roll back first-finger work');assert.equal(e.history.length,0);assert.equal(e.future.length,0);assert.equal(e.revision,revision);assert.equal(committed,0);assert.equal(splits,0);
  w.dispatch('pointerdown',1,110,120);w.dispatch('pointermove',1,120,120);w.dispatch('pointercancel',1,120,120);assert.deepEqual(e.payload(),initial,mode+': cancel must leave no partial stroke');assert.equal(e.history.length,0);
 }
 const w=element(),s=element(w),c=canvas(s);c.getBoundingClientRect=()=>s.getBoundingClientRect();let changed=0;const e=new MaskEditor(s,c,{style:{}},()=>changed++);e.load(64,48,Buffer.alloc(64*48,1).toString('base64'));e.editable=true;e.mode='erase';e.radius=1;w.dispatch('pointerdown',1,180,150);w.dispatch('pointermove',1,190,150);w.dispatch('pointerup',1,190,150);assert.ok(e.pixels.includes(0));assert.equal(e.history.length,1);assert.equal(changed,1,'one completed stroke commits once');e.undo();assert.ok(!e.pixels.includes(0));
 e.zoom(10);e.panMode=true;const old=e.payload();w.dispatch('pointerdown',1,100,100);w.dispatch('pointermove',1,140,120);w.dispatch('pointerup',1,140,120);assert.deepEqual(e.payload(),old,'explicit one-finger pan does not edit');e.panMode=false;
 w.dispatch('pointerdown',1,220,170);w.dispatch('pointerup',1,220,170);assert.equal(e.pixels[24*64+32],0,'zoomed and panned touch edits exact native pixel');assert.equal(e.pixels[24*64+35],1,'neighbor outside native brush stays intact');
 e.undo();e.ctx.fillText=()=>{};e.loadInstances(Buffer.from(new Uint16Array(64*48).fill(1).buffer).toString('base64'),'text');e.zoom(10);near(parseFloat(e.ctx.font.match(/([\d.]+)px/)[1])*10,14);
 console.log('PASS mobile gestures: fit/zoom/native coordinates, anchored pinch, scoped scroll, picker correction/cancel/undo, delayed second touch rollback across all edit modes, cancellation and single-stroke undo');
})().catch(error=>{console.error(error);process.exitCode=1;});
