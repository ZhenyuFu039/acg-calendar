const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8793';
(async()=>{
  const dir=path.resolve('logs/multigame-e2e','data-'+Date.now());await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8793','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
  let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'});const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('#reward-total').waitFor();
    assert.equal(await page.locator('#reward-total').innerText(),'11,000嵌晶玉');
    await page.locator('#game-select').selectOption('arknights-cn');
    await page.waitForFunction(()=>document.body.dataset.game==='arknights-cn'&&document.querySelector('#game-name').textContent==='明日方舟');
    assert.match(await page.locator('#source-status').innerText(),/PRTS 社区 Wiki/);
    assert.equal(await page.locator('#reward-summary').isVisible(),false);
    assert.equal(await page.locator('#maintenance-button').isVisible(),false);
    await page.locator('#reset-filters').click();await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===14);
    await page.locator('#event-search').fill('昨日海');
    assert.equal(await page.locator('.catalog-event').count(),2);
    await page.locator('.catalog-event').filter({hasText:'奖励兑换'}).click();
    assert.match(await page.locator('.detail-time-grid').innerText(),/2026\/10\/29 15:59/);
    assert.match(await page.locator('.wiki-evidence').innerText(),/固定修订|CC BY-NC-SA/);
    assert.equal(await page.locator('.reward-evidence').count(),0);
    await page.locator('#detail-dialog [data-close]').click();
    await page.locator('.catalog-event').filter({hasNotText:'奖励兑换'}).click();
    assert.equal(await page.locator('.detail-phases>div').count(),2);
    assert.match(await page.locator('.detail-time-grid').innerText(),/2026\/10\/22 15:59/);
    await page.locator('#toggle-completed').click();await page.waitForFunction(()=>document.querySelector('#toggle-completed').textContent.includes('撤销'));
    await page.locator('#detail-dialog [data-close]').click();await page.reload();await page.waitForFunction(()=>document.body.dataset.game==='arknights-cn');
    await page.locator('#rules-button').click();await page.waitForFunction(()=>document.querySelector('#rules-json').textContent.includes('arknights-cn'));
    assert.match(await page.locator('#rules-json').innerText(),/PRTS/);await page.locator('#rules-dialog [data-close]').click();
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('a[href^="/api/calendar.ics"]').click()]);
    const ics=await fs.readFile(await download.path(),'utf8');assert.match(ics,/PRTS/);assert.doesNotMatch(ics,/嵌晶玉/);
    await page.locator('#timezone-select').selectOption('Asia/Shanghai');
    await page.locator('#game-select').selectOption('all');await page.waitForFunction(()=>document.body.dataset.game==='all');
    await page.locator('#reset-filters').click();await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===38);
    const catalog=await page.locator('.activity-catalog').innerText();assert.match(catalog,/明日方舟/);assert.match(catalog,/终末地/);
    await page.locator('#fit-version').click();await page.waitForFunction(()=>document.querySelectorAll('.timeline-row').length===38);
    assert.match(await page.locator('.timeline-row').filter({hasText:'卡莉的芳菲童话'}).locator('.reward-cell').innerText(),/1,500/);
    assert.match(await page.locator('.timeline-row').filter({hasText:'昨日海'}).first().locator('.reward-cell').innerText(),/—/);
    for(const width of [320,390,768,1440,1920]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page overflow '+width);}
    await page.setViewportSize({width:1440,height:1050});await page.screenshot({path:'logs/multigame-e2e/combined.png',fullPage:false});
    await page.locator('#game-select').selectOption('endfield-cn');await page.waitForFunction(()=>document.querySelector('#reward-total')?.textContent==='11,000嵌晶玉');
    assert.deepEqual(errors,[]);console.log('PASS: game selection, combined timeline, PRTS deadlines/revisions/phases, completion persistence, currency isolation, scoped ICS/JSON rules, responsive layouts');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
