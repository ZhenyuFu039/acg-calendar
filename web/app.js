'use strict';
(() => {
  const W = window.PlaytimeWorkspace;
  const P = window.PlaytimePlanner;
  const combined = () => ['all','selected'].includes(S.game);
  const $ = selector => document.querySelector(selector);
  const DAY = 86400000;
  const categories = {
    event: { name: '活动', color: 'var(--event)' },
    gacha: { name: '寻访与申领', color: 'var(--gacha)' },
    exchange: { name: '奖励兑换', color: 'var(--exchange)' },
    update: { name: '玩法更新', color: 'var(--update)' },
    maintenance: { name: '版本维护', color: 'var(--maintenance)' }
  };
  const endpointLabels = {exact:'公告明确', estimated:'预计 / 待核实', confirmed:'开服已确认', manual:'手动校正', unknown:'待定'};
  const referenceLabels = {next_maintenance_start:'下次版本维护前，具体时间未公布', recruitment_cycles:'按寻访次数结束，具体日期未公布', maintenance_end:'随本版本维护结束开放', unpublished:'公告未给出结束时间', invalid_interval:'时间顺序异常，请核对'};
  const S = {data:null, game:null, version:null, view:'timeline', days:14, fit:false, anchor:null, selected:null, categories:new Set(['event','gacha','exchange','update','maintenance']), includePending:true, rules:null, rulesTab:'config', seenNotices:new Set(), initialized:false, pollTimer:null, toastTimer:null};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const now = () => Date.now();
  const allEvents = () => S.data?.events || [];
  const activeVersion = () => combined()?{id:S.game,name:S.game==='selected'?'自选游戏组合':'全部游戏',source:{origin:'combined'},maintenance:{start:null}}:S.data?.versions.find(version => version.id === S.version) || {id:S.game,name:gameInfo()?.name||'日历',source:{origin:'empty'},maintenance:{start:null}};
  const scopeEvents = () => allEvents().filter(e=>S.game==='all'||(S.game==='selected'?W.selected().includes(e.game):e.version_id===S.version));
  const gameInfo = () => S.data?.games.find(g=>g.id===S.game);
  const gameLabel = event => W.game(event.game)?.short_name || event.game_name || event.game;
  const syncState = () => combined()?{running:Object.values(S.data.sync_by_game).some(s=>s.running),error:Object.values(S.data.sync_by_game).map(s=>s.error).filter(Boolean).join('；')}:S.data.sync_by_game[S.game]||S.data.sync;
  function zone() {
    const candidate = S.data?.settings.timezone;
    const value = candidate && candidate !== 'auto' ? candidate : Intl.DateTimeFormat().resolvedOptions().timeZone;
    try { new Intl.DateTimeFormat('en-US', {timeZone:value}).format(); return value; }
    catch { return 'UTC'; }
  }
  function dateParts(value, timeZone=zone()) {
    return Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(part => [part.type,part.value]));
  }
  function civil(value) {
    const p = dateParts(value);
    return Date.UTC(+p.year,+p.month-1,+p.day)/DAY+(+p.hour*3600 + +p.minute*60 + +p.second)/86400;
  }
  function serialDate(serial) { return new Date(Math.floor(serial)*DAY); }
  function shortDay(serial, year=false) {
    const d=serialDate(serial);
    return (year?d.getUTCFullYear()+'/':'')+String(d.getUTCMonth()+1).padStart(2,'0')+'/'+String(d.getUTCDate()).padStart(2,'0');
  }
  function format(value, {timeZone=zone(),year=false,dateOnly=false,seconds=false}={}) {
    if (!value) return '时间待定';
    const p=dateParts(value,timeZone);
    return (year?p.year+'/':'')+p.month+'/'+p.day+(dateOnly?'':' '+p.hour+':'+p.minute+(seconds?':'+p.second:''));
  }
  function sourceInput(value) {
    if(!value) return '';
    const p=dateParts(value,'UTC');
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  }
  function countdown(value) {
    if(!value) return '截止时间待定';
    const delta=Date.parse(value)-now();
    if(delta<=0) return '已结束';
    const minutes=Math.ceil(delta/60000), days=Math.floor(minutes/1440), hours=Math.floor(minutes%1440/60);
    if(days) return `${days}天 ${hours}小时`;
    if(hours) return `${hours}小时 ${minutes%60}分`;
    return `${minutes}分钟`;
  }
  const narrow=matchMedia('(max-width:850px)');
  const local={
    get(key,fallback){try{const v=JSON.parse(localStorage.getItem('playtime.'+key));return v??fallback;}catch{return fallback;}},
    set(key,value){try{localStorage.setItem('playtime.'+key,JSON.stringify(value));}catch{}}
  };
  // Phone view choice is kept per browser and screen class, so polling and the desktop
  // setting never override it; wide screens keep using settings.view.
  const NARROW_VIEW='view.v1.narrow',NARROW_VIEWS=['upcoming','timeline','month','list'];
  function preferredView(){
    if(narrow.matches){const v=local.get(NARROW_VIEW,'upcoming');return NARROW_VIEWS.includes(v)?v:'upcoming';}
    return S.data.settings.view;
  }
  // On phones the filter sidebar folds to the game picker; the saved desktop sidebar is untouched.
  function paintSidebar(){const open=local.get('sidebar.v1.narrow',false)===true;$('.sidebar').classList.toggle('sidebar-expanded',open);const b=$('#sidebar-more');b.setAttribute('aria-expanded',String(open));b.textContent=open?'收起筛选 ▴':'范围与筛选 ▾';}
  function headingCompact(){return local.get('heading.v1',innerHeight<1000?'compact':'full')==='compact';}
  function paintHeading(){
    const compact=headingCompact();document.body.classList.toggle('heading-compact',compact);
    const b=$('#heading-toggle');b.textContent=compact?'展开标题':'收起标题';b.setAttribute('aria-expanded',String(!compact));
  }
  function upcomingRows(events) {
    const byId=new Map(events.map(e=>[e.id,e])),seen=new Set(),current=now();
    const nodes=(S.data.presentation.deadline_nodes||[]).filter(n=>byId.has(n.event_id)&&Date.parse(n.at)>current&&!seen.has(n.event_id+'|'+n.at+'|'+n.kind)&&seen.add(n.event_id+'|'+n.at+'|'+n.kind))
      .sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)||a.title.localeCompare(b.title));
    const uncertain=(S.data.presentation.uncertain_deadlines||[]).map(id=>byId.get(id)).filter(e=>e&&e.time_kind!=='point'&&(!e.end.at||Date.parse(e.end.at)>current));
    return {byId,nodes,uncertain};
  }
  function renderUpcoming(events) {
    const {byId,nodes,uncertain}=upcomingRows(events),today=Math.floor(civil(now()));
    if(!nodes.length&&!uncertain.length)return emptyCalendar();
    const item=(e,kind,when,extra)=>`<button type="button" class="upcoming-item ${e.completed?'completed':''}" data-event="${e.id}" style="--event-accent:${accent(e)}"><span class="event-avatar">${gameIcon(e.game)}</span><span class="upcoming-text"><strong>${e.starred?'★ ':''}${esc(cleanTitle(e))}</strong><small>${esc(gameLabel(e))} · ${kind}</small></span><span class="upcoming-time"><strong>${esc(when)}</strong>${extra}</span></button>`;
    // Only a confirmed overall end is labelled as such. An exact phase can coincide with
    // an estimated overall end; its own title still identifies what is actually closing.
    const nodeLabel=(n,e)=>e.end.at&&['exact','confirmed','manual'].includes(e.end.state)&&n.title===e.title&&Date.parse(n.at)===Date.parse(e.end.at)?(n.kind==='exchange'?'兑换关闭':'活动结束'):`<b class="upcoming-phase">${esc(n.title)}</b>`;
    const groups=new Map();
    for(const n of nodes){const day=Math.floor(civil(n.at));if(!groups.has(day))groups.set(day,[]);groups.get(day).push(n);}
    const label=day=>(day===today?'今天 · ':day===today+1?'明天 · ':'')+shortDay(day)+' 周'+'日一二三四五六'[serialDate(day).getUTCDay()];
    return `<div class="upcoming-list">${[...groups].map(([day,rows])=>`<section class="upcoming-day"><h3>${label(day)}<span>${rows.length} 项</span></h3>${rows.map(n=>item(byId.get(n.event_id),nodeLabel(n,byId.get(n.event_id)),format(n.at).split(' ')[1],countdownSlot(n.at))).join('')}</section>`).join('')}${uncertain.length?`<section class="upcoming-day upcoming-uncertain"><h3>预计 / 待定<span>${uncertain.length} 项 · 不计入精确截止</span></h3>${uncertain.map(e=>item(e,e.end.at?'预计结束':'截止待定',e.end.at?format(e.end.at):'待公告','<span>'+(e.end.at?'预计':'等待后续公告')+'</span>')).join('')}</section>`:''}</div>`;
  }
  const countdownSlot=value=>`<span data-countdown="${esc(value||'')}"></span>`;
  // Time-dependent values are written into existing nodes; the markup stays stable between polls.
  function syncTime(root=document) {
    for(const node of root.querySelectorAll('[data-countdown]')){const text=countdown(node.dataset.countdown);if(node.textContent!==text)node.textContent=text;}
    const current=civil(now());
    for(const node of root.querySelectorAll('[data-progress]')){const [a,b]=node.dataset.progress.split(',').map(Number);node.style.setProperty('--progress',Math.max(0,Math.min(100,(current-a)/(b-a)*100))+'%');}
    for(const node of root.querySelectorAll('[data-now-line]')){const [lo,days]=node.dataset.nowLine.split(',').map(Number);node.style.setProperty('--now-left',(current-lo)/days*100+'%');}
  }
  // Replace a region only when its markup changed; when it must be rebuilt, focus returns to
  // the control for the same activity or plan instead of falling back to <body>.
  function patch(el,html) {
    if(el._html===html&&el.firstChild===el._first)return false;
    const active=document.activeElement,key=el.contains(active)&&active!==el?focusKey(active):null;
    el.innerHTML=html;el._html=html;el._first=el.firstChild;
    if(key){const same=el.querySelector(key);if(same)same.focus({preventScroll:true});}
    return true;
  }
  function focusKey(node) {
    const data=[...node.attributes].filter(a=>a.name.startsWith('data-')&&!['data-countdown','data-progress','data-now-line'].includes(a.name)).map(a=>`[${a.name}="${CSS.escape(a.value)}"]`).join('');
    const kind=node.classList[0]?'.'+CSS.escape(node.classList[0]):'';
    return data||node.id?node.tagName.toLowerCase()+kind+(node.id?'#'+CSS.escape(node.id):'')+data:null;
  }
  function status(event) {
    if(event.completed) return '已完成';
    if(event.invalid_interval) return '时间待核对';
    if(event.end.at && Date.parse(event.end.at)<=now()) return event.end.state==='estimated'?'已过预计结束':'已结束';
    if(!event.start.at) return '开始时间待定';
    if(Date.parse(event.start.at)>now()) return event.start.state==='estimated'?'预计开放':'未开始';
    if(event.time_kind==='point') return '已更新';
    if(event.start.state==='estimated') return '开始待核实';
    if(!event.end.at) return '结束待定';
    return '进行中';
  }
  function cleanTitle(event) { return event.title.replace(/[「」]/g,''); }
  function artwork(event, className='') {
    const path=event.artwork?.path;
    return /^\/api\/artwork\/[a-f0-9]{32}\.(jpg|png|webp)$/.test(path||'')?`<img class="activity-art ${className}" src="${path}" alt="" loading="lazy" decoding="async">`:'';
  }
  function gameIcon(game) {
    const info=W.game(game), path=info?.icon;
    return path && /^(?:\/assets\/[a-zA-Z0-9_.-]+|\/api\/artwork\/[a-f0-9]{32}\.(?:png|jpg|webp))$/.test(path) ? `<img class="game-icon" src="${esc(path)}" alt="${esc(info.name)}" decoding="async">` : `<span class="game-icon game-monogram-fallback" aria-label="${esc(info?.name||game)}">${esc(info?.monogram||'?')}</span>`;
  }

  function accent(event) {
    let hash=0;for(const c of event.id) hash=(hash*31+c.charCodeAt(0))>>>0;
    return ['#69946e','#5e8ea3','#ae825d','#8f7bac','#9f8f4c','#668f91','#b07679','#7b8e57'][hash%8];
  }
  const rewardIcon = '<img class="reward-icon" src="/assets/oroberyl.png" alt="嵌晶玉" width="26" height="26">';
  const rewardNumber = value => Number(value).toLocaleString('zh-CN');
  function rewardBadge(event) {
    if(!W.game(event.game)?.currency) return '<span class="reward-unlisted" title="该游戏未启用奖励统计">—</span>';
    const r=event.reward;
    if(!r || r.state==='excluded') return '<span class="reward-unlisted">单列</span>';
    if(r.amount===null) return '<span class="reward-unlisted" title="公告未列明嵌晶玉数量，未计入已核实小计；不表示为零">未列明</span>';
    return `<span class="reward-badge ${r.state==='shared'?'shared':''}" title="${esc(r.note)}">${rewardIcon}<strong>${rewardNumber(r.amount)}</strong>${r.state==='shared'?'<small>共享</small>':event.category==='gacha'?'<small>试用</small>':''}</span>`;
  }
  function renderRewardSummary(version) {
    const r=version.reward_summary;
    const related=P.relatedRewards();
    $('#reward-summary').hidden=!r&&!related;
    if(!r) {$('#reward-summary').innerHTML=related;return;}
    $('#reward-summary').innerHTML=`<div class="reward-summary-amount">${rewardIcon}<div><span>本版本活动 · 已核实小计</span><strong id="reward-total">${rewardNumber(r.verified_total)}<small>嵌晶玉</small></strong></div></div><p>${r.claims.length} 笔独立奖励 · ${r.unlisted.length} 项公告未列明数量${r.conflicts.length?' · '+r.conflicts.length+' 项来源差异':''}<br>不含新地图、信用商店；不随筛选或完成状态变化</p><button id="reward-details" class="text-button" type="button">统计口径与明细 ↗</button>${related}`;
  }
  function rewardDetail(event) {
    const r=event.reward;
    if(!r) return '';
    const claims=(r.claims||[]).map(c=>`<li>${esc(c.text)} <a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${c.source==='briefing'?'官方导览':'更新公告'+(c.line?' · 第 '+c.line+' 行':'')} ↗</a></li>`).join('');
    return `<section class="reward-evidence"><div class="reward-detail-heading"><strong>活动嵌晶玉</strong>${rewardBadge(event)}</div><p class="help-text">${esc(r.note)}</p>${claims?'<ul class="reward-sources">'+claims+'</ul>':''}</section>`;
  }
  function openRewardDetails() {
    const r=activeVersion().reward_summary;
    $('#reward-title').textContent='「'+activeVersion().name+'」奖励明细';
    const rows=r.claims.map(c=>`<div class="reward-claim"><button type="button" data-event="${esc(c.event_id)}">${esc(c.title)}</button><span>${rewardIcon}<strong>${rewardNumber(c.amount)}</strong></span><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${c.source==='briefing'?'导览':'公告'+(c.line?' L'+c.line:'')} ↗</a></div>`).join('');
    const other=r.other_mentions.map(c=>`<li><strong>${esc(c.reason)}</strong> · ${c.amount===null?'数量未明确':rewardNumber(c.amount)}<details><summary>原文</summary><p>${esc(c.text)}</p><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">官方公告 ↗</a></details></li>`).join('');
    const conflicts=r.conflicts.map(c=>`<li>${esc(c.title)}：公告 ${rewardNumber(c.announcement)} / 导览 ${rewardNumber(c.briefing)}，采用公告数量。</li>`).join('');
    $('#reward-content').innerHTML=`<div class="reward-dialog-total">${rewardIcon}<strong>${rewardNumber(r.verified_total)}</strong><span>已核实小计</span></div><p class="help-text">${esc(r.scope)}<br>统计活动可获得总量，非账号未领取余额。公告未列明数量的条目不按零计算；导览数量为 1 的展示占位不计数。</p>${rows}${conflicts?'<h3>来源数量差异</h3><ul class="reward-sources">'+conflicts+'</ul>':''}<h3>公告未列明数量 · ${r.unlisted.length} 项</h3><p class="help-text">${r.unlisted.map(e=>esc(e.title)).join('；')||'无'}。未列明不代表奖励为零。</p>${other?'<h3>未纳入活动小计</h3><ul class="reward-sources">'+other+'</ul>':''}`;
    openDialog('#reward-dialog');
  }
  const searchQuery=()=>($('#event-search')?.value||'').trim().toLocaleLowerCase();
  const matchesSearch=(event,query)=>!query||(event.title+' '+event.subtype+' '+event.section+' '+(event.game_name||'')).toLocaleLowerCase().includes(query);
  // Each display filter, by id; baseFilteredEvents and the hidden-activity summary share these rules.
  function filterRules() {
    const query=searchQuery(),current=now();
    return [
      ['future','未开放',e=>W.view.hide_future&&!(e.start.at&&Date.parse(e.start.at)<=current)],
      ['category','类型筛选',e=>!S.categories.has(e.category)],
      ['completed','已完成',e=>S.data.settings.hide_completed&&e.completed],
      ['pending','截止待定',e=>!S.includePending&&e.time_kind!=='point'&&!e.end.at],
      ['search','搜索',e=>!matchesSearch(e,query)],
    ];
  }
  function baseFilteredEvents() {
    const rules=filterRules();
    return scopeEvents().filter(event=>!event.ignored&&!rules.some(([,,hides])=>hides(event)));
  }
  // [activities the view selects before folding, activities it actually draws], each by id once.
  // What the current view selects and what it really draws, each by activity id once.
  // folded: children collapsed under a parent; overflow: month bars beyond three lanes.
  function viewProjection(filtered,shown,lo,hi) {
    const tree=list=>{const drawn=P.arrange(list);return {inView:list,visible:drawn,folded:list.length-drawn.length,overflow:0};};
    if(S.view==='timeline')return tree(shown);
    if(S.view==='list')return tree(filtered);
    if(S.view==='upcoming'){const {byId,nodes,uncertain}=upcomingRows(filtered),ids=new Set([...nodes.map(n=>n.event_id),...uncertain.map(e=>e.id)]);const drawn=[...ids].map(id=>byId.get(id));return {inView:drawn,visible:drawn,folded:0,overflow:0};}
    const [first,last]=monthGrid(lo,hi),inView=filtered.filter(e=>overlaps(e,first,last)),drawn=new Map();
    for(let week=first;week<last;week+=7)for(const [event] of monthWeek(inView,week,week+7).placed)drawn.set(event.id,event);
    return {inView,visible:[...drawn.values()],folded:0,overflow:inView.length-drawn.size};
  }
  // F16: explain what the current filters hide, per reason and by stable id, so a hidden
  // activity is not mistaken for a failed sync. Buttons clear only their own filter.
  function renderHiddenSummary(filtered,{inView,visible,folded,overflow}) {
    const scope=scopeEvents().filter(e=>!e.ignored),rules=filterRules(),visibleIds=new Set(visible.map(e=>e.id));
    const base=new Set(baseFilteredEvents().map(e=>e.id)),afterFocus=new Set(filtered.map(e=>e.id)),inRange=new Set(inView.map(e=>e.id));
    const counts=rules.map(([id,label,hides])=>[id,label,scope.filter(hides).length]).filter(([,,n])=>n);
    const focus=[...base].filter(id=>!afterFocus.has(id)).length,range=[...afterFocus].filter(id=>!inRange.has(id)).length;
    const games=S.game==='all'?S.data.games.map(g=>g.id):S.game==='selected'?W.selected():[gameInfo()?.id||S.game];
    const added=new Set(games.flatMap(g=>S.data.sync_by_game?.[g]?.result?.calendar?.added_ids||[]));
    const newHidden=scope.filter(e=>added.has(e.id)&&!visibleIds.has(e.id)).length;
    // A version picker can hide a whole new version; count those per version, without switching.
    const scopeIds=new Set(scope.map(e=>e.id)),otherVersions=new Map();
    for(const e of allEvents())if(games.includes(e.game)&&!e.ignored&&added.has(e.id)&&!scopeIds.has(e.id))otherVersions.set(e.version_id,(otherVersions.get(e.version_id)||0)+1);
    const hidden=scope.length-afterFocus.size;
    const el=$('#hidden-summary');
    if(!hidden&&!newHidden&&!otherVersions.size){el.hidden=true;patch(el,'');return;}
    el.hidden=false;
    const versionName=id=>S.data.versions.find(v=>v.id===id)?.name||id;
    const outside={timeline:'日期范围外',month:'本月网格外',upcoming:'近期截止只列未来截止，其余'}[S.view];
    patch(el,`${[...otherVersions].map(([id,n])=>`<strong>「${esc(versionName(id))}」有 ${n} 条上次同步新增的活动</strong><button type="button" class="text-button" data-unhide="version" data-version="${esc(id)}">查看该版本</button>`).join('')}${newHidden?`<strong>上次同步新增的 ${newHidden} 条活动未显示</strong>`:''}<span>当前显示 ${visible.length} / 本范围 ${scope.length} 项${hidden?`，筛选隐藏 ${hidden} 项`:''}${counts.length>1?'（各原因可能重叠）':''}：</span>${counts.map(([id,label,n])=>`<button type="button" class="text-button" data-unhide="${id}">${label} ${n} · 显示</button>`).join('')}${focus?`<button type="button" class="text-button" data-unhide="focus">日期聚焦 ${focus} · 取消聚焦</button>`:''}${range&&outside?`<button type="button" class="text-button" data-unhide="range">${outside} ${range} · 在全部活动中查看</button>`:''}${overflow>0?`<button type="button" class="text-button" data-unhide="range">月历每格最多 3 条，另有 ${overflow} 项收在“+N 项”中 · 在全部活动中查看</button>`:''}${folded>0?`<span>另有 ${folded} 条子活动已折叠</span>`:''}`);
  }
  function filteredEvents() {return P.filterFocus(baseFilteredEvents());}
  function range() {
    if(S.view==='timeline') return [S.anchor,S.anchor+S.days];
    const d=serialDate(S.anchor), first=Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),1)/DAY;
    const last=Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,1)/DAY;
    return [first,last];
  }
  function overlaps(event,lo,hi) {
    if(!event.start.at || event.invalid_interval) return false;
    const start=civil(event.start.at);
    if(event.time_kind==='point') return start>=lo && start<hi;
    const end=event.end.at?civil(event.end.at):Infinity;
    return start<hi && end>lo;
  }
  async function api(path, method='GET', body) {
    const response=await fetch(path,{method,headers:method==='GET'?{}:{'Content-Type':'application/json','X-Calendar-Token':S.data.csrf_token},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok) throw new Error(result.error || '请求失败');
    return result;
  }
  function toast(message,error=false) {
    const el=$('#toast');
    el.textContent=message;el.classList.toggle('error',error);el.hidden=false;
    clearTimeout(S.toastTimer);S.toastTimer=setTimeout(()=>{el.hidden=true;},4500);
  }
  async function mutate(path,body={},method='POST') {
    const generation=S.scopeGeneration;
    try { const data=await api(path,method,body); if(generation===S.scopeGeneration)applyState(data); return true; }
    catch(error) { toast(error.message,true);return false; }
  }
  async function selectScope(game,selected_games,reset=false) {
    if(S.scoping)return;
    S.scopeGeneration=(S.scopeGeneration||0)+1;S.scoping=true;
    const controls=[$('#game-select'),...document.querySelectorAll('[data-select-game]')];controls.forEach(c=>c.disabled=true);
    try {
      if(game!=='selected')await api('/api/settings','POST',{active_game:game});
      const data=await api('/api/workspace','PATCH',{view:{scope:['all','selected'].includes(game)?game:'single',selected_games}});
      S.game=game;S.version=null;S.rules=null;
      if(reset){S.fit=false;S.days=Number($('#range-select').value);S.anchor=Math.floor(civil(now()));$('#event-search').value='';}
      S.scoping=false;applyState(data);
    }catch(error){toast(error.message,true);S.scoping=false;render();}
    finally{S.scoping=false;$('#game-select').disabled=false;document.querySelectorAll('[data-select-game]').forEach(c=>c.disabled=false);}
  }
  function applyState(data) {
    if(S.scoping)return;
    const first=!S.initialized;
    S.data=data;W.accept(data);
    const scopeSignature=JSON.stringify([W.view.scope,W.selected()]);
    if(S.scopeSignature&&S.scopeSignature!==scopeSignature)S.game=W.view.scope==='single'?W.selected()[0]:W.view.scope;
    S.scopeSignature=scopeSignature;
    $('#workspace-button').disabled=false;
    $('#fit-version').disabled=false;$('#data-button').disabled=false;
    if(!S.game) { const requested=new URLSearchParams(location.search).get('game'); S.game=['all','selected'].includes(requested)||data.games.some(g=>g.id===requested)?requested:(W.view.scope==='selected'?'selected':W.view.scope==='all'?'all':W.selected()[0])||data.games[0]?.id||'all'; }
    if(!['all','selected'].includes(S.game)&&!W.game(S.game)) S.game=data.games[0]?.id||'all';
    if(combined()) S.version=S.game;
    else if(!data.versions.some(version=>version.id===S.version&&version.game===S.game)) S.version=data.versions.find(v=>v.game===S.game)?.id;
    if(first) {
      S.view=preferredView();S.anchor=Math.floor(civil(now()));paintHeading();paintSidebar();
      data.notifications.forEach(notice=>S.seenNotices.add(notice.id));
      buildFilters();S.initialized=true;
    }
    const focusDate=W.view.focus.date;
    if(focusDate&&(first||S.lastFocusDate!==focusDate)){const day=Date.parse(focusDate)/DAY,[lo,hi]=range();if(S.view==='month'||day<lo||day>=hi)S.anchor=day;}
    S.lastFocusDate=focusDate;
    deliverBrowserNotices(data.notifications);
    render();
    if(first)P.query();
    window.PlaytimeCustomize.update();
    if(first&&new URLSearchParams(location.search).has("appearance")){const url=new URL(location.href),panel=url.searchParams.get('appearance');url.searchParams.delete('appearance');history.replaceState(null,'',url);if(panel==='reminders')openReminders();else window.PlaytimeCustomize.open(['layout','theme','network'].includes(panel)?panel:'theme');}
    if($('#reminder-dialog').open) renderInbox();
    $('#load-error').hidden=true;
  }
  function buildFilters() {
    $('#category-filters').innerHTML=Object.entries(categories).map(([id,category])=>`<label class="filter-row" style="--category:${category.color}"><input type="checkbox" data-category="${id}" ${S.categories.has(id)?'checked':''}><span class="category-dot"></span><span>${category.name}</span><span class="filter-count" data-count="${id}">0</span></label>`).join('');
  }
  function render() {
    const version=activeVersion();
    if(!version) return;
    const options=combined()?`<option value="${S.game}">${S.game==='selected'?'自选组合':'全部游戏'} · 各自来源</option>`:S.data.versions.filter(v=>v.game===S.game).map(v=>`<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
    if($('#version-select').innerHTML!==options) $('#version-select').innerHTML=options;
    $('#version-select').value=S.version;
    const gameOptions=S.data.games.map(g=>`<option value="${esc(g.id)}">${esc(g.name+' · '+g.server)}</option>`).join('')+'<option value="all">全部游戏 · 对照排期</option><option value="selected">自选游戏组合</option>';
    if($('#game-select').innerHTML!==gameOptions) $('#game-select').innerHTML=gameOptions;
    $('#game-select').value=S.game;
    const combo=S.data.games.map(g=>`<label class="check-row"><input type="checkbox" data-select-game="${esc(g.id)}" ${W.selected().includes(g.id)?'checked':''}>${esc(g.short_name+' · '+g.server)}</label>`).join('');
    if($('#game-combination').innerHTML!==combo) $('#game-combination').innerHTML=combo;
    $('#hide-future').checked=W.view.hide_future;$('#event-sort').value=W.view.sort;$('#event-group').value=W.view.group_by;
    $('#version-select').disabled=combined();
    $('#game-name').textContent=gameInfo()?.name||(S.game==='selected'?'自选游戏组合':'全部游戏');
    $('#game-source-note').textContent=gameInfo()?.source_label||'各自区服与来源 · 统一显示时区';
    $('#source-caption').textContent=(gameInfo()?.source_label||'各自来源')+' · 无 AI 调用';
    $('.game-monogram').innerHTML=combined()?'ALL':gameIcon(S.game);
    $('.version-label').textContent=S.game==='endfield-cn'?'版本':'日历范围';
    $('.page-heading .eyebrow').textContent=(S.game==='endfield-cn'?'ENDFIELD':S.game==='arknights-cn'?'ARKNIGHTS':'MY GAMES')+' / ACTIVITY CALENDAR';
    document.body.dataset.game=S.game;
    $('#maintenance-button').hidden=!scopeEvents().some(e=>e.category==='maintenance');
    $('a[href^="/api/calendar.ics"]').href='/api/calendar.ics?game='+S.game;
    $('#timezone-select').value=S.data.settings.timezone;
    $('#hide-completed').checked=S.data.settings.hide_completed;
    $('#combination-help').textContent='勾选后共用日期轴；'+reminderScopeText()+'（在“提醒”中设置）。';
    $('#version-summary').textContent=combined()?`${S.game==='selected'?'自选组合':'全部游戏'} · ${scopeEvents().length} 个独立条目 · ${reminderScopeText()}`:`${S.game==='endfield-cn'?'「'+version.name+'」版本':(gameInfo().name+' · '+gameInfo().server+' · '+version.name)} · ${scopeEvents().length} 个独立条目 · ${gameInfo().source_label}`;
    for(const id of Object.keys(categories)) $(`[data-count="${id}"]`).textContent=scopeEvents().filter(e=>e.category===id).length;
    $('#upcoming-view').setAttribute('aria-pressed',S.view==='upcoming');
    $('#timeline-view').setAttribute('aria-pressed',S.view==='timeline');
    $('#month-view').setAttribute('aria-pressed',S.view==='month');
    $('#list-view').setAttribute('aria-pressed',S.view==='list');
    $('.range-control').hidden=S.view!=='timeline'||S.fit;
    $('#fit-version').textContent=S.fit?'退出全览':'版本全览';
    $('#fit-version').setAttribute('aria-pressed',Boolean(S.fit));
    $('.date-navigation').classList.toggle('list-mode',['list','upcoming'].includes(S.view));
    const [lo,hi]=range();
    const date=serialDate(lo);
    $('#period-title').textContent=S.view==='month'?`${date.getUTCFullYear()} 年 ${date.getUTCMonth()+1} 月`:`${shortDay(lo,true)} — ${shortDay(hi-1,serialDate(hi-1).getUTCFullYear()!==date.getUTCFullYear())}`;
    const filtered=filteredEvents(), shown=['list','upcoming'].includes(S.view)?filtered:filtered.filter(event=>overlaps(event,lo,hi));
    const pending=filtered.filter(e=>!e.end.at&&e.time_kind!=='point').length;
    const total=scopeEvents().length;
    $('#visible-summary').textContent=S.view==='upcoming'?`筛选后 ${filtered.length} 项 · 按显示时区列出未来截止`:S.view==='list'?`本版本共 ${total} 项 · 当前显示 ${shown.length} 项`:`日期范围内 ${shown.length} / 筛选后 ${filtered.length} 项 · 本版本 ${total} 项`;
    $('#catalog-count').textContent=`${S.view==='list'?'全部列表不受日期范围限制':S.fit?'版本时间轴总览':'范围外活动可在“全部活动”查看'}${pending?' · '+pending+' 项截止待定':''}`;
    if(S.view==='list') $('#period-title').textContent=`「${version.name}」全部活动`;
    if(S.view==='upcoming') $('#period-title').textContent='近期截止';
    const unread=S.data.notifications.filter(n=>!n.read).length;
    $('#unread-count').textContent=unread;$('#unread-count').hidden=!unread;
    const sync=syncState();
    $('#sync-button').disabled=sync.running;
    $('#sync-button').classList.toggle('syncing',Boolean(sync.running));
    $('#sync-button').lastChild.textContent=sync.running?'同步中…':'同步资料';
    $('#sync-status').textContent=combined()?'各来源独立同步 · 数据保存在本机':sync.last_success?`最近同步 ${format(sync.last_success,{year:true})} · 数据保存在本机`:`${gameInfo().source_label}快照 ${format(version.source.fetched_at,{year:true})} · 数据保存在本机`;
    W.renderModules();P.update();renderClock();syncTime();
  }
  W.register('sources',()=>renderSourceStatus(activeVersion()));
  W.register('rewards',()=>renderRewardSummary(activeVersion()));
  W.register('deadlines',()=>renderDeadline(filteredEvents()));
  W.register('personal-plans',()=>P.plansModule());
  W.register('calendar',()=>{
    if(P.holding)return; // Keep pointer capture and the selection overlay during local polling.
    const [lo,hi]=range(), filtered=filteredEvents(), shown=['list','upcoming'].includes(S.view)?filtered:filtered.filter(e=>overlaps(e,lo,hi));
    const oldScroll=$('#calendar .timeline-scroll')||$('#calendar');
    const position=[oldScroll.scrollLeft,oldScroll.scrollTop];
    if(patch($('#calendar'),S.view==='timeline'?renderTimeline(shown,lo,hi):S.view==='list'?renderList(filtered):S.view==='upcoming'?renderUpcoming(filtered):renderMonth(filtered,lo,hi))){
      const newScroll=$('#calendar .timeline-scroll')||$('#calendar');
      [newScroll.scrollLeft,newScroll.scrollTop]=position;
    }
    renderHiddenSummary(filtered,viewProjection(filtered,shown,lo,hi));
    const unscheduled=filtered.filter(e=>!e.start.at||e.invalid_interval);
    $('#unscheduled').hidden=!unscheduled.length;
    patch($('#unscheduled'),unscheduled.length?`<h3>时间待核对 · ${unscheduled.length} 项</h3>${unscheduled.map(e=>`<button type="button" data-event="${e.id}">${esc(cleanTitle(e))}</button>`).join('')}`:'');
  });
  // F12: named stages and real waiting reasons instead of a percentage; the last round's
  // calendar and image results stay visible until the next sync.
  function syncProgress(sync) {
    if(!sync)return '';
    if(sync.running){
      const parts=[(sync.stage?.label||'正在同步')+'…'];
      if(sync.stage?.name==='images'&&sync.calendar_saved_at)parts.unshift('日历已更新');
      if(sync.waiting)parts.push(`等待 ${sync.waiting.site} 请求间隔约 ${sync.waiting.seconds} 秒`);
      return parts.join(' · ');
    }
    const c=sync.result?.calendar,i=sync.result?.images||{};
    if(!c||sync.error)return '';
    const changes=c.added||c.updated||c.removed?`新增 ${c.added} · 更新 ${c.updated}${c.removed?' · 移除 '+c.removed:''}`:'活动无变化';
    return '上次同步：'+changes+(i.added?` · 补齐图片 ${i.added} 张`:'')+(i.failed?` · ${i.failed} 张图片暂不可用`:'')+(i.notes?.length?' · '+i.notes.join('；'):'');
  }
  function renderSourceStatus(version) {
    const sync=syncState(), target=$('#source-status');
    if(S.game!=='endfield-cn') {
      const running=combined()?S.data.games.filter(g=>(S.game==='all'||W.selected().includes(g.id))&&S.data.sync_by_game[g.id]?.running).map(g=>g.short_name+' '+syncProgress(S.data.sync_by_game[g.id])):[];
      const label=combined()?S.data.games.filter(g=>S.game==='all'||W.selected().includes(g.id)).map(g=>g.short_name+'：'+g.source_label).join('；')+(running.length?' · 同步中：'+running.join('；'):''):gameInfo().source_label+' · '+gameInfo().server+' · '+gameInfo().timezone+(version.warnings?.length?' · '+version.warnings.length+' 项采集提示':'');
      const progress=combined()?'':syncProgress(sync);
      target.innerHTML=`<span class="status-point"></span><span>${esc(label+(sync.running?(combined()?'':' · '+progress):sync.error?' · 同步失败，保留缓存':progress?' · '+progress:S.game==='arknights-cn'&&version.source.origin==='snapshot'?' · 当前为内置快照':''))}</span>`;
      target.classList.toggle('warning',Boolean(sync.error||version.warnings?.length));target.title=sync.error||(version.warnings||[]).join('；');return;
    }
    let message;
    if(sync.running) message=syncProgress(sync)+' 现有日历仍可使用。';
    else if(sync.error) message=`联网同步失败，继续使用本地${version.source.origin==='snapshot'?'官网快照':'缓存'}。可稍后重试。`;
    else if(version.source.origin==='snapshot') message=`内置官网真实快照 · ${format(version.source.fetched_at,{year:true})} 采集 · 点击“同步公告”检查更新`;
    else message=`已连接官方公告 · ${format(version.source.fetched_at,{year:true})} 采集 · 预计开服时间有独立标记`;
    if(!sync.running&&!sync.error&&syncProgress(sync)) message+=' · '+syncProgress(sync);
    if(version.briefing) message+=` · 官方导览已关联 ${version.briefing.matched_events} 项${version.briefing.conflicts?' · '+version.briefing.conflicts+' 处时间需核对':''}`;
    if(S.data.briefing_previews?.length) message+=` · 已缓存 ${S.data.briefing_previews.length} 份新版本导览，等待对应更新公告`;
    if(S.data.source_checks?.skland?.status==='device_validation_required') message+=' · 森空岛接口需设备校验，未参与排期';
    target.innerHTML=`<span class="status-point"></span><span>${esc(message)}</span>`;
    target.classList.toggle('warning',Boolean(sync.error));
    target.title=sync.error||message;
  }
  function renderClock() {
    $('#clock-label').textContent=`${format(now(),{year:true})} · ${zone()}`;
    const offset=new Intl.DateTimeFormat('en-US',{timeZone:zone(),timeZoneName:'shortOffset'}).formatToParts(now()).find(p=>p.type==='timeZoneName')?.value || '';
    $('#zone-offset').textContent=offset.replace('GMT','UTC')+' · 按日期自动换算';
  }
  function renderDeadline(events) {
    const ids=new Set(events.map(e=>e.id)),next=(S.data.presentation.deadline_nodes||[]).filter(n=>ids.has(n.event_id)&&Date.parse(n.at)>now()).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at))[0];
    patch($('#deadline-strip'),next?`<span>NEXT DEADLINE</span><strong>${esc(format(next.at))}</strong><span>${next.kind==='exchange'?'兑换关闭 · ':''}${esc(next.title)} · 还剩 ${countdownSlot(next.at)}</span><button class="text-button" type="button" data-event="${next.event_id}">查看活动 ↗</button>`:'<span>DEADLINES</span><span>当前筛选中没有已明确的未来截止时间。</span>');
  }
  function bar(event,lo,hi,{month=false,top=0}={}) {
    const start=civil(event.start.at), isPoint=event.time_kind==='point';
    const end=isPoint?start+0.03:(event.end.at?civil(event.end.at):hi);
    const a=Math.max(lo,start),b=Math.min(hi,end),span=hi-lo;
    if(b<=a) return '';
    const left=(a-lo)/span*100,width=(b-a)/span*100;
    const cat=categories[event.category];
    const tooltip=`${cleanTitle(event)} · ${format(event.start.at,{year:true})} → ${event.end.at?format(event.end.at,{year:true}):referenceLabels[event.end.reference]||'截止待定'} · ${status(event)}`;
    if(isPoint&&!month) return `<button type="button" class="point-marker" style="--category:${cat.color};--bar-left:${left}%" data-event="${event.id}" aria-label="${esc(tooltip)}" title="${esc(tooltip)}"></button>`;
    const label=month?(combined()?gameLabel(event)+' · ':'')+cleanTitle(event):(start<lo?'← ':'')+format(event.start.at,{dateOnly:true})+(event.start.state==='estimated'?' 预计':'')+(event.end.at?' → '+format(event.end.at,{dateOnly:true}):' → 待定')+(end>hi?' →':'');
    const image=month?'':artwork(event,'bar-art');
    return `<button type="button" class="event-bar ${image?'has-art':''} ${!event.end.at&&!isPoint?'open-end':''} ${event.start.state==='estimated'?'estimated-start':''} ${end>hi?'clipped-end':''} ${event.completed?'completed':''}" style="--category:${cat.color};--event-accent:${accent(event)};--bar-left:${left}%;--bar-width:${width}%;--bar-top:${top}px" data-progress="${a},${b}" data-event="${event.id}" aria-label="${esc(tooltip)}" title="${esc(tooltip)}">${image}<span class="bar-label">${esc(label)}</span></button>`;
  }
  function renderTimeline(events,lo,hi) {
    const plans=P.planBars(lo,hi);
    if(!events.length&&!plans&&!P.creating) return emptyCalendar();
    events=P.arrange(events);
    const today=Math.floor(civil(now())), days=hi-lo;
    const axis=Array.from({length:days},(_,i)=>{
      const serial=lo+i,d=serialDate(serial),week=d.getUTCDay();
      const label=!S.fit||i%7===0||serial===today;
      return `<div class="axis-day ${serial===today?'today':''} ${[0,6].includes(week)?'weekend':''}" title="${shortDay(serial,true)}">${P.densityButton(serial,false,{fit:S.fit,label})}</div>`;
    }).join('');
    const groupOrder=(W.view.starred_first&&events.some(e=>e.starred)?['starred']:[]).concat(W.view.group_by==='none'?['all']:W.view.group_by==='game'?[...new Set(events.map(e=>e.game))]:['gacha','event','exchange','update','maintenance']);
    const rows=groupOrder.map(category=>{
      const definition=categories[category]||{name:category==='starred'?'★ 收藏':category==='all'?'全部活动':W.game(category)?.short_name||category,color:'var(--green)'};
      const group=events.filter(e=>{const root=e._group_event||e;return category==='starred'?root.starred:(!W.view.starred_first||!root.starred)&&(category==='all'||(W.view.group_by==='game'?root.game===category:root.category===category));});
      if(!group.length) return '';
      return `<div class="timeline-group" style="--category:${definition.color}"><span class="category-dot"></span>${definition.name}<span class="group-count">${String(group.length).padStart(2,'0')}</span></div>`+group.map(event=>{
        const markers=event.phases.filter(p=>p.at&&civil(p.at)>=lo&&civil(p.at)<hi).map(p=>`<button type="button" class="phase-mark" style="left:${(civil(p.at)-lo)/days*100}%" data-event="${event.id}" aria-label="${esc(event.title+' · '+p.title+' '+format(p.at,{year:true}))}" title="${esc(p.title+' '+format(p.at))}"></button>`).join('');
        const deadline=event.time_kind==='point'?`<strong>${esc(format(event.start.at))}</strong><span>更新节点</span>`:event.end.at?`<strong>${esc(format(event.end.at))}</strong><span>${event.end.state==='estimated'?'预计结束':countdownSlot(event.end.at)}</span>`:`<strong class="pending-label">截止待定</strong><span>${event.end.reference==='recruitment_cycles'?'按寻访次数结束':'等待后续公告'}</span>`;
        return `<div data-game="${event.game}" class="timeline-row ${event.completed?'completed':''}" style="--tree-depth:${event._tree_depth||0};--event-accent:${accent(event)};--game-color:${esc(W.game(event.game)?.color||'#6D8658')}"><div class="event-name-cell"><button type="button" class="event-name" data-event="${event.id}" style="--category:${definition.color}"><span class="event-avatar" title="点击图标只看${esc(gameLabel(event))}">${gameIcon(event.game)}</span><span class="event-name-text"><strong>${event.starred?'★ ':''}${esc(cleanTitle(event))}</strong><small><span class="game-label">${esc(gameLabel(event))}</span> · ${esc(event.subtype)} · ${esc(status(event))}</small></span></button>${P.familyControls(event)}</div><div class="bar-cell"><span class="today-column" style="--today-left:${(today-lo)/days*100}%" ${today<lo||today>=hi?'hidden':''}></span><span class="now-line" data-now-line="${lo},${days}" ${today<lo||today>=hi?'hidden':''}></span>${bar(event,lo,hi)}${markers}</div><button type="button" class="deadline-cell" data-event="${event.id}">${deadline}</button><button type="button" class="reward-cell" data-event="${event.id}" aria-label="${esc(cleanTitle(event))}的嵌晶玉奖励">${rewardBadge(event)}</button></div>`;
      }).join('');
    }).join('');
    return `<div class="timeline-scroll"><div class="timeline ${S.game==='arknights-cn'?'arknights-timeline':''} ${S.fit?'fit-version':''}" style="--days:${days};min-width:${S.fit?Math.max(1000,456+days*10):Math.max(840,296+days*28)}px"><div class="timeline-head"><div class="timeline-label">活动 / 开放状态 · 标题完整显示</div><div class="day-axis">${axis}</div><div class="timeline-deadline-head">截止 / 更新时刻<span>${esc(zone())}</span></div><div class="timeline-reward-head">嵌晶玉<span>${combined()?'仅终末地':'活动总量'}</span></div></div>${P.creationRow()}${plans}${rows}</div></div>`;
  }
  function renderList(events) {
    if(!events.length) return emptyCalendar();
    return '<div class="activity-catalog">'+P.arrange(events).map(event=>{
      const picture=artwork(event,'catalog-art');
      const end=event.time_kind==='point'?'单次更新':event.end.at?format(event.end.at,{year:true})+(event.end.state==='estimated'?' · 预计':''):'截止待定';
      return `<div class="catalog-family" style="--tree-depth:${event._tree_depth||0}"><button data-game="${event.game}" class="catalog-event ${event.completed?'completed':''}" type="button" data-event="${event.id}" style="--event-accent:${accent(event)};--game-color:${esc(W.game(event.game)?.color||'#6D8658')}"><span class="catalog-visual ${picture?'has-art':''}">${picture}<span class="catalog-game-badge">${gameIcon(event.game)}</span></span><span class="catalog-info"><small>${combined()?esc(gameLabel(event))+' · ':''}${esc(categories[event.category].name)} · ${esc(status(event))}</small><strong>${event.starred?'★ ':''}${esc(cleanTitle(event))}</strong><span>开始 ${esc(format(event.start.at,{year:true}))}${event.start.state==='estimated'?' · 预计':''}</span></span><span class="catalog-deadline"><small>${event.time_kind==='point'?'更新':'截止'}</small><strong>${esc(end)}</strong><span>${event.time_kind==='point'?'':countdownSlot(event.end.at)}</span>${rewardBadge(event)}</span><span class="catalog-arrow" aria-hidden="true">↗</span></button>${P.familyControls(event)}</div>`;
    }).join('')+'</div>';
  }
  // The month grid starts on Monday and pads with days of the neighbouring months.
  function monthGrid(monthStart,monthEnd) {
    const first=monthStart-(serialDate(monthStart).getUTCDay()+6)%7;
    return [first,first+Math.ceil((monthEnd-first)/7)*7];
  }
  // Lanes for one week: at most three bars are drawn, the rest are reached through "+N 项".
  // Shared with the hidden-activity summary so it counts what is really drawn.
  function monthWeek(events,lo,hi) {
    const visible=events.filter(e=>overlaps(e,lo,hi)).sort((a,b)=>civil(a.start.at)-civil(b.start.at)||a.title.localeCompare(b.title,'zh'));
    const ends=[],placed=[];
    for(const event of visible) {
      const start=Math.max(lo,civil(event.start.at)),end=Math.min(hi,event.time_kind==='point'?Math.floor(civil(event.start.at))+1:(event.end.at?civil(event.end.at):hi));
      let lane=ends.findIndex(value=>value<=start);
      if(lane<0) lane=ends.length;
      ends[lane]=end;
      if(lane<3) placed.push([event,lane]);
    }
    return {visible,placed};
  }
  function renderMonth(events,monthStart,monthEnd) {
    const first=monthGrid(monthStart,monthEnd)[0], weeks=Math.ceil((monthEnd-first)/7), today=Math.floor(civil(now()));
    let html='<div class="month-head">'+['一','二','三','四','五','六','日'].map(day=>`<span>周${day}</span>`).join('')+'</div>';
    for(let week=0;week<weeks;week++) {
      const lo=first+week*7,hi=lo+7;
      const {visible,placed}=monthWeek(events,lo,hi),bars=[],drawn=placed.map(([e])=>e.id);
      for(const [event,lane] of placed) {
        if(event.time_kind==='point') {
          const serial=Math.floor(civil(event.start.at)),left=(serial-lo)/7*100;
          bars.push(`<button type="button" class="event-bar" style="--category:${categories[event.category].color};--bar-left:${left}%;--bar-width:${100/7}%;--progress:0%;--bar-top:${38+lane*25}px" data-event="${event.id}" title="${esc(cleanTitle(event)+' '+format(event.start.at))}"><span class="bar-label">◆ ${esc(cleanTitle(event))}</span></button>`);
        } else bars.push(bar(event,lo,hi,{month:true,top:38+lane*25}));
      }
      const cells=Array.from({length:7},(_,i)=>{
        const day=lo+i,date=serialDate(day),extra=visible.filter(e=>overlaps(e,day,day+1)&&!drawn.includes(e.id)).length;
        return `<div class="month-cell ${day<monthStart||day>=monthEnd?'outside':''} ${day===today?'today':''}"><button type="button" class="month-date" data-day="${day}" aria-label="${shortDay(day,true)}的全部活动">${date.getUTCDate()}</button>${P.densityButton(day,true)}${P.monthPlans(day)}${extra?`<button type="button" class="month-more" data-day="${day}">+${extra} 项</button>`:''}</div>`;
      }).join('');
      html+=`<div class="month-week">${cells}${bars.join('')}</div>`;
    }
    return html;
  }
  function emptyCalendar() {
    return '<div class="empty-state"><strong>这段日期里没有符合筛选的活动</strong>可以切换日期、调整筛选，或 <button class="text-button" type="button" data-focus-version>查看版本开始日期</button>。</div>';
  }
  function openDialog(selector) {
    const dialog=$(selector);
    if(['#detail-dialog','#agenda-dialog','#reward-dialog','#rules-dialog'].includes(selector))addOfficialTimeNotice(dialog);
    if(!dialog.open) dialog.showModal();
  }
  function addOfficialTimeNotice(dialog) {
    const body=dialog.querySelector('.dialog-body');
    if(body&&!body.querySelector('.dialog-official-time-notice')) {
      const note=document.createElement('p');note.className='official-time-notice dialog-official-time-notice';
      note.textContent='具体活动情况请参考游戏官方时间';body.prepend(note);
    }
  }
  function openEvent(id) {
    if(P.creating)P.stopCreating();
    const event=allEvents().find(e=>e.id===id);
    if(!event) return;
    S.selected=id;P.selectEvent(id);
    for(const d of document.querySelectorAll('.feature-dialog[open]'))d.close();
    if($('#agenda-dialog').open) $('#agenda-dialog').close();
    renderDetail(event);openDialog('#detail-dialog');
  }
  function endpointDetail(endpoint,role,event) {
    if(role==='end'&&event.time_kind==='point') return '<div class="detail-time-value">更新节点</div><div class="detail-time-state">公告未设活动截止时间</div>';
    const value=endpoint.at?format(endpoint.at,{year:true}):'尚未公布';
    const basis=endpoint.reference==='briefing_explicit'?'官方版本导览明确':endpoint.reference==='activity_start'?'兑换开始未单列，暂按活动开始展示':endpoint.state==='unknown'?(referenceLabels[endpoint.reference]||'需要核对来源'):endpoint.state==='exact'&&event.game==='arknights-cn'?'PRTS 国服明确时间':endpointLabels[endpoint.state];
    return `<div class="detail-time-value">${esc(value)}</div><div class="detail-time-state">${esc(basis)}</div>`;
  }
  function renderDetail(event) {
    const cat=categories[event.category], isMaintenance=event.category==='maintenance', serverZone=W.game(event.game)?.timezone||'UTC';
    const upcoming=event.start.at&&Date.parse(event.start.at)>now();
    const target=upcoming?event.start.at:event.end.at;
    const countLabel=event.time_kind==='point'?'更新状态':upcoming?'距离'+(event.start.state==='estimated'?'预计开放':'开放'):'距离截止';
    const countValue=event.time_kind==='point'?(upcoming?countdown(event.start.at):'已更新'):countdown(target);
    const phases=event.phases.length?`<div class="detail-phases">${event.phases.map(phase=>`<div><strong>${esc(phase.title)}</strong><span>${esc(format(phase.at,{year:true}))}</span></div>`).join('')}</div>`:'';
    const requirements=event.requirements.length?'<p class="detail-description">参与条件：'+esc(event.requirements.join('；'))+'</p>':'';
    const version=S.data.versions.find(v=>v.id===event.version_id);
    const maintenance=isMaintenance?`<div class="maintenance-edit"><h3>确认实际开服时间</h3><p class="help-text">公告给出的是预计结束时间。确认实际开服后，这个版本中“维护后开放”的条目会一起更新。</p><form id="maintenance-form"><div class="edit-fields"><label>实际开服时间 · UTC<input id="actual-maintenance-end" type="datetime-local" value="${sourceInput(version.maintenance.actual_end)}"></label></div><div class="edit-actions"><button class="button button-primary" type="submit">保存开服记录</button><button id="clear-maintenance" class="text-button" type="button">恢复预计时间</button></div></form></div>`:'';
    const chosen=event.reminder_minutes===null?'follow':event.reminder_minutes.length===0?'off':event.reminder_minutes.length===1?String(event.reminder_minutes[0]):'custom';
    $('#detail-content').innerHTML=`<div class="dialog-header"><div><div class="eyebrow">${esc(event.version_name)} / ${esc(cat.name)}</div><h2 id="detail-title">${esc(cleanTitle(event))}</h2><div class="detail-meta"><span class="tag">${esc(status(event))}</span><span class="tag neutral">${esc(event.subtype)}</span>${event.has_overrides?'<span class="tag warning">已手动校正</span>':''}</div></div><button class="icon-button" type="button" data-close aria-label="关闭活动详情">×</button></div><div class="dialog-body">${artwork(event,'detail-hero')}<div class="help-text">你的时间 · ${esc(zone())}</div><div class="detail-time-grid"><div><div class="detail-time-label">开始</div>${endpointDetail(event.start,'start',event)}</div><div><div class="detail-time-label">${event.time_kind==='point'?'类型':'结束'}</div>${endpointDetail(event.end,'end',event)}</div></div><div class="detail-countdown"><span>${countLabel}</span><strong>${esc(countValue)}</strong></div><div class="help-text">来源时区（${esc(serverZone)}）：${event.start.at?esc(format(event.start.at,{timeZone:serverZone,year:true})):'开始待定'} → ${event.end.at?esc(format(event.end.at,{timeZone:serverZone,year:true})):event.time_kind==='point'?'更新节点':'结束待定'}</div>${phases}${rewardDetail(event)}${event.description?'<p class="detail-description">'+esc(event.description)+'</p>':''}${requirements}${event.warnings.length?'<p class="form-error">'+esc(event.warnings.join('；'))+'</p>':''}<div class="detail-actions"><button id="toggle-completed" class="button ${event.completed?'':'button-primary'}" type="button" aria-pressed="${event.completed}">${event.completed?'已完成 · 撤销':'标记已完成'}</button><button id="toggle-starred" class="button" type="button" aria-pressed="${event.starred}">${event.starred?'★ 已收藏':'☆ 收藏置顶'}</button><button id="open-event-reminders" class="button" type="button">提醒设置</button></div><label class="field-label" for="event-reminder">这个活动的提醒</label><select id="event-reminder"><option value="follow">跟随全局设置</option><option value="off">不提醒</option><option value="4320">结束前 3 天</option><option value="1440">结束前 24 小时</option><option value="60">结束前 1 小时</option>${chosen==='custom'?'<option value="custom">自定义提前量（保留）</option>':''}</select>${maintenance}<details class="source-box"><summary>查看公告原文与解析依据</summary><p class="help-text">${esc(event.source.time_label)}：${esc(event.source.time_text)}<br>标准化正文第 ${event.source.line} 行 · 规则解析，未调用 AI</p><pre>${esc(event.source.block_text)}</pre></details><a class="source-link" href="${esc(event.source.url)}" target="_blank" rel="noopener noreferrer">阅读官方公告 ↗</a><details class="edit-section"><summary>手动校正与备注</summary><p class="help-text">以下校正日期均使用 UTC。手动校正独立保存，同步公告不会覆盖。留空会恢复对应的公告时间。</p><form id="event-edit-form"><div class="edit-fields"><label>开始时间<input id="edit-start" type="datetime-local" value="${sourceInput(event.start.at)}" data-initial="${sourceInput(event.start.at)}"></label><label>结束时间<input id="edit-end" type="datetime-local" value="${sourceInput(event.end.at)}" data-initial="${sourceInput(event.end.at)}"></label></div><label class="field-label" for="edit-notes">我的备注</label><textarea id="edit-notes" maxlength="2000" placeholder="例如：周末完成剧情，兑换留到最后一天">${esc(event.notes)}</textarea><div class="edit-actions"><button class="button button-primary" type="submit">保存校正</button><button id="reset-event-times" class="text-button" type="button">恢复公告时间</button></div><div id="edit-error" class="form-error" role="alert" hidden></div></form></details></div>`;
    $('#event-reminder').value=chosen;
    if(event.source.kind==='local_json') $('#detail-content .source-link').textContent='阅读插件标注来源 ↗';
    if(event.source.kind==='zzz_official') {
      const evidence=event.source.evidence||{}, wiki=evidence.wiki;
      const panel=document.createElement('section');panel.className='wiki-evidence';
      panel.innerHTML=`<strong>绝区零官网 · 国服时间</strong><p>${esc(evidence.official_title)}<br>按公告正文活动时间解析；维护预计完成时间保留预计标记。</p><a href="${esc(evidence.maintenance_url)}" target="_blank" rel="noopener noreferrer">对应版本维护公告 ↗</a>${wiki?` · <a href="${esc(wiki.revision_url)}" target="_blank" rel="noopener noreferrer">BWIKI 头图依据 · 修订 ${esc(wiki.revision)}</a> · <a href="${esc(wiki.license.url)}" target="_blank" rel="noopener noreferrer">${esc(wiki.license.name)}</a>`:''}`;
      $('#detail-content .source-link').after(panel);
    }
    if(event.source.kind==='azurlane_wiki') {
      $('#detail-content .source-link').textContent='阅读 BWIKI 国服公告 ↗';
      $('#detail-content .source-box summary').textContent='查看国服时间与 Wiki 修订依据';
      const panel=document.createElement('section');panel.className='wiki-evidence';
      panel.innerHTML=`<strong>BWIKI · 碧蓝航线国服</strong><p>国服公告决定起止时间；英文 Wiki 可补充已核对的活动头图。${event.artwork?.shared_update?'当前图片为这篇国服更新公告的共用主视觉，并非该条目的独立宣传图。':''}已知维护日期、尚无明确时刻的截止暂按当天国服时间 10:00，并标为预计。</p><a href="${esc(event.source.evidence?.revision_url||event.source.url)}" target="_blank" rel="noopener noreferrer">查看公告固定修订 ↗</a>${event.artwork?.source_url?`<br><a href="${esc(event.artwork.source_url)}" target="_blank" rel="noopener noreferrer">查看头图出处 ↗</a>`:''}`;
      $('#detail-content .source-link').after(panel);
    }
    if(event.source.kind==='gamekee') {
      $('#detail-content .source-link').textContent='阅读 GameKee 来源公告 ↗';
      $('#detail-content .source-box summary').textContent='查看 GameKee 时间依据与原始记录';
      const panel=document.createElement('section');panel.className='wiki-evidence';
      panel.innerHTML=`<strong>GameKee · ${esc(W.game(event.game)?.server||'')}</strong><p>${event.source.evidence?.basis==='notice'?'按公告段落解析；公告明确时间优先于日历卡片，维护后开放仍为预计。':'仅有社区日历时间，公告尚未核对；不触发精确截止提醒。'}<br>社区整理与转载资料，当前未标为官方网站来源。</p><a href="${esc(event.source.calendar_url)}" target="_blank" rel="noopener noreferrer">查看来源活动日历 ↗</a>`;
      $('#detail-content .source-link').after(panel);
    }
    if(event.source.kind==='community_wiki') {
      $('#detail-content .source-box summary').textContent='查看 PRTS 原文与解析依据';
      const link=$('#detail-content .source-link');link.textContent='阅读 PRTS 活动页面 ↗';
      const panel=document.createElement('section');panel.className='wiki-evidence';
      panel.innerHTML=`<strong>PRTS 社区 Wiki · 国服</strong><p>页面修订 ${esc(event.source.revision_id)} · ${esc(event.source.revised_at||'')}<br>社区整理资料，具体变更以游戏与官方公告为准。</p><a href="${esc(event.source.revision_url)}" target="_blank" rel="noopener noreferrer">查看本次解析的固定修订 ↗</a> · <a href="${esc(event.source.license.url)}" target="_blank" rel="noopener noreferrer">${esc(event.source.license.name)}</a>`;
      link.after(panel);
    }
    if(event.artwork?.match==='official_activity_heading' && /^https:\/\/ak\.hypergryph\.com\/news\/\d+$/.test(event.artwork.source_url||'')) {
      const panel=document.createElement('section');panel.className='wiki-evidence official-art-evidence';
      panel.innerHTML=`<strong>活动图片 · 明日方舟官网</strong><p>${esc(event.artwork.source_title)}${event.artwork.shared_activity?' · 与主活动共用横幅':''}</p><a href="${esc(event.artwork.source_url)}" target="_blank" rel="noopener noreferrer">阅读图片对应的官方公告 ↗</a>`;
      $('#detail-content .source-link').after(panel);
    }
    if(event.briefing) {
      const source=event.briefing, times=source.cn_time;
      const start=times.startsAfterUpdate?'版本更新维护后':times.openTime||'未明确公布';
      const end=times.endTimeDisplay==='beforeUpdate'?'版本更新维护前':times.endTimeDisplay==='alwaysAvailable'?'常驻开放':times.closeTime||'未明确公布';
      const panel=document.createElement('section');panel.className='briefing-evidence';
      panel.innerHTML=`<strong>官方版本导览 · ${esc(source.title)}</strong><p>国服时间依据：${esc(start)} → ${esc(end)}</p><p>${source.match==='parent_activity'?'关联到同一玩法的素材，当前阶段时间沿用更新公告。':source.conflicts.length?'两份官方来源的时间存在差异，当前保留版本更新公告时间。':'已与版本更新公告核对；维护相关时间仍保留待确认标记。'}</p><a class="source-link" href="${esc(source.news_url)}" target="_blank" rel="noopener noreferrer">阅读导览上线公告 ↗</a> <a class="source-link" href="${esc(source.source_url)}" target="_blank" rel="noopener noreferrer">打开官方导览（入口随版本更新）↗</a>`;
      $('#detail-content .source-link').after(panel);
    }
    addOfficialTimeNotice($('#detail-dialog'));P.detail(event);
  }
  function openAgenda(day) {
    const events=filteredEvents().filter(e=>overlaps(e,day,day+1)).sort((a,b)=>Date.parse(a.start.at)-Date.parse(b.start.at));
    $('#agenda-title').textContent=shortDay(day,true)+' · '+events.length+' 项';
    $('#agenda-content').innerHTML=events.length?events.map(e=>`<button type="button" class="agenda-event" data-event="${e.id}" style="--category:${categories[e.category].color}"><strong><span class="category-dot"></span>${esc(cleanTitle(e))}</strong><span>${esc(format(e.start.at))} → ${e.end.at?esc(format(e.end.at)):'截止待定'} · ${esc(status(e))}</span></button>`).join(''):'<div class="empty-state">当天没有符合筛选的活动。</div>';
    $('#agenda-content').insertAdjacentHTML('beforeend',P.agendaPlans(day));
    openDialog('#agenda-dialog');
  }
  // F13: which games send deadline reminders, kept apart from what the page is showing.
  function reminderScopeText() {
    const scope=S.data.settings.reminder_scope||{mode:'all',games:[]};
    if(scope.mode==='follow')return '提醒跟随当前游戏组合';
    if(scope.mode==='custom')return scope.games?.length?'提醒：'+scope.games.map(id=>W.game(id)?.short_name||id).join('、'):'未选择提醒游戏';
    return '提醒覆盖全部已安装游戏';
  }
  function reminderScopeFields() {
    const scope=S.data.settings.reminder_scope||{mode:'all',games:null},chosen=scope.games||[];
    const games=[...S.data.games.map(g=>[g.id,g.short_name+' · '+g.server]),...chosen.filter(id=>!W.game(id)).map(id=>[id,id+'（未安装，重新安装后恢复）'])];
    const current=W.selected().map(id=>W.game(id)?.short_name||id).join('、')||'无';
    const option=(mode,label)=>`<label><input type="radio" name="reminder-scope" value="${mode}" ${scope.mode===mode?'checked':''}>${label}</label>`;
    return `<fieldset class="reminder-scope"><legend>提醒的游戏</legend>${option('all','全部已安装游戏（含以后安装的）')}${option('follow','跟随当前游戏组合（当前：'+esc(current)+'）')}${option('custom','自选游戏')}<div class="reminder-scope-games" ${scope.mode==='custom'?'':'hidden'}>${games.map(([id,label])=>`<label><input type="checkbox" data-reminder-game="${esc(id)}" ${chosen.includes(id)?'checked':''}>${esc(label)}</label>`).join('')}</div>${scope.mode==='custom'&&!chosen.length?'<p class="help-text warning">未选择任何游戏，截止提醒不会发送。</p>':''}<p class="help-text">只决定哪些游戏发送截止提醒，不受页面的搜索、类型或日期筛选影响；选“跟随当前游戏组合”时，切换日历游戏会一起改变提醒范围。单个活动的提醒设置仍然有效。</p></fieldset>`;
  }
  async function saveReminderScope(mode) {
    const scope=S.data.settings.reminder_scope||{mode:'all',games:null},list=$('.reminder-scope-games');
    // The custom list, even an empty one, is kept when switching modes; only a list that was
    // never saved (null) starts from the current combination.
    let games=scope.games;
    if(mode==='custom'&&list&&!list.hidden)games=[...document.querySelectorAll('[data-reminder-game]:checked')].map(b=>b.dataset.reminderGame);
    else if(mode==='custom'&&games===null)games=[...W.selected()];
    await mutate('/api/settings',{reminder_scope:{mode,games}});
    paintReminderSettings();
  }
  function openReminders() {
    paintReminderSettings();renderInbox();openDialog('#reminder-dialog');
  }
  function paintReminderSettings() {
    const settings=S.data.settings;
    $('#reminder-settings').innerHTML=`<label class="setting-row"><span><strong>开启截止提醒</strong><p>只为截止时间已明确、尚未完成的活动生成提醒。</p></span><input id="reminders-enabled" type="checkbox" ${settings.reminders_enabled?'checked':''}></label><div class="offset-choices" role="group" aria-label="提前多久提醒">${[[4320,'提前 3 天'],[1440,'提前 24 小时'],[60,'提前 1 小时']].map(([n,label])=>`<label><input type="checkbox" data-offset="${n}" ${settings.reminder_offsets.includes(n)?'checked':''}>${label}</label>`).join('')}</div>${reminderScopeFields()}<label class="setting-row"><span><strong>系统桌面提醒</strong><p>${S.data.native_available?'本地程序保持运行时，关闭网页后仍可交给系统通知。':'此环境暂不支持本机通知，可使用应用内或浏览器提醒。'}</p></span><input id="native-notifications" type="checkbox" ${settings.native_notifications?'checked':''} ${S.data.native_available?'':'disabled'}></label><p class="help-text">电脑关机、休眠或本地程序停止时暂停检查。重新运行后补查提醒窗口；同一活动、截止时间和提前量只提醒一次。浏览器提醒需要网页保持打开。</p><div class="settings-actions"><button id="test-reminder" class="button button-primary" type="button">发送测试提醒</button><button id="browser-notifications" class="button" type="button">${browserEnabled()?'关闭浏览器提醒':'开启浏览器提醒'}</button></div><p id="delivery-result" class="help-text"></p>`;
  }
  function renderInbox() {
    $('#notification-list').innerHTML=S.data.notifications.length?S.data.notifications.map(notice=>`<div class="notice ${notice.read?'read':''}"><span class="notice-dot"></span><${notice.event_id?'button type="button" data-event="'+esc(notice.event_id)+'"':'div'}><strong>${notice.game_name?esc(notice.game_name)+' · ':''}${esc(notice.title)}</strong><p>${esc(notice.message)}${notice.end_at?' · 截止 '+esc(format(notice.end_at,{year:true})):''}</p><small>${esc(format(notice.created_at,{year:true}))}</small></${notice.event_id?'button':'div'}></div>`).join(''):'<div class="empty-state">还没有提醒。可以发送一条测试提醒。</div>';
    const delivery=S.data.desktop_delivery;
    if($('#delivery-result')) $('#delivery-result').textContent=delivery?`${format(delivery.at)} · ${delivery.message}`:'';
  }
  function browserEnabled() {
    try { return localStorage.getItem('playtime-browser-notifications')==='true' && 'Notification' in window && Notification.permission==='granted'; }
    catch { return false; }
  }
  function deliverBrowserNotices(notices) {
    for(const notice of notices) {
      if(S.seenNotices.has(notice.id)) continue;
      S.seenNotices.add(notice.id);
      if(!browserEnabled()||notice.read) continue;
      try {
        const notification=new Notification('游时 · '+(notice.game_name?notice.game_name+' · ':'')+notice.title,{body:notice.message+(notice.end_at?' · 截止 '+format(notice.end_at,{year:true}):''),tag:notice.id});
        notification.onclick=()=>{window.focus();if(notice.event_id) openEvent(notice.event_id);};
      } catch { /* The persistent inbox remains available if the OS blocks delivery. */ }
    }
  }
  async function openRules() {
    try { S.rules=await api('/api/rules?game='+S.game);S.rulesTab='config';renderRules();openDialog('#rules-dialog'); }
    catch(error) { toast(error.message,true); }
  }
  function currentJSON() {
    return S.rulesTab==='config'?S.rules:{...activeVersion(),game:S.game,events:scopeEvents()};
  }
  function renderRules() {
    $('#show-config').setAttribute('aria-pressed',S.rulesTab==='config');
    $('#show-analysis').setAttribute('aria-pressed',S.rulesTab==='analysis');
    $('#rules-json').textContent=JSON.stringify(currentJSON(),null,2);
  }
  function downloadJSON(value,name) {
    const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
  }
  async function switchView(view) {
    if(S.view===view) return;
    S.view=view;S.fit=false;S.days=Number($('#range-select').value);
    if(narrow.matches){local.set(NARROW_VIEW,view);render();return;}
    render();await mutate('/api/settings',{view});
  }
  // Crossing the breakpoint adopts that screen class's view, except during plan drawing.
  narrow.addEventListener('change',()=>{if(!S.data||P.creating)return;S.view=preferredView();S.fit=false;render();});
  async function showAll(view='list') {
    S.categories=new Set(Object.keys(categories));S.includePending=true;
    $('#event-search').value='';$('#include-pending').checked=true;buildFilters();
    S.view=view;S.fit=false;S.days=Number($('#range-select').value);
    if(narrow.matches)local.set(NARROW_VIEW,view);
    await api('/api/settings','POST',narrow.matches?{hide_completed:false}:{hide_completed:false,view});
    await mutate('/api/workspace',{view:{hide_future:false,focus:{date:null,event_id:null}}},'PATCH');
  }
  async function fitVersion() {
    if(S.fit) {S.fit=false;S.days=Number($('#range-select').value);S.anchor=Math.floor(civil(now()));render();return;}
    await showAll('timeline');
    const events=scopeEvents().filter(e=>!e.invalid_interval);
    const starts=events.filter(e=>e.start.at).map(e=>civil(e.start.at));
    const ends=events.flatMap(e=>[e.start.at,e.end.at]).filter(Boolean).map(value=>civil(value));
    if(!starts.length) return;
    const lo=Math.floor(Math.min(...starts)),hi=Math.floor(Math.max(...ends))+1;
    if(hi-lo>120) {await showAll();toast('版本跨度超过 120 天，已用完整列表展示全部活动');return;}
    S.fit=true;S.anchor=lo;S.days=Math.max(7,hi-lo);render();
  }
  function movePeriod(direction) {
    if(S.fit) {S.fit=false;S.days=Number($('#range-select').value);}
    if(S.view==='timeline') S.anchor+=direction*S.days;
    else { const d=serialDate(S.anchor);S.anchor=Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+direction,1)/DAY; }
    render();
  }
  document.addEventListener('click',async event=>{
    const target=event.target.closest('button');
    if(!target) return;
    if(target.hasAttribute('data-close')) { target.closest('dialog').close();return; }
    if(target.dataset.event) {
      if($('#reminder-dialog').open) $('#reminder-dialog').close();
      if($('#reward-dialog').open) $('#reward-dialog').close();
      openEvent(target.dataset.event);return;
    }
    if(target.dataset.day) {if(await P.focusDay(Number(target.dataset.day)))openAgenda(Number(target.dataset.day));return;}
    if(target.dataset.unhide) {
      switch(target.dataset.unhide){
        case 'category':S.categories=new Set(Object.keys(categories));buildFilters();render();break;
        case 'search':$('#event-search').value='';render();break;
        case 'pending':S.includePending=true;$('#include-pending').checked=true;render();break;
        case 'completed':await mutate('/api/settings',{hide_completed:false});break;
        case 'future':await mutate('/api/workspace',{view:{hide_future:false}},'PATCH');break;
        case 'focus':await P.clearFocus();break;
        case 'range':await switchView('list');break;
        case 'version':S.version=target.dataset.version;S.anchor=Math.floor(civil(activeVersion().maintenance.start||now()));render();break;
      }
      return;
    }
    if(target.hasAttribute('data-focus-version')) {
      const start=activeVersion()?.maintenance.start;
      if(start) {S.anchor=Math.floor(civil(start));render();}
      return;
    }
    if(!S.data) return;
    switch(target.id) {
      case 'open-plugin-directory': {
        target.disabled=true;
        try {
          const result=await api('/api/plugins/open-directory','POST',{});
          const note=$('#plugin-directory-status');note.textContent='已打开：'+result.directory;note.hidden=false;
          toast('放入或移出完整插件文件夹后，请退出并重启游时');
        } catch(error) {toast(error.message,true);}
        finally {target.disabled=false;}
        break;
      }
      case 'reward-details':openRewardDetails();break;
      case 'previous-period':movePeriod(-1);break;
      case 'next-period':movePeriod(1);break;
      case 'today-button':S.fit=false;S.days=Number($('#range-select').value);S.anchor=Math.floor(civil(now()));render();break;
      case 'upcoming-view':await switchView('upcoming');break;
      case 'sidebar-more':{const open=!$('.sidebar').classList.contains('sidebar-expanded');local.set('sidebar.v1.narrow',open);paintSidebar();break;}
      case 'heading-toggle':local.set('heading.v1',headingCompact()?'full':'compact');paintHeading();W.renderModules();break;
      case 'more-tools':{const open=target.getAttribute('aria-expanded')!=='true';target.setAttribute('aria-expanded',String(open));target.textContent=open?'收起 ▴':'更多 ▾';$('.calendar-context').classList.toggle('links-open',open);break;}
      case 'timeline-view':await switchView('timeline');break;
      case 'month-view':await switchView('month');break;
      case 'list-view':await switchView('list');break;
      case 'fit-version':await fitVersion();break;
      case 'reset-filters':await showAll();break;
      case 'sync-button':
        if(await mutate('/api/sync',{game:S.game})) toast('正在同步当前游戏资料…');
        break;
      case 'inbox-button':case 'settings-button':case 'open-event-reminders':openReminders();break;
      case 'rules-button':await openRules();break;
      case 'show-config':S.rulesTab='config';renderRules();break;
      case 'show-analysis':S.rulesTab='analysis';renderRules();break;
      case 'download-rules':downloadJSON(currentJSON(),S.rulesTab==='config'?S.game+'.rules.json':S.game==='endfield-cn'?'endfield-version.json':S.game+'-calendar.json');break;
      case 'export-button':
        try { const data=await api('/api/export?game='+S.game);downloadJSON(data,S.game+'-calendar.json');toast('已导出规则与活动 JSON'); } catch(error) {toast(error.message,true);}
        break;
      case 'maintenance-button': {
        const maintenance=scopeEvents().find(e=>e.category==='maintenance');
        if(maintenance) openEvent(maintenance.id);else toast('这篇公告没有可识别的维护时间');
        break;
      }
      case 'toggle-starred': {
        const e=allEvents().find(e=>e.id===S.selected);
        if(await mutate('/api/events/'+e.id,{starred:!e.starred},'PATCH')) renderDetail(allEvents().find(item=>item.id===e.id));
        break;
      }
      case 'toggle-completed': {
        const e=allEvents().find(e=>e.id===S.selected);
        if(await mutate('/api/events/'+e.id,{completed:!e.completed},'PATCH')) renderDetail(allEvents().find(item=>item.id===e.id));
        break;
      }
      case 'reset-event-times':
        if(await mutate('/api/events/'+S.selected,{clear_overrides:true},'PATCH')) {renderDetail(allEvents().find(e=>e.id===S.selected));toast('已恢复公告时间');}
        break;
      case 'clear-maintenance': {
        const e=allEvents().find(e=>e.id===S.selected);
        if(await mutate('/api/versions/'+e.version_id+'/maintenance',{actual_end:null})) {renderDetail(allEvents().find(item=>item.id===S.selected));toast('已恢复预计开服时间');}
        break;
      }
      case 'test-reminder':
        if(await mutate('/api/notifications/test')) toast('测试提醒已写入收件箱');
        break;
      case 'read-notifications':
        if(await mutate('/api/notifications/read')) renderInbox();
        break;
      case 'browser-notifications':
        try {
          if(browserEnabled()) {localStorage.setItem('playtime-browser-notifications','false');target.textContent='开启浏览器提醒';toast('已关闭此浏览器的提醒');}
          else if(!('Notification' in window)) toast('此浏览器不支持系统通知，请使用应用内提醒',true);
          else {
            const permission=await Notification.requestPermission();
            if(permission==='granted') {localStorage.setItem('playtime-browser-notifications','true');target.textContent='关闭浏览器提醒';toast('已开启，网页保持打开时可接收浏览器提醒');}
            else toast('浏览器未授予通知权限，应用内提醒仍然可用');
          }
        } catch(error) {toast(error.message,true);}
        break;
    }
  });
  document.addEventListener('change',async event=>{
    const target=event.target;
    if(!S.data) return;
    if(target.dataset.category) {
      if(target.checked) S.categories.add(target.dataset.category);else S.categories.delete(target.dataset.category);
      render();return;
    }
    if(target.name==='reminder-scope') {await saveReminderScope(target.value);return;}
    if(target.dataset.reminderGame) {await saveReminderScope('custom');return;}
    if(target.dataset.offset) {
      const offsets=[...document.querySelectorAll('[data-offset]:checked')].map(input=>Number(input.dataset.offset));
      await mutate('/api/settings',{reminder_offsets:offsets});return;
    }
    if(target.dataset.selectGame) {
      const selected_games=[...document.querySelectorAll('[data-select-game]:checked')].map(el=>el.dataset.selectGame);
      await selectScope('selected',selected_games);return;
    }
    switch(target.id) {
      case 'hide-future':await mutate('/api/workspace',{view:{hide_future:target.checked}},'PATCH');break;
      case 'event-sort':await mutate('/api/workspace',{view:{sort:target.value}},'PATCH');break;
      case 'event-group':await mutate('/api/workspace',{view:{group_by:target.value}},'PATCH');break;
      case 'game-select':
        for(const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
        await selectScope(target.value,target.value==='all'?S.data.games.map(g=>g.id):target.value==='selected'?W.selected():[target.value],true);break;
      case 'version-select':S.version=target.value;S.anchor=Math.floor(civil(activeVersion().maintenance.start||now()));render();break;
      case 'timezone-select':await mutate('/api/settings',{timezone:target.value});if(W.view.focus.date)await mutate('/api/workspace',{view:{focus_timezone:zone()}},'PATCH');break;
      case 'range-select':S.fit=false;S.days=Number(target.value);render();break;
      case 'hide-completed':await mutate('/api/settings',{hide_completed:target.checked});break;
      case 'include-pending':S.includePending=target.checked;render();break;
      case 'reminders-enabled':await mutate('/api/settings',{reminders_enabled:target.checked});break;
      case 'native-notifications':await mutate('/api/settings',{native_notifications:target.checked});break;
      case 'sync-interval':await mutate('/api/settings',{sync_interval_minutes:Number(target.value)});break;
      case 'event-reminder': {
        if(target.value==='custom') break;
        const reminder_minutes=target.value==='follow'?null:target.value==='off'?[]:[Number(target.value)];
        if(await mutate('/api/events/'+S.selected,{reminder_minutes},'PATCH')) toast('活动提醒设置已保存');
        break;
      }
    }
  });
  document.addEventListener('submit',async event=>{
    if(event.target.id==='event-edit-form') {
      event.preventDefault();
      const patch={notes:$('#edit-notes').value};
      for(const role of ['start','end']) {
        const input=$('#edit-'+role);
        if(input.value!==input.dataset.initial) patch[role+'_override']=input.value?input.value+':00Z':null;
      }
      if(await mutate('/api/events/'+S.selected,patch,'PATCH')) {renderDetail(allEvents().find(e=>e.id===S.selected));toast('校正与备注已保存在本机');}
    } else if(event.target.id==='maintenance-form') {
      event.preventDefault();
      const value=$('#actual-maintenance-end').value;
      if(!value) {toast('请先填写实际开服时间',true);return;}
      const selected=allEvents().find(e=>e.id===S.selected);
      if(await mutate('/api/versions/'+selected.version_id+'/maintenance',{actual_end:value+':00Z'})) {renderDetail(allEvents().find(e=>e.id===S.selected));toast('已更新本版本所有关联开服时间');}
    }
  });
  document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{
    if(event.target!==dialog) return;
    const rect=dialog.getBoundingClientRect();
    if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom) dialog.close();
  }));
  async function refresh() {
    if(S.scoping)return;
    const generation=S.scopeGeneration;
    try {const data=await api('/api/state');if(generation===S.scopeGeneration)applyState(data);}
    catch(error) {
      if(!S.data) {$('#load-error').textContent='无法连接本地程序：'+error.message+'。请运行 start.ps1 或 python app.py。';$('#load-error').hidden=false;}
      else {$('#source-status').textContent='本地连接已断开，请检查程序是否仍在运行。现有日历保留显示。';$('#source-status').classList.add('warning');}
    }
  }
  $('#event-search').addEventListener('input',()=>{if(S.data) render();});
  document.addEventListener('error',event=>{
    if(event.target instanceof HTMLImageElement&&event.target.classList.contains('activity-art')) {
      event.target.parentElement?.classList.remove('has-art','with-image');event.target.remove();
    }
  },true);
  async function preparePlanning(){
    if(S.scoping)throw new Error('正在切换游戏，请稍候再创建规划。');
    if(narrow.matches)local.set(NARROW_VIEW,'timeline');else await api('/api/settings','POST',{view:'timeline'});
    S.view='timeline';
    const layout=structuredClone(S.data.workspace.layout),calendar=layout.modules.find(m=>m.id==='calendar');
    if(calendar.collapsed){calendar.collapsed=false;if(!await mutate('/api/workspace',{layout},'PATCH'))throw new Error('请先展开日历模块。');}
    render();
  }
  P.init({state:()=>S.data,game:()=>S.game,zone,dateParts,format,civil,range,isTimeline:()=>S.view==='timeline',timelineEvents:()=>{const[lo,hi]=range();return P.arrange(filteredEvents().filter(e=>overlaps(e,lo,hi)));},prepareTimeline:preparePlanning,render,toast,baseEvents:baseFilteredEvents,events:filteredEvents,api,mutate,applyState,openDialog,openAgenda,openEvent});
  window.PlaytimeCustomize.init({state:()=>S.data,mutate,openDialog,api,refresh,toast});
  refresh();
  S.pollTimer=setInterval(refresh,10000);
  setInterval(()=>{if(S.data){renderClock();syncTime();}},30000);
  window.addEventListener('focus',()=>{if(S.initialized) refresh();});
})();
