const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/webapp/static/app.js','utf8');
const body=source.slice(source.indexOf('async function api('),source.indexOf('async function json('));
(async()=>{
 for(const [url,expected] of [['/api/analyses/synthetic/analyze',330000],['/api/analyses/synthetic/analyze?engine=auto',330000],['/api/analyses/synthetic/approve',60000],['/api/profiles',60000]]){
  let duration,cleared=false;
  const api=new Function('fetch','setTimeout','clearTimeout','return '+body)(async()=>new Response('{}',{status:200}),(_,ms)=>{duration=ms;return 1;},()=>{cleared=true;});
  await api(url);assert.equal(duration,expected,url+': cold analysis must outlast Cloud Run processing timeout, other requests retain existing budget');assert.equal(cleared,true);
 }
 console.log('PASS cold analysis has330s deadline; approval/default requests retain60s; deadlines cleared after full response');
})().catch(e=>{console.error(e);process.exitCode=1;});
