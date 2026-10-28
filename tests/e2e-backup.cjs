// Personal backup from the data panel: create, change, restore, reject a wrong file, restore back.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8806';
(async()=>{
  const output=path.resolve('logs/backup-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8806','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    const state=async()=>await(await fetch(base+'/api/state')).json();let data=await state();
    const request=async(url,method,body)=>{const r=await fetch(base+url,{method,headers:{'Content-Type':'application/json','X-Calendar-Token':data.csrf_token},body:JSON.stringify(body)});assert.equal(r.status,200,await r.clone().text());return r.json();};
    const event=data.events[0];
    await request('/api/events/'+event.id,'POST',{starred:true,notes:'备份前的备注'});
    await request('/api/plans','POST',{title:'备份里的计划',start:'2026-11-01T12:00:00Z',end:'2026-11-01T13:00:00Z',timezone:'Asia/Shanghai'});
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN',timezoneId:'America/New_York'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('#workspace-button').click();await page.locator('#preferences-dialog[open]').waitFor();await page.locator('[data-preference-tab="backup"]').click();
    await page.locator('#backup-list').filter({hasText:'还没有备份'}).waitFor();
    await page.locator('#create-backup').click();await page.locator('#backup-status').filter({hasText:'已备份到本机'}).waitFor();
    const row=page.locator('.backup-row').first();assert.match(await row.innerText(),/手动备份[\s\S]*收藏 1[\s\S]*备注 1[\s\S]*计划 1/);
    const name=await row.locator('[data-restore-backup]').getAttribute('data-restore-backup');
    const download=await (await fetch(base+'/api/backups/'+name)).json();assert.equal(download.format,'playtime-personal-backup');
    // Change personal data after the backup.
    data=await state();await request('/api/events/'+event.id,'POST',{starred:false,notes:'改动后的备注'});
    await request('/api/plans/'+data.plans[0].id,'DELETE',{});
    // A calendar JSON export is refused and nothing changes.
    const exported=await (await fetch(base+'/api/export?game=all')).json();
    const wrong=path.join(output,'calendar-export.json');await fs.writeFile(wrong,JSON.stringify(exported));
    await page.locator('#restore-backup-file').setInputFiles(wrong);
    await page.locator('#backup-status.error').filter({hasText:'当前数据未改变'}).waitFor();
    assert.equal(await page.locator('#backup-preview').isHidden(),true,'A rejected file shows no preview');
    data=await state();assert.equal(data.plans.length,0);assert.equal(data.events.find(e=>e.id===event.id).notes,'改动后的备注');
    // The preview lists what would change, before anything is written; cancelling keeps data.
    const backupsBefore=(await (await fetch(base+'/api/backups')).json()).backups.length;
    await page.locator(`[data-restore-backup="${name}"]`).click();await page.locator('#backup-preview:not([hidden])').waitFor();
    const preview=await page.locator('#backup-preview').innerText();
    assert.match(preview,/收藏：新增 1 · 取消 0/);assert.match(preview,/备注：改动 1 条/);assert.match(preview,/个人计划：恢复 1 · 移除 0/);assert.match(preview,/恢复前会自动备份当前数据/);
    assert.equal((await state()).plans.length,0,'Preview writes nothing');
    assert.equal((await (await fetch(base+'/api/backups')).json()).backups.length,backupsBefore,'Preview makes no safety backup');
    await page.locator('[data-cancel-restore]').click();await page.locator('#backup-status',{hasText:'已取消'}).waitFor();
    assert.equal((await state()).plans.length,0);
    // Restore the manual backup from the list after confirming the preview.
    await page.locator(`[data-restore-backup="${name}"]`).click();await page.locator('[data-confirm-restore]').click();
    await page.locator('#backup-status').filter({hasText:'已恢复'}).waitFor();
    data=await state();assert.equal(data.plans[0].title,'备份里的计划');
    const restored=data.events.find(e=>e.id===event.id);assert.equal(restored.notes,'备份前的备注');assert.equal(restored.starred,true);
    assert.equal(await page.locator('.backup-row').filter({hasText:'恢复前自动备份'}).count(),1);
    await page.locator('#backup-list').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'backup-panel.png')});
    // Upload the safety copy as a file to return to the changed data.
    const safety=(await (await fetch(base+'/api/backups')).json()).backups.find(b=>b.reason==='before-restore');
    const file=path.join(output,safety.name);await fs.writeFile(file,await (await fetch(base+'/api/backups/'+safety.name)).text());
    await page.locator('#restore-backup-file').setInputFiles(file);await page.locator('[data-confirm-restore]').click();
    await page.locator('#backup-status:not(.error)').filter({hasText:'已恢复'}).waitFor();
    data=await state();assert.equal(data.plans.length,0);assert.equal(data.events.find(e=>e.id===event.id).notes,'改动后的备注');
    // A backup differing only in previously omitted fields must still explain the changes.
    const latest=await request('/api/backups','POST',{});
    const omitted=await (await fetch(base+'/api/backups/'+latest.backup.name)).json();
    omitted.desktop_window=null;
    omitted.contents.local_state[event.id]={...omitted.contents.local_state[event.id],ignored:true,reminder_minutes:[]};
    omitted.contents.network_policy={policy:{interval_seconds:60},paused:true};
    const omittedFile=path.join(output,'restore-settings-only.json');await fs.writeFile(omittedFile,JSON.stringify(omitted));
    await page.locator('#restore-backup-file').setInputFiles(omittedFile);
    await page.locator('#backup-preview:not([hidden])').waitFor();
    const settingPreview=await page.locator('#backup-preview').innerText();
    assert.match(settingPreview,/忽略状态：忽略 1/);assert.match(settingPreview,/单活动提醒：改动 1 条/);
    assert.match(settingPreview,/采集设置：同站请求间隔、联网暂停状态/);assert.doesNotMatch(settingPreview,/与当前数据相同/);
    assert.equal((await state()).network.paused,false,'Preview must not pause the network');
    await page.locator('[data-confirm-restore]').click();
    await page.locator('#backup-status:not(.error)',{hasText:'已恢复'}).waitFor();
    data=await state();assert.equal(data.network.paused,true);assert.equal(data.network.policy.interval_seconds,60);
    assert.equal(data.events.find(e=>e.id===event.id).ignored,true);assert.deepEqual(data.events.find(e=>e.id===event.id).reminder_minutes,[]);
    for(const width of [1440,375]){await page.setViewportSize({width,height:812});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow '+width);}
    await page.locator('#backup-list').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'backup-panel-mobile.png')});
    assert.deepEqual(errors,[]);console.log('PASS: manual backup listing and download, wrong-format upload rejected without changes, cancel keeps data, restore from list with safety copy, restore from file, ignored/event reminders/network-only changes explained before confirmation, responsive panel');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
