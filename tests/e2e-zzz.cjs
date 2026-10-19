// Fifth-game integration: real official images, two versions and conservative time handling.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8798';
const fetch=url=>new Promise((resolve,reject)=>{http.get(url,r=>{const b=[];r.on('data',c=>b.push(c));r.on('end',()=>{const s=Buffer.concat(b).toString();resolve({json:async()=>JSON.parse(s),text:async()=>s,ok:r.statusCode===200});});}).on('error',reject);});
(async()=>{
 const output=path.resolve('logs/zzz-e2e'),dir=path.join(output,'data-'+Date.now());await fs.mkdir(dir,{recursive:true});
 for(const name of ['nikke-global','azurlane-cn','zzz-cn'])await fs.cp('plugins/'+name,path.join(dir,'plugins',name),{recursive:true});
 const server=spawn(process.env.CALENDAR_PYTHON||'python',['-X','utf8','app.py','--port','8798','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
 let browser;
 try{
  for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#game-select option[value="zzz-cn"]').waitFor({state:'attached'});
  await page.locator('#game-select').selectOption('zzz-cn');await page.waitForFunction(()=>document.body.dataset.game==='zzz-cn');
  const state=await(await fetch(base+'/api/state')).json();assert.equal(state.games.length,5);
  const versions=state.versions.filter(v=>v.game==='zzz-cn');assert.equal(versions.length,2);
  await page.locator('#reset-filters').click();
  await page.locator('#version-select').selectOption(versions.find(v=>v.name==='3.3版本').id);
  await page.locator('#list-view').click();await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===5);
  assert.equal(await page.locator('#reward-summary').isVisible(),false);
  await page.waitForFunction(()=>[...document.querySelectorAll('.game-icon')].every(i=>i.complete&&i.naturalWidth>0));
  await page.locator('.catalog-event').filter({hasText:'倾掷浮生'}).click();
  const detail=page.locator('#detail-content');
  assert.match(await detail.innerText(),/预计/);assert.match(await detail.innerText(),/2026\/10\/20 23:00/);assert.match(await detail.innerText(),/2026\/11\/10 22:59/);
  assert.match(await detail.innerText(),/绝区零官网 · 国服时间/);
  assert.equal(await detail.locator('.dialog-official-time-notice').innerText(),'具体活动情况请参考游戏官方时间');
  await page.waitForFunction(()=>{const i=document.querySelector('.detail-hero');return i&&i.complete&&i.naturalWidth>0});
  await page.locator('#toggle-starred').click();await page.waitForFunction(()=>document.querySelector('#toggle-starred').getAttribute('aria-pressed')==='true');
  assert.equal(await detail.locator('.dialog-official-time-notice').count(),1);
  await page.screenshot({path:path.join(output,'zzz-detail.png')});await page.locator('#detail-dialog [data-close]').click();
  await page.locator('#fit-version').click();await page.locator('.timeline-row').first().waitFor();
  await page.screenshot({path:path.join(output,'zzz-timeline.png')});
  for(const width of [320,768,1440]){await page.setViewportSize({width,height:1050});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow at '+width);}
  await page.locator('#version-select').selectOption(versions.find(v=>v.name==='3.2版本').id);
  await page.locator('#list-view').click();await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===20);
  await page.locator('.catalog-event').filter({hasText:'天使应援大作战'}).click();assert.match(await detail.innerText(),/2026\/11\/29 14:59/);await page.locator('#detail-dialog [data-close]').click();
  await page.locator('#game-select').selectOption('all');await page.waitForFunction(()=>document.body.dataset.game==='all');
  await page.locator('#timeline-view').click();
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/workspace')&&r.request().method()==='PATCH'),page.locator('#event-group').selectOption('none')]);
  await page.reload();await page.waitForFunction(()=>document.body.dataset.game==='all'&&document.querySelector('#event-group').value==='none');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/workspace')&&r.request().method()==='PATCH'),page.locator('#event-sort').selectOption('remaining')]);
  await page.reload();await page.waitForFunction(()=>document.body.dataset.game==='all'&&document.querySelector('#event-sort').value==='remaining');
  await page.waitForFunction(()=>[...document.querySelectorAll('.game-icon')].every(i=>i.complete&&i.naturalWidth>0));
  await page.screenshot({path:path.join(output,'five-game-timeline.png')});
  await page.locator('#list-view').click();
  await page.locator('.game-combination summary').click();
  for(const game of ['arknights-cn','nikke-global','azurlane-cn'])await page.locator('[data-select-game="'+game+'"]').uncheck();
  await page.waitForFunction(()=>document.body.dataset.game==='selected');
  const selected=await(await fetch(base+'/api/state')).json();assert.deepEqual(new Set(selected.workspace.view.selected_games),new Set(['endfield-cn','zzz-cn']));
  assert.equal(await page.locator('.catalog-event').count(),49);
  const ics=await(await fetch(base+'/api/calendar.ics?game=zzz-cn')).text();
  assert.equal((ics.match(/BEGIN:VEVENT/g)||[]).length,11);assert.doesNotMatch(ics,/PRTS|倾掷浮生/);
  assert.deepEqual(errors,[]);console.log('PASS: five-game discovery, 25 ZZZ records/23 images, version filtering, estimated maintenance, New York DST, details/attribution, APP icon, combination, responsive layout and 11 conservative ICS entries');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
