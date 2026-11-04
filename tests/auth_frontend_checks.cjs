const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const handlers={};
const node={hidden:false,setAttribute(){},removeAttribute(){},innerHTML:'',textContent:''};
let navigations=[];
const context={document:{querySelector(){return node},addEventListener(name,fn){handlers[name]=fn}},
 window:{addEventListener(){},dispatchEvent(){}},location:{pathname:'/institution'},
 history:{pushState(_,__,path){navigations.push(path)}},fetch:()=>new Promise(()=>{}),
 Event:class {},console};
vm.runInNewContext(fs.readFileSync('src/auth_preview/static/app.js','utf8'),context);
function click(href,extra={}){
 let prevented=false;
 const anchor={getAttribute(name){return name==='href'?href:(extra[name]??null)},hasAttribute(name){return name in extra},...extra};
 handlers.click({target:{closest(){return anchor}},preventDefault(){prevented=true},ctrlKey:false,metaKey:false,button:0});
 return prevented;
}
assert.equal(click('/api/workspace/export.csv',{download:''}),false,'CSV links must download normally');
assert.equal(click('/signin',{target:'_blank'}),false,'New-tab links must retain browser behavior');
assert.equal(click('/api/artifacts/bau-masks'),false,'API links must not become SPA routes');
assert.equal(click('/signup'),true,'Application navigation should use the SPA');
assert.deepEqual(navigations,['/signup']);
const ui=fs.readFileSync('src/auth_preview/static/app.js','utf8');
assert(!ui.includes('Try other institution'));
assert(!ui.includes('Try private mask'));
assert(!ui.includes('Other institution'));
assert(!ui.includes('id="display-name"'),'Signup uses username only');
assert(!ui.includes('id="complete-email"'),'Recovery asks email only once');
console.log('Auth frontend: CSV/browser navigation and no cross-institution controls passed');
