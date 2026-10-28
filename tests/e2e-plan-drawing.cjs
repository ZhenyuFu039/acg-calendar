// Opt-in drawing with real pointer events, timezone validation and activity comparisons.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8804',DAY=86400000;
(async()=>{
  const output=path.resolve('logs/plan-drawing-e2e'),dir=path.join(output,'data-'+Date.now());await fs.mkdir(dir,{recursive:true});
  await fs.cp('examples/plugins/sample-game',path.join(dir,'plugins/sample-game'),{recursive:true});
  const feed=path.join(dir,'plugins/sample-game/events.json'),fixture=JSON.parse(await fs.readFile(feed,'utf8'));fixture.versions[0].events[1].end.state='estimated';await fs.writeFile(feed,JSON.stringify(fixture));
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8804','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('QA server failed');await new Promise(r=>setTimeout(r,200));}
    const state=async()=>await(await fetch(base+'/api/state')).json(),initial=await state(),source=initial.events.filter(e=>e.game==='sample-game');
    const request=async(url,method,body)=>{const r=await fetch(base+url,{method,headers:{'Content-Type':'application/json','X-Calendar-Token':initial.csrf_token},body:JSON.stringify(body)});assert.equal(r.status,200);return r.json();};
    await request('/api/plans','POST',{title:'跨游戏规划也要列出',game_id:'arknights-cn',start:'2026-10-20T21:00:00Z',end:'2026-10-20T23:00:00Z',notes:'并非只显示当前游戏'});
    let s=await request('/api/plans','POST',{title:'仅兑换仍开放',game_id:'sample-game',event_id:source[0].id,start:'2026-11-02T15:00:00Z',end:'2026-11-02T16:00:00Z'});
    const first=s.plans.find(p=>p.title==='跨游戏规划也要列出'),exchange=s.plans.find(p=>p.title==='仅兑换仍开放');
    s=await request('/api/plans','POST',{title:'轴外规划仍完整保留',start:'2027-01-01T12:00:00Z',end:'2027-01-01T13:00:00Z'});const outside=s.plans.find(p=>p.title==='轴外规划仍完整保留');
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050},timezoneId:'America/New_York',locale:'zh-CN'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('#game-select option[value="sample-game"]').waitFor({state:'attached'});await page.locator('#game-select').selectOption('sample-game');
    await page.waitForFunction(()=>document.body.dataset.game==='sample-game'&&!document.querySelector('#game-select').disabled);await page.locator('#fit-version').click();await page.locator('.fit-version').waitFor();
    assert.equal(await page.locator('[data-plan-create-track]').count(),0);assert.equal(await page.locator('#plan-draw-tools').isVisible(),false);
    await page.locator('#rail-plans').click();assert.equal(await page.locator('[data-plan-summary]').count(),3);
    assert.equal(await page.locator(`[data-plan-summary="${first.id}"] .plan-coverage [data-event]`).count(),2);
    assert.equal(await page.locator(`[data-plan-summary="${exchange.id}"] .plan-coverage [data-event]`).count(),1);
    assert.match(await page.locator(`[data-plan-summary="${exchange.id}"] .plan-coverage`).textContent(),/示例兑换处/);assert.match(await page.locator(`[data-plan-summary="${exchange.id}"] .plan-coverage`).textContent(),/预计 \/ 待定/);
    assert.equal(await page.locator(`[data-plan-summary="${outside.id}"] .plan-coverage [data-event]`).count(),0);
    await page.locator(`[data-plan-summary="${exchange.id}"] summary`).click();
    await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/state')),page.evaluate(()=>window.dispatchEvent(new Event('focus')))]);
    // The list starts folded (F20); the user's opening must survive a poll.
    assert.equal(await page.locator(`[data-plan-summary="${exchange.id}"] details`).evaluate(e=>e.open),true,'Polling must preserve disclosure state');
    await page.locator(`[data-plan-summary="${exchange.id}"] summary`).click();
    await page.screenshot({path:path.join(output,'all-plans-and-activities.png')});
    await page.locator('#plans-dialog [data-close]').click();await page.locator('[data-fold-parent]').first().click();await page.waitForFunction(()=>document.querySelector('[data-fold-parent]').getAttribute('aria-expanded')==='false');
    await page.locator('#rail-plans').click();assert.equal(await page.locator(`[data-plan-summary="${first.id}"] .plan-coverage [data-event]`).count(),1);assert.equal(await page.locator(`[data-plan-summary="${exchange.id}"] .plan-coverage [data-event]`).count(),0);
    await page.locator('#plans-dialog [data-close]').click();await page.locator('[data-fold-parent]').first().click();
    await page.locator('#rail-plans').click();await page.locator('[data-start-plan]').click();await page.locator('[data-plan-create-track]').waitFor();
    assert.equal(await page.locator('#plans-dialog').evaluate(d=>d.open),false);assert.equal(await page.locator('#calendar-expand').getAttribute('aria-pressed'),'true');
    const axis=async()=>await page.evaluate(()=>{const a=document.querySelector('.day-axis'),r=a.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,lo:Date.parse(a.querySelector('.axis-date').getAttribute('aria-label'))/86400000,days:a.querySelectorAll('.axis-date').length};});
    let a=await axis(),track=await page.locator('[data-plan-create-track]').boundingBox();
    const x=(date,axis)=>axis.x+(Date.parse(date)/DAY-axis.lo)/axis.days*axis.width;
    // Reverse drag; a local state poll must not tear down the captured track.
    const from=x('2026-10-22T20:00:00Z',a),to=x('2026-10-20T18:00:00Z',a),y=track.y+track.height/2;
    await page.mouse.move(from,y);await page.mouse.down();await page.mouse.move((from+to)/2,y,{steps:8});
    await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/state')),page.evaluate(()=>window.dispatchEvent(new Event('focus')))]);
    await page.locator('.plan-selection-overlay').waitFor();await page.mouse.move(to,y,{steps:8});await page.screenshot({path:path.join(output,'drawing-preview.png')});await page.mouse.up();
    await page.locator('#plan-editor-dialog[open]').waitFor();assert.equal(await page.locator('#plan-draw-tools').isVisible(),false);assert.equal(await page.locator('[data-plan-create-track]').count(),0);
    assert.equal(await page.locator('#plan-timezone').inputValue(),'America/New_York');assert.equal(await page.locator('#plan-game').inputValue(),'sample-game');
    assert.ok(Math.abs(Date.parse((await page.locator('#plan-start').inputValue())+'Z')-Date.parse('2026-10-20T18:00:00Z'))<=1800000);
    assert.ok(Math.abs(Date.parse((await page.locator('#plan-end').inputValue())+'Z')-Date.parse('2026-10-22T20:00:00Z'))<=1800000);
    assert.equal((await state()).plans.length,3,'Dragging creates a draft, not a saved plan');
    await page.locator('#plan-title').fill('拖选生成的规划');await page.locator('#plan-form button[type=submit]').click();await page.locator('#plans-dialog[open]').waitFor();
    let saved=(await state()).plans.find(p=>p.title==='拖选生成的规划');assert.ok(saved);assert.equal(await page.locator(`[data-plan-summary="${saved.id}"] .plan-coverage [data-event]`).count(),2);
    await page.locator('#plans-dialog [data-close]').click();await page.locator('.personal-plan-bar').filter({hasText:'拖选生成的规划'}).waitFor();
    // Click-only and Esc cancel do not save; normal event clicking resumes.
    await page.locator('#rail-plans').click();await page.locator('[data-start-plan]').click();await page.locator('[data-plan-create-track]').click();assert.equal(await page.locator('#plan-editor-dialog').evaluate(d=>d.open),false);
    await page.keyboard.press('Escape');await page.waitForTimeout(450);assert.equal(await page.locator('[data-plan-create-track]').count(),0);
    await page.locator('.event-bar').first().click();await page.locator('#detail-dialog[open]').waitFor();await page.locator('#detail-dialog [data-close]').click();assert.equal((await state()).plans.length,4);
    // An external game change aborts only the pending drag, never writes a stale selection.
    await page.locator('#rail-plans').click();await page.locator('[data-start-plan]').click();track=await page.locator('[data-plan-create-track]').boundingBox();
    await page.mouse.move(track.x+30,track.y+30);await page.mouse.down();await page.mouse.move(track.x+90,track.y+30,{steps:4});
    await request('/api/workspace','PATCH',{view:{scope:'single',selected_games:['arknights-cn']}});await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await page.waitForFunction(()=>document.body.dataset.game==='arknights-cn'&&!document.querySelector('.plan-selection-overlay'));
    await page.mouse.up();assert.equal(await page.locator('#plan-editor-dialog').evaluate(d=>d.open),false);assert.equal((await state()).plans.length,4);
    await page.keyboard.press('Escape');await page.locator('#game-select').selectOption('sample-game');await page.waitForFunction(()=>document.body.dataset.game==='sample-game'&&!document.querySelector('#game-select').disabled);await page.locator('#fit-version').click();await page.locator('.fit-version').waitFor();
    // The selected wall-clock range is validated in its own zone across DST (25h).
    await page.locator('#rail-plans').click();await page.locator('[data-start-plan]').click();await page.locator('#plan-draw-step').selectOption('1440');
    a=await axis();track=await page.locator('[data-plan-create-track]').boundingBox();await page.mouse.move(x('2026-11-01T00:00:00Z',a),track.y+30);await page.mouse.down();await page.mouse.move(x('2026-11-02T00:00:00Z',a),track.y+30,{steps:10});await page.mouse.up();
    await page.locator('#plan-editor-dialog[open]').waitFor();assert.equal(await page.locator('#plan-start').inputValue(),'2026-11-01T00:00');assert.equal(await page.locator('#plan-end').inputValue(),'2026-11-02T00:00');
    await page.locator('#plan-title').fill('夏令时整日规划');await page.locator('#plan-form button[type=submit]').click();await page.locator('#plans-dialog[open]').waitFor();
    const dst=(await state()).plans.find(p=>p.title==='夏令时整日规划');assert.equal(Date.parse(dst.end)-Date.parse(dst.start),25*3600000);
    // Empty official scope still has a drawing lane; temporary mode never survives reload.
    await page.locator('#plans-dialog [data-close]').click();await page.locator('#event-search').fill('没有这个活动');for(let i=0;i<4;i++)await page.locator('#next-period').click();
    assert.equal(await page.locator('.personal-plan-bar').count(),0);assert.equal(await page.locator('.event-bar').count(),0);
    await page.locator('#month-view').click();await page.locator('.month-cell').first().waitFor();await page.locator('#rail-plans').click();await page.locator('[data-start-plan]').click();await page.locator('[data-plan-create-track]').waitFor();
    await page.setViewportSize({width:320,height:720});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.equal(await page.locator('#plan-draw-cancel').isVisible(),true);
    await page.reload();await page.locator('#rail-plans').waitFor();assert.equal(await page.locator('#plan-draw-tools').isVisible(),false);
    await page.goto(base+'/?planner=1');await page.locator('#plans-dialog[open]').waitFor();assert.equal(await page.locator('[data-plan-summary]').count(),5,'Native desktop entry opens the complete overview');
    const after=(await state()).events.filter(e=>e.game==='sample-game');for(let i=0;i<2;i++){assert.deepEqual(after[i].start,source[i].start);assert.deepEqual(after[i].end,source[i].end);}
    assert.deepEqual(errors,[]);console.log('PASS: opt-in forward/reverse pointer drawing, polling capture, draft confirmation, Esc/click cancellation, unchanged normal clicks, 25h DST range, empty timeline, mobile/reload; all plans and overlap details respect visible rows/folds and preserve official dates');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
