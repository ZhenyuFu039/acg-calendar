// Full local workflow: linked focus, explicit families, deadline nodes and plans.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8803';
(async()=>{
  const output=path.resolve('logs/planner-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});await fs.cp('examples/plugins/sample-game',path.join(dir,'plugins/sample-game'),{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8803','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    const state=async()=>await(await fetch(base+'/api/state')).json();let data=await state();
    const request=async(url,method,body)=>{const r=await fetch(base+url,{method,headers:{'Content-Type':'application/json','X-Calendar-Token':data.csrf_token},body:JSON.stringify(body)});assert.equal(r.status,200);return r.json();};
    const source=structuredClone(data.events.filter(e=>e.game==='sample-game'));
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN',timezoneId:'America/New_York'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('#game-select option[value="sample-game"]').waitFor({state:'attached'});
    await page.locator('#game-select').selectOption('sample-game');await page.locator('#fit-version').click();
    await page.waitForFunction(()=>document.querySelectorAll('.timeline-row').length===2);
    const fold=page.locator('[data-fold-parent]').first();await fold.click();
    await page.waitForFunction(()=>document.querySelectorAll('.timeline-row').length===1);
    assert.match(await page.locator('.family-deadlines').innerText(),/活动.*11\/02[\s\S]*兑换.*11\/09/);
    await page.reload();await page.locator('[data-fold-parent][aria-expanded="false"]').waitFor();
    await page.locator('#fit-version').click();
    assert.equal(await page.locator('[data-deadline-day="2026-11-09"]').getAttribute('aria-label'),'2026-11-09，活动截止 0 项，兑换截止 1 项');
    await page.locator('[data-deadline-day="2026-11-09"]').click();await page.locator('#deadlines-dialog').waitFor();
    assert.match(await page.locator('#deadlines-content').innerText(),/兑换关闭.*示例兑换处/);
    assert.equal((await state()).workspace.view.focus.date,null);
    await page.locator('[data-only-day]').click();
    await page.waitForFunction(()=>PlaytimeWorkspace.view.focus.date==='2026-11-09');
    assert.match(await page.locator('#focus-context').innerText(),/2026-11-09/);
    await page.locator('#list-view').click();
    assert.equal(await page.locator('.catalog-event').count(),1);
    await page.locator('[data-clear-focus]').click();await page.waitForFunction(()=>PlaytimeWorkspace.view.focus.date===null);await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===1); // Fold persists independently.
    await page.locator('[data-fold-parent]').click();await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===2);
    // Starred child is promoted out of a folded parent.
    await page.locator('[data-fold-parent]').click();await request('/api/events/'+source[1].id,'PATCH',{starred:true});await page.reload();
    await page.waitForFunction(()=>document.querySelectorAll('.catalog-event').length===2);
    assert.match(await page.locator('.catalog-event').first().innerText(),/★ 示例兑换处/);
    await request('/api/events/'+source[1].id,'PATCH',{starred:false});await page.reload();await page.locator('[data-fold-parent]').click();
    // Plan form validates gaps and repeated local times, then preserves actual duration when moved.
    await page.locator('#rail-plans').click();await page.locator('[data-new-plan]').click();
    await page.locator('#plan-title').fill('周末推进剧情');await page.locator('#plan-game').selectOption('sample-game');
    await page.locator('#plan-event').selectOption(source[0].id);await page.locator('#plan-timezone').fill('America/New_York');
    await page.locator('#plan-start').fill('2026-03-08T02:15');await page.locator('#plan-end').fill('2026-03-08T04:15');
    await page.locator('#plan-form button[type=submit]').click();await page.locator('#plan-error').filter({hasText:'不存在'}).waitFor();
    await page.locator('#plan-start').fill('2026-11-01T01:15');await page.locator('#plan-end').fill('2026-11-01T02:15');
    await page.locator('#plan-form button[type=submit]').click();await page.locator('#plan-error').filter({hasText:'重复'}).waitFor();
    await page.locator('#plan-fold-start').selectOption('0');await page.locator('#plan-notes').fill('完成主线第一章');
    await page.locator('#plan-form button[type=submit]').click();await page.locator('#plans-dialog[open]').waitFor();
    let plan=(await state()).plans[0];assert.equal(plan.start,'2026-11-01T05:15:00Z');assert.equal(plan.end,'2026-11-01T07:15:00Z');
    await page.locator('#plans-content [data-plan]').click();await page.locator('#plan-title').fill('重复时刻改名保留原 UTC');await page.locator('#plan-form button[type=submit]').click();await page.locator('#plans-dialog[open]').waitFor();assert.equal((await state()).plans[0].start,'2026-11-01T05:15:00Z');await page.locator('#plans-content [data-plan]').click();await page.locator('[data-move-plan="1440"]').click();
    await page.waitForFunction(()=>document.querySelector('#plan-start').value==='2026-11-02T00:15');
    plan=(await state()).plans[0];assert.equal(plan.start,'2026-11-02T05:15:00Z');assert.equal(Date.parse(plan.end)-Date.parse(plan.start),7200000);
    await page.locator('#plan-title').fill('周末推进剧情 · 已调整');await page.locator('#plan-form button[type=submit]').click();await page.locator('#plans-dialog[open]').waitFor();
    await page.locator('#plans-dialog [data-close]').click();await page.locator('#fit-version').click();await page.locator('.personal-plan-bar').waitFor();
    await page.locator('#month-view').click();await page.locator('#next-period').click();await page.locator('.month-plan').first().waitFor();await page.screenshot({path:path.join(output,'month-plans.png')});
    await page.locator('.month-plan').first().click();await page.locator('#deadlines-dialog[open]').waitFor();assert.match(await page.locator('#deadlines-content').innerText(),/周末推进剧情 · 已调整/);
    await page.locator('#deadlines-dialog [data-close]').click();assert.equal((await state()).workspace.view.focus.date,null);
    await page.reload();await page.locator('#rail-plans').click();await page.locator('#plans-content [data-plan]').waitFor();
    assert.equal((await state()).plans.length,1);const after=(await state()).events.filter(e=>e.game==='sample-game');
    for(let i=0;i<2;i++){assert.deepEqual(after[i].start,source[i].start);assert.deepEqual(after[i].end,source[i].end);}
    await page.locator('#plans-dialog [data-close]').click();await page.locator('#fit-version').click();await page.locator('.personal-plan-bar').waitFor();
    await page.screenshot({path:path.join(output,'plans-and-families.png')});
    for(const width of [320,768,1440]){await page.setViewportSize({width,height:720});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow '+width);if(width>850)assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1),'Body scroll '+width);else assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>{const s=getComputedStyle(e);return /(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+2&&e.clientHeight>30&&!e.closest('dialog')}).map(e=>e.className)),[],'Nested scroll '+width);}
    await page.setViewportSize({width:1440,height:1050});
    // External scope changes are adopted by an already open page.
    await request('/api/workspace','PATCH',{view:{scope:'single',selected_games:['arknights-cn']}});await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await page.waitForFunction(()=>document.body.dataset.game==='arknights-cn');
    await page.locator('#rail-plans').click();assert.equal(await page.locator('#plans-content [data-plan]').count(),1);await page.locator('#plans-dialog [data-close]').click();
    // Related reward claims include shared child pools and never change the version total.
    await page.locator('#game-select').selectOption('endfield-cn');await page.waitForFunction(()=>document.body.dataset.game==='endfield-cn'&&!document.querySelector('#game-select').disabled);
    const focusDate='2026-11-05';await request('/api/workspace','PATCH',{view:{focus:{date:focusDate,event_id:null},focus_timezone:'America/New_York'}});
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await page.locator('.related-rewards').waitFor();
    data=await state();const bounds=data.presentation.focus_bounds,claims=new Map();
    for(const e of data.events.filter(e=>e.game==='endfield-cn'&&!e.ignored&&e.start.at&&Date.parse(e.start.at)<Date.parse(bounds.end)&&(!e.end.at||Date.parse(e.end.at)>Date.parse(bounds.start))))if(e.reward?.state!=='excluded')for(const c of e.reward?.claims||[])claims.set(c.id,c.amount);
    assert.equal((await page.locator('#reward-total').innerText()).replace(/[^0-9]/g,''),'11000');
    assert.ok((await page.locator('.related-rewards').innerText()).includes([...claims.values()].reduce((a,b)=>a+b,0).toLocaleString()));
    await page.setViewportSize({width:1440,height:720});assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));await page.screenshot({path:path.join(output,'focused-rewards.png')});
    await page.locator('[data-clear-focus]').click();await page.waitForFunction(()=>PlaytimeWorkspace.view.focus.date===null);await page.setViewportSize({width:1440,height:1050});
    await page.locator('#game-select').selectOption('sample-game');await page.locator('#rail-plans').click();await page.locator('#plans-content [data-plan]').click();
    await page.locator('.plan-delete summary').click();await page.locator('[data-delete-plan]').click();await page.locator('#plans-dialog[open]').waitFor();assert.equal((await state()).plans.length,0);
    assert.deepEqual(errors,[]);console.log('PASS: explicit family folding + starred child; distinct deadlines and density; cross-view date and cross-window game focus; local DST validation; independent plan create/edit/move/delete, timeline bars, month marks, restart persistence; responsive fixed viewport; provider API 1');
  }catch(error){if(browser){const page=browser.contexts()[0]?.pages()[0];if(page){await page.screenshot({path:path.join(output,'failure.png')});await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({state:await stateSafe(),page:await page.evaluate(()=>({period:document.querySelector('#period-title')?.textContent,view:document.body.dataset.calendarView,plans:document.querySelectorAll('.month-plan').length,dialogs:[...document.querySelectorAll('dialog[open]')].map(n=>n.id)}))},null,2));}}throw error;}finally{if(browser)await browser.close();server.kill();}
  async function stateSafe(){try{return await(await fetch(base+'/api/state')).json();}catch{return null;}}
})().catch(e=>{console.error(e);process.exitCode=1;});
