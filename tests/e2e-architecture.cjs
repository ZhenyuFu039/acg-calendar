// Runtime discovery of a third game, custom scopes and independent UI modules.
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8795';
(async()=>{
  const output=path.resolve('logs/architecture-e2e'), dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  await fs.cp('examples/plugins/sample-game',path.join(dir,'plugins/sample-game'),{recursive:true});
  const artKey='12345678901234567890123456789012.png';
  await fs.mkdir(path.join(dir,'artwork'),{recursive:true});
  await fs.copyFile('web/assets/oroberyl.png',path.join(dir,'artwork',artKey));
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8795','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
  let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}), errors=[];
    page.on('pageerror',e=>{errors.push(e.message);console.error('Browser:',e.message);});
    await page.goto(base);await page.locator('#reward-total').waitFor();
    let directoryOpens=0;
    await page.route('**/api/plugins/open-directory',async route=>{
      assert.equal(route.request().method(),'POST');
      assert.ok(route.request().headers()['x-calendar-token']);
      assert.deepEqual(route.request().postDataJSON(),{});
      directoryOpens++;
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({opened:true,directory:path.join(dir,'plugins')})});
    });
    await page.locator('#open-plugin-directory').click();
    await page.waitForFunction(()=>!document.querySelector('#plugin-directory-status').hidden);
    assert.equal(directoryOpens,1);
    assert.equal(await page.locator('#plugin-directory-status').innerText(),'已打开：'+path.join(dir,'plugins'));
    assert.match(await page.locator('#plugin-directory-help').innerText(),/移出以卸载/);
    await page.unroute('**/api/plugins/open-directory');
    assert.equal(await page.locator('#game-select option[value="sample-game"]').count(),1);
    await page.locator('#game-select').selectOption('sample-game');
    await page.waitForFunction(()=>document.body.dataset.game==='sample-game');
    await page.locator('#reset-filters').click();
    await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===2);
    assert.equal(await page.locator('.catalog-event').count(),2);
    assert.equal(await page.locator('#reward-summary').isVisible(),false);
    assert.match(await page.locator('#source-status').innerText(),/本地示例数据/);
    await page.locator('.catalog-event').filter({hasText:'示例兑换处'}).click();
    assert.match(await page.locator('#detail-content').innerText(),/2026\/11\/09 10:00/);
    await page.locator('#toggle-starred').click();
    await page.waitForFunction(()=>document.querySelector('#toggle-starred').getAttribute('aria-pressed')==='true');
    await page.locator('#detail-dialog [data-close]').click();
    assert.match(await page.locator('.catalog-event').first().innerText(),/★ 示例兑换处/);
    await page.locator('#game-select').selectOption('all');
    await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===40);
    await page.locator('.game-combination summary').click();
    await page.locator('[data-select-game="arknights-cn"]').uncheck();
    await page.waitForFunction(()=>document.body.dataset.game==='selected'&&document.querySelectorAll('.catalog-event').length===26);
    assert.equal(await page.locator('.catalog-event[data-game="arknights-cn"]').count(),0);
    await page.reload();await page.waitForFunction(()=>document.body.dataset.game==='selected');
    assert.equal(await page.locator('.catalog-event').count(),26);
    await page.locator('#event-sort').selectOption('remaining');
    await page.locator('#event-group').selectOption('none');
    await page.locator('#fit-version').click();await page.locator('.timeline-row').first().waitFor();
    assert.match(await page.locator('.timeline-row').first().innerText(),/★ 示例兑换处/);
    const count=await page.locator('.timeline-row').count();
    await page.locator('#hide-future').check();
    await page.waitForFunction(n=>document.querySelectorAll('.timeline-row').length<n,count);
    await page.reload();await page.waitForFunction(()=>document.querySelector('#hide-future').checked);
    const state=await(await fetch(base+'/api/state')).json();
    const patch=async data=>{
      const response=await fetch(base+'/api/workspace',{method:'PATCH',headers:{'Content-Type':'application/json','X-Calendar-Token':state.csrf_token},body:JSON.stringify(data)});
      assert.equal(response.status,200);return response.json();
    };
    const layout=structuredClone(state.workspace.layout);
    layout.modules.find(m=>m.id==='sources').visible=false;
    await patch({layout,theme:{accent:'#8873AE'}});
    await page.reload();await page.locator('#game-select option[value="sample-game"]').waitFor({state:'attached'});
    assert.equal(await page.locator('#source-status').isVisible(),false);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--green').trim()),'#8873AE');
    assert.equal(await page.locator('#event-sort').inputValue(),'remaining');
    // A controlled cached image tests the shared hero renderer, without network.
    await page.route('**/api/state',async route=>{
      const response=await route.fetch(), data=await response.json();
      data.events.find(e=>e.game==='sample-game').artwork={path:'/api/artwork/'+artKey};
      await route.fulfill({response,json:data});
    });
    await patch({view:{hide_future:false,selected_games:['sample-game'],scope:'selected'}});
    await page.reload();
    await page.waitForFunction(()=>document.body.dataset.game==='selected'&&document.querySelectorAll('[data-select-game]:checked').length===1&&document.querySelector('[data-select-game="sample-game"]').checked);
    await page.locator('#list-view').click();await page.locator('.catalog-event').filter({hasText:'示例节日'}).click();
    const hero=page.locator('.detail-hero');await hero.waitFor();
    assert.ok(await hero.evaluate(i=>i.complete&&i.naturalWidth>0));
    const bounds=await hero.boundingBox();assert.ok(bounds.height>50&&bounds.height<=241);
    await page.locator('#detail-dialog [data-close]').click();
    await page.unroute('**/api/state');
    await patch({view:{selected_games:['endfield-cn','sample-game'],scope:'selected',hide_future:false}});
    await page.reload();await page.locator('#fit-version').click();await page.locator('.timeline-row').first().waitFor();
    for(const width of [320,768,1440]) {await page.setViewportSize({width,height:1050});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow at '+width);}
    await page.setViewportSize({width:1440,height:1050});await page.screenshot({path:path.join(output,'plugin-combination.png'),fullPage:false});
    assert.deepEqual(errors,[]);
    console.log('PASS: local plugin discovery, third-game metadata, custom combination persistence, starred rows, remaining sort, future filter, module/theme isolation, detail hero, responsive layouts');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
