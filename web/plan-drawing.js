'use strict';
/* Transient pointer interaction; only the existing plan form persists data. */
window.PlaytimePlanDrawing = (() => {
  let H, active=false, starting=false, drag=null, expandedBefore=false, suppressUntil=0;
  const $=s=>document.querySelector(s);
  // Coordinates are local wall time; the existing backend resolves UTC and DST on save.
  function local(serial){return new Date(Math.round(serial*1440)*60000).toISOString().slice(0,16);}
  function context(){return JSON.stringify([H.game(),H.range(),H.zone(),H.timelineEvents().map(e=>e.id)]);}
  function message(text){$('#plan-draw-status').textContent=text;}
  function removeDrag(){
    const old=drag;drag=null;$('.plan-selection-overlay')?.remove();
    if(old?.track.hasPointerCapture(old.pointerId))old.track.releasePointerCapture(old.pointerId);
  }
  function stop({render=true}={}){
    if(!active&&!drag)return;
    removeDrag();active=false;document.body.classList.remove('plan-creating');$('#plan-draw-tools').hidden=true;
    window.PlaytimeWorkspace.focus(expandedBefore);
    if(render)H.render();
  }
  async function start(){
    if(active||starting)return;starting=true;
    try{
      H.closeDialogs();await H.prepareTimeline();expandedBefore=document.body.classList.contains('calendar-focused');
      active=true;document.body.classList.add('plan-creating');$('#plan-draw-tools').hidden=false;
      message('在日期轴或活动时间区域按住并左右拖动，松开后填写规划。Esc 可取消。');
      $('#plan-draw-zone').textContent=H.zone();window.PlaytimeWorkspace.focus(true);H.render();
      $('#plan-draw-cancel').focus({preventScroll:true});
    }catch(e){H.toast(e.message,true);}finally{starting=false;}
  }
  function row(){return active?'<div class="timeline-row plan-create-row"><div class="timeline-label"><strong>＋ 拖选游玩时段</strong><small>也可在下方活动时间区域拖选</small></div><div class="bar-cell plan-draw-track" data-plan-create-track role="group" aria-label="拖动选择个人规划时段"><span>按住拖动 → 松开填写名称</span></div><div class="deadline-cell">以当前显示时区为准</div><div class="reward-cell">个人规划</div></div>':'';}
  function atX(x){const r=drag.track.getBoundingClientRect();return drag.lo+Math.max(0,Math.min(1,(x-r.left)/r.width))*(drag.hi-drag.lo);}
  function selection(x){
    const snap=s=>Math.round(s*1440/drag.step)*drag.step/1440;
    let a=Math.max(drag.lo,Math.min(drag.hi,snap(Math.min(drag.from,atX(x))))),b=Math.max(drag.lo,Math.min(drag.hi,snap(Math.max(drag.from,atX(x)))));
    if(b<=a){b=Math.min(drag.hi,a+drag.step/1440);a=Math.max(drag.lo,b-drag.step/1440);}
    return {a,b,local_start:local(a),local_end:local(b),timezone:drag.zone};
  }
  function preview(x){
    const s=selection(x),r=drag.track.getBoundingClientRect(),root=drag.timeline.getBoundingClientRect(),head=drag.timeline.querySelector('.timeline-head').getBoundingClientRect();
    let overlay=$('.plan-selection-overlay');
    if(!overlay){overlay=document.createElement('div');overlay.className='plan-selection-overlay';overlay.setAttribute('aria-hidden','true');drag.timeline.append(overlay);}
    overlay.style.left=(r.left-root.left+(s.a-drag.lo)/(drag.hi-drag.lo)*r.width)+'px';
    overlay.style.width=Math.max(2,(s.b-s.a)/(drag.hi-drag.lo)*r.width)+'px';
    overlay.style.top=(head.bottom-root.top)+'px';overlay.style.height=Math.max(50,drag.timeline.scrollHeight-(head.bottom-root.top))+'px';
    message(s.local_start.replace('T',' ')+' → '+s.local_end.replace('T',' ')+' · '+s.timezone+' · 松开后可微调');
  }
  function down(e){
    if(!active||e.button!==0||!e.isPrimary||e.target.closest('dialog'))return;
    const track=e.target.closest('#calendar .bar-cell,#calendar .day-axis');if(!track)return;
    e.preventDefault();e.stopImmediatePropagation();
    const [lo,hi]=H.range();drag={track,timeline:track.closest('.timeline'),lo,hi,zone:H.zone(),step:Number($('#plan-draw-step').value),pointerId:e.pointerId,x:e.clientX,context:context()};
    drag.from=atX(e.clientX);track.setPointerCapture(e.pointerId);preview(e.clientX);
  }
  function move(e){if(!drag||e.pointerId!==drag.pointerId)return;e.preventDefault();e.stopImmediatePropagation();preview(e.clientX);}
  function up(e){
    if(!drag||e.pointerId!==drag.pointerId)return;
    e.preventDefault();e.stopImmediatePropagation();suppressUntil=performance.now()+400;
    const valid=drag.context===context(),moved=Math.abs(e.clientX-drag.x)>=4,s=selection(e.clientX);
    removeDrag();
    if(!valid){message('显示范围已改变，请重新拖选。');H.render();return;}
    if(!moved){message('请按住并左右拖动选择时段，也可以点击“直接填写时间”。');H.render();return;}
    stop();H.onSelection(s);
  }
  function update(){
    if(!active)return;
    if(!H.isTimeline()){stop({render:false});return;}
    $('#plan-draw-zone').textContent=H.zone();
    if(drag&&drag.context!==context()){removeDrag();message('显示范围已改变，请重新拖选。');H.render();}
  }
  function init(host){
    H=host;
    $('.catalog-tools').insertAdjacentHTML('afterend','<div id="plan-draw-tools" class="plan-draw-tools" hidden><div><strong>创建个人规划 · 拖选模式</strong><span id="plan-draw-status" role="status" aria-live="polite"></span></div><label>吸附<select id="plan-draw-step"><option value="30">30 分钟</option><option value="60">1 小时</option><option value="1440">1 天</option></select></label><span id="plan-draw-zone"></span><button type="button" class="button button-small" data-draw-manual>直接填写时间</button><button type="button" id="plan-draw-cancel" class="button button-small">取消 · Esc</button></div>');
    document.addEventListener('pointerdown',down,true);document.addEventListener('pointermove',move,true);document.addEventListener('pointerup',up,true);
    document.addEventListener('pointercancel',e=>{if(drag&&e.pointerId===drag.pointerId){removeDrag();message('拖选已取消，可以重新选择。');H.render();}},true);
    document.addEventListener('lostpointercapture',e=>{if(drag&&e.pointerId===drag.pointerId){removeDrag();H.render();}},true);
    document.addEventListener('click',e=>{if((active&&e.target.closest('#calendar .bar-cell,#calendar .day-axis'))||(performance.now()<suppressUntil&&e.target.closest('#calendar'))){e.preventDefault();e.stopImmediatePropagation();}},true);
    document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.id==='plan-draw-cancel')stop();else if(b.hasAttribute('data-draw-manual')){stop();H.onManual();}});
    document.addEventListener('keydown',e=>{if(active&&e.key==='Escape'&&!document.querySelector('dialog[open]')){e.preventDefault();e.stopImmediatePropagation();stop();$('#plans-button').focus({preventScroll:true});}},true);
    window.addEventListener('blur',()=>{if(drag){removeDrag();message('拖选已取消，可以重新选择。');H.render();}});
  }
  return {init,start,stop,row,update,get active(){return active;},get holding(){return Boolean(drag);}};
})();
