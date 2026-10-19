// A fourth game, CN scheduling authority and cross-Wiki cached event artwork.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8797';
const fetch=url=>new Promise((resolve,reject)=>{http.get(url,r=>{const b=[];r.on('data',c=>b.push(c));r.on('end',()=>{const s=Buffer.concat(b).toString();resolve({json:async()=>JSON.parse(s),text:async()=>s,ok:r.statusCode===200});});}).on('error',reject);});
(async()=>{
 const output=path.resolve('logs/azurlane-e2e'),dir=path.join(output,'data-'+Date.now());await fs.mkdir(dir,{recursive:true});
 for(const name of ['nikke-global','azurlane-cn'])await fs.cp('plugins/'+name,path.join(dir,'plugins',name),{recursive:true});
 const server=spawn(process.env.CALENDAR_PYTHON||'python',['-X','utf8','app.py','--port','8797','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
 let browser;
 try{
  for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#game-select option[value="azurlane-cn"]').waitFor({state:'attached'});
  await page.locator('#game-select').selectOption('azurlane-cn');await page.waitForFunction(()=>document.body.dataset.game==='azurlane-cn');
  await page.locator('#reset-filters').click();await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===20);
  assert.equal(await page.locator('#reward-summary').isVisible(),false);
  await page.locator('.catalog-event').filter({hasText:'严厉女仆不严厉'}).click();
  const detail=page.locator('#detail-content');assert.match(await detail.innerText(),/11月5日维护/);assert.match(await detail.innerText(),/2026\/11\/04 21:00/);assert.match(await detail.innerText(),/预计/);assert.match(await detail.innerText(),/英文 Wiki 仅补充/);
  const notice='具体活动情况请参考游戏官方时间';
  assert.equal(await page.locator('#official-time-notice').innerText(),notice);
  assert.equal(await detail.locator('.dialog-official-time-notice').innerText(),notice);
  await page.locator('#toggle-starred').click();await page.waitForFunction(()=>document.querySelector('#toggle-starred').getAttribute('aria-pressed')==='true');
  assert.equal(await detail.locator('.dialog-official-time-notice').count(),1);
  await page.waitForFunction(()=>{const i=document.querySelector('.detail-hero');return i&&i.complete&&i.naturalWidth>0});
  assert.equal(await page.locator('.wiki-evidence a').count(),2);
  await page.screenshot({path:path.join(output,'azurlane-detail.png')});
  await page.locator('#detail-dialog [data-close]').click();
  for(const game of ['endfield-cn','arknights-cn','nikke-global']) {
    await page.locator('#game-select').selectOption(game);await page.waitForFunction(g=>document.body.dataset.game===g,game);
    await page.locator('#list-view').click();await page.locator('.catalog-event').first().click();
    assert.equal(await detail.locator('.dialog-official-time-notice').innerText(),notice);
    await page.locator('#detail-dialog [data-close]').click();
  }
  await page.locator('#game-select').selectOption('azurlane-cn');await page.waitForFunction(()=>document.body.dataset.game==='azurlane-cn');
  await page.locator('.catalog-event').filter({hasText:'世界巡游 · 第三十一期'}).click();
  assert.match(await detail.innerText(),/2026\/11\/30 10:59/);
  await page.locator('#detail-dialog [data-close]').click();
  await page.locator('#fit-version').click();await page.locator('.timeline-row').first().waitFor();
  for(const width of [320,768,1440]){await page.setViewportSize({width,height:1050});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow at '+width);}
  await page.screenshot({path:path.join(output,'azurlane-timeline.png')});
  await page.locator('#game-select').selectOption('all');await page.waitForFunction(()=>document.body.dataset.game==='all');
  await page.locator('.game-combination summary').click();
  await page.locator('[data-select-game="endfield-cn"]').uncheck();await page.locator('[data-select-game="arknights-cn"]').uncheck();
  await page.waitForFunction(()=>document.body.dataset.game==='selected');
  const state=await(await fetch(base+'/api/state')).json();
  assert.deepEqual(new Set(state.workspace.view.selected_games),new Set(['nikke-global','azurlane-cn']));
  const ics=await(await fetch(base+'/api/calendar.ics?game=azurlane-cn')).text();
  assert.equal((ics.match(/BEGIN:VEVENT/g)||[]).length,2);assert.doesNotMatch(ics,/PRTS/);
  assert.deepEqual(errors,[]);console.log('PASS: fourth-game discovery, 20 CN events, estimated 10am maintenance deadline, global notice across four games and detail refresh, BWIKI attribution, DST conversion, game combinations, responsive layout and conservative ICS');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
