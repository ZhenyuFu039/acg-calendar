// F13: choose reminder games in the reminder dialog, separately from the calendar's game combination.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8808';
(async()=>{
  const output=path.resolve('logs/reminder-scope-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8808','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    const state=async()=>await(await fetch(base+'/api/state')).json();const games=(await state()).games.map(g=>g.id);
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/?game='+games[0]);await page.locator('#calendar .event-name').first().waitFor();
    assert.match(await page.locator('#combination-help').textContent(),/提醒覆盖全部已安装游戏/);
    await page.locator('#inbox-button').click();await page.locator('#reminder-dialog[open]').waitFor();
    assert.equal(await page.locator('input[name="reminder-scope"][value="all"]').isChecked(),true,'Existing installs keep reminding every game');
    assert.equal(await page.locator('.reminder-scope-games').isVisible(),false);
    // First custom choice starts from the current combination (one game here).
    await page.locator('input[name="reminder-scope"][value="custom"]').check();await page.locator('.reminder-scope-games').waitFor();
    assert.deepEqual((await state()).settings.reminder_scope,{mode:'custom',games:[games[0]]});
    await page.locator(`[data-reminder-game="${games[1]}"]`).check();
    await page.waitForFunction(n=>document.querySelectorAll('[data-reminder-game]:checked').length===n,2);
    assert.deepEqual((await state()).settings.reminder_scope.games.sort(),[games[0],games[1]].sort());
    // Following the combination keeps the custom list for later.
    await page.locator('input[name="reminder-scope"][value="follow"]').check();await page.locator('.reminder-scope-games').waitFor({state:'hidden'});
    assert.equal((await state()).settings.reminder_scope.mode,'follow');
    assert.match(await page.locator('#reminder-settings').textContent(),/跟随当前游戏组合（当前：/);
    await page.locator('input[name="reminder-scope"][value="custom"]').check();await page.locator('.reminder-scope-games').waitFor();
    assert.equal(await page.locator('[data-reminder-game]:checked').count(),2,'Custom list is restored');
    for(const id of [games[0],games[1]])await page.locator(`[data-reminder-game="${id}"]`).uncheck();
    await page.locator('#reminder-settings',{hasText:'未选择任何游戏，截止提醒不会发送'}).waitFor();
    // An explicitly emptied list stays empty across mode switches and reloads (only a never-saved list is prefilled).
    for(const other of ['all','follow']){
      await page.locator(`input[name="reminder-scope"][value="${other}"]`).check();await page.locator('.reminder-scope-games').waitFor({state:'hidden'});
      await page.locator('input[name="reminder-scope"][value="custom"]').check();await page.locator('.reminder-scope-games').waitFor();
      assert.deepEqual((await state()).settings.reminder_scope,{mode:'custom',games:[]},'Empty custom list after '+other);
      assert.equal(await page.locator('[data-reminder-game]:checked').count(),0);
    }
    await page.reload();await page.locator('#calendar .event-name').first().waitFor();
    await page.locator('#inbox-button').click();await page.locator('#reminder-settings',{hasText:'未选择任何游戏，截止提醒不会发送'}).waitFor();
    assert.equal(await page.locator('[data-reminder-game]:checked').count(),0);
    await page.locator('#reminder-dialog [data-close]').click();
    await page.waitForFunction(()=>document.querySelector('#combination-help').textContent.includes('未选择提醒游戏'));
    await page.locator('#inbox-button').click();await page.locator('input[name="reminder-scope"][value="all"]').check();
    await page.waitForFunction(()=>document.querySelector('#combination-help').textContent.includes('提醒覆盖全部已安装游戏'));
    await page.setViewportSize({width:375,height:812});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'reminder-scope-phone.png')});
    assert.deepEqual(errors,[]);console.log('PASS: reminder scope defaults to all games, custom list starts from the combination and is kept across modes, follow mode, empty-list warning, help text, phone width');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
