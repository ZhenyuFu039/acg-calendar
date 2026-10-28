// Real plugin snapshot and media, without external networking during UI QA.
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const http=require('node:http');
const fetch=url=>new Promise((resolve,reject)=>{
  http.get(url,response=>{const chunks=[];response.on('data',c=>chunks.push(c));response.on('end',()=>{
    const body=Buffer.concat(chunks).toString();resolve({ok:response.statusCode===200,text:async()=>body,json:async()=>JSON.parse(body)});
  });}).on('error',reject);
});
const base='http://127.0.0.1:8796';
(async()=>{
  const output=path.resolve('logs/nikke-e2e'), dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  await fs.cp('plugins/nikke-global',path.join(dir,'plugins/nikke-global'),{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['-X','utf8','app.py','--port','8796','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
  let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}), errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);
    await page.locator('#game-select option[value="nikke-global"]').waitFor({state:'attached'});
    await page.locator('#game-select').selectOption('nikke-global');
    await page.waitForFunction(()=>document.body.dataset.game==='nikke-global');
    await page.locator('#reset-filters').click();await page.locator('#list-view').click();
    await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===24);
    assert.equal(await page.locator('.catalog-event').filter({hasText:'挑战关卡'}).count(),0);
    assert.equal(await page.locator('.catalog-event').filter({hasText:'妮姬试用功能'}).count(),0);
    assert.equal(await page.locator('.catalog-event').filter({hasText:'周年情报站'}).count(),0);
    await page.locator('.catalog-event').filter({hasText:'【支线】LITTLE WITCH TRICK'}).click();
    await page.locator('.detail-explanation summary').click();assert.match(await page.locator('#detail-content').innerText(),/挑战关卡时间/);
    await page.locator('#detail-dialog [data-close]').click();
    assert.equal(await page.locator('#reward-summary').isVisible(),false);
    const state=await(await fetch(base+'/api/state')).json();
    const game=state.games.find(g=>g.id==='nikke-global');
    assert.ok((await fetch(base+game.icon)).ok);
    assert.match(game.source_label,/GameKee/);
    const cards=page.locator('.catalog-event');
    await cards.filter({hasText:'Cinderella Special'}).click();
    assert.match(await page.locator('#detail-content').innerText(),/2026\/12\/02 14:59/);
    assert.match(await page.locator('#detail-content').innerText(),/GameKee.*国际服/s);
    const hero=page.locator('.detail-hero');await hero.waitFor();
    await page.waitForFunction(()=>{const i=document.querySelector('.detail-hero');return i&&i.complete&&i.naturalWidth>0});
    await page.screenshot({path:path.join(output,'nikke-detail.png'),fullPage:false});
    await page.locator('#detail-dialog [data-close]').click();
    const icons=page.locator('.catalog-event img').filter({visible:true});
    assert.ok(await icons.count()>0);
    await page.locator('#fit-version').click();await page.locator('.timeline-row').first().waitFor();
    for(const width of [320,768,1440]){
      await page.setViewportSize({width,height:1050});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow at '+width);
    }
    await page.screenshot({path:path.join(output,'nikke-timeline.png'),fullPage:false});
    await page.locator('#game-select').selectOption('all');
    await page.waitForFunction(()=>document.body.dataset.game==='all');
    await page.locator('.game-combination summary').click();
    await page.locator('[data-select-game="arknights-cn"]').uncheck();
    await page.waitForFunction(()=>document.body.dataset.game==='selected');
    const selected=await(await fetch(base+'/api/state')).json();
    assert.deepEqual(new Set(selected.workspace.view.selected_games),new Set(['endfield-cn','nikke-global']));
    assert.equal(await page.locator('.timeline-row[data-game="arknights-cn"]').count(),0);
    const ics=await(await fetch(base+'/api/calendar.ics?game=nikke-global')).text();
    assert.match(ics,/FULL BURST DAY/);assert.doesNotMatch(ics,/PRTS/);
    assert.equal((ics.match(/BEGIN:VEVENT/g)||[]).length,6);
    assert.match(ics,/X-PLAYTIME-SKIPPED:18/); // Estimated starts remain in app, not confirmed ICS entries.
    assert.deepEqual(errors,[]);
    console.log('PASS: NIKKE discovery, 24 activities, merged challenge, excluded trial/anniversary, local logo and hero, UTC9/DST dates, community evidence, currency isolation, selected game combination, responsive layouts and ICS');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
