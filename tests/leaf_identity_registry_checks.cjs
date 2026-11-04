const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const source=fs.readFileSync('src/webapp/static/app.js','utf8');
 const {MaskEditor}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/mask-editor.js','utf8')).toString('base64'));
 const ctx={createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}};
 const editor=new MaskEditor({style:{},parentElement:{clientWidth:400,clientHeight:300}},{style:{},getContext:()=>ctx,addEventListener(){}},{style:{}},()=>{});
 editor.load(4,1,Buffer.alloc(4,1).toString('base64'));editor.loadInstances(Buffer.from([1,0,1,0,1,0,1,0]).toString('base64'),'photo');editor.saveUndo();
 const masks=editor.payload(),metadata=editor.instanceMetadata();metadata.instances.push({label_id:2,internal_instance_id:'old-tab-intermediate',active:false});metadata.next_label_id=3;
 const nodes={'approval-error':{textContent:'Add identity registry must retain intermediate instances'}};
 const body=source.slice(source.indexOf('async function draft(){'),source.indexOf('async function approve(){'));
 const run=new Function('$','json','editor','pending','say','buttons',`let working=false,analysis={id:'photo'},dirty=true;${body};return draft;`)(id=>nodes[id],async()=>({id:'photo',instance_metadata:metadata}),editor,async()=>{},()=>{},()=>{});
 await run();assert.equal(nodes['approval-error'].textContent,'','Successful repair clears obsolete approval error');assert.equal(editor.internalId(2),'old-tab-intermediate');assert.equal(editor.instanceMetadata().instances.find(r=>r.label_id===2).active,false);
 assert.equal(editor.history.length,1,'Draft recovery preserves Undo');assert.equal(editor.payload().instance_base64,masks.instance_base64);editor.undo();assert.equal(editor.internalId(2),'old-tab-intermediate','Undo cannot purge recovered identities');
 console.log('PASS repaired draft clears stale approval error and merges historical registry without changing pixels or Undo history');
})().catch(e=>{console.error(e);process.exitCode=1;});
