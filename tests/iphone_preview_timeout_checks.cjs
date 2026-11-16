const fs=require('fs'),assert=require('assert/strict');
(async()=>{
const source=fs.readFileSync('src/webapp/static/pot-calibration.js','utf8');let timer,revoked=false;
const image={set src(v){},decode(){return new Promise(()=>{});}};
const fakeURL={createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{revoked=true;}};
const code=source.replace('export class PotCalibration','class PotCalibration');
const Picker=new Function('Image','URL','setTimeout','clearTimeout',code+';return PotCalibration;')(function(){return image;},fakeURL,fn=>{timer=fn;return 1;},()=>{});
const p=new Picker({addEventListener(){}},()=>{},null),pending=p.load({});timer();await assert.rejects(pending,/Scale preview timed out/);assert(revoked);assert.equal(p.file,null);
console.log('PASS: stalled scale image decode expires and releases blob URL without replacing source frame');
})().catch(e=>{console.error(e);process.exitCode=1;});
