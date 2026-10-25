// F18 step 1 and F19: polling keeps focus; wide screens give the calendar room; phones scroll as one page.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8807';
const nested=()=>[...document.querySelectorAll('body *')].filter(e=>{const s=getComputedStyle(e);return /(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+2&&e.clientHeight>30&&!e.closest('dialog');}).map(e=>e.className);
(async()=>{
  const output=path.resolve('logs/layout-focus-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8807','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    // A multi-phase activity (same supported shape as test_presentation.py), only in this temporary database:
    // two phases close on the same New York day before the activity itself ends.
    execFileSync(process.env.CALENDAR_PYTHON||'python',['-c',['import sys, copy','from pathlib import Path','from acg_calendar.storage import Store',
      's=Store(Path(sys.argv[1])/"calendar.sqlite3")','v=s.versions()[0]','e=v["events"][0]','e.update(title="阶段核对活动",time_kind="interval",completed=False)',
      'e["start"].update(at="2026-10-15T00:00:00Z",state="exact",reference=None)','e["end"].update(at="2026-11-04T01:00:00Z",state="exact",reference=None)',
      'e["phases"]=[{"title":"挑战关卡关闭","at":"2026-11-03T01:00:00Z","state":"exact"},{"title":"第二阶段截止","at":"2026-11-03T03:00:00Z","state":"exact"}]',
      'edge=copy.deepcopy(e)','edge.update(id="f19-estimated-coincidence",title="预计截止重合活动")',
      'edge["end"].update(at="2026-11-06T01:00:00Z",state="estimated")',
      'edge["phases"]=[{"title":"挑战实际关闭 <测试>","at":"2026-11-06T01:00:00Z","state":"exact"}]',
      'v["events"].append(edge)','s.save_version(v)'].join('\n'),dir],{windowsHide:true});
    const state=async()=>await(await fetch(base+'/api/state')).json();let data=await state();
    const request=async(url,method,body)=>{const r=await fetch(base+url,{method,headers:{'Content-Type':'application/json','X-Calendar-Token':data.csrf_token},body:JSON.stringify(body)});assert.equal(r.status,200,await r.clone().text());return r.json();};
    // Freeze the browser clock just before the snapshot's sixth-from-last cluster of deadlines,
    // so the test does not depend on the day it runs.
    const nodes=data.presentation.deadline_nodes.map(n=>Date.parse(n.at)).sort((a,b)=>a-b);assert.ok(nodes.length>=8,'Snapshot needs deadlines');
    const frozen=new Date(nodes[0]-3600000);
    const plan=await request('/api/plans','POST',{title:'布局测试计划',start:new Date(frozen.getTime()+86400000).toISOString().replace('.000',''),end:new Date(frozen.getTime()+90000000).toISOString().replace('.000',''),timezone:'UTC'});
    data=await state();
    browser=await chromium.launch({channel:'msedge',headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:900},locale:'zh-CN',timezoneId:'America/New_York'});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.clock.setFixedTime(frozen);
    await page.goto(base+'/?game=all');await page.locator('#calendar .event-name').first().waitFor();

    // F19 wide: compact heading on ≤1000px-high screens, folded plans, no page scroll, larger calendar.
    assert.equal(await page.evaluate(()=>document.body.classList.contains('heading-compact')),true);
    assert.equal(await page.locator('#personal-plans-panel .plan-card').count(),0,'Plans are folded to one line by default');
    assert.match(await page.locator('#personal-plans-panel').innerText(),/个人规划 · 1 项/);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1&&document.documentElement.scrollWidth<=innerWidth+1));
    const calendarHeight=(await page.locator('#calendar').boundingBox()).height;
    assert.ok(calendarHeight>=280,'1440×900 with one plan keeps at least 280px for the calendar; got '+calendarHeight);
    await page.screenshot({path:path.join(output,'wide-default.png')});
    await page.locator('[data-plans-fold]').click();await page.locator('#personal-plans-panel .plan-card').waitFor();
    await page.locator('[data-plans-fold]').click();assert.equal(await page.locator('#personal-plans-panel .plan-card').count(),0);
    await page.locator('#heading-toggle').click();assert.equal(await page.locator('.display-title').isVisible(),true);
    await page.locator('#heading-toggle').click();assert.equal(await page.locator('.display-title').isVisible(),false);

    // F18 step 1: an unchanged poll keeps the very same node and focus.
    const name=page.locator('#calendar .event-name[data-event]').nth(1);const id=await name.getAttribute('data-event');
    await name.focus();const handle=await name.elementHandle();
    const progress=await page.locator('#calendar .event-bar').first().evaluate(n=>n.style.getPropertyValue('--progress'));assert.match(progress,/%$/,'Progress is filled in place');
    await page.waitForTimeout(11000);
    assert.equal(await handle.evaluate(n=>n.isConnected&&document.activeElement===n),true,'Unchanged poll must not rebuild the focused row');
    // A real change rebuilds, and focus returns to the same activity's control.
    const other=data.event_order.find(e=>e!==id);await request('/api/events/'+other,'POST',{starred:true});
    await page.waitForFunction(()=>document.querySelector('#calendar .timeline-group')?.textContent.includes('收藏'),null,{timeout:15000});
    assert.equal(await handle.evaluate(n=>n.isConnected),false,'Changed data rebuilds the timeline');
    assert.deepEqual(await page.evaluate(()=>[document.activeElement.dataset.event,document.activeElement.classList[0]]),[id,'event-name']);
    await request('/api/events/'+other,'POST',{starred:false});

    // F19 phone: one scrolling page, upcoming deadlines by default, at least four official rows on first screen.
    await page.setViewportSize({width:375,height:812});await page.reload();await page.locator('.upcoming-item').first().waitFor();
    assert.equal(await page.locator('#upcoming-view').getAttribute('aria-pressed'),'true');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal overflow');
    assert.deepEqual(await page.evaluate(nested),[],'No nested vertical scrollers');
    const visible=await page.evaluate(()=>[...document.querySelectorAll('.upcoming-item')].filter(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}).length);
    assert.ok(visible>=4,'Phone first screen shows at least four official rows with a plan present; got '+visible);
    await page.screenshot({path:path.join(output,'phone-upcoming.png')});
    // Phase closings keep their own names; only the node at the activity's end reads 活动结束.
    const phaseRows=await page.locator('.upcoming-item',{hasText:'阶段核对活动'}).evaluateAll(rows=>rows.map(r=>[r.closest('.upcoming-day').querySelector('h3').firstChild.textContent.trim(),r.querySelector('small').textContent.split(' · ').pop(),r.querySelector('.upcoming-time strong').textContent]));
    assert.deepEqual(phaseRows,[['11/02 周一','挑战关卡关闭','20:00'],['11/02 周一','第二阶段截止','22:00'],['11/03 周二','活动结束','20:00']]);
    // An exact phase at the same instant as an estimated overall end must keep its phase
    // name, with the uncertain activity end shown separately. Titles remain plain text.
    const coincident=page.locator('.upcoming-item[data-event="f19-estimated-coincidence"]');
    assert.deepEqual(await coincident.evaluateAll(rows=>rows.map(r=>[r.querySelector('small').textContent.split(' · ').pop(),r.querySelector('.upcoming-time strong').textContent])),[['挑战实际关闭 <测试>','20:00'],['预计结束','11/05 20:00']]);
    assert.equal(await coincident.locator('测试').count(),0,'Phase title is escaped');
    const phaseDetail=coincident.filter({has:page.locator('.upcoming-phase')});
    await phaseDetail.click();await page.locator('#detail-dialog[open]').waitFor();
    assert.match(await page.locator('#detail-title').innerText(),/预计截止重合活动/);
    await page.locator('#detail-dialog [data-close]').click();
    // The filter sidebar folds to the game picker on phones and expands on demand.
    assert.equal(await page.locator('#view-filters').isVisible(),false);
    await page.locator('#sidebar-more').click();assert.equal(await page.locator('#view-filters').isVisible(),true);
    await page.locator('#sidebar-more').click();assert.equal(await page.locator('#view-filters').isVisible(),false);
    // Phone view choice stays in this browser and survives polling and reload; the desktop setting is untouched.
    await page.locator('#month-view').click();await page.locator('.month-cell').first().waitFor();
    await page.waitForTimeout(10500);assert.equal(await page.locator('#month-view').getAttribute('aria-pressed'),'true');
    assert.equal((await state()).settings.view,data.settings.view);
    await page.reload();await page.locator('.month-cell').first().waitFor();assert.equal(await page.locator('#month-view').getAttribute('aria-pressed'),'true');
    await page.locator('#timeline-view').click();await page.locator('.timeline-scroll').waitFor();
    assert.equal(await page.locator('.event-name-cell').first().evaluate(n=>Math.round(n.getBoundingClientRect().width)),120);
    assert.deepEqual(await page.evaluate(nested),[]);
    // Secondary links live under 更多; plan creation uses the form on phones.
    assert.equal(await page.locator('#maintenance-button').isVisible(),false);
    await page.locator('#more-tools').click();assert.equal(await page.locator('a[href^="/api/calendar.ics"]').isVisible(),true);
    await page.locator('#plans-button').click();await page.locator('[data-start-plan]').click();await page.locator('#plan-editor-dialog[open]').waitFor();
    assert.equal(await page.evaluate(()=>document.body.classList.contains('plan-creating')),false);
    await page.screenshot({path:path.join(output,'phone-timeline.png')});
    // Back to wide: the saved desktop view is used again.
    await page.locator('#plan-editor-dialog [data-close]').click();await page.setViewportSize({width:1440,height:900});
    await page.waitForFunction(v=>document.querySelector('#'+v+'-view').getAttribute('aria-pressed')==='true',data.settings.view);
    assert.equal((await state()).plans[0].id,plan.plans[0].id);
    // F12: the status line names the current stage and waiting reason, then this round's results.
    execFileSync(process.env.CALENDAR_PYTHON||'python',['-c',['import sys','from pathlib import Path','from acg_calendar.storage import Store',
      's=Store(Path(sys.argv[1])/"calendar.sqlite3")',
      's.set_meta("sync",{**s.get_meta("sync",{}),"running":True,"stage":{"name":"images","label":"补齐活动图片"},"calendar_saved_at":"2026-10-25T00:00:00Z","waiting":{"site":"web.hycdn.cn","seconds":10}})',
      's.set_meta("sync:arknights-cn",{**s.get_meta("sync:arknights-cn",{}),"running":False,"error":None,"result":{"calendar":{"added":2,"updated":1,"removed":0,"total":40},"images":{"ready":30,"added":3,"failed":1}}})'].join('\n'),dir],{windowsHide:true});
    await page.locator('#game-select').selectOption('endfield-cn');
    await page.locator('#source-status',{hasText:'日历已更新 · 补齐活动图片… · 等待 web.hycdn.cn 请求间隔约 10 秒'}).waitFor({timeout:15000});
    await page.locator('#game-select').selectOption('arknights-cn');
    await page.locator('#source-status',{hasText:'上次同步：新增 2 · 更新 1 · 补齐图片 3 张 · 1 张图片暂不可用'}).waitFor({timeout:15000});
    assert.deepEqual(errors,[]);console.log('PASS: sync stages and results in the status line, unchanged polls keep nodes and focus, rebuilds restore focus, compact wide layout with folded plans, phone document scroll with upcoming list (≥4 rows, phase closings named), local phone view preference, narrow timeline, More menu, form-only phone planning');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
