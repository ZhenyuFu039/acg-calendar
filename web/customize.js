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
    $('#sync-schedule').innerHTML='<strong>下次自动复查（本机时间）</strong>'+Object.entries(host.state().sync_schedule||{}).map(([id,s])=>`<p>${escape(host.state().games.find(g=>g.id===id)?.name||id)}：${s.enabled?escape(new Date(s.next_check_at).toLocaleString()):'自动复查已由启动参数关闭'}</p>`).join('');
    $('#network-toggle').textContent=n.paused?'恢复联网':'暂停联网';
    $('#network-sites').innerHTML=n.sites.length?n.sites.map(s=>`<div class="network-site"><strong>${escape(s.site)}</strong><span>${s.paused?'已暂停':s.active?'请求中':s.next_allowed_at?'等待 / 冷却':'可请求'} · 本小时 ${s.requests}/${n.policy.hourly_limit}</span><p>${s.next_allowed_at?'下次允许请求：'+escape(new Date(s.next_allowed_at).toLocaleString()):'遵守站点配额与请求间隔'}${s.error?' · '+escape(s.error):''}</p><button type="button" data-network-site="${escape(s.site)}" data-paused="${!s.paused}" class="button button-small">${s.paused?'恢复该站':'暂停该站'}</button></div>`).join(''):'<p class="help-text">尚未发起联网请求。离线快照和已有图片仍可显示。</p>';
  }
  const reasons={manual:'手动备份',upgrade:'升级前自动备份','before-restore':'恢复前自动备份','before-clear':'清除记录前自动备份'};
  function backupStatus(text,error=false){const node=$('#backup-status');node.textContent=text;node.classList.toggle('error',error);}
  function paintBackups(rows){
    $('#backup-list').innerHTML=rows.length?rows.map(b=>{
      if(b.error)return `<div class="backup-row"><strong>${escape(b.name)}</strong><span>${escape(b.error)}</span></div>`;
      const c=b.counts||{};
      return `<div class="backup-row"><strong>${escape(new Date(b.created_at).toLocaleString())} · ${escape(reasons[b.reason]||b.reason)}</strong><span>v${escape(b.app_version)}${b.note?' · '+escape(b.note):''} · 收藏 ${c.starred||0} · 完成 ${c.completed||0} · 备注 ${c.notes||0} · 校正 ${c.corrections||0} · 计划 ${c.plans||0}</span><a class="button button-small" href="/api/backups/${encodeURIComponent(b.name)}" download="${escape(b.name)}">下载</a><button type="button" class="button button-small" data-restore-backup="${escape(b.name)}">恢复</button></div>`;
    }).join(''):'<p class="help-text">还没有备份。</p>';
  }
  async function backups(){try{paintBackups((await host.api('/api/backups')).backups);}catch(error){backupStatus('无法读取备份列表：'+error.message,true);}}
  // F14: a restore first shows what it would change; only the explicit confirmation writes.
  const settingNames={timezone:'显示时区',view:'默认视图',reminders_enabled:'提醒开关',reminder_offsets:'提醒提前量',native_notifications:'系统通知',sync_interval_minutes:'自动同步周期',hide_completed:'隐藏已完成',active_game:'当前游戏',reminder_scope:'提醒的游戏',disabled_plugins:'停用的插件'};
  let pendingRestore=null;
  function previewLines(p){
    const c=p.changes,lines=[],eg=x=>x.examples?.length?`（例：${x.examples.map(escape).join('、')}）`:'';
    if(c.starred.added||c.starred.removed)lines.push(`收藏：新增 ${c.starred.added} · 取消 ${c.starred.removed}${eg(c.starred)}`);
    if(c.completed.added||c.completed.removed)lines.push(`完成状态：标记 ${c.completed.added} · 取消 ${c.completed.removed}${eg(c.completed)}`);
    if(c.ignored?.added||c.ignored?.removed)lines.push(`忽略状态：忽略 ${c.ignored.added} · 取消 ${c.ignored.removed}${eg(c.ignored)}`);
    if(c.event_reminders?.changed)lines.push(`单活动提醒：改动 ${c.event_reminders.changed} 条${eg(c.event_reminders)}`);
    if(c.notes.changed)lines.push(`备注：改动 ${c.notes.changed} 条${eg(c.notes)}`);
    if(c.corrections.changed)lines.push(`日期校正：改动 ${c.corrections.changed} 处${eg(c.corrections)}`);
    if(c.plans.added||c.plans.removed||c.plans.changed)lines.push(`个人计划：恢复 ${c.plans.added} · 移除 ${c.plans.removed} · 改动 ${c.plans.changed}${eg(c.plans)}`);
    if(c.settings.keys.length)lines.push('设置：'+c.settings.keys.map(k=>settingNames[k]||k).join('、'));
    if(c.workspace.changed)lines.push('布局与主题恢复为备份中的样子');
    if(c.desktop_window?.included)lines.push('桌面窗口位置与外观恢复为备份中的样子（桌面版下次刷新时应用）');
    if(c.network_policy?.keys.length){const names={interval_seconds:'同站请求间隔',hourly_limit:'每站每小时请求上限',round_limit:'每轮每站请求上限',paused:'联网暂停状态'};lines.push('采集设置：'+c.network_policy.keys.map(k=>names[k]||k).map(escape).join('、'));}
    return lines;
  }
  async function restore(body,label){
    backupStatus('正在比对备份与当前数据…');
    try{
      const p=await host.api('/api/backups/preview','POST',body),lines=previewLines(p),box=$('#backup-preview');
      pendingRestore=body;backupStatus('');
      box.innerHTML=`<strong>恢复「${escape(label)}」将会：</strong>${lines.length?'<ul>'+lines.map(l=>`<li>${l}</li>`).join('')+'</ul>':'<p>与当前数据相同，没有需要恢复的改动。</p>'}${p.warnings.length?'<p class="help-text">注意：'+p.warnings.map(escape).join('；')+'</p>':''}<p class="help-text">恢复前会自动备份当前数据，可随时恢复回来。</p><div class="backup-actions"><button type="button" class="button button-primary" data-confirm-restore>确认恢复</button><button type="button" class="button" data-cancel-restore>取消</button></div>`;
      box.hidden=false;box.querySelector('[data-confirm-restore]').focus();
    }catch(error){pendingRestore=null;$('#backup-preview').hidden=true;backupStatus('无法恢复，当前数据未改变：'+error.message,true);}
  }
  async function confirmRestore(){
    const body=pendingRestore;pendingRestore=null;$('#backup-preview').hidden=true;if(!body)return;
    backupStatus('正在恢复…');
    try{
      const {result,backups:rows}=await host.api('/api/backups/restore','POST',body);paintBackups(rows);await host.refresh();
      const c=result.restored;
      backupStatus(`已恢复：收藏 ${c.starred} · 完成 ${c.completed} · 备注 ${c.notes} · 校正 ${c.corrections} · 计划 ${c.plans}。恢复前的数据已另存为备份。`+(result.warnings.length?' 注意：'+result.warnings.join('；'):''));
    }catch(error){backupStatus('恢复失败：'+error.message,true);}
  }
  // F15: installed plugins as loaded at startup; changes apply after a restart.
  const pluginStatus={loaded:'已加载',disabled:'已停用',manifest_disabled:'插件自身设为停用（manifest 中 enabled:false）',error:'加载失败'};
  async function plugins(){
    try{
      const report=await host.api('/api/plugins'),rows=report.plugins;
      const clearButton=(id,name)=>id?`<button type="button" class="text-button" data-clear-records="${escape(id)}" data-name="${escape(name)}">清除个人记录…</button>`:'';
      $('#plugin-list').innerHTML=`<p class="help-text">内置：${report.builtins.map(g=>escape(g.name)+' '+clearButton(g.id,g.name)).join('、')}（随程序提供，不能停用）。插件目录：${escape(report.directory)}</p>`+(rows.length?rows.map(p=>`<div class="backup-row plugin-row"><strong>${escape(p.name||p.folder)}${p.server?' · '+escape(p.server):''}</strong><span>${escape(pluginStatus[p.status]||p.status)}${p.restart_required?` · ${p.enabled_setting?'已设为启用':'已设为停用'}，重启游时后生效`:''}${p.error?' · '+escape(p.error):''}</span><span>文件夹 ${escape(p.folder)}${p.version?' · 版本 '+escape(p.version):''}${p.api_version?' · 插件 API '+escape(p.api_version):''}${p.source_label?' · '+escape(p.source_label):''} ${clearButton(p.id,p.name||p.folder)}</span>${p.status==='manifest_disabled'?'':`<button type="button" class="button button-small" data-plugin-toggle="${escape(p.folder)}" data-enable="${!p.enabled_setting}">${p.enabled_setting?'停用':'启用'}</button>`}</div>`).join(''):'<p class="help-text">插件目录中还没有插件。</p>');
    }catch(error){$('#plugins-status').textContent='无法读取插件：'+error.message;}
  }
  function select(value){
    tab=value;
    for(const node of document.querySelectorAll('[data-preference-panel]')){
      const name=node.dataset.preferencePanel;node.hidden=name!==tab;node.id='preferences-panel-'+name;
      node.setAttribute('role',name==='plugins'?'region':'tabpanel');node.setAttribute('aria-labelledby',name==='plugins'?'preferences-title':'preferences-tab-'+name);
    }
    for(const b of document.querySelectorAll('[data-preference-tab]')){const name=b.dataset.preferenceTab,selected=name===tab;b.id='preferences-tab-'+name;b.setAttribute('aria-controls','preferences-panel-'+name);b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;}
    $('#preferences-title').textContent=tab==='network'?'同步与网络':tab==='plugins'?'游戏管理':tab==='backup'?'备份与恢复':'设置 · 外观与布局';$('#preferences-dialog').dataset.section=tab==='plugins'?'games':'settings';$('#preferences-dialog').scrollTop=0;
  }
  function open(value='layout'){
    if(!host.state())return;
    layout=structuredClone(host.state().workspace.layout);paintLayout();paintTheme(host.state().workspace.theme);
    const n=host.state().network;for(const key of Object.keys(n.policy))$('#network-'+key).value=n.policy[key];
    $('#preferences-error').textContent='';backupStatus('');opened=true;select(value);network();if(value==='network'||value==='backup')backups();if(value==='plugins')plugins();host.openDialog('#preferences-dialog');
    $('#workspace-button').setAttribute('aria-expanded',String(value!=='network'));$('#data-button').setAttribute('aria-expanded',String(value==='network'));
  }
  function download(value){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));a.download='playtime-theme.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  return {
    init(api){
      const narrow=matchMedia('(max-width:850px)'),filters=$('#view-filters');filters.open=!narrow.matches;narrow.addEventListener('change',e=>{filters.open=!e.matches;});
      for(const [group,fields] of Object.entries(colorGroups))$('#theme-'+group).innerHTML=Object.entries(fields).map(([key,label])=>`<label>${label}<input id="theme-${key}" type="color"></label>`).join('');
      host=api;W.configure(layout=>host.mutate('/api/workspace',{layout},'PATCH'));
      $('#preferences-dialog').addEventListener('close',()=>{opened=false;$('#workspace-button').setAttribute('aria-expanded','false');$('#data-button').setAttribute('aria-expanded','false');});
      $('.preference-tabs').addEventListener('keydown',e=>{
        const current=e.target.closest('[data-preference-tab]');if(!current||e.altKey||e.ctrlKey||e.metaKey||!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
        const tabs=[...document.querySelectorAll('[data-preference-tab]')].filter(n=>!n.disabled&&n.checkVisibility());if(!tabs.length)return;
        const i=tabs.indexOf(current),next=e.key==='Home'?tabs[0]:e.key==='End'?tabs.at(-1):tabs[(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
        e.preventDefault();next.focus();next.click();
      });
      document.addEventListener('click',async e=>{
        const b=e.target.closest('button');if(!b)return;
        if(b.id==='workspace-button'){open('theme');return;}
        if(b.id==='data-button'){open('network');return;}
        if(b.id==='rail-calendar'){document.querySelector('#calendar-expand').focus({preventScroll:true});return;}
        if(b.id==='rail-games'){open('plugins');return;}
        if(b.dataset.preferenceTab){select(b.dataset.preferenceTab);if(tab==='network'||tab==='backup')backups();if(tab==='plugins')plugins();return;}
        if(b.id==='manage-plugins'){open('plugins');return;}
        if(b.dataset.pluginToggle){
          // One plugin per request, and the whole list waits for it, so quick successive clicks cannot overwrite each other.
          const list=$('#plugin-list');list.setAttribute('aria-busy','true');list.querySelectorAll('button').forEach(x=>{x.disabled=true;});
          try{if(await host.mutate('/api/plugins/state',{folder:b.dataset.pluginToggle,enabled:b.dataset.enable==='true'}))$('#plugins-status').textContent='已保存。退出并重新启动游时后生效；个人记录不会被删除。';}
          finally{list.removeAttribute('aria-busy');await plugins();}
          return;
        }
        // Clearing a game's records is destructive: preview first, then an explicit confirmation; a backup is made automatically.
        if(b.dataset.clearRecords){
          try{
            const p=await host.api('/api/games/records/preview','POST',{game:b.dataset.clearRecords});
            $('#plugins-status').innerHTML=`<div class="backup-preview"><strong>清除「${escape(p.name)}」的个人记录：</strong><ul><li>收藏 ${p.starred} · 完成 ${p.completed} · 备注 ${p.notes} · 日期校正 ${p.corrections}（共 ${p.marks} 条标记）</li><li>关联的个人计划 ${p.plans} 项</li></ul><p class="help-text">只删除这个游戏的个人记录，官方活动、插件文件和其他游戏不受影响。删除前会自动备份，可在“数据与同步 → 备份与恢复”中恢复。</p><div class="backup-actions"><button type="button" class="button button-primary" data-confirm-clear="${escape(p.game)}">确认清除</button><button type="button" class="button" data-cancel-clear>取消</button></div></div>`;
            $('#plugins-status [data-confirm-clear]').focus();
          }catch(error){$('#plugins-status').textContent='无法清除：'+error.message;}
          return;
        }
        if(b.hasAttribute('data-cancel-clear')){$('#plugins-status').textContent='已取消，个人记录未改变。';return;}
        if(b.dataset.confirmClear){
          try{const r=await host.api('/api/games/records/clear','POST',{game:b.dataset.confirmClear});host.applyState?.(r);await host.refresh();$('#plugins-status').textContent=`已清除「${r.cleared.name}」的 ${r.cleared.marks} 条标记和 ${r.cleared.plans} 项计划；清除前的数据已备份为 ${r.cleared.safety_backup}。`;}
          catch(error){$('#plugins-status').textContent='清除失败，个人记录未改变：'+error.message;}
          return;
        }
        if(b.id==='plugins-open-directory'){try{const r=await host.api('/api/plugins/open-directory','POST',{});$('#plugins-status').textContent='已打开：'+r.directory;}catch(error){$('#plugins-status').textContent=error.message;}return;}
        if(b.hasAttribute('data-confirm-restore')){await confirmRestore();return;}
        if(b.hasAttribute('data-cancel-restore')){pendingRestore=null;$('#backup-preview').hidden=true;backupStatus('已取消，当前数据未改变。');return;}
        if(b.dataset.restoreBackup){await restore({name:b.dataset.restoreBackup},b.closest('.backup-row').querySelector('strong').textContent);return;}
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
          case 'create-backup':try{const r=await host.api('/api/backups','POST',{});paintBackups(r.backups);backupStatus('已备份到本机：'+r.backup.name);}catch(error){backupStatus('备份失败：'+error.message,true);}break;
          case 'restore-backup-file-button':$('#restore-backup-file').click();break;
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
      $('#restore-backup-file').addEventListener('change',async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;try{if(f.size>8000000)throw Error('备份文件超过 8 MB');const doc=JSON.parse(await f.text());await restore({backup:doc},f.name);}catch(error){backupStatus('恢复失败，当前数据未改变：'+error.message,true);}});
      $('#import-theme').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>10000)throw Error('主题文件超过 10 KB');const theme=JSON.parse(await f.text());if(await host.mutate('/api/workspace',{theme},'PATCH')){paintTheme(host.state().workspace.theme);$('#preferences-error').textContent='主题已导入并保存。';}}catch(error){$('#preferences-error').textContent='导入失败：'+error.message;}e.target.value='';});
    },
    open,update(){if(opened&&tab==='network')network();}
  };
})();
