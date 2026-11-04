const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/webapp/static/pot-calibration.js','utf8').replace('export class PotCalibration','globalThis.PotCalibration=class PotCalibration');eval(source);
const canvas={width:120,height:100,addEventListener(){},getBoundingClientRect(){return{x:20,y:30,left:20,top:30,width:24,height:20};},getContext(){return{drawImage(){},beginPath(){},arc(){},fill(){},moveTo(){},lineTo(){},stroke(){}};}};
let changed=0;const p=new PotCalibration(canvas,()=>changed++);p.image={};
p.click({clientX:22,clientY:40});p.click({clientX:42,clientY:40});assert.deepEqual(p.points,[[10,50],[110,50]]);assert.equal(p.distance(),100);
p.click({clientX:30,clientY:40});assert.equal(p.points.length,2);p.redo();assert.equal(p.distance(),null);assert.deepEqual(p.points,[]);
p.click({clientX:19,clientY:40});assert.equal(p.points.length,0);assert.equal(changed,3);
console.log('PASS: two-point native coordinate mapping, exact distance, redo and bounds');
