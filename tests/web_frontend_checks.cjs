// Execute actual request handlers with out-of-order responses; no real annotation.
const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/webapp/static/app.js','utf8');
let selected={id:'plant-A',display_name:'Plant A'},selectionRevision=1,analysis=null,resumeId=null,historyMeasurements=[];let guidanceRenders=0;function renderGuidance(){guidanceRenders++;}
const ui={'resume-row':{hidden:true}};
const $=id=>ui[id]||(ui[id]={textContent:'',hidden:false,replaceChildren(){},append(){},closest(){return{hidden:false};}});
function Option(text,value){return {text,value};}
const waiting={};async function api(url){return new Promise(resolve=>waiting[url]=resolve);}
function formatArea(value){return value===null?'none':String(value);}
let chart=[];function graph(rows){chart=rows;}
eval(source.slice(source.indexOf('async function pending('),source.indexOf('async function chooseFile(')));
eval(source.slice(source.indexOf('async function history('),source.indexOf('function graph(')));
eval(source.slice(source.indexOf('async function showAnalysis('),source.indexOf('function toggleEditor(')));
(async()=>{
 const a=pending(),ha=history();selected={id:'plant-B',display_name:'Plant B'};selectionRevision++;
 const b=pending(),hb=history();
 const respond=(url,data)=>waiting[url]({json:async()=>data});
 respond('/api/profiles/plant-B/analyses',[{id:'analysis-B',date:'2026-10-08',status:'DRAFT'}]);await b;
 respond('/api/profiles/plant-A/analyses',[{id:'analysis-A',date:'2026-10-08',status:'DRAFT'}]);await a;
 assert.equal(resumeId,'analysis-B');assert.equal(ui['resume-context'].textContent,'For Plant B - 2026-10-08');assert.equal(guidanceRenders,1,'A stale plant response must not redraw guidance or replace resume context');
 const data=value=>({measurements:[],growth:{current_area_cm2:value,previous_area_cm2:null,daily_growth_cm2:null,
   percentage_growth:null,relative_growth_rate_per_day:null,reason:'no_previous_measurement'}});
 respond('/api/profiles/plant-B/history',data(2));await hb;
 respond('/api/profiles/plant-A/history',data(1));await ha;
 assert.equal(ui.current.textContent,'2');assert.equal(ui.export.href,'/api/profiles/plant-B/export.csv');
 await assert.rejects(showAnalysis({profile_id:'plant-A'}),/another plant/);
 await assert.rejects(showAnalysis({profile_id:'plant-B',pipeline:null}),/not ready/);
 console.log('PASS: stale plant responses discarded; cross-plant/unready review rejected');
})().catch(error=>{console.error(error);process.exitCode=1;});
