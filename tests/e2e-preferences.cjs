// Host-owned preferences, UI persistence and cooldown controls, with no live collection.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),http=require('node:http');
const {spawn,spawnSync}=require('node:child_process');const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const python=process.env.CALENDAR_PYTHON||'python',base='http://127.0.0.1:8801';
const request=(route,method='GET',data=null,token='')=>new Promise((resolve,reject)=>{
 const body=data===null?null:JSON.stringify(data),req=http.request(base+route,{method,headers:{'Content-Type':'application/json','X-Calendar-Token':token,...(body?{'Content-Length':Buffer.byteLength(body)}:{})}},res=>{const chunks=[];res.on('data',b=>chunks.push(b));res.on('end',()=>{const text=Buffer.concat(chunks).toString();resolve({status:res.statusCode,data:JSON.parse(text)});});});req.on('error',reject);req.end(body);
});
(async()=>{
 const output=path.resolve('logs/preferences-e2e'),dir=path.join(output,'data-'+Date.now());await fs.mkdir(dir,{recursive:true});
 for(const name of ['nikke-global','azurlane-cn','zzz-cn'])await fs.cp('plugins/'+name,path.join(dir,'plugins',name),{recursive:true});
 const seed=`from pathlib import Path\nimport sys,time\nfrom acg_calendar.storage import Store\ns=Store(Path(sys.argv[1])/'calendar.sqlite3')\ns.set_meta('network-policy',{'sites':{'biligame.com':{'paused':False,'next_at':0,'cooldown_until':time.time()+600,'window_start':time.time(),'requests':17,'failures':1,'error':'测试冷却：不发起真实请求','active':False}}})`;
 assert.equal(spawnSync(python,['-X','utf8','-c',seed,dir],{windowsHide:true}).status,0);
 const server=spawn(python,['-X','utf8','app.py','--port','8801','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
 try{
  for(let i=0;i<50;i++){try{if((await request('/api/health')).status===200)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
  browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#game-select option[value="zzz-cn"]').waitFor({state:'attached'});
  const before=(await request('/api/state')).data;assert.equal(before.provider_api_version,1);assert.equal(before.games.length,5);
  assert.equal(before.settings.sync_interval_minutes,1440);assert.equal(before.network.policy.interval_seconds,10);
  assert.match(await page.locator('h1').innerText(),/限时活动周期可视化/);assert.match(await page.locator('.header-context').innerText(),/规划你的游戏时间/);
  for(const preset of ['arknights','endfield','nikke','azurlane','zzz','dark','light']){
    await page.locator('#workspace-button').click();assert.equal(await page.locator('[data-preference-panel="theme"]').isVisible(),true);
    assert.notEqual(await page.locator('#theme-preset').inputValue(),'custom','Unmodified presets must not be mislabeled as custom because of hex case');
    assert.match(await page.locator('#theme-preset option[value="light"]').innerText(),/标准/);
    await page.locator('#theme-preset').selectOption(preset);await page.locator('#save-theme').click();await page.waitForFunction(p=>document.documentElement.dataset.theme===p,preset);
    assert.equal((await request('/api/state')).data.workspace.theme.preset,preset);
    assert.equal(await page.locator('.brand-mark').evaluate(n=>getComputedStyle(n).borderRadius),'4px','Logo geometry is independent of theme controls');
    if(['endfield','arknights','zzz','nikke'].includes(preset))await page.screenshot({path:path.join(output,'theme-'+preset+'.png'),animations:'disabled'});
    if(preset==='endfield'){
      assert.equal(await page.locator('#sync-button').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(255, 250, 0)');
      assert.equal(await page.locator('#sync-button').evaluate(n=>getComputedStyle(n).color),'rgb(17, 17, 17)');
      assert.equal(await page.locator('.calendar-panel').first().evaluate(n=>getComputedStyle(n).borderRadius),'0px');
      const top=await page.locator('.timeline-head').evaluate(n=>n.getBoundingClientRect().top);
      await page.locator('.timeline-scroll').evaluate(n=>{n.scrollTop=240;});
      assert.ok(Math.abs(await page.locator('.timeline-head').evaluate(n=>n.getBoundingClientRect().top)-top)<2);
      await page.setViewportSize({width:2560,height:1313});
      assert.equal(await page.locator('.app-layout').evaluate(n=>n.getBoundingClientRect().width),2560);
      assert.equal(await page.locator('.main-content').evaluate(n=>n.getBoundingClientRect().right),2560);
      assert.equal(await page.locator('#deadline-strip').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(255, 250, 0)');
      assert.equal(await page.locator('#deadline-strip strong').evaluate(n=>getComputedStyle(n).color),'rgb(17, 17, 17)');
      assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));
      await page.screenshot({path:path.join(output,'workspace-wide.png'),animations:'disabled'});
      const saved=(await request('/api/state')).data.workspace;
      await page.locator('.timeline-scroll').evaluate(n=>n.scrollTop=180);
      await page.evaluate(()=>PlaytimeWorkspace.renderModules());
      assert.equal(await page.locator('.timeline-scroll').evaluate(n=>n.scrollTop),180,'Periodic renders must preserve reading position');
      const normalHeight=(await page.locator('#calendar').boundingBox()).height;
      await page.locator('#calendar-expand').click();
      assert.equal(await page.locator('.sidebar').isVisible(),false);assert.equal(await page.locator('.app-header').isVisible(),false);
      assert.equal(await page.locator('#deadline-strip').isVisible(),false);
      assert.ok((await page.locator('#calendar').boundingBox()).height>normalHeight+150);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));
      await page.screenshot({path:path.join(output,'timeline-expanded.png'),animations:'disabled'});
      const focusedEvent=await page.locator('.event-name').first().getAttribute('data-event');
      await page.locator('.event-name').first().click();await page.locator('#detail-dialog').waitFor({state:'visible'});
      await page.waitForFunction(id=>PlaytimeWorkspace.view.focus.event_id===id,focusedEvent);
      saved.view.focus.event_id=focusedEvent; // Opening details now records the shared event focus.
      await page.keyboard.press('Escape');assert.equal(await page.locator('#calendar-expand').getAttribute('aria-pressed'),'true','Closing a detail must not close focus mode');
      await page.keyboard.press('Escape');assert.equal(await page.locator('#calendar-expand').getAttribute('aria-pressed'),'false');
      assert.equal(await page.locator('.sidebar').isVisible(),true);assert.deepEqual((await request('/api/state')).data.workspace,saved);
      for(const viewport of [{width:1440,height:720},{width:320,height:720}]){
        await page.setViewportSize(viewport);
        if(viewport.width<=850){
          // F19: phones scroll as one document, open the upcoming-deadline list and keep no nested vertical scrollers.
          assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal page overflow');
          assert.equal(await page.locator('#upcoming-view').getAttribute('aria-pressed'),'true','Phones open the upcoming-deadline list by default');
          assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>{const s=getComputedStyle(e);return /(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+2&&e.clientHeight>30&&!e.closest('dialog')}).map(e=>e.className)),[],'No nested vertical scrolling on phones');
          await page.locator('#timeline-view').click();await page.locator('.timeline-scroll').waitFor();
        } else assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1 && document.documentElement.scrollWidth<=innerWidth+1),'The page itself must not scroll');
        const toggle=await page.locator('#calendar-expand').boundingBox();assert.ok(toggle.y>=0&&toggle.y+toggle.height<=viewport.height,'Expand remains reachable on short screens');
        await page.screenshot({path:path.join(output,`workspace-short-${viewport.width}.png`),animations:'disabled'});
        const shortHeight=(await page.locator('.timeline-scroll').boundingBox()).height;
        assert.ok(shortHeight>=100,`Short-screen ${viewport.width} must still show activity rows; scroll height=${shortHeight}`);
        await page.locator('#calendar-expand').click();assert.ok((await page.locator('#calendar').boundingBox()).height>viewport.height-190);
        await page.locator('#month-view').click();await page.locator('.month-cell').first().waitFor({state:'visible'});
        await page.locator('#timeline-view').click();await page.locator('#calendar-expand').click();
      }
      await page.setViewportSize({width:1440,height:1050});
    }
    if(preset==='zzz')assert.equal(await page.locator('#sync-button').evaluate(n=>getComputedStyle(n).borderRadius),'32px');
  }
  await page.locator('#data-button').click();assert.equal(await page.locator('[data-preference-panel="network"]').isVisible(),true);assert.equal(await page.locator('#sync-interval').isVisible(),true);assert.match(await page.locator('#sync-schedule').innerText(),/下次自动复查/);assert.equal(await page.locator('#sync-interval option').count(),2);await page.locator('#sync-interval').selectOption('1080');await page.waitForFunction(()=>document.querySelector('#sync-interval').value==='1080');await page.locator('#preferences-dialog [data-close]').click();assert.equal((await request('/api/state')).data.settings.sync_interval_minutes,1080);
  await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="layout"]').click();await page.locator('[data-layout-visible="sources"]').uncheck();
  await page.locator('[data-layout-move="0"][data-step="1"]').click();
  await page.locator('#sidebar-position').selectOption('right');await page.locator('#sidebar-width').fill('280');await page.locator('#desktop-calendar-position').selectOption('top');await page.locator('#save-layout').click();
  await page.waitForFunction(()=>document.body.dataset.sidebar==='right');assert.equal(await page.locator('#source-status').isVisible(),false);
  await page.reload();await page.waitForFunction(()=>document.body.dataset.sidebar==='right');
  let state=(await request('/api/state')).data;assert.equal(state.workspace.layout.sidebar.width,280);assert.equal(state.workspace.layout.modules[1].id,'sources');
  const layout=state.workspace.layout;
  await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="theme"]').click();await page.locator('#theme-preset').selectOption('dark');
  await page.locator('.theme-group').filter({hasText:'04 / 几何与排版'}).locator('summary').click();
  await page.locator('#theme-font_size').fill('16');await page.locator('#theme-radius').fill('12');await page.locator('#theme-desktop_radius').fill('7');await page.locator('#theme-desktop_control_radius').fill('3');await page.locator('#theme-spacing').fill('18');await page.locator('#theme-card_opacity').fill('80');await page.locator('#save-theme').click();
  await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
  await page.screenshot({path:path.join(output,'dark-timeline.png'),animations:'disabled'});
  assert.equal(await page.locator('.bar-label').first().evaluate(n=>getComputedStyle(n).color),'rgb(227, 235, 221)');
  assert.notEqual(await page.locator('.filter-row').first().evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(245, 247, 239)');
  state=(await request('/api/state')).data;assert.deepEqual(state.workspace.layout,layout);assert.equal(state.workspace.theme.card_opacity,.8);assert.equal(state.workspace.theme.radius,12);assert.equal(state.workspace.theme.desktop_radius,7);assert.equal(state.workspace.theme.desktop_control_radius,3);
  await page.reload();await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');assert.equal(await page.evaluate(()=>getComputedStyle(document.body).color),'rgb(227, 235, 221)');
  await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="theme"]').click();
  assert.equal(await page.locator('#theme-preset').inputValue(),'custom');
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export-theme').click()]);const exportedTheme=JSON.parse(await fs.readFile(await download.path(),'utf8'));assert.equal(exportedTheme.font_size,16);
  await page.locator('#theme-text').fill('#252d23');await page.locator('#save-theme').click();await page.waitForFunction(()=>document.querySelector('#preferences-error').textContent.includes('未保存'));
  assert.equal((await request('/api/state')).data.workspace.theme.text.toUpperCase(),'#E3EBDD');
  await page.locator('#theme-preset').selectOption('light');await page.locator('#save-theme').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');assert.deepEqual((await request('/api/state')).data.workspace.layout,layout);
  await page.locator('#workspace-button').click();
  await page.locator('#import-theme').setInputFiles({name:'theme.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(exportedTheme))});await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');assert.equal((await request('/api/state')).data.workspace.theme.font_size,16);
  await page.locator('#preferences-dialog [data-close]').click();
  await page.goto(base+'/?appearance=reminders');await page.locator('#reminder-dialog').waitFor({state:'visible'});await page.locator('#reminder-dialog [data-close]').click();
  for(const width of [320,768,1440]){await page.setViewportSize({width,height:1050});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Custom theme overflow at '+width);}
  await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="theme"]').click();await page.locator('#reset-theme').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
  await page.locator('[data-preference-tab="network"]').click();await page.locator('#network-interval_seconds').fill('12');await page.locator('#network-hourly_limit').fill('80');await page.locator('#network-round_limit').fill('20');await page.locator('#save-network').click();
  await page.waitForFunction(()=>document.querySelector('#preferences-error').textContent.includes('限制已保存'));
  const cooldown=(await request('/api/network')).data.sites[0].next_allowed_at;
  await page.locator('#network-toggle').click();await page.waitForFunction(()=>document.querySelector('#network-summary').textContent.includes('联网已暂停'));
  await page.locator('[data-network-site="biligame.com"]').click();await page.waitForFunction(()=>document.querySelector('[data-network-site]').dataset.paused==='false');
  await page.locator('[data-network-site="biligame.com"]').click();await page.waitForFunction(()=>document.querySelector('[data-network-site]').dataset.paused==='true');
  state=(await request('/api/state')).data;assert.equal(state.network.sites[0].next_allowed_at,cooldown);assert.equal(state.network.policy.interval_seconds,12);assert.equal(state.network.paused,true);
  assert.equal((await request('/api/network','PATCH',{policy:{interval_seconds:0}},state.csrf_token)).status,400);
  await page.screenshot({path:path.join(output,'network-controls.png')});
  await page.reload();await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="network"]').click();assert.match(await page.locator('#network-summary').innerText(),/联网已暂停/);assert.equal(await page.locator('#network-hourly_limit').inputValue(),'80');
  await page.locator('#network-toggle').click();await page.waitForFunction(()=>document.querySelector('#network-summary').textContent.includes('联网已启用'));
  await page.locator('[data-preference-tab="layout"]').click();await page.locator('#edit-layout').click();
  const frame=page.locator('[data-frame="calendar"]');await frame.getByRole('button',{name:'上移日历 / 时间轴',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-frame="calendar"]').style.order==='2');
  const rewardBox=await page.locator('[data-frame="rewards"]').boundingBox(),calendarBox=await frame.boundingBox();assert.ok(rewardBox.y+rewardBox.height<=calendarBox.y,'Editing handles must not overlap the preceding module');
  await page.screenshot({path:path.join(output,'module-editing.png')});
  await frame.getByRole('button',{name:'展开或折叠日历 / 时间轴',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-module="calendar"]').hidden);
  await frame.getByRole('button',{name:'展开或折叠日历 / 时间轴',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('[data-module="calendar"]').hidden);
  await page.locator('#finish-layout').click();await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="layout"]').click();await page.locator('#sidebar-visible').uncheck();await page.locator('#save-layout').click();await page.waitForFunction(()=>document.body.dataset.sidebar==='hidden');
  assert.equal(await page.locator('.sidebar').isVisible(),false);
  await page.locator('#workspace-button').click();await page.locator('[data-preference-tab="layout"]').click();await page.locator('#reset-layout').click();await page.waitForFunction(()=>document.body.dataset.sidebar==='left');await page.locator('#preferences-dialog [data-close]').click();
  await page.locator('#rail-games').click();await page.waitForFunction(()=>document.body.dataset.sidebar==='hidden');await page.locator('#rail-games').click();await page.waitForFunction(()=>document.body.dataset.sidebar==='left');
  for(const width of [320,768,1440]){await page.setViewportSize({width,height:1050});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow at '+width);if(width===320){await page.locator('#sidebar-more').click();const a=await page.locator('#game-select').boundingBox(),b=await page.locator('#version-select').boundingBox();assert.ok(a.y+a.height<=b.y,'Mobile game/version controls must not overlap');await page.screenshot({path:path.join(output,'design-mobile-320.png'),animations:'disabled'});await page.locator('#sidebar-more').click();}}
  state=(await request('/api/state')).data;assert.equal(state.provider_api_version,1);assert.deepEqual(state.games.map(g=>g.id),before.games.map(g=>g.id));assert.equal(state.events.length,before.events.length);assert.deepEqual(errors,[]);
  console.log('PASS: layout reorder/collapse/sidebar recovery and persistence; dark/custom theme/export/contrast guard/reset isolation; shared network policy, pause and cooldown controls; plugin API 1 and all five calendars unchanged; responsive UI');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
