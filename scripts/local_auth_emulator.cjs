// Official pinned Auth emulator only. No console transport, no CLI login, no mail.
const path=require('node:path'),{createRequire}=require('node:module');
const root=process.env.HYDRO_FIREBASE_TOOLS_ROOT;
if(!root||process.env.K_SERVICE) throw Error('Explicit local tools path required');
const req=createRequire(path.join(root,'package.json'));
if(req('./package.json').version!=='15.22.4') throw Error('Unexpected Firebase Tools version');
const {logger}=req('./lib/logger'); const winston=req('winston');
logger.clear();logger.add(new winston.transports.Console({silent:true}));
const {AuthEmulator,SingleProjectMode}=req('./lib/emulator/auth');
const emulator=new AuthEmulator({projectId:'demo-hydroponic-leaf-research',host:'127.0.0.1',port:9099,singleProjectMode:SingleProjectMode.ERROR});
let stopping=false;
async function stop(){if(stopping)return;stopping=true;await emulator.stop();process.exit(0);}
// The parent owns this child. Broken stdin also cleans it after parent failure.
process.stdin.setEncoding('utf8');process.stdin.on('data',data=>{if(data.includes('stop'))void stop();});
process.stdin.on('end',()=>void stop());
// Ctrl+C also reaches this child console group; parent snapshots before stop.
process.on('SIGINT',()=>{});process.on('SIGTERM',()=>void stop());
emulator.start().catch(()=>process.exit(1));
