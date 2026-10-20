'use strict';
/* Host preferences only: no provider metadata, scripts or plugin formats are changed. */
window.PlaytimeCustomize=(()=>{
  const $=s=>document.querySelector(s),W=window.PlaytimeWorkspace;
  const names={calendar:'日历 / 时间轴',sources:'来源说明',rewards:'活动奖励',deadlines:'近期截止','personal-plans':'个人计划'};
  const colorGroups={surfaces:{background:'页面背景',surface:'主要表面',panel_alt:'次级表面',text:'正文',muted:'次要文字',border:'分隔线'},signals:{accent:'强调块',accent_text:'强调块文字',secondary:'辅助强调',link:'链接与焦点',nav_background:'导航背景',nav_text:'导航文字'},events:{bar_background:'时间条底色',bar_text:'时间条文字',event_color:'活动',gacha_color:'寻访 / 调频',exchange_color:'兑换',update_color:'玩法更新',maintenance_color:'维护'}};
  const colors=Object.assign({},...Object.values(colorGroups));
  const metrics=['font_size','radius','control_radius','desktop_radius','desktop_control_radius','spacing','border_width','heading_weight','texture_opacity','card_opacity'];
  let host,layout,tab='layout',opened=false,themeBase='light';
  function layoutFields(){layout.sidebar={visible:$('#sidebar-visible').checked,position:$('#sidebar-position').value,width:Number($('#sidebar-width').value)};layout.desktop_calendar=$('#desktop-calendar-position').value;}
  const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function paintLayout(){
    $('#layout-rows').innerHTML=layout.modules.map((r,i)=>`<div class="layout-option" data-layout-row="${r.id}" draggable="true"><strong>${names[r.id]}</strong><label><input data-layout-visible="${r.id}" type="checkbox" ${r.visible?'checked':''} ${r.id==='calendar'?'disabled':''}> 显示</label><label><input data-layout-fold="${r.id}" type="checkbox" ${r.collapsed?'checked':''}> 折叠</label><button type="button" data-layout-move="${i}" data-step="-1" aria-label="上移${names[r.id]}" ${i===0?'disabled':''}>↑</button><button type="button" data-layout-move="${i}" data-step="1" aria-label="下移${names[r.id]}" ${i===layout.modules.length-1?'disabled':''}>↓</button></div>`).join('');
    $('#sidebar-visible').checked=layout.sidebar.visible;$('#sidebar-position').value=layout.sidebar.position;$('#sidebar-width').value=layout.sidebar.width;$('#desktop-calendar-position').value=layout.desktop_calendar;
  }
  function paintTheme(theme){
    const presets=host.state().workspace_defaults.themes,labels=host.state().workspace_defaults.theme_names||{};
    themeBase=theme.preset;
    const custom=Object.keys(presets[theme.preset]).some(key=>theme[key]!==presets[theme.preset][key]);
    $('#theme-preset').innerHTML=Object.keys(presets).map(id=>`<option value="${id}">${escape(labels[id]||id)}</option>`).join('')+(custom?'<option value="custom" disabled>自定义 · 当前</option>':'');
    $('#theme-preset').value=custom?'custom':theme.preset;
    for(const key of Object.keys(colors))$('#theme-'+key).value=theme[key];
    for(const key of metrics)$('#theme-'+key).value=key==='card_opacity'?Math.round(theme[key]*100):theme[key];
    $('#theme-texture').value=theme.texture;
    preview();
  }
  function themeValue(){
    const theme={schema_version:1,preset:$('#theme-preset').value==='custom'?themeBase:$('#theme-preset').value};
    for(const key of Object.keys(colors))theme[key]=$('#theme-'+key).value;
    for(const key of metrics)theme[key]=Number($('#theme-'+key).value)/(key==='card_opacity'?100:1);
    theme.texture=$('#theme-texture').value;
    return theme;
  }
  function preview(){
    const theme=themeValue(),preview=$('#theme-preview');
    preview.style.background=theme.background;preview.style.color=theme.text;preview.style.borderColor=theme.border;
    preview.querySelector('article').style.cssText=`background:color-mix(in srgb,${theme.surface} ${theme.card_opacity*100}%,transparent);border-radius:${theme.radius}px;padding:${theme.spacing}px;font-size:${theme.font_size}px`;
    preview.querySelector('small').style.color=theme.muted;
    preview.querySelector('.preview-nav').style.cssText=`background:${theme.nav_background};color:${theme.nav_text};border-color:${theme.border}`;
    preview.querySelector('.preview-signal').style.cssText=`background:${theme.accent};color:${theme.accent_text};border-radius:${theme.control_radius}px`;
    preview.querySelector('.preview-track').style.cssText=`background:${theme.bar_background};color:${theme.bar_text};border-left:4px solid ${theme.event_color}`;
    preview.querySelector('strong').style.fontWeight=theme.heading_weight;
  }
  function network(){
    if(!host?.state())return;
    const n=host.state().network;if(!n)return;
    const interval=$('#sync-interval'),minutes=host.state().settings.sync_interval_minutes;
    if(document.activeElement!==interval){if(![...interval.options].some(o=>Number(o.value)===minutes))interval.add(new Option('每 '+minutes+' 分钟',minutes));interval.value=minutes;}
    $('#network-summary').textContent=`${n.paused?'联网已暂停':'联网已启用'} · ${n.active} 个请求执行中 · ${n.waiting} 个等待中 · 本次命中缓存 ${n.cache_hits} 次`;
    $('#network-toggle').textContent=n.paused?'恢复联网':'暂停联网';
    $('#network-sites').innerHTML=n.sites.length?n.sites.map(s=>`<div class="network-site"><strong>${escape(s.site)}</strong><span>${s.paused?'已暂停':s.active?'请求中':s.next_allowed_at?'等待 / 冷却':'可请求'} · 本小时 ${s.requests}/${n.policy.hourly_limit}</span><p>${s.next_allowed_at?'下次允许请求：'+escape(new Date(s.next_allowed_at).toLocaleString()):'遵守站点配额与请求间隔'}${s.error?' · '+escape(s.error):''}</p><button type="button" data-network-site="${escape(s.site)}" data-paused="${!s.paused}" class="button button-small">${s.paused?'恢复该站':'暂停该站'}</button></div>`).join(''):'<p class="help-text">尚未发起联网请求。离线快照和已有图片仍可显示。</p>';
  }
  function select(value){tab=value;for(const node of document.querySelectorAll('[data-preference-panel]'))node.hidden=node.dataset.preferencePanel!==tab;for(const b of document.querySelectorAll('[data-preference-tab]'))b.setAttribute('aria-selected',String(b.dataset.preferenceTab===tab));$('#preferences-title').textContent=tab==='network'?'数据与同步':'外观与布局';}
  function open(value='layout'){
    if(!host.state())return;
    layout=structuredClone(host.state().workspace.layout);paintLayout();paintTheme(host.state().workspace.theme);
    const n=host.state().network;for(const key of Object.keys(n.policy))$('#network-'+key).value=n.policy[key];
    $('#preferences-error').textContent='';opened=true;select(value);network();host.openDialog('#preferences-dialog');
    $('#workspace-button').setAttribute('aria-expanded',String(value!=='network'));$('#data-button').setAttribute('aria-expanded',String(value==='network'));
  }
  function download(value){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));a.download='playtime-theme.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  return {
    init(api){
      const narrow=matchMedia('(max-width:850px)'),filters=$('#view-filters');filters.open=!narrow.matches;narrow.addEventListener('change',e=>{filters.open=!e.matches;});
      for(const [group,fields] of Object.entries(colorGroups))$('#theme-'+group).innerHTML=Object.entries(fields).map(([key,label])=>`<label>${label}<input id="theme-${key}" type="color"></label>`).join('');
      host=api;W.configure(layout=>host.mutate('/api/workspace',{layout},'PATCH'));
      $('#preferences-dialog').addEventListener('close',()=>{opened=false;$('#workspace-button').setAttribute('aria-expanded','false');$('#data-button').setAttribute('aria-expanded','false');});
      document.addEventListener('click',async e=>{
        const b=e.target.closest('button');if(!b)return;
        if(b.id==='workspace-button'){open('theme');return;}
        if(b.id==='data-button'){open('network');return;}
        if(b.id==='rail-calendar'){document.querySelector('#calendar-expand').focus({preventScroll:true});return;}
        if(b.id==='rail-games'){const next=structuredClone(host.state().workspace.layout);next.sidebar.visible=!next.sidebar.visible;await host.mutate('/api/workspace',{layout:next},'PATCH');return;}
        if(b.dataset.preferenceTab){select(b.dataset.preferenceTab);return;}
        if(b.dataset.layoutMove){layoutFields();const i=Number(b.dataset.layoutMove),to=i+Number(b.dataset.step);layout.modules.splice(to,0,layout.modules.splice(i,1)[0]);paintLayout();return;}
        if(b.dataset.networkSite){await host.mutate('/api/network',{site:b.dataset.networkSite,paused:b.dataset.paused==='true'},'PATCH');network();return;}
        switch(b.id){
          case 'save-layout':{
            layoutFields();
            if(await host.mutate('/api/workspace',{layout},'PATCH'))$('#preferences-dialog').close();break;}
          case 'reset-layout':layout=structuredClone(host.state().workspace_defaults.layout);paintLayout();if(await host.mutate('/api/workspace',{layout},'PATCH'))$('#preferences-error').textContent='布局已恢复默认，主题与个人标记保持不变。';break;
          case 'edit-layout':$('#preferences-dialog').close();W.edit(true);$('#finish-layout').hidden=false;break;
          case 'finish-layout':W.edit(false);b.hidden=true;break;
          case 'save-theme':if(await host.mutate('/api/workspace',{theme:themeValue()},'PATCH'))$('#preferences-dialog').close();else $('#preferences-error').textContent='主题未保存，请检查颜色对比度与数值范围。';break;
          case 'reset-theme':if(await host.mutate('/api/workspace',{theme:host.state().workspace_defaults.theme},'PATCH')){paintTheme(host.state().workspace.theme);$('#preferences-error').textContent='主题已恢复默认，布局保持不变。';}break;
          case 'export-theme':download(themeValue());break;
          case 'network-toggle':await host.mutate('/api/network',{paused:!host.state().network.paused},'PATCH');network();break;
          case 'save-network':{const policy={};for(const key of Object.keys(host.state().network.policy))policy[key]=Number($('#network-'+key).value);if(await host.mutate('/api/network',{policy},'PATCH'))$('#preferences-error').textContent='采集限制已保存，现有冷却时间和额度不会被清除。';break;}
        }
      });
      $('#layout-rows').addEventListener('change',e=>{const id=e.target.dataset.layoutVisible||e.target.dataset.layoutFold;if(!id)return;layout.modules.find(r=>r.id===id)[e.target.dataset.layoutVisible?'visible':'collapsed']=e.target.checked;});
      $('#layout-rows').addEventListener('dragstart',e=>{const row=e.target.closest('[data-layout-row]');if(row)e.dataTransfer.setData('text/plain',row.dataset.layoutRow);});
      $('#layout-rows').addEventListener('dragover',e=>e.preventDefault());
      $('#layout-rows').addEventListener('drop',e=>{e.preventDefault();layoutFields();const id=e.dataTransfer.getData('text/plain'),to=e.target.closest('[data-layout-row]')?.dataset.layoutRow;const a=layout.modules.findIndex(r=>r.id===id),b=layout.modules.findIndex(r=>r.id===to);if(a>=0&&b>=0){layout.modules.splice(b,0,layout.modules.splice(a,1)[0]);paintLayout();}});
      $('#theme-fields').addEventListener('input',preview);
      $('#theme-preset').addEventListener('change',()=>paintTheme(host.state().workspace_defaults.themes[$('#theme-preset').value]));
      $('#import-theme').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>10000)throw Error('主题文件超过 10 KB');const theme=JSON.parse(await f.text());if(await host.mutate('/api/workspace',{theme},'PATCH')){paintTheme(host.state().workspace.theme);$('#preferences-error').textContent='主题已导入并保存。';}}catch(error){$('#preferences-error').textContent='导入失败：'+error.message;}e.target.value='';});
    },
    open,update(){if(opened&&tab==='network')network();}
  };
})();
