/* Development entry point is served only by development.install(). */
window.addEventListener('auth-ready',async()=>{
  const app=window.leafAuth,selector=document.getElementById('dev-persona');
  if(!selector)return;
  try{
    const data=await app.api('/api/dev/personas');
    selector.replaceChildren(new Option('New visitor',''));
    for(const u of data.personas)selector.add(new Option(`${u.username} · ${u.status} · ${u.role}`,u.id));
    selector.value=app.state.user?.id||'';
    const org=app.state.organization;
    document.getElementById('dev-active').textContent=`Active: ${app.state.user?.username||'New visitor'} · ${org?.name||'No verified institution'}${app.state.user?.status==='PENDING'?' (requested affiliation only)':''}`;
    const mailbox=async()=>{
      const {messages}=await app.api('/api/dev/mailbox');
      const box=document.getElementById('dev-mailbox');box.replaceChildren();
      if(!messages.length)box.textContent='No test emails yet.';
      for(const m of messages){const message=document.createElement('p');message.textContent=`${m.email} · ${m.purpose==='verify'?'Verify email':'Reset password'} · Code: ${m.code} · expires 10 minutes after issue`;box.append(message);}
    };
    document.getElementById('dev-mail-refresh').onclick=mailbox;
    await mailbox();
  }catch(e){app.notice(e.message,true);}
});
document.addEventListener('change',async e=>{
  if(e.target.id!=='dev-persona')return;
  const app=window.leafAuth;e.target.disabled=true;
  try{
    await app.api('/api/dev/switch',{user_id:e.target.value||null});
    await app.refresh();
    app.go(app.state.user?(app.state.user.status==='ACTIVE'?'/analyze':'/pending'):'/signup');
    app.notice('Test account selected. Workspace permissions are checked by the backend.');
  }catch(err){app.notice(err.message,true);}finally{e.target.disabled=false;}
});
