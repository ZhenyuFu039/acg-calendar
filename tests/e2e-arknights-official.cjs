const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8831';
(async()=>{
  const dir=path.resolve('logs/arknights-official-e2e','data-'+Date.now());await fs.mkdir(dir,{recursive:true});
  const seed=spawnSync(process.env.CALENDAR_PYTHON||'python',['-c',`
import sys
from pathlib import Path
from acg_calendar.storage import Store
from acg_calendar.arknights import ArknightsCollector
from acg_calendar.artwork import ArtworkCache
from acg_calendar.parser import ROOT
d=Path(sys.argv[1]);s=Store(d/'calendar.sqlite3')
html=(ROOT/'tests/fixtures/arknights-official-preview.html').read_text(encoding='utf-8')
def fetch(url):
    if url=='https://ak.hypergryph.com/news': return '<a href="/news/111">[活动预告]2026感谢庆典即将开启</a>'
    if url=='https://ak.hypergryph.com/news/111': return html
    raise AssertionError('Unexpected network URL: '+url)
images=ArtworkCache(d,downloader=lambda url:(ROOT/'web/assets/arknights-app-icon.png').read_bytes())
ArknightsCollector(s,d,official_fetcher=fetch,official_cache=images).collect()
`,dir],{windowsHide:true,encoding:'utf8'});
  assert.equal(seed.status,0,seed.stderr);
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8831','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
  let browser;
  try{
    for(let i=0;i<60;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===59)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'});
    await page.clock.setFixedTime(new Date('2026-10-27T20:00:00Z'));
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('#game-select').selectOption('arknights-cn');
    await page.waitForFunction(()=>document.body.dataset.game==='arknights-cn');
    assert.match(await page.locator('#source-status').innerText(),/官网|官方/);
    await page.locator('#reset-filters').click();await page.locator('#list-view').click();
    await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===9);
    for(const name of ['将雪投火','卫戍寻访','联合行动'])assert.equal(await page.locator('.catalog-event').filter({hasText:name}).count(),1);
    await page.locator('#fit-version').click();
    await page.waitForFunction(()=>document.querySelectorAll('.timeline-row').length===9);
    assert.equal(await page.locator('.timeline-row').filter({hasText:'将雪投火'}).count(),1);
    await page.locator('[data-category="gacha"]').uncheck();
    await page.waitForFunction(()=>![...document.querySelectorAll('.timeline-row')].some(n=>n.textContent.includes('将雪投火')));
    assert.match(await page.locator('#hidden-summary').innerText(),/将雪投火/);
    await page.locator('#hidden-summary [data-unhide="category"]').click();
    await page.locator('#hide-future').check();
    await page.waitForFunction(()=>![...document.querySelectorAll('.timeline-row')].some(n=>n.textContent.includes('将雪投火')));
    assert.match(await page.locator('#hidden-summary').innerText(),/未开放/);
    await page.locator('#hidden-summary [data-unhide="future"]').click();
    await page.locator('#reset-filters').click();await page.locator('#list-view').click();
    await page.locator('.catalog-event').filter({hasText:'视相博物馆：演化',hasNotText:'奖励兑换'}).click();
    assert.match(await page.locator('.detail-time-grid').innerText(),/官网国服明确时间/);
    assert.doesNotMatch(await page.locator('#detail-content').innerText(),/PRTS|undefined/);
    assert.match(await page.locator('.source-box').textContent(),/官网发布日期/);
    assert.equal(await page.locator('#detail-content .source-link').first().getAttribute('href'),'https://ak.hypergryph.com/news/111');
    await page.waitForFunction(()=>{const i=document.querySelector('img.detail-hero');return i&&i.complete&&i.naturalWidth>0;});
    await page.locator('#toggle-starred').click();await page.waitForFunction(()=>document.querySelector('#toggle-starred').textContent.includes('已收藏'));
    await page.locator('#detail-dialog [data-close]').click();await page.reload();
    await page.locator('#reset-filters').click();await page.locator('#list-view').click();
    await page.locator('.catalog-event').filter({hasText:'时间啊，向前！',hasNotText:'奖励兑换'}).click();
    assert.equal(await page.locator('.detail-phases>div').count(),2);
    await page.locator('#detail-dialog [data-close]').click();
    const ics=await (await fetch(base+'/api/calendar.ics?game=arknights-cn')).text();
    assert.match(ics,/ak\.hypergryph\.com\/news\/111/);assert.doesNotMatch(ics,/PRTS/);
    await page.route('**/api/state',async route=>{
      const response=await route.fetch({headers:{...route.request().headers(),'if-none-match':''}});
      const state=await response.json();state.sync_by_game['arknights-cn'].error='<urlopen error TLS handshake timed out>';
      await route.fulfill({response,json:state});
    });
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#source-status').textContent.includes('同步未完成'));await page.locator('#source-details').click();
    assert.match(await page.locator('#sync-results').innerText(),/本轮失败，原有日历保留[\s\S]*<urlopen error TLS handshake timed out>/);await page.locator('#preferences-dialog [data-close]').click();
    await page.screenshot({path:'logs/arknights-official-e2e/calendar.png',fullPage:false});
    assert.deepEqual(errors,[]);
    console.log('PASS: official source labels, UTC/local dates, details/banner, phases, personal state and source-preserving ICS; no live source requests');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
