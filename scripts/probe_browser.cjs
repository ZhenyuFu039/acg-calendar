// Optional browser fallback for public, JavaScript-rendered announcement pages.
// Requires Playwright and Edge; uses a fresh browser context, no account login.
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output=path.resolve(process.argv[2]||'logs/scrape-probe/browser');
const detailMode=process.argv.includes('--details');
const pages=detailMode?{
  azurlane:'https://game.bilibili.com/blhx/',
  nikke:'https://nikke-en.com/newsdetail.html?content_id=9ff3fe24abf0ca437fa9c7dabd8f369872a8&sid=494&from=list&col_name=IMPORTANT&father_content_id=9ff3fe24abf0ca437fa9c7dabd8f369872a8'
}:{endfield:'https://endfield.hypergryph.com/news',azurlane:'https://game.bilibili.com/blhx/',nikke:'https://nikke-en.com/news.html'};
const isAnnouncement=url=>/web-news\.hypergryph\.com\/api\/bulletin|api\.biligame\.com\/news\/|playerinfinite\.com\/api\/.*(?:GetContent|GetLabel)/.test(url);

(async()=>{
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try{
    const results=await Promise.all(Object.entries(pages).map(async([name,url])=>{
      const context=await browser.newContext({locale:'zh-CN',viewport:{width:1360,height:900}});
      const page=await context.newPage();
      const result={name,url,ai_used:false,fetched_at_utc:new Date().toISOString(),method:'playwright-edge',requests:[],data_responses:[],errors:[]};
      const tasks=[];
      await page.route('**/*',async route=>{
        const request=route.request();
        if(['image','media','font'].includes(request.resourceType())||/googletagmanager|google-analytics|aix_track|report\.(?:biligame|bilibili)|onetrust\.com|rumt-sg\.com|sentry\.|event-log-|line3-log\.|data\.bilibili\.com|\.(?:mp3|mp4|webm|ogg)(?:\?|$)/.test(request.url()))await route.abort();
        else await route.continue();
      });
      page.on('pageerror',error=>{if(result.errors.length<8)result.errors.push(error.message.slice(0,350));});
      page.on('response',response=>{
        const request=response.request(),type=request.resourceType();
        if(!['fetch','xhr'].includes(type)||!isAnnouncement(response.url()))return;
        if(result.requests.length<80)result.requests.push({url:response.url(),status:response.status(),method:request.method(),body:request.postData()});
        if(response.status()!==200||result.data_responses.length>=25)return;
        const index=result.data_responses.length;
        const entry={url:response.url(),status:response.status(),file:`${name}-data-${index}.txt`};
        result.data_responses.push(entry);
        tasks.push((async()=>{
          try{
            const text=await response.text();
            if(text.length>2000000){entry.error='over 2 MB';return;}
            entry.characters=text.length;
            await fs.writeFile(path.join(output,entry.file),text,'utf8');
          }catch(error){entry.error=error.message.slice(0,150);}
        })());
      });
      try{
        const response=await page.goto(url,{waitUntil:'domcontentloaded',timeout:30000});
        result.status=response?.status();
        await page.waitForTimeout(10000);
        if(detailMode&&name==='azurlane'){
          const title=page.locator('.newstitle').filter({hasText:'2026维护公告'}).first();
          await title.waitFor({state:'attached',timeout:10000});
          result.clicked_title=await title.textContent();
          await title.evaluate(node=>node.click());
          await page.waitForTimeout(5000);
        }
        result.final_url=page.url();
        result.title=await page.title();
        const text=await page.locator('body').innerText({timeout:5000});
        result.text_characters=text.length;
        result.links=await page.locator('a[href]').evaluateAll(nodes=>nodes.map(n=>({url:n.href,text:n.innerText.trim()})).filter(n=>n.text));
        await fs.writeFile(path.join(output,name+'.txt'),text,'utf8');
        await fs.writeFile(path.join(output,name+'.html'),await page.content(),'utf8');
        if(name==='endfield'){
          const first=page.locator('[class*="NoticeList_item__"]').first();
          if(await first.count()){
            result.clicked_title=await first.innerText();
            await first.click({timeout:10000});
            await page.waitForTimeout(2500);
            result.detail_url=page.url();
            result.detail_title=await page.title();
            const detail=await page.locator('body').innerText();
            result.detail_characters=detail.length;
            await fs.writeFile(path.join(output,name+'-detail.txt'),detail,'utf8');
            await fs.writeFile(path.join(output,name+'-detail.html'),await page.content(),'utf8');
          }
        }
      }catch(error){result.error=error.message;}
      await Promise.allSettled(tasks);
      await context.close();
      await fs.writeFile(path.join(output,name+'.json'),JSON.stringify(result,null,2),'utf8');
      return result;
    }));
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify(results,null,2),'utf8');
    console.log(JSON.stringify(results,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
