// R1: planner entries are safe before the first local state arrives and work right after it.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8810';
(async()=>{
  const output=path.resolve('logs/plan-ready-e2e'),dir=path.join(output,'data-'+Date.now());
  await fs.mkdir(dir,{recursive:true});
  const server=spawn(process.env.CALENDAR_PYTHON||'python',['app.py','--port','8810','--data-dir',dir,'--no-sync','--no-desktop'],{windowsHide:true,stdio:'ignore'});let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===49)throw Error('Server failed');await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    // Hold the first state response until the test releases it.
    let release,held=false;const gate=new Promise(r=>release=r);
    await page.route('**/api/state',async route=>{if(!held){held=true;await gate;}await route.continue();});
    await page.goto(base);await page.locator('#plans-button').waitFor({state:'attached'});
    assert.equal(await page.locator('#plans-button').isDisabled(),true,'Disabled while the state is loading');
    assert.equal(await page.locator('#uncertain-deadlines').isDisabled(),true);
    await page.locator('#plans-button').evaluate(n=>{n.disabled=false;n.click();n.disabled=true;}); // Even a forced click is ignored safely.
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#plans-dialog').evaluate(n=>n.open),false);
    assert.deepEqual(errors,[],'No error before the state arrives');
    release();
    await page.locator('#plans-button:not([disabled])').waitFor();
    await page.locator('#plans-button').click();await page.locator('#plans-dialog[open]').waitFor();
    assert.match(await page.locator('#plans-content').innerText(),/创建个人规划/);
    assert.deepEqual(errors,[]);console.log('PASS: planner entries disabled and click-safe before the first state, enabled and working after it');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
