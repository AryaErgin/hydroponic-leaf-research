// Official installed firebase-tools15.22.4 Auth Emulator, loopback/demo only.
// No file/console transport receives action links, passwords or JWTs.
const path=require('node:path'),{createRequire}=require('node:module');
const root=process.env.HYDRO_FIREBASE_TOOLS_ROOT;
if(!root||process.env.K_SERVICE)throw Error('Explicit local Firebase Tools path required');
const requireTool=createRequire(path.join(root,'package.json'));
const version=requireTool('./package.json').version;
if(version!=='15.22.4')throw Error('Reinspect emulator wrapper before changing the pinned CLI version');
const {logger}=requireTool('./lib/logger');const winston=requireTool('winston');
logger.clear();logger.add(new winston.transports.Console({silent:true}));
const {AuthEmulator,SingleProjectMode}=requireTool('./lib/emulator/auth');
const emulator=new AuthEmulator({projectId:'demo-hydroponic-leaf-research',host:'127.0.0.1',port:9099,singleProjectMode:SingleProjectMode.ERROR});
emulator.start().then(()=>console.log(JSON.stringify({status:'ready',host:'127.0.0.1',port:9099,project:'demo-hydroponic-leaf-research',version,emails_sent:false}))).catch(()=>{console.error('Local Firebase Auth Emulator failed to start');process.exitCode=1;});
process.on('SIGTERM',async()=>{await emulator.stop();process.exit(0);});
process.on('SIGINT',async()=>{await emulator.stop();process.exit(0);});
