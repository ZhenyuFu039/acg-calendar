// F15: plugin page lists plugins, turns one off and on again; changes apply after a restart and keep personal records.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8811';
(async()=>{
  const output=path.resolve('logs/plugin-manager-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(path.join(dir,'plugins'),{recursive:true});
  await fs.cp(path.resolve('examples/plugins/sample-game'),path.join(dir,'plugins','sample-game'),{recursive:true});
  // Two more plugins, one in a Chinese folder name with a space (M2).
  for(const [folder,id] of [['plugin-alpha','plugin-alpha'],['日历 乙','plugin-bravo']]){
    await fs.cp(path.resolve('examples/plugins/sample-game'),path.join(dir,'plugins',folder),{recursive:true});
    const file=path.join(dir,'plugins',folder,'manifest.json'),manifest=JSON.parse(await fs.readFile(file,'utf8'));
    manifest.game={...manifest.game,id,name:'插件 '+id};await fs.writeFile(file,JSON.stringify(manifest));
  }
  await fs.mkdir(path.join(dir,'plugins','broken'));await fs.writeFile(path.join(dir,'plugins','broken','manifest.json'),'{"api_version":99}');
  let server,browser;
  const start=async()=>{
    server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8811','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)return;}catch{}await new Promise(r=>setTimeout(r,200));}
    throw Error('Server failed');
  };
  const stop=async()=>{const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;
    for(let i=0;i<50;i++){try{await fetch(base+'/api/health');}catch{return;}await new Promise(r=>setTimeout(r,100));}throw Error('Old server still answering');};
  try{
    await start();
    let data=await(await fetch(base+'/api/state')).json();
    const event=data.events.find(e=>e.game==='sample-game');
    await fetch(base+'/api/events/'+event.id,{method:'POST',headers:{'Content-Type':'application/json','X-Calendar-Token':data.csrf_token},body:JSON.stringify({notes:'停用后也要保留'})});
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('#calendar .event-name').first().waitFor();
    await page.locator('#manage-plugins').click();await page.locator('#preferences-dialog[open]').waitFor();
    assert.equal(await page.locator('#preferences-title').textContent(),'日历插件');
    const row=page.locator('.plugin-row',{hasText:'示例游戏（虚构）'});await row.waitFor();
    assert.match(await row.innerText(),/已加载[\s\S]*文件夹 sample-game[\s\S]*插件 API 1/);
    assert.match(await page.locator('.plugin-row',{hasText:'broken'}).innerText(),/加载失败[\s\S]*api_version/);
    assert.match(await page.locator('#plugin-list').innerText(),/内置：.*不能停用/);
    await row.locator('[data-plugin-toggle]').click();
    await page.locator('.plugin-row',{hasText:'已设为停用，重启游时后生效'}).waitFor();
    assert.deepEqual((await(await fetch(base+'/api/state')).json()).settings.disabled_plugins,['sample-game']);
    await page.screenshot({path:path.join(output,'plugin-manager.png')});
    // Restart: the game is gone from the calendar, its row offers 启用, personal notes remain stored.
    await stop();await start();await page.reload();await page.locator('#calendar').waitFor();
    data=await(await fetch(base+'/api/state')).json();
    assert.equal(data.games.some(g=>g.id==='sample-game'),false);
    await page.locator('#manage-plugins').click();
    const off=page.locator('.plugin-row',{hasText:'sample-game'});await off.waitFor();
    assert.match(await off.innerText(),/已停用/);
    await off.locator('[data-plugin-toggle]').click();await page.locator('.plugin-row',{hasText:'已设为启用，重启游时后生效'}).waitFor();
    await stop();await start();
    data=await(await fetch(base+'/api/state')).json();
    assert.equal(data.events.find(e=>e.id===event.id).notes,'停用后也要保留');
    // M1: while one toggle is saving, the other rows wait; both choices persist.
    await page.reload();await page.locator('#data-button:not([disabled])').waitFor();await page.locator('#manage-plugins').click();
    let release,held=false;const gate=new Promise(r=>release=r);
    await page.route('**/api/plugins/state',async route=>{if(!held){held=true;const response=await route.fetch();await gate;await route.fulfill({response});}else await route.continue();});
    await page.locator('.plugin-row',{hasText:'plugin-alpha'}).locator('[data-plugin-toggle]').click();
    await page.waitForFunction(()=>document.querySelector('#plugin-list').getAttribute('aria-busy')==='true');
    assert.equal(await page.locator('.plugin-row',{hasText:'plugin-bravo'}).locator('[data-plugin-toggle]').isDisabled(),true,'Other rows wait for the save');
    release();
    await page.waitForFunction(()=>!document.querySelector('#plugin-list').hasAttribute('aria-busy'));
    await page.locator('.plugin-row',{hasText:'plugin-bravo'}).locator('[data-plugin-toggle]:not([disabled])').click();
    await page.locator('.plugin-row',{hasText:'plugin-bravo'}).filter({hasText:'已设为停用'}).waitFor();
    assert.deepEqual((await(await fetch(base+'/api/state')).json()).settings.disabled_plugins,['plugin-alpha','日历 乙'].sort());
    await stop();await start();
    data=await(await fetch(base+'/api/state')).json();
    assert.equal(data.games.some(g=>['plugin-alpha','plugin-bravo'].includes(g.id)),false,'Both stay off after restart, including the Chinese folder');
    for(const width of [375]){await page.setViewportSize({width,height:812});await page.reload();await page.locator('#data-button:not([disabled])').waitFor();
      await page.evaluate(()=>window.PlaytimeCustomize.open('plugins'));await page.locator('.plugin-row').first().waitFor();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
    assert.deepEqual(errors,[]);console.log('PASS: plugin list with loaded/error/builtin rows, disable and enable apply after restart, personal notes kept, overlapping toggles wait and both persist, Chinese/space folder, phone width');
  }finally{if(browser)await browser.close();if(server)server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
