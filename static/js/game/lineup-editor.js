// Starting positions are independent of the off-ball movement instructions.
import { ROLE_BEHAVIOR } from './role-behavior.js';
export const ROLE_AREAS = {defender:[.13,.22],support:[.25,.34],attacker:[.37,.45]};
const labels={defender:'Defenders',support:'Midfielders',attacker:'Attackers'};
const ns='http://www.w3.org/2000/svg';
export function clampPosition(point,role){
  const [lo,hi]=ROLE_AREAS[role];
  return {x:Math.max(lo,Math.min(hi,point.x)),y:Math.max(.15,Math.min(.85,point.y))};
}
export function canPlace(players,index,point){
  return players.every((p,i)=>i===index||Math.hypot((p.x-point.x)*1400,(p.y-point.y)*875)>=86);
}
export function changeRole(players,index,role){
  if(index===0||!ROLE_AREAS[role]||players.filter((p,i)=>i!==index&&p.role===role).length>=2)return null;
  const next=structuredClone(players),[lo,hi]=ROLE_AREAS[role];
  for(const y of [players[index].y,.35,.65,.2,.8,.5]){
    const point={x:(lo+hi)/2,y};
    if(canPlace(next,index,point)){next[index]={role,...point};return next;}
  }
  return null;
}
export function createLineupEditor({side,formation,lineup,human,onChange}){
  const el=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;};
  const root=el('details','','lineup-editor');
  root.append(el('summary','Customize starting positions'));
  const content=el('div','','tactics-content');root.append(content);
  content.append(el('p','One keeper + four outfield players. Each role group holds at most two. Select a player, choose a role, then drag or tap in the marked area. Positions lock at kickoff.','tactics-intro'));
  const groups=el('div','','lineup-groups');content.append(groups);
  let preset=formation,players=structuredClone(lineup||formation.positions),selected=1,locked=false,stroke=null;
  const groupNodes={};
  for(const role of Object.keys(ROLE_AREAS)){
    const group=el('section','','lineup-group'),title=el('h4',''),buttons=el('div','','tactics-roster');
    const empty=el('span','Empty','lineup-empty');buttons.append(empty);
    group.append(title,buttons);groups.append(group);groupNodes[role]={title,buttons,empty};
  }
  const playerButtons=players.map((p,index)=>{
    if(index===0)return null;
    const b=el('button',`#${index+1}${human&&index===4?' · You':''}`);b.type='button';b.dataset.player=String(index);
    b.addEventListener('click',()=>{if(locked)return;selected=index;stroke=null;render();});return b;
  });
  const controls=el('div','','tactics-toolbar'),label=el('label','Selected player role'),select=el('select','');
  select.id=`lineup-role-${side}`;label.htmlFor=select.id;
  for(const [role,name] of Object.entries(labels)){const opt=el('option',name);opt.value=role;select.append(opt);}
  label.append(select);controls.append(label);
  const reset=el('button','Use formation preset','btn btn-secondary');reset.type='button';controls.append(reset);content.append(controls);
  const behavior=el('p','','tactics-intro lineup-behavior');behavior.setAttribute('aria-live','polite');content.append(behavior);
  const svg=document.createElementNS(ns,'svg');svg.classList.add('lineup-pitch');svg.setAttribute('viewBox','0 0 700 875');
  svg.setAttribute('role','application');svg.setAttribute('tabindex','0');svg.setAttribute('aria-label',`Team ${side.toUpperCase()} starting positions. Select a player and use arrow keys to move. Escape cancels a drag.`);content.append(svg);
  const status=el('p','','tactics-status');status.setAttribute('role','status');content.append(status);
  content.append(el('p',`Own half, attacking right. Team B is mirrored in play. #1 stays in goal.${human?' #5 is still yours to control, in any role.':''} Keyboard: select a player above, focus the pitch, then use arrow keys. Goals and halftime restore these positions. Choosing a formation preset replaces your positions; drawn destinations stay unchanged.`,'tactics-help'));
  function draw(){
    svg.replaceChildren();
    const shape=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text)n.textContent=text;svg.append(n);return n;};
    shape('rect',{width:700,height:875,fill:'#103f38',rx:16});
    shape('path',{d:'M20 20H680V855H20Z M20 325H135V550H20',fill:'none',stroke:'#b9d6cc','stroke-opacity':.5});
    for(const [role,[lo,hi]]of Object.entries(ROLE_AREAS)){
      shape('rect',{x:lo*1400,y:130,width:(hi-lo)*1400,height:615,fill:'#f5f7fb',opacity:players[selected].role===role?.18:.06,stroke:'#f5f7fb','stroke-dasharray':'8 8'});
      shape('text',{x:(lo+hi)*700,y:90,fill:'#f5f7fb','text-anchor':'middle','font-size':22},role==='support'?'MID':role==='defender'?'DEF':'ATT');
    }
    const visible=players.map((p,i)=>stroke&&i===selected?{...p,...stroke.point}:p);
    for(const [i,p]of visible.entries()){
      shape('circle',{cx:p.x*1400,cy:p.y*875,r:i===selected?32:27,fill:i===0?'#edc887':i===selected?'#edc887':'#f5f7fb',stroke:'#2459d3','stroke-width':i===selected?5:2});
      shape('text',{x:p.x*1400,y:p.y*875+9,fill:'#301a24','text-anchor':'middle','font-size':26,'font-weight':750},String(i+1));
    }
  }
  function render(message){
    for(const [role,group]of Object.entries(groupNodes)){
      const entries=players.map((p,i)=>({...p,index:i})).filter(p=>p.role===role);
      group.title.textContent=`${labels[role]} · ${entries.length}/2`;group.empty.hidden=!!entries.length;
      for(const p of entries){const b=playerButtons[p.index];b.disabled=locked;b.setAttribute('aria-pressed',String(p.index===selected));if(b.parentNode!==group.buttons)group.buttons.append(b);}
    }
    select.value=players[selected].role;select.disabled=locked;reset.disabled=locked;
    behavior.textContent=human&&selected===4?'#5 is controlled by your kicks in every role. Your AI teammates follow their roles automatically.':`Default AI: ${ROLE_BEHAVIOR[players[selected].role]} Drawn runs or hold instructions replace automatic positioning.`;
    for(const opt of select.options)opt.disabled=opt.value!==players[selected].role&&players.filter(p=>p.role===opt.value).length>=2;
    status.textContent=message||`#${selected+1} selected · ${labels[players[selected].role]} · move within the highlighted area.`;
    draw();
  }
  function commit(point){
    if(!canPlace(players,selected,point)){render('Leave more space between players. Position unchanged.');return;}
    players[selected]={...players[selected],...point};onChange(structuredClone(players));render();
  }
  select.addEventListener('change',()=>{if(locked)return;const next=changeRole(players,selected,select.value);if(next){players=next;onChange(structuredClone(players));}render(next?undefined:'This group already has two players.');});
  reset.addEventListener('click',()=>{if(locked)return;players=structuredClone(preset.positions);onChange(null);render('Formation preset restored.');});
  const pointAt=e=>{const b=svg.getBoundingClientRect();return {x:(e.clientX-b.left)/b.width*.5,y:(e.clientY-b.top)/b.height};};
  svg.addEventListener('pointerdown',e=>{
    if(locked||stroke||e.button!==0)return;e.preventDefault();svg.focus();const point=pointAt(e);
    let hit=-1,distance=Infinity;
    players.forEach((p,i)=>{const gap=Math.hypot((p.x-point.x)*1400,(p.y-point.y)*875);if(i>0&&gap<65&&gap<distance){hit=i;distance=gap;}});
    if(hit>=0)selected=hit;
    stroke={id:e.pointerId,point:clampPosition(point,players[selected].role)};svg.setPointerCapture(e.pointerId);render();
  });
  svg.addEventListener('pointermove',e=>{if(stroke?.id!==e.pointerId)return;stroke.point=clampPosition(pointAt(e),players[selected].role);draw();});
  svg.addEventListener('pointerup',e=>{if(stroke?.id!==e.pointerId)return;const point=clampPosition(pointAt(e),players[selected].role);stroke=null;commit(point);});
  const cancel=()=>{if(stroke){stroke=null;render('Move cancelled.');}};
  for(const name of ['pointercancel','lostpointercapture','blur'])svg.addEventListener(name,cancel);
  svg.addEventListener('keydown',e=>{
    if(locked||e.ctrlKey||e.altKey||e.metaKey)return;
    if(e.key==='Escape'){e.preventDefault();cancel();return;}
    if(e.key==='Enter'||e.key===' '){e.preventDefault();return;}
    const delta={ArrowLeft:[-.005,0],ArrowRight:[.005,0],ArrowUp:[0,-.02],ArrowDown:[0,.02]}[e.key];
    if(delta){e.preventDefault();stroke=null;commit(clampPosition({x:players[selected].x+delta[0],y:players[selected].y+delta[1]},players[selected].role));}
  });
  render();
  return {root,setDisabled(value){locked=value;stroke=null;render();},setFormation(value){preset=value;players=structuredClone(value.positions);stroke=null;render();}};
}
