// Real October 22 bulletin: five distinct activities share one sourced visual.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const output=path.resolve('logs/bulletin-artwork-e2e'),dir=path.join(output,'data-'+Date.now());
 await fs.mkdir(dir,{recursive:true});
 await fs.cp('plugins/azurlane-cn',path.join(dir,'plugins/azurlane-cn'),{recursive:true});
 const python=process.env.CALENDAR_PYTHON||'python';
 const prepare=`import json,sys
from pathlib import Path
from acg_calendar.azurlane import AzurLaneProvider,compile_feed
from acg_calendar.storage import Store
from acg_calendar.gamekee import atomic_json
p=Path(sys.argv[1])/'plugins/azurlane-cn'
provider=AzurLaneProvider(p,json.loads((p/'manifest.json').read_text(encoding='utf-8')),Store(Path(sys.argv[1])/'calendar.sqlite3'))
pages=json.loads(Path('samples/azurlane-cn-20261022.json').read_text(encoding='utf-8'))['pages']
feed=compile_feed(pages,provider.config,'offline')
assert not provider.attach_bulletin_visuals(feed,pages)
assert not provider.attach_images(feed)
atomic_json(p/'events.json',feed)
`;
 const prep=spawnSync(python,['-X','utf8','-c',prepare,dir],{windowsHide:true,encoding:'utf8'});
 assert.equal(prep.status,0,prep.stderr);
 const port=8808,base='http://127.0.0.1:'+port;
 const server=spawn(python,['-X','utf8','app.py','--port',String(port),'--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
 let browser;
 try{
  for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server did not start');await new Promise(r=>setTimeout(r,200));}
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#game-select').selectOption('azurlane-cn');
  await page.waitForFunction(()=>document.body.dataset.game==='azurlane-cn');
  await page.locator('#reset-filters').click();
  await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===5);
  const state=await (await fetch(base+'/api/state')).json();
  const rows=state.events.filter(e=>e.game==='azurlane-cn');
  assert.equal(rows.length,5);assert.ok(rows.every(e=>e.artwork.shared_update));
  for(const title of ['港区假日故事集 光与暗的邀约','限界挑战：天蝎座','飓风与雾霭回廊 · 活动商店']){
   await page.locator('.catalog-event').filter({hasText:title}).click();
   await page.waitForFunction(()=>{const img=document.querySelector('.detail-hero');return img&&img.complete&&img.naturalWidth===1920});
   assert.match(await page.locator('#detail-content').innerText(),/共用主视觉/);
   assert.match(await page.locator('.wiki-evidence a').first().getAttribute('href'),/oldid=418469/);
   if(title.startsWith('港区'))await page.screenshot({path:path.join(output,'new-activity-detail.png')});
   await page.locator('#detail-dialog [data-close]').click();
  }
  await page.locator('#timeline-view').click();
  await page.waitForFunction(()=>document.querySelectorAll('.timeline-row .activity-art').length===5);
  await page.screenshot({path:path.join(output,'new-activities-timeline.png')});
  assert.deepEqual(errors,[]);
  console.log('PASS: five new activities, shared image on timeline and details, real 1920px asset, fixed revision provenance, cached offline preparation');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
