'use strict';
/* Shared view context. Modules consume host data; game providers never own UI. */
window.PlaytimeWorkspace = (() => {
  const listeners = new Set();
  let data = null, eventOrder = new Map(), editing = false, saveLayout = null, focused = false;
  const names={calendar:'日历 / 时间轴',sources:'来源说明',rewards:'活动奖励',deadlines:'近期截止', 'personal-plans':'个人计划'};
  const modules = new Map();
  let layoutQueue=Promise.resolve();
  function changeLayout(edit){layoutQueue=layoutQueue.then(async()=>{const layout=structuredClone(data.workspace.layout);edit(layout);return saveLayout?.(layout);});return layoutQueue;}
  // Explicit tracks keep the calendar flexible even when modules are reordered.
  // Only adjacent summary cards share a row; saved module order is untouched.
  function sizeModules(){
    const container=document.querySelector('.workspace-modules');if(!container)return;
    const frames=[...container.children].filter(n=>n.classList.contains('module-frame')).sort((a,b)=>Number(a.style.order)-Number(b.style.order));
    let row=0,previous=null;const tracks=[],wide=matchMedia('(min-width:1101px)').matches;
    for(const frame of frames){
      frame.style.gridColumn='1 / -1';frame.style.gridRow='';
      if(frame.hidden || (focused&&frame.dataset.frame!=='calendar'))continue;
      const summary=['rewards','deadlines'].includes(frame.dataset.frame);
      const paired=wide&&summary&&previous&&['rewards','deadlines'].includes(previous.dataset.frame)&&!previous.classList.contains('module-collapsed')&&!frame.classList.contains('module-collapsed')&&previous.style.gridColumn==='1 / -1';
      if(paired){const split=previous.dataset.frame==='deadlines'?8:6;previous.style.gridColumn=`1 / ${split}`;frame.style.gridColumn=`${split} / -1`;}
      else{row++;tracks.push(editing?'max-content':frame.dataset.frame==='calendar'&&!frame.classList.contains('module-collapsed')?'minmax(0,1fr)':'auto');}
      frame.style.gridRow=String(row);previous=frame;
    }
    container.style.gridTemplateRows=tracks.join(' ');
  }
  document.addEventListener('click',e=>{if(e.target.closest('#calendar-expand'))api.focus(!focused);});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&focused&&!document.querySelector('dialog[open]')){api.focus(false);document.querySelector('#calendar-expand').focus({preventScroll:true});}});
  window.addEventListener('resize',sizeModules);
  const api = {
    accept(state) { data = state; eventOrder = new Map((state.event_order || []).map((id,i)=>[id,i])); for (const fn of listeners) fn(state); },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    game(id) { return data?.games.find(g => g.id === id); },
    get view() { return data?.workspace.view; },
    selected() { return data?.workspace.view.selected_games || []; },
    configure(save) { saveLayout=save; },
    edit(value) { if(value)api.focus(false);editing=value;document.body.classList.toggle('editing-layout',value);api.renderModules(); },
    focus(value) {
      if(value&&editing){api.edit(false);document.querySelector('#finish-layout').hidden=true;}
      focused=value;document.body.classList.toggle('calendar-focused',value);
      const button=document.querySelector('#calendar-expand');
      button.setAttribute('aria-pressed',String(value));
      button.title=value?'收起时间轴（Esc）':'展开时间轴';button.setAttribute('aria-label',button.title);
      button.querySelector('path').setAttribute('d',value?'m6 3 6 6 6-6M6 21l6-6 6 6':'m6 9 6-6 6 6M6 15l6 6 6-6');
      sizeModules();
    },
    applyTheme(theme) {
      const root=document.documentElement;
      root.dataset.theme=theme.preset;
      const rgb=theme.background.slice(1).match(/../g).map(v=>parseInt(v,16));
      root.dataset.mode=(rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722)<128?'dark':'light';
      root.style.colorScheme=root.dataset.mode;
      for(const [field,variable] of Object.entries({accent:'green',accent_text:'accent-ink',secondary:'secondary',link:'link',background:'page',surface:'paper',panel_alt:'panel-alt',text:'ink',muted:'muted',border:'line',nav_background:'nav-bg',nav_text:'nav-ink',bar_background:'bar-bg',bar_text:'bar-ink',event_color:'event',gacha_color:'gacha',exchange_color:'exchange',update_color:'update',maintenance_color:'maintenance',card_opacity:'card-opacity'}))root.style.setProperty('--'+variable,theme[field]);
      root.style.fontSize=theme.font_size+'px';root.style.setProperty('--ui-scale',theme.font_size/14);
      root.style.setProperty('--ui-radius',theme.radius+'px');root.style.setProperty('--ui-gap',theme.spacing+'px');
      root.style.setProperty('--control-radius',theme.control_radius+'px');root.style.setProperty('--border-width',theme.border_width+'px');root.style.setProperty('--heading-weight',theme.heading_weight);root.style.setProperty('--texture-alpha',theme.texture_opacity/100);root.dataset.texture=theme.texture;
    },
    async move(id,step,target=null) {return changeLayout(layout=>{
      const rows=layout.modules;
      const from=rows.findIndex(r=>r.id===id),to=target?rows.findIndex(r=>r.id===target):Math.max(0,Math.min(rows.length-1,from+step));
      if(from<0||to<0||from===to)return;
      rows.splice(to,0,rows.splice(from,1)[0]);
    });},
    register(id, render) {
      if (modules.has(id)) throw Error('Duplicate module: ' + id);
      modules.set(id, render);
      return () => modules.delete(id);
    },
    renderModules() {
      for (const spec of data?.workspace.layout.modules || []) {
        const node = document.querySelector(`[data-module="${spec.id}"]`);
        if (node) node.hidden = false;
        modules.get(spec.id)?.({state: data, spec});
        if (node) {
          let frame=node.closest('.module-frame');
          if(!frame) {
            frame=document.createElement('div');frame.className='module-frame';frame.dataset.frame=spec.id;
            const header=document.createElement('div');header.className='module-handle';header.draggable=true;
            const title=document.createElement('strong');title.textContent=names[spec.id];header.append(title);
            for(const [text,label,action] of [['↑','上移'+names[spec.id],()=>api.move(spec.id,-1)],['↓','下移'+names[spec.id],()=>api.move(spec.id,1)],['↕','展开或折叠'+names[spec.id],()=>changeLayout(layout=>{const row=layout.modules.find(r=>r.id===spec.id);row.collapsed=!row.collapsed;}) ]]) {
              const b=document.createElement('button');b.type='button';b.textContent=text;b.setAttribute('aria-label',label);b.addEventListener('click',action);header.append(b);
            }
            header.addEventListener('dragstart',e=>e.dataTransfer.setData('text/plain',spec.id));
            frame.addEventListener('dragover',e=>{if(editing)e.preventDefault();});
            frame.addEventListener('drop',e=>{if(!editing)return;e.preventDefault();const id=e.dataTransfer.getData('text/plain');if(data.workspace.layout.modules.some(r=>r.id===id))api.move(id,0,spec.id);});
            node.before(frame);frame.append(header,node);
          }
          frame.style.order=data.workspace.layout.modules.indexOf(spec);
          const empty=node.hidden;
          frame.hidden=!spec.visible||(!editing&&empty&&!spec.collapsed);
          frame.classList.toggle('module-collapsed',spec.collapsed);
          frame.querySelector('.module-handle button:last-child').textContent=spec.collapsed?'展开':'折叠';
          frame.querySelector('.module-handle button:last-child').setAttribute('aria-expanded',String(!spec.collapsed));
          node.hidden=empty||spec.collapsed;
          frame.querySelector('.module-handle').hidden=!editing&&!spec.collapsed;
        }
      }
      if(data?.workspace.theme)api.applyTheme(data.workspace.theme);
      const layout=data?.workspace.layout;if(layout){
        document.body.dataset.sidebar=layout.sidebar.visible?layout.sidebar.position:'hidden';
        document.querySelector('#rail-games')?.setAttribute('aria-pressed',String(layout.sidebar.visible));
        document.documentElement.style.setProperty('--sidebar-width',layout.sidebar.width+'px');
      }
      sizeModules();
    },
    compare(a, b) {
      return (eventOrder.get(a.id) ?? Infinity) - (eventOrder.get(b.id) ?? Infinity) || a.id.localeCompare(b.id);
    }
  };
  return api;
})();
