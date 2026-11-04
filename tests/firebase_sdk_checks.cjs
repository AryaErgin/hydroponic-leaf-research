// Official web SDK contract against the local emulator. No inbox delivery claim.
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url'),crypto=require('node:crypto');
(async()=>{
 if(process.env.K_SERVICE)throw Error('Local test only');
 const client=path.resolve(__dirname,'../src/firebase_preview/client');
 const appSdk=await import(pathToFileURL(require.resolve('firebase/app',{paths:[client]})).href);
 const sdk=await import(pathToFileURL(require.resolve('firebase/auth',{paths:[client]})).href);
 const projectId='demo-hydroponic-leaf-research';
 const app=appSdk.initializeApp({projectId,apiKey:'fake-api-key',authDomain:projectId+'.firebaseapp.com'},crypto.randomUUID());
 const auth=sdk.getAuth(app);sdk.connectAuthEmulator(auth,'http://127.0.0.1:9099',{disableWarnings:true});
 await sdk.setPersistence(auth,sdk.inMemoryPersistence);
 const email='sdk-'+crypto.randomUUID()+'@example.test',password='Synthetic Firebase passphrase 47!';
 async function code(kind){
  const r=await fetch('http://127.0.0.1:9099/emulator/v1/projects/'+projectId+'/oobCodes');assert.equal(r.status,200);
  return (await r.json()).oobCodes.filter(x=>x.requestType===kind&&(x.email===email||x.newEmail===newEmail)).at(-1).oobCode;
 }
 let newEmail='changed-'+crypto.randomUUID()+'@example.test';
 try{
  let credential=await sdk.createUserWithEmailAndPassword(auth,email,password);assert.equal(credential.user.emailVerified,false);
  await sdk.sendEmailVerification(credential.user);
  const verification=await code('VERIFY_EMAIL');await sdk.applyActionCode(auth,verification);
  await assert.rejects(sdk.applyActionCode(auth,verification));
  await sdk.signOut(auth);credential=await sdk.signInWithEmailAndPassword(auth,email,password);assert.equal(credential.user.emailVerified,true);
  await sdk.verifyBeforeUpdateEmail(credential.user,newEmail);assert.equal(credential.user.email,email);
  const change=await code('VERIFY_AND_CHANGE_EMAIL');await sdk.applyActionCode(auth,change);
  await sdk.signOut(auth);credential=await sdk.signInWithEmailAndPassword(auth,newEmail,password);assert.equal(credential.user.emailVerified,true);
  await sdk.sendPasswordResetEmail(auth,newEmail);
  const r=await fetch('http://127.0.0.1:9099/emulator/v1/projects/'+projectId+'/oobCodes');
  const reset=(await r.json()).oobCodes.filter(x=>x.requestType==='PASSWORD_RESET'&&x.email===newEmail).at(-1).oobCode;
  await sdk.confirmPasswordReset(auth,reset,password+' reset');
  await assert.rejects(sdk.confirmPasswordReset(auth,reset,password+' reset'));
  await sdk.signOut(auth);await assert.rejects(sdk.signInWithEmailAndPassword(auth,newEmail,password));
  credential=await sdk.signInWithEmailAndPassword(auth,newEmail,password+' reset');
  await sdk.updatePassword(credential.user,password+' final');
  await sdk.signOut(auth);credential=await sdk.signInWithEmailAndPassword(auth,newEmail,password+' final');
  await assert.rejects(sdk.applyActionCode(auth,'invalid-code'));
  await sdk.deleteUser(credential.user);
  console.log(JSON.stringify({status:'passed',flows:['registration','verification','verified_email_change','password_reset','password_change','single_use','invalid_code'],emails_sent:false,real_inbox_delivery:'UNVERIFIED'}));
 }finally{await sdk.signOut(auth);await appSdk.deleteApp(app);}
})().catch(()=>{console.error('Firebase SDK contract failed; provider credentials and codes suppressed');process.exitCode=1;});
