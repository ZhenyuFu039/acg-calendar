'use strict';
/* Host-only linked views and personal plans. No provider-specific selectors. */
window.PlaytimePlanner = (() => {
  let H, editing=null, editingPlan=null, managerSignature=null, focusQueue=Promise.resolve();
  const $=s=>document.querySelector(s), W=window.PlaytimeWorkspace;
  const D=window.PlaytimePlanDrawing;
  const E=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state=()=>H.state(), meta=()=>state()?.presentation||{}, view=()=>W.view;
  const dateKey=serial=>new Date(Math.floor(serial)*86400000).toISOString().slice(0,10);
  const instantDay=value=>{const p=H.dateParts(value);return `${p.year}-${p.month}-${p.day}`;};
  const inDay=(start,end)=>{const b=meta().focus_bounds;return !b||(Date.parse(start)<Date.parse(b.end)&&Date.parse(end)>Date.parse(b.start));};
  function filterFocus(events){const b=meta().focus_bounds;if(!b)return events;return events.filter(e=>e.start.at&&!e.invalid_interval&&(e.time_kind==='point'?Date.parse(e.start.at)>=Date.parse(b.start)&&Date.parse(e.start.at)<Date.parse(b.end):inDay(e.start.at,e.end.at||'9999-12-31T00:00:00Z')));}
  function scopedPlans({focus=true}={}){return (state()?.plans||[]).filter(p=>(!p.game_id||H.game()==='all'||(H.game()==='selected'?W.selected().includes(p.game_id):H.game()===p.game_id))&&(!focus||inDay(p.start,p.end)));}
  function nodes(){const ids=new Set(H.baseEvents().map(e=>e.id));return (meta().deadline_nodes||[]).filter(n=>ids.has(n.event_id));}
  function density(day){const rows=nodes().filter(n=>instantDay(n.at)===day);return {rows,activity:rows.filter(n=>n.kind==='activity').length,exchange:rows.filter(n=>n.kind==='exchange').length};}
  function densityButton(serial,month=false,axis={}){const day=dateKey(serial),d=density(day),counts=month?`活动 ${d.activity} / 兑 ${d.exchange}`:`${d.activity?`<i class="density-activity">${d.activity}</i>`:''}${d.exchange?`<i class="density-exchange">${d.exchange}</i>`:''}`;return `<button type="button" class="density-day ${d.rows.length?'has-deadlines':''} ${view().focus.date===day?'date-selected':''}" data-deadline-day="${day}" aria-label="${day}，活动截止 ${d.activity} 项，兑换截止 ${d.exchange} 项" title="${day} · 活动 ${d.activity} / 兑换 ${d.exchange}（已明确、未完成）">${month?'':`<strong>${axis.fit?(axis.label?Number(day.slice(5,7))+'/'+Number(day.slice(8)):''):Number(day.slice(8))}</strong>`}<small>${d.rows.length?counts:month?'':'·'}</small></button>`;}
  function focusDay(day){const key=typeof day==='number'?dateKey(day):day;focusQueue=focusQueue.then(()=>H.mutate('/api/workspace',{view:{focus:{date:key,event_id:null},focus_timezone:H.zone()}},'PATCH'));return focusQueue;}
  async function clearFocus(){return H.mutate('/api/workspace',{view:{focus:{date:null,event_id:null}}},'PATCH');}
  function selectEvent(id){if(view().focus.event_id===id)return;focusQueue=focusQueue.then(()=>H.mutate('/api/workspace',{view:{focus:{event_id:id}}},'PATCH'));}
  function relatedClaims(){const claims=new Map();for(const event of H.events())if(event.reward&&event.reward.state!=='excluded')for(const c of event.reward.claims||[])claims.set(c.id,c.amount);return claims;}
  function relatedRewards(){if(!view().focus.date)return '';const claims=relatedClaims(),games=H.game()==='all'?state().games.map(g=>g.id):H.game()==='selected'?W.selected():[H.game()];if(!games.some(g=>W.game(g)?.currency))return '';return `<p class="related-rewards">${E(view().focus.date)} 关联活动 · ${claims.size?`${[...claims.values()].reduce((a,b)=>a+b,0).toLocaleString()} 嵌晶玉 / ${claims.size} 笔独立奖励`:'未有已核实关联数量；未列明不按零'}<br>按共享奖励 ID 去重；版本小计保持不变。</p>`;}
  function banner(){
    const f=view().focus, el=$('#focus-context');el.hidden=!f.date;
    $('#clear-activity-focus').hidden=!f.event_id||Boolean(f.date);document.body.classList.toggle('date-focused',Boolean(f.date));
    const event=H.baseEvents().find(e=>e.id===f.event_id), ids=new Set(H.events().map(e=>e.id)),claims=relatedClaims();
    const reward=claims.size?` · 关联已核实奖励 ${[...claims.values()].reduce((a,b)=>a+b,0).toLocaleString()} 嵌晶玉（不改变版本小计）`:'';
    el.innerHTML=`<span>${f.date?`聚焦 ${E(f.date)} · ${E(view().focus_timezone)} · ${ids.size} 项活动${reward}`:'活动聚焦'}${event?' · '+E(event.title):''}</span><button type="button" class="text-button" data-clear-focus>清除聚焦 ×</button>`;
    const count=new Set((meta().uncertain_deadlines||[]).filter(id=>H.baseEvents().some(e=>e.id===id))).size;
    $('#uncertain-deadlines').textContent=`预计 / 待定 ${count}（不计入截止密度）`;
  }
  function arrange(events){
    const sorted=[...events].sort(W.compare),by=new Map(sorted.map(e=>[e.id,e])),parents=meta().parents||{},children=new Map();
    const parent=e=>view().starred_first&&e.starred?null:by.get(parents[e.id]);
    for(const e of sorted){const p=parent(e);if(p){if(!children.has(p.id))children.set(p.id,[]);children.get(p.id).push(e);}}
    const result=[],seen=new Set();
    function visit(e,depth,root){if(seen.has(e.id))return;seen.add(e.id);result.push({...e,_tree_depth:depth,_group_event:root});if(!view().collapsed_parents.includes(e.id))for(const c of children.get(e.id)||[])visit(c,depth+1,root);}
    for(const e of sorted)if(!parent(e))visit(e,0,e);
    return result;
  }
  function familyIds(id){const result=new Set([id]),queue=[id];while(queue.length){for(const c of meta().children?.[queue.shift()]||[])if(!result.has(c)){result.add(c);queue.push(c);}}return result;}
  function familyControls(event){
    const childIds=meta().children?.[event.id]||[],phases=event.phases||[],closed=view().collapsed_parents.includes(event.id);
    if(!childIds.length&&!phases.length)return '';
    const visible=new Set(H.events().map(e=>e.id)),family=familyIds(event.id);
    const deadlines=(meta().deadline_nodes||[]).filter(n=>family.has(n.event_id)&&visible.has(n.event_id));
    const labels=deadlines.map(n=>`${n.kind==='exchange'?'兑换':'活动'} ${H.format(n.at)} · ${n.title}`);
    for(const e of H.events())if(family.has(e.id)&&(meta().uncertain_deadlines||[]).includes(e.id))labels.push(`截止预计 / 待定 · ${e.title}`);
    const summary=closed?`<span class="family-deadlines" title="${E(labels.join('\n'))}">${labels.map(t=>`<span>${E(t)}</span>`).join('')||'子项截止待定，请展开核对'}</span>`:phases.length?`<span class="family-deadlines">${phases.map(p=>`<span>${E(p.title)} ${E(H.format(p.at))}${['exact','confirmed','manual'].includes(p.state)?'':' · 预计/待定'}</span>`).join('')}</span>`:'';
    return `<div class="family-controls"><button type="button" class="text-button" data-fold-parent="${E(event.id)}" aria-expanded="${!closed}">${closed?'▸':'▾'} ${childIds.length} 个子活动${phases.length?' · '+phases.length+' 个阶段':''}</button>${summary}</div>`;
  }
  function planRow(p){return `<button type="button" class="plan-card" data-plan="${E(p.id)}"><strong>◇ ${E(p.title)}</strong><span>${E(H.format(p.start,{year:true}))} → ${E(H.format(p.end,{year:true}))}</span><small>个人安排 · ${E(W.game(p.game_id)?.short_name||'未关联游戏')} · 显示时区 ${E(H.zone())}</small></button>`;}
  // Folded to one line by default so official activities keep the space; per-browser choice.
  const plansOpen=()=>{try{return localStorage.getItem('playtime.plans-module.v1')==='open';}catch{return false;}};
  function plansModule(){
    const rows=scopedPlans(),el=$('#personal-plans-panel'),open=plansOpen();el.hidden=!rows.length;
    const next=[...rows].sort((a,b)=>Date.parse(a.end)-Date.parse(b.end)).find(p=>Date.parse(p.end)>Date.now());
    const html=`<div class="plans-module-head"><button type="button" class="text-button plans-fold" data-plans-fold aria-expanded="${open}"><strong>◇ 个人规划 · ${rows.length} 项</strong></button>${!open&&next?`<span class="plans-next">下一项：${E(next.title)} · ${E(H.format(next.start))}</span>`:''}<button type="button" class="text-button" data-plans-manager>管理 / 新建 ↗</button></div>${open?rows.map(planRow).join(''):''}`;
    if(el._html!==html){el.innerHTML=html;el._html=html;}
  }
  function planBars(lo,hi){return scopedPlans().filter(p=>H.civil(p.start)<hi&&H.civil(p.end)>lo).map(p=>{
    const a=Math.max(lo,H.civil(p.start)),b=Math.min(hi,H.civil(p.end));
    return `<div class="timeline-row personal-plan-row"><button type="button" class="event-name" data-plan="${E(p.id)}"><span class="event-name-text"><strong>◇ ${E(p.title)}</strong><small>个人计划 · 可编辑 / 移动</small></span></button><div class="bar-cell plan-track"><button type="button" class="personal-plan-bar" data-plan="${E(p.id)}" style="left:${(a-lo)/(hi-lo)*100}%;width:${(b-a)/(hi-lo)*100}%" title="${E(p.title+' · '+H.format(p.start,{year:true})+' → '+H.format(p.end,{year:true}))}"><span>◇ ${E(p.title)}</span></button></div><button type="button" class="deadline-cell" data-plan="${E(p.id)}"><strong>${E(H.format(p.start))}</strong><span>→ ${E(H.format(p.end))}</span></button><span class="reward-cell">个人安排</span></div>`;
  }).join('');}
  function monthPlans(serial){const rows=scopedPlans().filter(p=>H.civil(p.start)<serial+1&&H.civil(p.end)>serial);return rows.length?`<button type="button" class="month-plan" data-day="${serial}">◇ 计划 ${rows.length}</button>`:'';}
  function agendaPlans(serial){return `<div class="agenda-plans"><strong>个人安排</strong>${scopedPlans().filter(p=>H.civil(p.start)<serial+1&&H.civil(p.end)>serial).map(planRow).join('')}<button class="button button-small" type="button" data-new-plan-day="${dateKey(serial)}">＋ 安排这一天的游玩</button></div>`;}
  function manager(){
    if(D.active)D.stop();
    const rows=[...(state().plans||[])].sort((a,b)=>Date.parse(b.updated_at)-Date.parse(a.updated_at)||Date.parse(a.start)-Date.parse(b.start));
    const [lo,hi]=H.range(),events=H.timelineEvents(),rangeText=dateKey(lo)+' → '+dateKey(hi-1);
    const scopeText=H.game()==='all'?'全部游戏':H.game()==='selected'?'自选组合：'+(W.selected().map(id=>W.game(id)?.short_name||id).join('、')||'未选游戏'):W.game(H.game())?.name||H.game();
    const signature=JSON.stringify([rows,lo,hi,H.zone(),H.game(),W.selected(),view().focus,events.map(e=>[e.id,e.title,e.start,e.end,e.time_kind]),rows.map(p=>state().events.find(e=>e.id===p.event_id)?.title),state().games.map(g=>[g.id,g.name,g.short_name])]);
    if($('#plans-dialog').open&&signature===managerSignature)return;
    managerSignature=signature;
    const content=$('#plans-content'),scroll=content.scrollTop,disclosure=new Map([...content.querySelectorAll('[data-plan-summary]')].map(row=>[row.dataset.planSummary,row.querySelector('details').open]));
    $('#plans-content').innerHTML=`<div class="planning-manager-actions"><button class="button button-primary" type="button" data-start-plan>＋ 创建个人规划 · 时间轴拖选</button><button class="text-button" type="button" data-new-plan>直接填写时间</button></div><p class="help-text">全部 ${rows.length} 项规划，不受游戏或日期筛选隐藏。活动对照按当前时间轴：${E(scopeText)} · ${E(rangeText)} · ${E(H.zone())}${view().focus.date?' · 聚焦 '+E(view().focus.date):''} · ${events.length} 个显示条目；仅表示时间重叠，不会自动加入游玩任务。</p>${rows.map(p=>planSummary(p,events,lo,hi)).join('')||'<div class="empty-state"><strong>还没有个人规划</strong>点击“创建个人规划”，在时间轴上拖出游玩时段。</div>'}`;
    $('#plans-title').textContent='个人规划 · 全部 '+rows.length+' 项';
    for(const row of content.querySelectorAll('[data-plan-summary]'))if(disclosure.has(row.dataset.planSummary))row.querySelector('details').open=disclosure.get(row.dataset.planSummary);
    H.openDialog('#plans-dialog');
    content.scrollTop=scroll;
  }
  function matches(p,events,lo,hi){
    const start=Date.parse(p.start),end=Date.parse(p.end);
    return events.flatMap(e=>{
      if(!e.start.at||e.invalid_interval)return [];
      const a=Date.parse(e.start.at),b=e.end.at?Date.parse(e.end.at):Infinity;
      if(e.time_kind==='point')return a>=start&&a<end&&H.civil(a)>=lo&&H.civil(a)<hi?[{event:e,label:'更新发生在规划内'}]:[];
      const overlapStart=Math.max(start,a),overlapEnd=Math.min(end,b);
      if(overlapStart>=overlapEnd||H.civil(overlapStart)>=hi||H.civil(overlapEnd)<=lo)return [];
      const trusted=ep=>['exact','confirmed','manual'].includes(ep.state);
      const certainty=!trusted(e.start)||!trusted(e.end)?'预计 / 待定 · ':'';
      return [{event:e,label:certainty+(start>=a&&end<=b?'规划期间开放':'部分时段重叠')}];
    });
  }
  function planSummary(p,events,lo,hi){
    const rank=e=>e.id===p.event_id?0:p.game_id&&e.game===p.game_id?1:2;
    const covered=matches(p,events,lo,hi).sort((a,b)=>rank(a.event)-rank(b.event)),linked=state().events.find(e=>e.id===p.event_id);
    const items=covered.map(({event:e,label})=>`<li><button type="button" class="text-button" data-event="${E(e.id)}">${E(W.game(e.game)?.short_name||e.game)} · ${E(e.title)} ↗</button><small>${E(label)} · ${E(H.format(e.start.at,{year:true}))} → ${e.time_kind==='point'?'更新节点':e.end.at?E(H.format(e.end.at,{year:true})):'结束待定'}</small></li>`).join('');
    return `<article class="plan-summary" data-plan-summary="${E(p.id)}"><header><button type="button" class="plan-summary-title" data-plan="${E(p.id)}"><strong>◇ ${E(p.title)}</strong><span>编辑 / 移动 ↗</span></button></header><p class="plan-summary-time">${E(H.format(p.start,{year:true}))} → ${E(H.format(p.end,{year:true}))}</p><p class="help-text">${E(W.game(p.game_id)?.name||p.game_id||'未限定游戏')} · 计划时区 ${E(p.timezone)}${p.event_id?' · 手动关联：'+E(linked?.title||'来源活动已不可用'):''}</p>${p.notes?`<p class="plan-summary-notes">${E(p.notes)}</p>`:''}<details class="plan-coverage"><summary>期间可参与活动 · ${covered.length} 项${p.event_id||p.game_id?'（手动关联的排在前面）':''}</summary>${items?'<ul>'+items+'</ul>':'<p class="help-text">当前显示的游戏、日期和筛选范围内没有重叠活动；规划仍完整保存。</p>'}</details></article>`;
  }
  function closeDialogs(){for(const d of document.querySelectorAll('dialog[open]'))d.close();}
  function localValue(at,timezone){const p=H.dateParts(at,timezone);return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;}
  function editor(id=null,{day=null,eventId=null,selection=null}={}){
    D.stop();
    const p=id?state().plans.find(p=>p.id===id):null;if(id&&!p)return;
    if(!p&&!day&&!selection)day=view().focus.date;
    editing=id;editingPlan=p;closeDialogs();const event=state().events.find(e=>e.id===eventId),tz=p?.timezone||selection?.timezone||H.zone();
    const next=new Date(Math.ceil(Date.now()/1800000)*1800000),start=p?.start||next.toISOString(),end=p?.end||new Date(next.valueOf()+3600000).toISOString();
    const games=state().games.map(g=>`<option value="${E(g.id)}">${E(g.name)}</option>`).join('');
    const foldSelect=role=>`<select id="plan-fold-${role}" aria-label="${role==='start'?'开始':'结束'}重复时刻"><option value="">自动（重复时提示选择）</option><option value="0">重复时取较早一次</option><option value="1">重复时取较晚一次</option></select>`;
    $('#plan-editor-title').textContent=p?'编辑个人计划':'新建个人计划';
    $('#plan-editor-content').innerHTML=`<form id="plan-form"><p class="help-text">以分钟为粒度。此安排不会更改活动公告或截止日期。保存后按显示时区换算。</p><label class="field-label" for="plan-title">计划名称</label><input id="plan-title" required maxlength="200" value="${E(p?.title||event?.title||'')}"><div class="plan-fields"><label>开始（当地时间）<input id="plan-start" required type="datetime-local" step="60" value="${day?day+'T19:00':localValue(start,tz)}">${foldSelect('start')}</label><label>结束（当地时间）<input id="plan-end" required type="datetime-local" step="60" value="${day?day+'T20:00':localValue(end,tz)}">${foldSelect('end')}</label></div><label class="field-label" for="plan-timezone">计划时区（IANA）</label><input id="plan-timezone" required list="plan-zones" value="${E(tz)}"><datalist id="plan-zones">${['Asia/Shanghai','Asia/Tokyo','America/New_York','Europe/London','UTC'].map(t=>`<option value="${t}">`).join('')}</datalist><p class="help-text">时区用于解释上方当地时间；修改时区后保持输入的钟面时间。夏令时不存在或重复的时刻会明确提示。</p><div class="plan-fields"><label>关联游戏（可选）<select id="plan-game"><option value="">不关联</option>${games}${p?.game_id&&!W.game(p.game_id)?`<option value="${E(p.game_id)}">${E(p.game_id)}（已卸载，保留）</option>`:''}</select></label><label>关联活动（可选）<select id="plan-event"></select></label></div><label class="field-label" for="plan-notes">备注</label><textarea id="plan-notes" maxlength="2000">${E(p?.notes||'')}</textarea><p id="plan-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">保存个人计划</button>${p?`<fieldset class="plan-moves"><legend>移动整段计划（保持持续时长）</legend>${[[-1440,'前移 24 小时'],[-60,'前移 1 小时'],[60,'后移 1 小时'],[1440,'后移 24 小时']].map(([v,l])=>`<button type="button" class="button button-small" data-move-plan="${v}">${l}</button>`).join('')}<p class="help-text">移动会一并保存当前输入；跨夏令时时按实际经过时间移动。</p></fieldset><details class="plan-delete"><summary>删除计划</summary><button type="button" class="button" data-delete-plan>确认删除这条个人计划</button></details>`:''}</form>`;
    if(selection){$('#plan-start').value=selection.local_start;$('#plan-end').value=selection.local_end;$('#plan-form .help-text').textContent='已从时间轴选择时段。填写名称即可保存，也可微调时间；夏令时会在保存时校验。';}
    $('#plan-game').value=p?.game_id||event?.game||(selection&&W.game(H.game())?H.game():'');eventOptions(p?.event_id||eventId);
    H.openDialog('#plan-editor-dialog');
  }
  function eventOptions(selected=null){const g=$('#plan-game').value,rows=state().events.filter(e=>!g||e.game===g),label=e=>(W.game(e.game)?.short_name||e.game)+' · '+e.title,seen=new Map();for(const e of rows)seen.set(label(e),(seen.get(label(e))||0)+1);$('#plan-event').innerHTML='<option value="">不关联</option>'+rows.map(e=>`<option value="${E(e.id)}">${E(label(e)+(seen.get(label(e))>1?' · '+(e.version_name||'')+(e.start.at?' '+H.format(e.start.at,{dateOnly:true}):''):''))}</option>`).join('');if(selected&&!state().events.some(e=>e.id===selected))$('#plan-event').insertAdjacentHTML('beforeend',`<option value="${E(selected)}">来源活动已不可用（保留关联）</option>`);$('#plan-event').value=selected||'';}
  async function save(move=null){
    if(!$('#plan-form').reportValidity())return;
    const body={title:$('#plan-title').value,local_start:$('#plan-start').value,local_end:$('#plan-end').value,timezone:$('#plan-timezone').value.trim(),game_id:$('#plan-game').value||null,event_id:$('#plan-event').value||null,notes:$('#plan-notes').value};
    for(const role of ['start','end']){
      const fold=$('#plan-fold-'+role).value;
      if(editingPlan&&body.timezone===editingPlan.timezone&&body['local_'+role]===localValue(editingPlan[role],body.timezone)&&fold===''){body[role]=editingPlan[role];delete body['local_'+role];}
      else if(fold!=='')body['fold_'+role]=Number(fold);
    }
    if(move!==null)body.move_minutes=move;
    const submit=$('#plan-form button[type=submit]'),buttons=[...$('#plan-form').querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    try{const data=await H.api('/api/plans'+(editing?'/'+editing:''),editing?'PATCH':'POST',body);H.applyState(data);if(move!==null)editor(editing);else{$('#plan-editor-dialog').close();manager();}}
    catch(e){$('#plan-error').textContent=e.message;$('#plan-error').hidden=false;}
    finally{if(submit.isConnected)buttons.forEach(b=>b.disabled=false);}
  }
  async function deadlines(day){
    if(!await focusDay(day))return;
    const d=density(day);$('#deadlines-title').textContent=day+' · 已明确的待完成截止';
    $('#deadlines-content').innerHTML=`<p class="help-text">${E(H.zone())} · 活动 ${d.activity} / 兑换 ${d.exchange}。节点按活动与时刻去重；折叠不减少计数。数量不代表游玩时长。</p>${d.rows.map(n=>`<button type="button" class="agenda-event" data-event="${E(n.event_id)}"><strong>${n.kind==='exchange'?'兑换关闭':'活动 / 阶段关闭'} · ${E(n.title)}</strong><span>${E(H.format(n.at,{year:true}))}</span></button>`).join('')||'<p>这一天没有已明确的未完成截止节点。</p>'}<button type="button" class="button" data-agenda-date="${day}">查看当天活动与计划</button>`;
    H.openDialog('#deadlines-dialog');
  }
  // Entry points stay disabled until the first local state arrives (they all read it).
  function update(){if(!state())return;if($('#plans-button').disabled){$('#plans-button').disabled=$('#uncertain-deadlines').disabled=false;$('#plans-button').removeAttribute('title');}D.update();banner();if($('#plans-dialog')?.open)manager();document.querySelectorAll('[data-event]').forEach(el=>el.classList.toggle('event-focused',el.dataset.event===view().focus.event_id));}
  function detail(event){$('#detail-content .detail-actions')?.insertAdjacentHTML('beforeend',`<button type="button" class="button" data-plan-for-event="${E(event.id)}">◇ 安排游玩</button>`);const parent=state().events.find(e=>e.id===meta().parents?.[event.id]);if(parent)$('#detail-content .detail-actions')?.insertAdjacentHTML('beforebegin',`<p class="help-text">所属主活动：<button type="button" class="text-button" data-event="${E(parent.id)}">${E(parent.title)} ↗</button> · 具体截止沿用此子活动时间</p>`);}
  function init(host){
    H=host;
    const tools=$('.catalog-tools');tools.insertAdjacentHTML('afterend','<div id="focus-context" class="focus-context" hidden></div>');tools.insertAdjacentHTML('beforeend','<button type="button" id="uncertain-deadlines" class="text-button" title="预计或待定时间不计入精确截止密度" disabled>预计 / 待定</button><button type="button" id="clear-activity-focus" class="text-button" data-clear-selection hidden title="清除活动选择">清除选择 ×</button><button type="button" class="button button-small" id="plans-button" data-plans-manager disabled title="正在读取本地数据…">◇ 个人规划</button>');
    D.init({...host,closeDialogs,onSelection:selection=>editor(null,{selection}),onManual:()=>editor()});
    for(const [id,title,content]of [['plans','个人游玩计划','plans-content'],['plan-editor','个人计划','plan-editor-content'],['deadlines','截止明细','deadlines-content']])document.body.insertAdjacentHTML('beforeend',`<dialog id="${id}-dialog" class="feature-dialog" aria-labelledby="${id}-title"><div class="dialog-header"><h2 id="${id}-title">${title}</h2><button type="button" class="icon-button" data-close="${id}-dialog" aria-label="关闭">×</button></div><div class="dialog-body" id="${content}"></div></dialog>`);
    for(const d of document.querySelectorAll('.feature-dialog'))d.addEventListener('click',e=>{if(e.target!==d)return;const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();});
    document.addEventListener('submit',e=>{if(e.target.id==='plan-form'){e.preventDefault();save();}});
    document.addEventListener('change',e=>{if(e.target.id==='plan-game')eventOptions();});
    // An error describes the input it was raised for; any edit clears it until the next save is checked.
    for(const type of ['input','change'])document.addEventListener(type,e=>{if(e.target.closest('#plan-form')&&!$('#plan-error').hidden){$('#plan-error').hidden=true;$('#plan-error').textContent='';}});
    document.addEventListener('click',async e=>{
      const b=e.target.closest('button');if(!b||!state())return;
      if(b.hasAttribute('data-clear-focus')||b.hasAttribute('data-clear-selection'))await clearFocus();
      else if(b.dataset.foldParent){const collapsed=new Set(view().collapsed_parents);if(collapsed.has(b.dataset.foldParent))collapsed.delete(b.dataset.foldParent);else collapsed.add(b.dataset.foldParent);await H.mutate('/api/workspace',{view:{collapsed_parents:[...collapsed]}},'PATCH');}
      else if(b.dataset.deadlineDay)await deadlines(b.dataset.deadlineDay);
      else if(b.hasAttribute('data-plans-manager'))manager();
      else if(b.hasAttribute('data-plans-fold')){try{localStorage.setItem('playtime.plans-module.v1',plansOpen()?'closed':'open');}catch{}plansModule();W.renderModules();}
      // Drag selection fights touch scrolling on phones; the form is the creation path there.
      else if(b.hasAttribute('data-start-plan')){if(matchMedia('(max-width:850px)').matches)editor();else await D.start();}
      else if(b.dataset.plan)editor(b.dataset.plan);
      else if(b.hasAttribute('data-new-plan'))editor();
      else if(b.dataset.newPlanDay)editor(null,{day:b.dataset.newPlanDay});
      else if(b.dataset.planForEvent)editor(null,{eventId:b.dataset.planForEvent});
      else if(b.hasAttribute('data-move-plan'))await save(Number(b.dataset.movePlan));
      else if(b.hasAttribute('data-delete-plan')){if(await H.mutate('/api/plans/'+editing,{},'DELETE')){$('#plan-editor-dialog').close();manager();}}
      else if(b.dataset.agendaDate){$('#deadlines-dialog').close();H.openAgenda(Date.parse(b.dataset.agendaDate)/86400000);}
    });
    // The game badge is a scope control, while the surrounding title opens details.
    document.addEventListener('click',e=>{const icon=e.target.closest('.event-avatar .game-icon,.catalog-game-badge .game-icon');if(!icon)return;const row=icon.closest('[data-game]');if(row){e.preventDefault();e.stopImmediatePropagation();$('#game-select').value=row.dataset.game;$('#game-select').dispatchEvent(new Event('change',{bubbles:true}));}},true);
  }
  function query(){const url=new URL(location.href);if(url.searchParams.has('planner')){url.searchParams.delete('planner');history.replaceState(null,'',url);manager();}else if(url.searchParams.has('event')){const id=url.searchParams.get('event');url.searchParams.delete('event');history.replaceState(null,'',url);H.openEvent(id);}else if(url.searchParams.has('planEvent')){const id=url.searchParams.get('planEvent');url.searchParams.delete('planEvent');history.replaceState(null,'',url);editor(null,{eventId:id});}else if(url.searchParams.has('plan')){const id=url.searchParams.get('plan');url.searchParams.delete('plan');history.replaceState(null,'',url);editor(id);}}
  return {init,filterFocus,arrange,familyControls,densityButton,focusDay,clearFocus,selectEvent,update,detail,plansModule,planBars,monthPlans,agendaPlans,relatedRewards,query,creationRow:()=>D.row(),stopCreating:()=>D.stop(),get creating(){return D.active;},get holding(){return D.holding;}};
})();
