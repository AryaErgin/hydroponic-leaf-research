const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const {MaskEditor}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/mask-editor.js','utf8')).toString('base64'));
 function editor(){const ctx={createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}};const e=new MaskEditor({style:{},parentElement:{clientWidth:400,clientHeight:300}},{style:{},getContext:()=>ctx,addEventListener(){}},{style:{}},()=>{});e.load(14,1,Buffer.alloc(14,1).toString('base64'));const b=Buffer.alloc(28);for(let i=0;i<14;i++)b.writeUInt16LE(Math.floor(i/2)+1,i*2);e.loadInstances(b.toString('base64'),'analysis-numbering');return e;}
 const e=editor(),identities=new Map(e.instanceMetadata().instances.map(r=>[r.label_id,r.internal_instance_id]));
 const numbers=e=>e.stats().ids.map(id=>e.displayNumber(id));
 e.deleteLeaf(5);assert.deepEqual(e.stats().ids,[1,2,3,4,6,7]);assert.deepEqual(numbers(e),[1,2,3,4,5,6]);
 for(const id of e.stats().ids)assert.equal(e.internalId(id),identities.get(id));
 e.undo();assert.deepEqual(numbers(e),[1,2,3,4,5,6,7]);assert.equal(e.internalId(5),identities.get(5));
 e.redo();assert.deepEqual(e.stats().ids,[1,2,3,4,6,7]);assert.deepEqual(numbers(e),[1,2,3,4,5,6]);e.undo();
 e.deleteLeaf(1);assert.equal(e.displayNumber(2),1);e.undo();e.deleteLeaf(7);assert.deepEqual(numbers(e),[1,2,3,4,5,6]);e.undo();
 e.selectedLeaf=3;const split=Buffer.from(e.payload().instance_base64,'base64');split.writeUInt16LE(8,5*2);e.applySplit(split.toString('base64'),8);
 assert.deepEqual(e.stats().ids,[1,2,3,8,4,5,6,7]);assert.deepEqual(numbers(e),[1,2,3,4,5,6,7,8]);assert.equal(e.internalId(3),identities.get(3));
 const splitIdentity=e.internalId(8);e.mergeLeaves([4,8]);assert.deepEqual(e.stats().ids,[1,2,3,8,5,6,7]);assert.equal(e.internalId(8),splitIdentity);assert.equal(e.labels[6],8,'earliest display position must survive, not lowest technical label');
 e.undo();assert.deepEqual(e.stats().ids,[1,2,3,8,4,5,6,7]);e.undo();assert.deepEqual(e.stats().ids,[1,2,3,4,5,6,7]);
 assert.equal(e.internalId(8),splitIdentity,'Undo must retain intermediate identity in registry');
 e.deleteLeaf(7);const added=e.addLeaf();assert.ok(added>8,'technical IDs must not be recycled after Undo');assert.equal(e.stats().ids.length,6,'empty Add is not a leaf');e.radius=1;e.beginAdd();e.disc([13,0]);e.commitAdd();assert.equal(e.displayNumber(added),7);assert.notEqual(e.internalId(added),identities.get(7));
 const payload=e.payload(),clone=editor();clone.loadInstances(payload.instance_base64,'analysis-numbering',payload.instance_metadata);assert.deepEqual(clone.instanceMetadata(),e.instanceMetadata());assert.deepEqual(numbers(clone),numbers(e));
 clone.mode='reassign';clone.selectedLeaf=1;clone.radius=1;clone.saveUndo();clone.disc([2.5,0]);clone.render();assert.deepEqual(numbers(clone),[1,2,3,4,5,6]);assert.equal(clone.internalId(1),identities.get(1));clone.undo();assert.deepEqual(numbers(clone),[1,2,3,4,5,6,7]);
 e.undo();assert.deepEqual(numbers(e),[1,2,3,4,5,6]);e.undo();e.undo();assert.equal(e.internalId(7),identities.get(7));assert.deepEqual(numbers(e),[1,2,3,4,5,6,7]);
 const cleared=editor(),old=cleared.internalId(1);cleared.clear();cleared.mode='paint';cleared.selectedLeaf=1;cleared.radius=1;cleared.saveUndo();cleared.disc([1,0]);cleared.render();assert.notEqual(cleared.internalId(cleared.selectedLeaf),old,'Paint after Clear must create a new identity');assert.ok(cleared.selectedLeaf>7);const fresh=cleared.internalId(cleared.selectedLeaf);cleared.undo();assert.equal(cleared.stats().ids.length,0);cleared.redo();assert.equal(cleared.internalId(cleared.selectedLeaf),fresh);
 const emptied=editor();for(const id of [...emptied.stats().ids])emptied.deleteLeaf(id);emptied.mode='reassign';emptied.selectedLeaf=1;emptied.radius=1;emptied.disc([1,0]);assert.equal(emptied.stats().ids.length,0,'Reassign cannot resurrect a retired identity');emptied.mode='paint';emptied.disc([1,0]);emptied.render();assert.ok(emptied.selectedLeaf>7);assert.notEqual(emptied.internalId(emptied.selectedLeaf),old);assert.deepEqual(numbers(emptied),[1]);
 console.log('PASS contiguous Delete first/middle/last, Split order, earliest Merge, Add, Undo/Redo, Reassign, stable identities and metadata reload');
})().catch(e=>{console.error(e);process.exitCode=1;});
