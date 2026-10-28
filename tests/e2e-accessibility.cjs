// Host accessibility regression. Local fixtures only; does not claim screen-reader/device certification.
const fs=require('node:fs/promises'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
  const out=path.resolve('logs/uiux-accessibility',String(Date.now())),dir=path.join(out,'data');await fs.mkdir(dir,{recursive:true});
  for(const game of ['nikke-global','azurlane-cn','zzz-cn'])await fs.cp(path.resolve('plugins',game),path.join(dir,'plugins',game),{recursive:true});
  const port=await new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
  const base='http://127.0.0.1:'+port,server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port',String(port),'--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});
  let browser,page;const errors=[],observations=[];
  try{
    for(let i=0;i<70;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===69)throw Error('Server did not start');await new Promise(r=>setTimeout(r,150));}
    browser=await chromium.launch({channel:'msedge',headless:true});
    page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN',timezoneId:'America/New_York'});page.on('pageerror',e=>errors.push(e.message));
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.clock.setFixedTime(new Date('2026-10-27T20:00:00Z'));
    async function names(label){const rows=[];for(const role of ['button','link','combobox','textbox','searchbox','checkbox','radio','slider','spinbutton','tab']){const count=await page.getByRole(role).count(),named=await page.getByRole(role,{name:/\S/}).count();rows.push({role,count,named});}observations.push({label,roles:rows});assert.ok(rows.every(r=>r.count===r.named),label+': unnamed controls '+JSON.stringify(rows));}
    await page.goto(base+'/?game=all');await page.locator('.timeline-row[data-game="nikke-global"]').first().waitFor();await names('workbench');
    assert.equal(await page.getByRole('heading',{level:1}).count(),1);await page.keyboard.press('Tab');assert.equal(await page.getByRole('link',{name:'跳到活动日程'}).evaluate(n=>n===document.activeElement),true);await page.keyboard.press('Enter');assert.equal(await page.locator('#calendar').evaluate(n=>n===document.activeElement),true);
    await page.locator('#workspace-button').click();await names('settings');
    await page.getByRole('tab',{name:'主题配色',exact:true}).focus();await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab',{name:'模块布局',exact:true}).getAttribute('aria-selected'),'true','Arrow keys must activate the next settings tab');
    await page.keyboard.press('End');assert.equal(await page.getByRole('tab',{name:'备份与恢复',exact:true}).getAttribute('aria-selected'),'true');
    await page.keyboard.press('Home');assert.equal(await page.getByRole('tab',{name:'主题配色',exact:true}).getAttribute('aria-selected'),'true');
    assert.ok(await page.getByRole('tab',{name:'主题配色',exact:true}).evaluate(n=>parseFloat(getComputedStyle(n).outlineWidth))>=2,'Keyboard focus needs a visible outline');
    assert.equal(await page.getByRole('tab').evaluateAll(nodes=>nodes.filter(n=>n.tabIndex===0).length),1);
    assert.equal(await page.getByRole('tabpanel').count(),1);
    await page.locator('#preferences-dialog [data-close]').click();
    await page.locator('.event-name').first().click();await names('event details');await page.locator('.detail-reminder summary').click();await names('event reminder');await page.locator('#detail-dialog [data-close]').click();
    await page.locator('#inbox-button').click();await names('inbox');await page.locator('#reminder-rules summary').click();await names('notification rules');await page.locator('#reminder-dialog [data-close]').click();
    await page.locator('#rail-plans').click();await names('plan list');await page.locator('[data-new-plan]').click();await names('plan editor');await page.locator('#plan-editor-dialog [data-close]').click();
    await page.locator('#rail-games').click();await page.waitForFunction(()=>document.querySelector('#plugin-list').textContent.includes('NIKKE'));await names('game manager');await page.locator('#preferences-dialog [data-close]').click();
    const contrast=async()=>page.evaluate(()=>{
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d',{willReadFrequently:true});
      const rgba=c=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=c;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].map((n,i)=>i===3?n/255:n);};
      const blend=(a,b)=>[0,1,2].map(i=>a[i]*a[3]+b[i]*(1-a[3])).concat(1),lum=c=>c.slice(0,3).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((a,n,i)=>a+n*[.2126,.7152,.0722][i],0);
      return ['.axis-date','.event-name strong','.deadline-strip strong','#sync-button','#source-details'].map(selector=>{const e=document.querySelector(selector),stack=[];for(let n=e;n;n=n.parentElement)stack.push(n);let bg=[255,255,255,1];for(const n of stack.reverse())bg=blend(rgba(getComputedStyle(n).backgroundColor),bg);const fg=blend(rgba(getComputedStyle(e).color),bg),a=lum(fg),b=lum(bg);return {selector,fg,bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
    });
    for(const preset of ['light','dark','endfield','arknights','nikke','azurlane','zzz']){
      await page.locator('#workspace-button').click();await page.locator('#theme-preset').selectOption(preset);await page.locator('#save-theme').click();await page.waitForFunction(p=>document.documentElement.dataset.theme===p,preset);
      const sample=await contrast();observations.push({preset,contrast:sample});assert.ok(sample.every(s=>s.ratio>=4.5),preset+': low contrast '+JSON.stringify(sample));
    }
    const fontBefore=await page.locator('.axis-date').first().evaluate(n=>parseFloat(getComputedStyle(n).fontSize));
    await page.locator('#workspace-button').click();await page.locator('#theme-font_size').locator('xpath=ancestor::details[1]').locator('summary').click();await page.locator('#theme-font_size').fill('18');await page.locator('#save-theme').click();await page.waitForFunction(()=>document.documentElement.style.fontSize==='18px');
    assert.ok(await page.locator('.axis-date').first().evaluate(n=>parseFloat(getComputedStyle(n).fontSize))>fontBefore*1.2,'The date ruler must honor the larger user font setting');
    await page.locator('#filter-button').click();assert.equal(await page.locator('#view-filters').isVisible(),false);
    await page.setViewportSize({width:375,height:812});await page.locator('.upcoming-item').first().waitFor();
    assert.ok(await page.locator('.upcoming-text strong').first().evaluate(n=>parseFloat(getComputedStyle(n).fontSize))>16,'Larger fonts must also enlarge activity names');
    await page.locator('#filter-button').click();assert.equal(await page.locator('.sidebar').getAttribute('aria-modal'),'true');
    const active=()=>page.evaluate(()=>document.activeElement?.id||document.activeElement?.tagName);
    assert.equal(await active(),'sidebar-more');assert.equal(await page.locator('#view-filters').isVisible(),true,'Desktop collapsed filters must open in the phone sheet');await page.locator('#game-select').focus();await page.keyboard.press('Shift+Tab');assert.ok(await page.evaluate(()=>document.activeElement.closest('.sidebar')));
    await page.keyboard.press('Tab');assert.equal(await active(),'game-select');await page.keyboard.press('Escape');
    assert.equal(await active(),'filter-button');assert.equal(await page.locator('.sidebar').getAttribute('aria-modal'),null);
    await page.locator('#filter-button').click();await page.locator('#manage-plugins').click();await page.locator('#preferences-dialog[open]').waitFor();await page.locator('#preferences-dialog [data-close]').click();
    assert.ok(await page.evaluate(()=>document.activeElement?.getBoundingClientRect().width>0),'Closing an action from the filter sheet needs a visible focus target');
    await page.screenshot({path:path.join(out,'phone-large-font.png')});
    const touch=await browser.newContext({viewport:{width:375,height:812},deviceScaleFactor:2,hasTouch:true,locale:'zh-CN',timezoneId:'America/New_York',reducedMotion:'reduce'}),p=await touch.newPage();p.on('pageerror',e=>errors.push(e.message));
    await p.clock.setFixedTime(new Date('2026-10-27T20:00:00Z'));await p.goto(base+'/?game=all');await p.locator('.upcoming-item').first().waitFor();
    assert.equal(await p.evaluate(()=>matchMedia('(pointer:coarse)').matches),true);
    for(const id of ['#sync-button','#filter-button','#calendar-expand','#month-view'])assert.ok((await p.locator(id).boundingBox()).height>=44,id+' must be touch reachable');
    assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.ok(await p.locator('.upcoming-item').evaluateAll(nodes=>nodes.filter(n=>{const r=n.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}).length)>=3,'Touch mode with larger fonts must retain three readable upcoming entries');
    await p.locator('#filter-button').tap();await p.locator('#sidebar-more').tap();assert.equal(await p.locator('.sidebar.sidebar-expanded').count(),0);
    await p.screenshot({path:path.join(out,'touch-dpr2.png')});await p.setViewportSize({width:720,height:450});await p.screenshot({path:path.join(out,'zoom-equivalent-200.png')});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await touch.close();
    assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'observations.json'),JSON.stringify(observations,null,2));console.log('PASS: named controls, keyboard settings tabs, focus wrap/return and nested sheet actions; '+out);
  }catch(error){await fs.writeFile(path.join(out,'observations.json'),JSON.stringify(observations,null,2));if(page)await page.screenshot({path:path.join(out,'failure.png')});throw error;}finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
