// F16: the page explains which filters hide activities, by reason, and clears one filter at a time.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8809';
(async()=>{
  const output=path.resolve('logs/hidden-summary-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8809','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    const state=async()=>await(await fetch(base+'/api/state')).json();let data=await state();
    const request=async(url,method,body)=>{const r=await fetch(base+url,{method,headers:{'Content-Type':'application/json','X-Calendar-Token':data.csrf_token},body:JSON.stringify(body)});assert.equal(r.status,200,await r.clone().text());return r.json();};
    const game='arknights-cn',events=data.events.filter(e=>e.game===game&&!e.ignored);
    // Two activities count as new from the last sync (same shape the collectors write).
    const newIds=events.slice(0,2).map(e=>e.id);
    execFileSync(process.env.CALENDAR_PYTHON||'python',['-c',['import sys,json','from pathlib import Path','from acg_calendar.storage import Store',
      's=Store(Path(sys.argv[1])/"calendar.sqlite3")','m=s.get_meta("sync:arknights-cn",{})',
      'm.update(running=False,error=None,result={"calendar":{"added":2,"updated":0,"removed":0,"total":0,"added_ids":json.loads(sys.argv[2])},"images":{"ready":0,"added":0,"failed":0,"notes":[]}})',
      's.set_meta("sync:arknights-cn",m)'].join('\n'),dir,JSON.stringify(newIds)],{windowsHide:true});
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/?game='+game);await page.locator('#list-view').click();await page.locator('.catalog-event').first().waitFor();
    const bar=page.locator('#hidden-summary');
    assert.equal(await bar.isHidden(),true,'No filters, nothing hidden: no bar');
    // Search hides everything except one title; the two new activities are reported as hidden.
    const keep=events.find(e=>!newIds.includes(e.id)&&!e.parent_id);
    await page.locator('#event-search').fill(keep.title.replace(/[「」]/g,''));
    await bar.waitFor();
    const text=await bar.innerText();
    assert.match(text,/上次同步新增的 2 条活动未显示/);
    assert.match(text,/搜索 \d+ · 显示/);
    await page.locator('[data-unhide="search"]').click();
    assert.equal(await page.locator('#event-search').inputValue(),'');await bar.waitFor({state:'hidden'});
    // Category filter: count equals that category's activities; clearing it restores every category only.
    const category=events.find(e=>e.category!=='event').category,inCategory=events.filter(e=>e.category===category).length;
    await page.locator(`[data-category="${category}"]`).uncheck();
    await page.locator('[data-unhide="category"]',{hasText:`类型筛选 ${inCategory} · 显示`}).waitFor();
    await page.locator('[data-unhide="category"]').click();await bar.waitFor({state:'hidden'});
    assert.equal(await page.locator(`[data-category="${category}"]`).isChecked(),true);
    // Completed: a server setting, cleared through the API without touching other filters.
    await request('/api/events/'+keep.id,'POST',{completed:true});await request('/api/settings','POST',{hide_completed:true});
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await page.locator('[data-unhide="completed"]',{hasText:'已完成 1 · 显示'}).waitFor({timeout:15000});
    await page.locator('#event-search').fill('不会匹配任何活动的关键词');
    await page.locator('[data-unhide="completed"]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-unhide="completed"]'));
    assert.equal((await state()).settings.hide_completed,false);
    assert.equal(await page.locator('#event-search').inputValue(),'不会匹配任何活动的关键词','Other filters are left alone');
    await page.locator('[data-unhide="search"]').click();await bar.waitFor({state:'hidden'});
    // Deterministic views with a fixed clock and a local two-version sample game (H1/H2).
    const sample=path.join(dir,'plugins','sample-game');await fs.mkdir(sample,{recursive:true});
    await fs.copyFile(path.resolve('examples/plugins/sample-game/manifest.json'),path.join(sample,'manifest.json'));
    const at=(v,state='exact')=>({at:v,state:v?state:'unknown'});
    const row=(id,title,start,end,category='event')=>({id,title,category,start:at(start),end:at(end),source_url:'https://example.com/'});
    await fs.writeFile(path.join(sample,'events.json'),JSON.stringify({schema_version:1,versions:[
      {id:'v1',name:'版本一',source_url:'https://example.com/',events:[row('ended','已结束活动','2026-10-01T00:00:00Z','2026-10-10T00:00:00Z'),row('soon','近期活动','2026-10-15T00:00:00Z','2026-10-25T00:00:00Z'),
        row('neighbor','邻月活动','2026-11-01T12:00:00Z','2026-11-02T12:00:00Z'),row('pending','待定活动','2026-10-15T00:00:00Z',null),row('shop','兑换示例','2026-10-15T00:00:00Z','2026-10-30T00:00:00Z','exchange')]},
      {id:'v2',name:'版本二',source_url:'https://example.com/',events:[row('fresh','新版本活动','2026-10-28T00:00:00Z','2026-11-20T00:00:00Z')]},
      // Week of 09/28: four bars start together (D goes to "+N 项"); X also overflows there but is drawn the next week.
      {id:'v3',name:'版本三',source_url:'https://example.com/',events:['A','B','C','D'].map(k=>row('crowd'+k,'拥挤'+k,'2026-10-01T00:00:00Z','2026-10-04T00:00:00Z')).concat([row('crowdX','拥挤X','2026-10-02T00:00:00Z','2026-10-10T00:00:00Z')])}]}));
    // Restart with the new plugin: wait until the old process has exited and its port is free,
    // so the health check cannot reach the previous server.
    await browser.close();const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;
    for(let i=0;i<50;i++){try{await fetch(base+'/api/health');}catch{break;}if(i===49)throw Error('Old server still answering');await new Promise(r=>setTimeout(r,100));}
    const server2=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8809','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
    try{
      for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
      data=await state();
      const id=local=>'sample-game-'+require('node:crypto').createHash('sha256').update(local).digest('hex').slice(0,20);
      const v1=data.events.find(e=>e.id===id('soon')).version_id,v2=data.events.find(e=>e.id===id('fresh')).version_id;
      execFileSync(process.env.CALENDAR_PYTHON||'python',['-c',['import sys,json','from pathlib import Path','from acg_calendar.storage import Store',
        's=Store(Path(sys.argv[1])/"calendar.sqlite3")',
        'r=lambda ids:{"calendar":{"added":len(ids),"updated":0,"removed":0,"total":0,"added_ids":ids},"images":{"ready":0,"added":0,"failed":0,"notes":[]}}',
        's.set_meta("sync:sample-game",{**s.get_meta("sync:sample-game",{}),"running":False,"error":None,"result":r(json.loads(sys.argv[2]))})',
        's.set_meta("sync:arknights-cn",{**s.get_meta("sync:arknights-cn",{}),"running":False,"error":None,"result":r(json.loads(sys.argv[3]))})'].join('\n'),
        dir,JSON.stringify([id('ended'),id('neighbor'),id('fresh')]),JSON.stringify([data.events.find(e=>e.game==='arknights-cn').id])],{windowsHide:true});
      browser=await chromium.launch({channel:'msedge',headless:true});
      const ctx=await browser.newContext({viewport:{width:375,height:812},locale:'zh-CN',timezoneId:'UTC'});const p2=await ctx.newPage();p2.on('pageerror',e=>errors.push(e.message));
      await p2.clock.setFixedTime(new Date('2026-10-20T12:00:00Z'));
      await p2.goto(base+'/?game=sample-game');await p2.waitForFunction(v=>[...document.querySelector('#version-select').options].some(o=>o.value===v),v1);await p2.locator('#version-select').evaluate((n,v)=>{n.value=v;n.dispatchEvent(new Event('change',{bubbles:true}));},v1);await p2.locator('.upcoming-item').first().waitFor();
      await p2.locator('[data-category="exchange"]').evaluate(n=>n.click());
      const bar2=p2.locator('#hidden-summary');await bar2.waitFor();
      const drawn=async()=>p2.evaluate(()=>new Set([...document.querySelectorAll('#calendar [data-event]')].map(n=>n.dataset.event)).size);
      const shownCount=async()=>Number((await bar2.innerText()).match(/当前显示 (\d+)/)[1]);
      // Upcoming draws soon, neighbor and pending; the ended activity is explained, and only it counts as new-but-hidden.
      assert.equal(await shownCount(),await drawn());assert.equal(await drawn(),3);
      let summary=await bar2.innerText();
      assert.match(summary,/近期截止只列未来截止，其余 1/);
      assert.match(summary,/上次同步新增的 1 条活动未显示/);
      assert.match(summary,/「版本二」有 1 条上次同步新增的活动/);
      assert.equal((summary.match(/上次同步新增的活动/g)||[]).length,1,'New activities of other games are not reported here');
      // Month view draws the neighbouring-month activity in the grid, so nothing new is reported missing.
      await p2.setViewportSize({width:1440,height:900});await p2.locator('#month-view').click();await p2.locator('.month-cell').first().waitFor();
      await p2.waitForFunction(id=>document.querySelector(`#calendar [data-event="${id}"]`),id('neighbor'));
      assert.equal(await shownCount(),await drawn());
      summary=await bar2.innerText();assert.doesNotMatch(summary,/上次同步新增的 \d+ 条活动未显示/);
      // The version notice switches version only when asked, keeping the other filters.
      await p2.locator('[data-unhide="version"]').click();
      await p2.waitForFunction(v=>document.querySelector('#version-select').value===v,v2);
      await p2.waitForFunction(id=>document.querySelector(`#calendar [data-event="${id}"]`),id('fresh'));
      assert.equal(await p2.locator('[data-category="exchange"]').isChecked(),false,'Category filter is preserved');
      await p2.screenshot({path:path.join(output,'hidden-summary-month.png')});
      // Month overflow (H1): only drawn bars count as shown; a new activity left in "+N 项" is reported.
      execFileSync(process.env.CALENDAR_PYTHON||'python',['-c',['import sys,json','from pathlib import Path','from acg_calendar.storage import Store',
        's=Store(Path(sys.argv[1])/"calendar.sqlite3")','m=s.get_meta("sync:sample-game",{})',
        'm["result"]={"calendar":{"added":1,"updated":0,"removed":0,"total":0,"added_ids":json.loads(sys.argv[2])},"images":{"ready":0,"added":0,"failed":0,"notes":[]}}',
        's.set_meta("sync:sample-game",m)'].join('\n'),dir,JSON.stringify([id('crowdD')])],{windowsHide:true});
      const v3=data.events.find(e=>e.id===id('crowdA')).version_id;
      await p2.reload();await p2.waitForFunction(v=>[...document.querySelector('#version-select').options].some(o=>o.value===v),v3);
      await p2.locator('#version-select').evaluate((n,v)=>{n.value=v;n.dispatchEvent(new Event('change',{bubbles:true}));},v3);
      await p2.waitForFunction(id=>document.querySelector(`#calendar [data-event="${id}"]`),id('crowdX'));
      await bar2.waitFor();
      assert.equal(await drawn(),4,'A, B, C and X are drawn; D only behind "+N 项"');
      assert.equal(await shownCount(),await drawn());
      summary=await bar2.innerText();
      assert.match(summary,/上次同步新增的 1 条活动未显示/);
      assert.match(summary,/另有 1 项收在“\+N 项”中/);
      assert.doesNotMatch(summary,/子活动已折叠/,'Overflow is not described as folding');
      await p2.locator('[data-unhide="range"]').click();await p2.waitForFunction(()=>document.querySelector('#list-view').getAttribute('aria-pressed')==='true');
      await p2.waitForFunction(id=>document.querySelector(`#calendar [data-event="${id}"]`),id('crowdD'));
    }finally{server2.kill();}
    assert.deepEqual(errors,[]);console.log('PASS: per-reason hidden counts by stable id, new-from-sync notice, one-filter-at-a-time clearing for search, category and completed, no bar without filters; fixed-clock upcoming and month counts match drawn activities, other-version new activities with an explicit switch, other games ignored');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
