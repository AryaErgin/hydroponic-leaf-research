const fs=require('fs'),assert=require('assert/strict');
const source=fs.readFileSync('src/webapp/static/app.js','utf8'),body=source.slice(source.indexOf('async function api('),source.indexOf('async function json('));
(async()=>{let posts=0,latest={authenticated:true,csrf_token:'new',user:{id:'same',org_id:'org'}};globalThis.leafScienceSession={integrated:true,csrf_token:'old',user:{id:'same',org_id:'org'}};
const api=new Function('fetch','return '+body)(async(url,options)=>{if(url==='/api/session')return new Response(JSON.stringify(latest));posts++;assert.equal(options.headers.get('X-CSRF-Token'),'new');return new Response('prepared',{headers:{'Content-Type':'image/jpeg'}});});
await api('/api/images/prepare',{method:'POST'});assert.equal(posts,1);
latest={authenticated:false};await assert.rejects(api('/api/images/prepare',{method:'POST'}),/another tab/);assert.equal(posts,1);
latest={authenticated:true,user:{id:'other',org_id:'org'}};await assert.rejects(api('/api/images/prepare',{method:'POST'}),/account or institution changed/);assert.equal(posts,1);
console.log('PASS: photo retry refreshes rotated CSRF, preserves expired tab and refuses changed account');})().catch(e=>{console.error(e);process.exitCode=1;});
