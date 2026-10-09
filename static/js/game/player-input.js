import { controlAccess, normalizeAngle, shotPower, shotFromPull } from './player-controls.js';

// Input is event driven: it never starts an animation loop or moves physics bodies.
export function createPlayerInput(options) {
  const { canvas, getState, getOnline, isBusy, getGround, hitTest, onAim, onClear, onSelect, onKick, setOrbitEnabled, getOrbitEnabled } = options;
  const ids = ['player-select','player-prev','player-next','aim-angle','kick-power','kick-power-value','kick-btn','aim-cancel','player-control-status'];
  const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
  let selectedIndex = 0, angle = 0, basePower = 72, preview = false;
  let passType = 'normal', gesture = null, stamp = '', optionStamp = '', team = 'a', notice = '';
  const access = () => controlAccess(getState(), getOnline(), isBusy());
  const rule = () => controlAccess(getState(), getOnline());
  const power = () => shotPower(basePower, passType, getState()?.power_cap);
  const stateStamp = () => {
    const state = getState(), online = getOnline();
    return [online.active, online.roomId, online.side, online.generation, online.ready, online.status, online.leaving, online.connectionError,
      state?.game_mode, state?.is_player_a, state?.online_move_count ?? state?.kick_count, state?.penalty_shootout, state?.game_over].join(':');
  };
  function origin() {
    const state = getState();
    return rule().useBall ? state?.ball : state?.[team === 'a' ? 'players_a' : 'players_b']?.[selectedIndex];
  }
  function resetAim() {
    const home = origin(), ball = getState()?.ball;
    angle = home && ball && Math.hypot(ball.x-home.x, ball.y-home.y) > 1
      ? normalizeAngle(Math.atan2(ball.y-home.y, ball.x-home.x)*180/Math.PI) : team === 'b' ? 180 : 0;
    basePower = 72; preview = false; onClear();
  }
  function drawAim() {
    const home = origin();
    if (!home || !preview || !access().canKick) { onClear(); return; }
    const rad = angle*Math.PI/180, distance = basePower*1.1;
    onAim({x:home.x,y:home.y}, {pullX:home.x-Math.cos(rad)*distance,pullY:home.y-Math.sin(rad)*distance,kickAngle:rad,power:power()});
  }
  function cancelGesture(restore = true) {
    if (gesture) {
      const previous = gesture; gesture = null;
      if (restore) { angle = previous.angle; basePower = previous.basePower; }
      setOrbitEnabled(previous.orbitEnabled);
      try { if (canvas.hasPointerCapture(previous.id)) canvas.releasePointerCapture(previous.id); } catch (_) {}
    }
    preview = false; onClear(); options.onHover?.(false);
  }
  function sync() {
    const legal = rule(), current = access(), nextStamp = stateStamp();
    if (stamp !== nextStamp) {
      cancelGesture();
      if (team !== legal.team) selectedIndex = 0;
      team = legal.team;
      if (!legal.indices.includes(selectedIndex)) selectedIndex = legal.indices[0] ?? 0;
      resetAim(); notice = ''; stamp = nextStamp;
    }
    const nextOptions = `${legal.team}:${legal.indices.join(',')}`;
    if (optionStamp !== nextOptions) {
      ui['player-select'].replaceChildren(...legal.indices.map(index => {
        const option = document.createElement('option'); option.value = String(index);
        option.textContent = index === 0 ? '#1 · Keeper / captain' : `#${index+1} · Outfield`;
        return option;
      })); optionStamp = nextOptions;
    }
    ui['player-select'].value = String(selectedIndex);
    ui['player-select'].disabled = !current.canKick || legal.indices.length < 2;
    for (const id of ['player-prev','player-next']) ui[id].disabled = !current.canKick || legal.indices.length < 2;
    for (const id of ['aim-angle','kick-power','aim-cancel']) ui[id].disabled = !current.canKick;
    ui['kick-btn'].disabled = !current.canKick || power() <= 5;
    ui['aim-angle'].value = String(Math.round(angle));
    ui['kick-power'].value = String(Math.round(basePower));
    ui['kick-power-value'].value = `${Math.round(power())}%`;
    ui['kick-power'].setAttribute('aria-valuetext', `${Math.round(power())} percent kick power`);
    ui['player-control-status'].textContent = notice || current.reason;
    ui['player-control-status'].setAttribute('data-error', String(!!notice));
    onSelect(team, selectedIndex, current.canKick && legal.indices.includes(selectedIndex));
  }
  function select(index) {
    const legal = rule();
    if (!access().canKick || !legal.indices.includes(index)) return;
    cancelGesture(); selectedIndex = index; resetAim(); notice = ''; sync();
    options.onDirty?.();
  }
  function cycle(direction) {
    const indices = rule().indices;
    if (indices.length < 2) return;
    select(indices[(indices.indexOf(selectedIndex)+direction+indices.length)%indices.length]);
  }
  function aimChanged() { notice = ''; preview = true; drawAim(); sync(); }
  function kick() {
    sync();
    if (!access().canKick || power() <= 5) return;
    const command = {index:selectedIndex,angle,power:power()};
    cancelGesture(false); notice = '';
    Promise.resolve(onKick(command.index,command.angle,command.power)).catch(error => {
      notice = error.message || 'Could not submit your move. Try again.'; sync();
    });
  }
  ui['player-select'].addEventListener('change', event => select(Number(event.target.value)));
  ui['player-prev'].addEventListener('click', () => cycle(-1));
  ui['player-next'].addEventListener('click', () => cycle(1));
  ui['aim-angle'].addEventListener('input', event => {
    const value = event.target.valueAsNumber;
    if (access().canKick && Number.isFinite(value)) { angle=normalizeAngle(value); aimChanged(); }
  });
  ui['kick-power'].addEventListener('input', event => {
    if (access().canKick) { basePower=Number(event.target.value); aimChanged(); }
  });
  ui['kick-btn'].addEventListener('click', kick);
  ui['aim-cancel'].addEventListener('click', () => { cancelGesture(); resetAim(); sync(); });

  document.addEventListener('keydown', event => {
    const target = event.target;
    if (event.ctrlKey || event.metaKey || event.altKey || target.closest?.('input,textarea,select,button,a,[contenteditable="true"],[role="textbox"]') || target.isContentEditable || [...document.querySelectorAll('[role="dialog"][aria-modal="true"]:not([hidden])')].some(dialog => dialog.getClientRects().length)) return;
    if (target !== canvas && target !== document.body) return;
    if (event.key === 'Escape') { cancelGesture(); sync(); return; }
    if (!access().canKick) return;
    const step=event.shiftKey?1:5, key=event.key.toLowerCase();
    if (key==='enter' || key===' ') { event.preventDefault(); if(!event.repeat) kick(); return; }
    if (key==='q') { event.preventDefault(); if(!event.repeat) cycle(event.shiftKey?-1:1); return; }
    if (['s','a','w','n'].includes(key)) {
      event.preventDefault(); setPassType({s:'short',a:'long',w:'through',n:'normal'}[key]); return;
    }
    if (event.key==='ArrowLeft') angle=normalizeAngle(angle-step);
    else if (event.key==='ArrowRight') angle=normalizeAngle(angle+step);
    else if (event.key==='ArrowUp') basePower=Math.min(100,basePower+step);
    else if (event.key==='ArrowDown') basePower=Math.max(0,basePower-step);
    else return;
    event.preventDefault(); cancelGesture(false); aimChanged();
  });
  function setPassType(value) {
    if (!['normal','short','long','through'].includes(value)) return;
    passType=value;
    document.querySelectorAll('.pass-types button').forEach(button => {
      const active=button.id===`pass-${value}`;
      button.style.background=''; button.style.color=''; button.setAttribute('aria-pressed',String(active));
    });
    if (access().canKick) aimChanged(); else sync();
  }

  // Capture phase owns a player gesture before OrbitControls sees it. Empty
  // pitch and secondary buttons remain available for camera navigation.
  canvas.addEventListener('pointerdown', event => {
    if (gesture) {
      if (gesture.id !== event.pointerId) cancelGesture();
      event.preventDefault(); event.stopImmediatePropagation(); sync(); return;
    }
    if (event.button !== 0 || !event.isPrimary || !access().canKick) return;
    const legal=rule(), index=hitTest(event.clientX,event.clientY,legal);
    if (index === null || !legal.indices.includes(index)) return;
    const point=getGround(event.clientX,event.clientY);
    if (!point) return;
    select(index);
    const home=origin(); if(!home) return;
    gesture={id:event.pointerId,x:event.clientX,y:event.clientY,anchor:{x:point.x,y:point.y},home:{x:home.x,y:home.y},moved:false,stamp:stateStamp(),angle,basePower,orbitEnabled:getOrbitEnabled()};
    setOrbitEnabled(false); options.onHover?.(false);
    canvas.focus({preventScroll:true});
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
    event.preventDefault(); event.stopImmediatePropagation();
  },true);
  function updateGesture(event) {
    if (!gesture || gesture.id !== event.pointerId) return false;
    if (gesture.stamp!==stateStamp() || !access().canKick) { cancelGesture(); sync(); return false; }
    if (!gesture.moved && Math.hypot(event.clientX-gesture.x,event.clientY-gesture.y)<6) return false;
    const point=getGround(event.clientX,event.clientY); if(!point) return false;
    gesture.moved=true;
    const pull=shotFromPull(gesture.home,{x:gesture.home.x+point.x-gesture.anchor.x,y:gesture.home.y+point.y-gesture.anchor.y},110,passType,getState()?.power_cap);
    angle=pull.angle; basePower=pull.basePower; aimChanged(); return true;
  }
  canvas.addEventListener('pointermove', event => {
    if(gesture) {
      if(event.pointerId!==gesture.id) return;
      updateGesture(event); event.preventDefault(); event.stopImmediatePropagation();
    } else options.onHover?.(access().canKick && hitTest(event.clientX,event.clientY,rule())!==null);
  },true);
  canvas.addEventListener('pointerup', event => {
    if(!gesture || event.pointerId!==gesture.id) return;
    updateGesture(event);
    const fire=!!gesture?.moved && gesture.stamp===stateStamp() && access().canKick && power()>5;
    cancelGesture(!fire); sync();
    event.preventDefault(); event.stopImmediatePropagation();
    if(fire) kick();
  },true);
  for(const event of ['pointercancel','lostpointercapture']) canvas.addEventListener(event, e => {
    if(gesture?.id===e.pointerId) { cancelGesture(); sync(); }
  });
  window.addEventListener('blur',()=>{cancelGesture();sync();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelGesture();sync();}});
  return { sync, select, setPassType, cancel:()=>{cancelGesture();sync();}, reportError:message=>{notice=message;sync();},
    get selectedIndex(){return selectedIndex;}, get team(){return team;}, get dragging(){return !!gesture;},
    snapshot:()=>({team,selectedIndex,allowedIndices:rule().indices,angle,basePower,power:power(),dragging:!!gesture,pointerId:gesture?.id??null,busy:isBusy(),canKick:access().canKick,reason:access().reason,ball:getState()?.ball?{x:getState().ball.x,y:getState().ball.y}:null}) };
}
