const fs=require('node:fs'),assert=require('node:assert/strict');
async function moduleFile(path){return import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path,'utf8')).toString('base64'));}
(async()=>{
 const {MaskEditor}=await moduleFile('src/webapp/static/mask-editor.js');
 const ctx={createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(im){this.image=im;},beginPath(){},moveTo(){},lineTo(){},stroke(){},strokeText(){},fillText(){}};
 const events={},canvas={getContext:()=>ctx,addEventListener(n,f){events[n]=f;},style:{},getBoundingClientRect:()=>({left:0,top:0,width:6,height:2}),setPointerCapture(){}};
 const stage={style:{},parentElement:{clientWidth:400,clientHeight:300}},messages=[];
 const e=new MaskEditor(stage,canvas,{style:{}},()=>{},()=>{},()=>{},m=>messages.push(m));
 const pixels=[0,1,1,1,1,0,0,1,1,1,1,0],ids=[0,1,1,2,2,0,0,1,1,0,0,0];
 e.load(6,2,Buffer.from(pixels).toString('base64'));const raw=Buffer.alloc(24);ids.forEach((v,i)=>raw.writeUInt16LE(v,i*2));e.loadInstances(raw.toString('base64'),'a');
 const before=Array.from(e.labels);e.addLeaf();assert.equal(e.mode,'add');e.radius=1;e.beginAdd();e.disc([3,1]);assert.deepEqual(Array.from(e.labels),before);e.commitAdd();
 assert.deepEqual(Array.from(e.pixels),pixels);for(let i=0;i<ids.length;i++)if(![3,8,9,10].includes(i))assert.equal(e.labels[i],ids[i],'Add changed an unpainted neighbor');
 assert.equal(e.labels[9],3);assert.ok(messages.some(m=>m.includes('New leaf created')));e.undo();assert.deepEqual(Array.from(e.labels),before);
 assert.equal(e.mode,'select','Undoing Add creation must restore a usable neutral tool');assert.equal(e.addLeafId,null);
 e.editable=true;const pointer={clientX:1,clientY:0,button:0,pointerId:1,preventDefault(){}};
 assert.doesNotThrow(()=>events.pointerdown(pointer));assert.equal(e.drawing,false);assert.equal(e.selectedLeaf,1);
 e.addLeaf();e.addLeaf();e.undo();assert.equal(e.mode,'select');assert.equal(e.addLeafId,null);
 e.addLeaf();e.selectedLeaf=1;assert.doesNotThrow(()=>events.pointerdown(pointer));assert.equal(e.drawing,false);assert.equal(e.mode,'select');
 e.selectedLeaf=2;e.mode='reassign';e.disc([1,0]);assert.equal(e.labels[1],2);assert.deepEqual(Array.from(e.pixels),pixels);
 const {approvalState,graphSeries,reviewScale}=await moduleFile('src/webapp/static/review-state.js');
 const state={analysis:{status:'DRAFT',profile_id:'p'},selected:{id:'p'},stats:{total:750,ids:[1],unassigned:338,invalid:0},confirmed:true};
 assert.equal(approvalState(state).reason,'Cannot approve: 338 lamina pixels are unassigned.');
 state.stats.unassigned=0;assert.equal(approvalState(state).enabled,true);state.saving=true;assert.match(approvalState(state).reason,/Saving/);
 state.saving=false;state.analysis={status:'DRAFT',profile_id:'p',geometry_unchanged:true,image_size_px:[208,199],calibration:{reference_size_px:[1200,1600],cm_per_pixel:.008}};
 assert.equal(approvalState(state).enabled,false);assert.match(approvalState(state).reason,/check the scale.*Recalibrate.*pot edges/i);assert.equal(reviewScale(state.analysis),null);
 const rows=[{id:'old',date:'2020-01-01',projected_area_cm2:3},{id:'new',date:'2020-01-02',captured_at:'2020-01-02T05:00:00Z',approved_at:'2026-10-14',projected_area_cm2:4,leaf_count:2,mean_leaf_area_cm2:2,median_leaf_area_cm2:1.5}];
 const series=graphSeries(rows,'total');assert.equal(series.points[1].time,Date.parse(rows[1].captured_at));assert.equal(graphSeries(rows,'median').points.length,1);assert.equal(graphSeries(rows,'count').unit,'leaves');
 console.log('PASS UX native Add/Reassign invariants, exact undo, explicit approval state, captured-time graph/median');
})().catch(e=>{console.error(e);process.exitCode=1;});
