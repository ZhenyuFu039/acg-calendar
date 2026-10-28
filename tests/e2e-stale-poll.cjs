// A poll that started before a change and answers after it must not overwrite the change.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8813';
(async()=>{
  const output=path.resolve('logs/stale-poll-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8813','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/?game=all');await page.locator('#calendar .event-name').first().waitFor();
    // The next state poll reads the server now (before the change) but hands its answer back only when released.
    await page.evaluate(()=>{
      const original=window.fetch,W=window.PlaytimeWorkspace,accept=W.accept.bind(W);window.__applied=[];
      W.accept=d=>{window.__applied.push(d.settings.hide_completed);return accept(d);};
      window.fetch=async(url,options)=>{
        if(!window.__held&&String(url).endsWith('/api/state')&&!(options&&options.method&&options.method!=='GET')){
          window.__held=true;const response=await original(url,options);await new Promise(r=>window.__release=r);return response;
        }
        return original(url,options);
      };
      window.dispatchEvent(new Event('focus'));
    });
    await page.waitForFunction(()=>typeof window.__release==='function');
    await page.locator('#hide-completed').check(); // The change, saved while the old poll is still pending.
    await page.waitForFunction(()=>window.__applied.includes(true));
    await page.evaluate(()=>window.__release());await page.waitForTimeout(500);
    const applied=await page.evaluate(()=>window.__applied);
    assert.equal(await page.locator('#hide-completed').isChecked(),true,'A stale poll overwrote a newer change; applied states: '+JSON.stringify(applied));
    // A poll is not even sent while any write is unfinished, with one or with two writes held.
    await page.evaluate(()=>{
      const original=window.fetch;window.__held=[];window.__pollsDuringWrites=0;
      window.fetch=async(url,options)=>{
        const write=options&&options.method&&options.method!=='GET';
        if(!write&&String(url).endsWith('/api/state')&&window.__held.length)window.__pollsDuringWrites++;
        if(write&&window.__holdWrites){await new Promise(r=>window.__held.push(r));}
        return original(url,options);
      };
      window.__holdWrites=true;
    });
    const focusPoll=async()=>{await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await page.waitForTimeout(300);};
    await page.locator('#hide-completed').uncheck();await page.waitForFunction(()=>window.__held.length===1);
    await focusPoll();
    assert.equal(await page.locator('#hide-completed').isChecked(),false,'A poll during one unfinished write rolled the change back');
    await page.locator('#hide-future').check();await page.waitForFunction(()=>window.__held.length===2);
    await page.evaluate(()=>{window.__holdWrites=false;window.__held.shift()();});await page.waitForTimeout(300); // First write finishes; the second is still open.
    await focusPoll();
    assert.equal(await page.evaluate(()=>window.__pollsDuringWrites),0,'A state poll was sent while a write was unfinished');
    await page.evaluate(()=>window.__held.shift()());await page.waitForTimeout(300);
    assert.equal(await page.locator('#hide-completed').isChecked(),false);assert.equal(await page.locator('#hide-future').isChecked(),true);
    await page.locator('#hide-future').uncheck();await page.locator('#hide-completed').check();await page.waitForTimeout(300);
    // Unchanged data is answered 304 and leaves the page alone; a change made elsewhere still arrives.
    await page.evaluate(()=>{const original=window.fetch;window.__statuses=[];window.fetch=async(url,options)=>{const response=await original(url,options);if(String(url).endsWith('/api/state'))window.__statuses.push(response.status);return response;};});
    const poll=async()=>{const count=await page.evaluate(()=>{window.dispatchEvent(new Event('focus'));return window.__statuses.length;});await page.waitForFunction(n=>window.__statuses.length>n,count);await page.waitForTimeout(200);};
    await poll();await poll();
    const calendar=await page.evaluate(()=>{window.__calendarNode=document.querySelector('#calendar').firstChild;return window.__statuses.at(-1);});
    assert.equal(calendar,304,'An unchanged poll should be answered 304: '+JSON.stringify(await page.evaluate(()=>window.__statuses)));
    await poll();assert.equal(await page.evaluate(()=>document.querySelector('#calendar').firstChild===window.__calendarNode),true,'An unchanged poll rebuilt the calendar');
    const token=(await (await fetch(base+'/api/state')).json()).csrf_token;
    await fetch(base+'/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Calendar-Token':token},body:JSON.stringify({hide_completed:false})});
    await poll();assert.equal(await page.evaluate(()=>window.__statuses.at(-1)),200);
    assert.equal(await page.locator('#hide-completed').isChecked(),false,'A change made in another window did not arrive');
    assert.deepEqual(errors,[]);console.log('PASS: a poll answered after a change does not overwrite it; unchanged polls answer 304 and keep the page; outside changes arrive');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
