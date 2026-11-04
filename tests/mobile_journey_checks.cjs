const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const ui=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('src/webapp/static/user-message.js','utf8')).toString('base64'));
 assert.match(ui.userMessage('calibration reference dimensions mismatch'),/mark|calibrat/i);
 assert.match(ui.userMessage('identity registry error'),/saved|review|retry|reload/i);
 assert.doesNotMatch(ui.userMessage('invalid calibration payload'),/payload/);
 assert.match(ui.userMessage('Failed to fetch'),/connection|connect/i);
 const app=fs.readFileSync('src/webapp/static/app.js','utf8');
 assert.doesNotMatch(app,/disabled=working\|\|!selected\|\|!file\|\|\(selected.calibration/,'Untrusted scale must not disable segmentation');
 assert.match(app,/\/api\/images\/prepare/);
 assert.match(app,/calibration_choice:'none'/,'New plants must not inherit an unrelated physical pot');
 assert.match(app,/request_id:setupRequestId/,'Double clicks and uncertain retries use the same creation ID');
 assert.match(app,/async function init\(\)\{working=true;buttons\(\);try/,'Plant input must wait for initial profile restoration');
 const html=fs.readFileSync('src/webapp/static/index.html','utf8');
 assert.match(html,/id="pot-undo"/);assert.match(html,/id="pot-viewport"/);
 assert.match(html,/viewport-fit=cover/);assert.match(html,/image\/heic/);
 assert.doesNotMatch(html,/id="pot-diameter"[^>]*value="5.82"/);
 console.log('PASS mobile journey: human messages, unscaled analysis, image normalization, plant retry identity, guided calibration controls');
})().catch(e=>{console.error(e);process.exitCode=1;});
