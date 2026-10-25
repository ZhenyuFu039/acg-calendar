// F20: plan form errors clear on edit, same-named activities are told apart, view-specific empty text,
// renamed and folded overlap list, no reward control for games without a reward currency.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8812';
(async()=>{
  const output=path.resolve('logs/polish-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  // Two versions of a sample game, each with an activity of the same name.
  const sample=path.join(dir,'plugins','sample-game');await fs.mkdir(sample,{recursive:true});
  await fs.copyFile(path.resolve('examples/plugins/sample-game/manifest.json'),path.join(sample,'manifest.json'));
  const row=(id,start,end)=>({id,title:'版本更新维护',category:'maintenance',start:{at:start,state:'exact'},end:{at:end,state:'exact'},source_url:'https://example.com/'});
  await fs.writeFile(path.join(sample,'events.json'),JSON.stringify({schema_version:1,versions:[
    {id:'v1',name:'第一季',source_url:'https://example.com/',events:[row('m1','2026-09-01T00:00:00Z','2026-09-01T06:00:00Z')]},
    {id:'v2',name:'第二季',source_url:'https://example.com/',events:[row('m2','2026-11-01T00:00:00Z','2026-11-01T06:00:00Z')]}]}));
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8812','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    const data=await(await fetch(base+'/api/state')).json();
    const duplicate='版本更新维护';
    const linked=data.events.find(e=>e.game==='endfield-cn'&&e.start.at&&e.end.at&&e.time_kind!=='point');
    await fetch(base+'/api/plans',{method:'POST',headers:{'Content-Type':'application/json','X-Calendar-Token':data.csrf_token},body:JSON.stringify({title:'对照计划',start:linked.start.at,end:linked.end.at,timezone:'UTC',game_id:'endfield-cn',event_id:linked.id})});
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.clock.setFixedTime(new Date(Date.parse(linked.start.at)+3600000)); // The linked activity is inside the visible range.
    await page.goto(base+'/?game=all');await page.locator('#plans-button:not([disabled])').waitFor();
    // Overlap list: renamed, folded, manual link first.
    await page.locator('#plans-button').click();await page.locator('#plans-dialog[open]').waitFor();
    const coverage=page.locator('.plan-coverage').first();
    assert.match(await coverage.locator('summary').innerText(),/期间可参与活动 · \d+ 项（手动关联的排在前面）/);
    assert.equal(await coverage.evaluate(n=>n.open),false,'Folded by default');
    assert.equal(await coverage.locator('[data-event]').first().getAttribute('data-event'),linked.id);
    // Plan form: an error disappears as soon as the input changes.
    await page.locator('[data-new-plan]').click();await page.locator('#plan-editor-dialog[open]').waitFor();
    await page.locator('#plan-title').fill('错误提示测试');await page.locator('#plan-start').fill('2026-11-02T10:00');await page.locator('#plan-end').fill('2026-11-02T09:00');
    await page.locator('#plan-form button[type=submit]').click();await page.locator('#plan-error:not([hidden])').waitFor();
    await page.locator('#plan-end').fill('2026-11-02T11:00');
    assert.equal(await page.locator('#plan-error').isHidden(),true,'Stale error cleared on edit');
    // Same-named linked activities carry their version or date.
    await page.locator('#plan-game').selectOption('sample-game');
    const labels=await page.locator('#plan-event option').evaluateAll((o,t)=>o.map(x=>x.textContent).filter(x=>x.includes(t)),duplicate);
    assert.ok(labels.length>1);assert.equal(new Set(labels).size,labels.length,'Each duplicate option reads differently: '+labels.join(' | '));
    await page.locator('#plan-editor-dialog [data-close]').click();await page.locator('#plans-dialog [data-close]').click().catch(()=>{});
    // Empty list view explains search/filters, not dates.
    await page.locator('#list-view').click();await page.locator('#event-search').fill('不会匹配任何活动的关键词');
    await page.locator('.empty-state',{hasText:'没有符合当前搜索和筛选的活动'}).waitFor();
    assert.doesNotMatch(await page.locator('.empty-state').innerText(),/切换日期/);
    assert.deepEqual(errors,[]);console.log('PASS: overlap list renamed/folded/linked-first, plan error clears on edit, duplicate activities distinguished, list empty text');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
