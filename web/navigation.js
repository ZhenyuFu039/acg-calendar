'use strict';
/* Host UI composition. Provider data and persistent state remain owned by the existing APIs. */
window.PlaytimeUX=(()=>{
  let H;
  const $=s=>document.querySelector(s),W=window.PlaytimeWorkspace;
  const E=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function label(id,text){const node=$(id);if(!node)return;const span=node.querySelector('span:not(.unread)');if(span)span.textContent=text;else node.textContent=text;node.setAttribute('aria-label',text);node.title=text;}
  function init(host){
    H=host;document.body.classList.add('ux-workspace');
    const pageTitle=$('.page-heading h1'),visualTitle=document.createElement('h2');visualTitle.textContent=pageTitle.textContent;pageTitle.replaceWith(visualTitle);pageTitle.classList.add('sr-only');$('#main-content').prepend(pageTitle);
    const skip=document.createElement('a');skip.href='#calendar';skip.className='skip-link';skip.textContent='跳到活动日程';document.body.prepend(skip);$('#calendar').tabIndex=-1;
    label('#rail-games','游戏管理');label('#workspace-button','设置');label('#data-button','同步详情');
    $('#rail-games').removeAttribute('aria-pressed');$('#rail-games').title='管理游戏、区服与日历插件';$('#rail-games').disabled=true;
    const plans=document.createElement('button');plans.id='rail-plans';plans.className='rail-action';plans.type='button';plans.disabled=true;plans.innerHTML='<span aria-hidden="true">◇</span><span>个人计划</span>';plans.setAttribute('aria-label','个人计划');$('#rail-games').before(plans);
    const actions=$('.header-actions');actions.prepend($('#data-button'));actions.prepend($('#inbox-button'));
    const timezone=$('.timezone-label');timezone.classList.add('header-timezone');actions.prepend(timezone);
    actions.prepend($('#official-time-notice'));
    const filter=document.createElement('button');filter.id='filter-button';filter.type='button';filter.className='button button-small';filter.innerHTML='<span class="filter-label">范围与筛选</span><span class="mobile-filter-label">筛选</span>';filter.setAttribute('aria-label','范围与筛选');filter.setAttribute('aria-expanded','false');$('.catalog-tools').prepend(filter);
    $('.sidebar').id='calendar-filters';filter.setAttribute('aria-controls','calendar-filters');
    $('.catalog-tools').append($('#more-tools'));
    $('[data-preference-panel="layout"]').prepend($('#heading-toggle'));
    const timeField=document.createElement('label');timeField.className='settings-timezone field-label';timeField.textContent='显示时区';const timeSelect=$('#timezone-select').cloneNode(true);timeSelect.id='settings-timezone';timeField.append(timeSelect);$('.preference-tabs').before(timeField);
    timeSelect.addEventListener('change',()=>{$('#timezone-select').value=timeSelect.value;$('#timezone-select').dispatchEvent(new Event('change',{bubbles:true}));});
    const placeholder=document.createElement('div');placeholder.className='filter-placeholder';placeholder.hidden=true;$('.sidebar').before(placeholder);
    const inertBefore=new Map();
    function sheet(opening){
      const sidebar=$('.sidebar');
      if(opening){placeholder.style.height=sidebar.getBoundingClientRect().height+'px';sidebar.classList.remove('filters-reduced');$('#view-filters').open=true;}
      placeholder.hidden=!opening;sidebar.setAttribute('role',opening?'dialog':'complementary');
      if(opening)sidebar.setAttribute('aria-modal','true');else sidebar.removeAttribute('aria-modal');
      filter.setAttribute('aria-expanded',String(opening));
      for(const selector of ['.main-content','.app-header','.workspace-rail','.skip-link']){const node=$(selector);if(opening){if(!inertBefore.has(node))inertBefore.set(node,node.inert);node.inert=true;}else if(inertBefore.has(node)){node.inert=inertBefore.get(node);inertBefore.delete(node);}}
      if(opening)queueMicrotask(()=>$('#sidebar-more').focus({preventScroll:true}));else if(!$('dialog[open]'))filter.focus({preventScroll:true});
    }
    document.addEventListener('click',e=>{
      if(!matchMedia('(max-width:850px)').matches)return;
      if(e.target.closest('#manage-plugins')&&$('.sidebar.sidebar-expanded'))$('#sidebar-more').click();
      if(e.target.closest('#sidebar-more'))sheet(!$('.sidebar').classList.contains('sidebar-expanded'));
    },true);
    window.addEventListener('resize',()=>{if(!placeholder.hidden&&!matchMedia('(max-width:850px)').matches){$('.sidebar').classList.remove('sidebar-expanded');$('#sidebar-more').setAttribute('aria-expanded','false');sheet(false);}});
    $('#calendar-expand').insertAdjacentHTML('beforeend','<span class="focus-label">专注时间轴</span>');$('#calendar-expand').setAttribute('aria-label','专注时间轴');
    $('#month-view').textContent='截止总览';$('#list-view').textContent='活动列表';$('#reset-filters').textContent='清除筛选';
    $('#fit-version').title='仅调整日期范围，不会清除筛选';
    const guide=$('.calendar-legend');guide.insertAdjacentHTML('afterbegin','<span>截止总览只统计明确的未完成截止；数量不代表游玩耗时。</span>');
    const install=document.createElement('div');install.className='install-calendar';
    for(const id of ['#open-plugin-directory','#plugin-directory-help','#plugin-directory-status'])install.append($(id));
    $('[data-preference-panel="plugins"]').prepend(install);
    $('#manage-plugins').textContent='管理游戏与插件 ↗';
    const backupTab=document.createElement('button');backupTab.type='button';backupTab.dataset.preferenceTab='backup';backupTab.setAttribute('role','tab');backupTab.setAttribute('aria-selected','false');backupTab.textContent='备份与恢复';$('.preference-tabs').append(backupTab);
    const backupPanel=document.createElement('section');backupPanel.dataset.preferencePanel='backup';backupPanel.hidden=true;backupPanel.append($('.backup-section'));$('.preference-tabs').parentElement.insertBefore(backupPanel,$('#preferences-error'));
    const net=$('[data-preference-panel="network"]'),results=document.createElement('section');results.id='sync-results';results.setAttribute('aria-label','各游戏资料与同步结果');net.prepend(results);
    const advanced=document.createElement('details');advanced.className='network-advanced';advanced.innerHTML='<summary>高级采集限制与站点诊断</summary>';
    for(const node of [net.querySelector('.preference-grid'),$('#save-network'),$('#network-sites')])advanced.append(node);net.append(advanced);
    const schedules=document.createElement('details');schedules.className='sync-schedules';schedules.innerHTML='<summary>查看各游戏自动复查排程</summary>';$('#sync-schedule').before(schedules);schedules.append($('#sync-schedule'));
    const rules=document.createElement('details');rules.id='reminder-rules';rules.innerHTML='<summary>提醒规则与通知权限</summary>';rules.append($('#reminder-settings'));$('#reminder-dialog .dialog-body').append(rules);
    const settingsReminder=document.createElement('button');settingsReminder.type='button';settingsReminder.className='button';settingsReminder.id='notification-settings';settingsReminder.textContent='提醒规则';$('.preference-tabs').after(settingsReminder);
    const saveHint=document.createElement('p');saveHint.className='help-text settings-save-hint';saveHint.textContent='主题与布局需要点击应用；提醒、游戏启停和采集周期自动保存。';$('#preferences-error').before(saveHint);
    document.addEventListener('click',async e=>{
      const b=e.target.closest('button');if(!b)return;
      if(b.id==='rail-plans')$('#plans-button').click();
      if(b.id==='rail-calendar'&&$('dialog[open]'))$('dialog[open]').close();
      if(b.id==='notification-settings'){$('#preferences-dialog').close();H.openReminders();rules.open=true;}
      if(b.id==='inbox-button')rules.open=false;
      if(b.id==='source-details')$('#data-button').click();
      if(b.id==='filter-button'){
        if(matchMedia('(max-width:850px)').matches)$('#sidebar-more').click();
        else if(document.body.dataset.sidebar==='hidden'){const layout=structuredClone(H.state().workspace.layout);layout.sidebar.visible=true;await H.mutate('/api/workspace',{layout},'PATCH');$('#view-filters').open=true;}
        else {const sidebar=$('.sidebar');sidebar.classList.toggle('filters-reduced');$('#view-filters').open=!sidebar.classList.contains('filters-reduced');}
        b.setAttribute('aria-expanded',String($('#view-filters').open));
      }
    });
    document.addEventListener('keydown',e=>{
      if($('dialog[open]')||!$('.sidebar.sidebar-expanded')||!matchMedia('(max-width:850px)').matches)return;
      if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();$('#sidebar-more').click();}
      if(e.key==='Tab'){const nodes=[...$('.sidebar').querySelectorAll('button,select,input,summary,a[href],[tabindex]')].filter(n=>!n.disabled&&n.tabIndex>=0&&n.checkVisibility()),i=nodes.indexOf(document.activeElement);if((e.shiftKey&&i<=0)||(!e.shiftKey&&i===nodes.length-1)){e.preventDefault();nodes[e.shiftKey?nodes.length-1:0]?.focus();}}
    },true);
  }
  function update(state){
    if(!state)return;
    const scope=H.game()==='all'?'全部已安装游戏':H.game()==='selected'?W.selected().map(id=>W.game(id)?.short_name||id).join('、')||'空组合':W.game(H.game())?.short_name||H.game();
    $('#sync-button').title='同步范围：'+scope;
    $('#rail-plans').disabled=false;
    $('#rail-games').disabled=false;
    const games=state.games.filter(g=>H.game()==='all'||(H.game()==='selected'?W.selected().includes(g.id):g.id===H.game()));
    const statuses=games.map(g=>state.sync_by_game?.[g.id]||{}),running=statuses.filter(s=>s.running).length,failed=statuses.filter(s=>s.error&&!s.running).length;
    const brief=running?`${running} 款正在同步 · 现有日历可用`:failed?`${failed} 款同步未完成 · 保留已有日历`:`${games.length} 款本地日历 · 倒计时无需联网`;
    const versionFor=g=>H.game()===g.id?H.version():state.versions.find(v=>v.game===g.id);
    const message=games.length===1?(versionFor(games[0])?.source?.label||games[0].source_label)+' · '+(H.progress(statuses[0])||brief):brief;
    $('#settings-timezone').value=$('#timezone-select').value;
    const source=$('#source-status');source.setAttribute('aria-label','日历与同步状态');source.classList.toggle('warning',failed>0);source.title=message;
    const statusHTML=`<span class="status-point"></span><span>${E(message)}</span><button type="button" id="source-details" class="text-button">资料与同步详情 ↗</button>`;
    if(source.innerHTML!==statusHTML)source.innerHTML=statusHTML;
    const cards=games.map(g=>{
      const s=state.sync_by_game?.[g.id]||{},v=versionFor(g),source=v?.source||{};
      const progress=H.progress(s),stamp=source.fetched_at?H.format(source.fetched_at,{year:true}):'尚未记录';
      const material=source.origin==='snapshot'?'内置快照':source.origin==='empty'?'暂无资料':'本地缓存';
      return `<article class="sync-result"><h3>${E(g.name)}</h3><dl><dt>配置来源</dt><dd>${E(g.source_label)}</dd><dt>当前资料</dt><dd>${E(material)} · ${E(stamp)}</dd><dt>最近成功</dt><dd>${E(s.last_success?H.format(s.last_success,{year:true}):'未有成功同步记录')}</dd><dt>本轮结果</dt><dd>${E(progress||(s.error?'本轮失败，原有日历保留':'尚未取得新的同步结果'))}</dd></dl>${s.error?`<p class="sync-error">${E(s.error)}</p>`:''}${v?.warnings?.length?`<details><summary>${v.warnings.length} 项资料提示</summary><p>${v.warnings.map(E).join('<br>')}</p></details>`:''}</article>`;
    }).join('')||'<p>当前组合没有游戏。请在日程范围中选择要查看的游戏。</p>';
    const results=$('#sync-results');if(results.innerHTML!==cards)results.innerHTML=cards;
  }
  function detail(){
    const body=$('#detail-content .dialog-body'),actions=body?.querySelector('.detail-actions');if(!actions)return;
    const times=body.querySelector('.detail-time-grid'),countdown=body.querySelector('.detail-countdown');times.after(countdown);countdown.after(actions);
    const source=body.querySelector('.source-link');source.textContent='查看来源原文 ↗';actions.append(source);
    const explanation=document.createElement('details');explanation.className='detail-explanation';explanation.innerHTML='<summary>活动说明与参与条件</summary>';
    for(const node of [...body.querySelectorAll('.detail-description')])explanation.append(node);if(explanation.children.length>1)body.querySelector('.source-box').before(explanation);
    const reminder=document.createElement('details');reminder.className='detail-reminder';reminder.innerHTML='<summary>此活动的提醒设置</summary>';
    const select=$('#event-reminder'),field=body.querySelector('label[for="event-reminder"]');if(field)reminder.append(field);reminder.append(select);const global=$('#open-event-reminders');if(global)reminder.append(global);body.querySelector('.source-box').before(reminder);
  }
  return {init,update,detail,get ready(){return Boolean(H);}};
})();
