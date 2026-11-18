// A bounded drawing editor. It stores intentions; the server executes physics.
import { ROLE_BEHAVIOR } from './role-behavior.js';
export const MAX_WAYPOINTS = 16;
const ns = 'http://www.w3.org/2000/svg';
export const emptyPlans = () => Array.from({length:5}, () => ({mode:'auto', points:[]}));
export function clampPoint(point, index) {
  return {x: Math.max(.06, Math.min(index === 0 ? .14 : .94, point.x)),
    y: Math.max(index === 0 ? .40 : .09, Math.min(index === 0 ? .60 : .91, point.y))};
}
const gap = (a,b) => Math.hypot((a.x-b.x)*1400, (a.y-b.y)*875);

export function simplifyRun(points, limit=MAX_WAYPOINTS) {
  // Remove the least significant corner until the bounded route fits, retaining
  // endpoints. This keeps bends instead of simply truncating a long drawing.
  const out = [];
  for(const point of points)if(!out.length||gap(point,out.at(-1))>=5)out.push({...point});
  if(points.length && (!out.length || gap(out[out.length-1],points[points.length-1]) > .1)) out.push({...points[points.length-1]});
  while(out.length > 2) {
    let best=1, error=Infinity;
    for(let i=1;i<out.length-1;i++) {
      const a=out[i-1], b=out[i], c=out[i+1];
      const dx=(c.x-a.x)*1400, dy=(c.y-a.y)*875, length=dx*dx+dy*dy;
      const t=length?Math.max(0,Math.min(1,((b.x-a.x)*1400*dx+(b.y-a.y)*875*dy)/length)):0;
      const deviation=Math.hypot((b.x-a.x)*1400-t*dx,(b.y-a.y)*875-t*dy);
      if(deviation<error){error=deviation;best=i;}
    }
    if(out.length<=limit && error>8)break;
    out.splice(best,1);
  }
  return out;
}

export function createTacticsBoard({side, formation, plans, human, onChange}) {
  const root=document.createElement('details'); root.className='tactics-board';
  const summary=document.createElement('summary'); summary.textContent='Draw player instructions'; root.append(summary);
  const content=document.createElement('div');content.className='tactics-content';root.append(content);
  const intro=document.createElement('p');intro.className='tactics-intro';
  intro.textContent='Choose a player, then draw a run or tap a destination. Runs advance across kicks and hold at the end. Instructions lock at kickoff.';
  content.append(intro);
  const roster=document.createElement('div');roster.className='tactics-roster';roster.setAttribute('role','group');roster.setAttribute('aria-label','Choose a player to instruct');content.append(roster);
  const toolbar=document.createElement('div');toolbar.className='tactics-toolbar';content.append(toolbar);
  const modeLabel=document.createElement('label');modeLabel.textContent='Instruction';
  const mode=document.createElement('select');mode.id=`tactics-mode-${side}`;modeLabel.htmlFor=mode.id;
  for(const [value,label] of [['auto','Automatic tactics'],['route','Draw a run'],['hold','Hold a position']]){
    const option=document.createElement('option');option.value=value;option.textContent=label;mode.append(option);
  }
  modeLabel.append(mode);toolbar.append(modeLabel);
  const button=(label,action)=>{const el=document.createElement('button');el.type='button';el.className='btn btn-secondary';el.textContent=label;el.addEventListener('click',action);toolbar.append(el);return el;};
  const undo=button('Undo point',()=>{
    if(locked||manual())return;
    const p=draft[selected];p.points.pop();if(!p.points.length)p.mode='auto';commit();
  });
  const clear=button('Clear player',()=>{if(!locked&&!manual()){draft[selected]={mode:'auto',points:[]};commit();}});
  const svg=document.createElementNS(ns,'svg');svg.classList.add('tactics-pitch');svg.setAttribute('viewBox','0 0 1400 875');svg.setAttribute('tabindex','0');svg.setAttribute('role','application');
  svg.setAttribute('aria-label',`Team ${side.toUpperCase()} tactics drawing board. Arrow keys move the cursor. Enter adds a waypoint. Escape cancels a drawing.`);
  content.append(svg);
  const status=document.createElement('p');status.className='tactics-status';status.setAttribute('role','status');content.append(status);
  const help=document.createElement('p');help.className='tactics-help';
  help.textContent='Both boards face right; Team B is mirrored in play. Drag from a numbered player to replace its run, or tap to add waypoints. Keyboard: arrows move the cursor, Enter adds a point. Keeper stays in the marked area. Players still kick normally; blocked routes wait or take a small sidestep. Goals and halftime restart the runs.';
  content.append(help);
  let layout=formation, draft=structuredClone(plans || emptyPlans()), selected=1, locked=false, stroke=null;
  if(human)draft[4]={mode:'auto',points:[]};
  let cursor={...layout.positions[Math.min(selected,layout.positions.length-1)]}, showCursor=false;
  const manual=()=>human&&selected===4;
  const home=()=>clampPoint(layout.positions[selected] || {x:.5,y:.5},selected);
  const nodes=[];
  for(let index=0;index<5;index++){
    const item=document.createElement('button');item.type='button';
    item.addEventListener('click',()=>{if(!locked){selected=index;stroke=null;cursor=home();showCursor=false;render();}});
    roster.append(item);nodes.push(item);
  }
  function commit(){onChange(structuredClone(draft));render();}
  function addPoint(point){
    if(manual()||locked)return;
    const p=draft[selected], next=clampPoint(point,selected);
    if(p.mode==='hold')p.points=[next];
    else {if(p.mode==='auto'){p.mode='route';p.points=[home()];}
      if(p.points.length>=MAX_WAYPOINTS){status.textContent='This run has 16 waypoints. Undo a point or draw a new run.';return;}
      if(!p.points.length||gap(p.points.at(-1),next)>5)p.points.push(next);
    }
    commit();
  }
  mode.addEventListener('change',()=>{
    if(locked||manual())return;
    const last=draft[selected].points.at(-1)||home();
    draft[selected]={mode:mode.value,points:mode.value==='auto'?[]:[{...last}]};commit();
  });
  function shape(tag,attrs,text){const el=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))el.setAttribute(k,v);if(text!==undefined)el.textContent=text;svg.append(el);return el;}
  function render(){
    const p=draft[selected];mode.value=p.mode;
    mode.disabled=locked||manual();undo.disabled=locked||manual()||!p.points.length;clear.disabled=locked||manual()||p.mode==='auto';
    for(let i=0;i<5;i++){
      const role=layout.positions[i]?.role||'player';
      nodes[i].textContent=`#${i+1} ${human&&i===4?'You':role==='goalkeeper'?'Keeper':role==='defender'?'Defender':role==='support'?'Support':'Striker'}`;
      nodes[i].setAttribute('aria-label',nodes[i].textContent);
      nodes[i].title=draft[i].mode==='auto'?'Automatic tactics':draft[i].mode==='hold'?'Hold a position':'Follow a drawn run';
      nodes[i].setAttribute('aria-pressed',String(i===selected));nodes[i].disabled=locked;
      nodes[i].dataset.mode=draft[i].mode;
    }
    svg.replaceChildren();
    shape('rect',{x:0,y:0,width:1400,height:875,rx:28,fill:'#164b40'});
    shape('path',{d:'M20 20H1380V855H20Z M700 20V855 M20 295H175V580H20 M1380 295H1225V580H1380',stroke:'#9dbbb0','stroke-width':4,fill:'none',opacity:.7});
    shape('circle',{cx:700,cy:437.5,r:110,stroke:'#9dbbb0','stroke-width':3,fill:'none',opacity:.7});
    if(selected===0)shape('rect',{x:84,y:350,width:112,height:175,fill:'#efc995','fill-opacity':.15,stroke:'#efc995','stroke-width':3,'stroke-dasharray':'8 8'});
    shape('text',{x:1270,y:65,'text-anchor':'end',fill:'#fff7ee','font-size':25,'font-weight':700},'ATTACK →');
    for(let i=0;i<5;i++){
      const plan=draft[i], points=stroke&&i===selected?stroke.points:plan.points;
      const color=i===selected?'#ffd598':'#e1a6b5';
      if(points.length){
        const start=layout.positions[i];
        const line=[start,...points];
        shape('polyline',{points:line.map(pt=>`${pt.x*1400},${pt.y*875}`).join(' '),fill:'none',stroke:color,'stroke-width':i===selected?7:4,'stroke-dasharray':plan.mode==='hold'?'10 10':'none','stroke-linejoin':'round','stroke-linecap':'round',opacity:i===selected?1:.65});
        const end=points.at(-1), previous=points.length>1?points.at(-2):start;
        const x=end.x*1400,y=end.y*875, angle=Math.atan2((end.y-previous.y)*875,(end.x-previous.x)*1400)*180/Math.PI;
        shape('path',{d:'M-20 -13L0 0L-20 13',transform:`translate(${x} ${y}) rotate(${angle})`,stroke:color,'stroke-width':6,fill:'none'});
        points.forEach((pt,j)=>shape('circle',{cx:pt.x*1400,cy:pt.y*875,r:j===points.length-1?12:6,fill:color}));
      }
    }
    layout.positions.forEach((player,i)=>{
      const x=player.x*1400,y=player.y*875;
      if(i===selected)shape('circle',{cx:x,cy:y,r:41,fill:'none',stroke:'#ffd598','stroke-width':5});
      shape('circle',{cx:x,cy:y,r:30,fill:i===0?'#efc995':'#fff7ee',stroke:'#3f1727','stroke-width':3});
      shape('text',{x,y:y+10,'text-anchor':'middle',fill:'#3f1727','font-size':30,'font-weight':800},i+1);
    });
    if(showCursor&&!manual())shape('path',{d:`M${cursor.x*1400-18} ${cursor.y*875}h36 M${cursor.x*1400} ${cursor.y*875-18}v36`,stroke:'#fff7ee','stroke-width':4});
    status.textContent=manual()?'#5 is yours: your kicks control this player. Draw instructions for your AI teammates.':`#${selected+1}: ${p.mode==='auto'?`Automatic — ${ROLE_BEHAVIOR[layout.positions[selected]?.role] || 'Follow the formation and playstyle.'}`:p.mode==='hold'?'hold the marked position.':`${p.points.length} waypoints, then hold.`} ${selected===0?'Keeper movement remains limited.':''}`;
  }
  function at(event){const box=svg.getBoundingClientRect();return {x:(event.clientX-box.left)/box.width,y:(event.clientY-box.top)/box.height};}
  svg.addEventListener('pointerdown',event=>{
    if(locked||stroke||event.button!==0)return;
    const point=at(event), box=svg.getBoundingClientRect();
    const hit=layout.positions.findIndex(p=>Math.hypot((p.x-point.x)*box.width,(p.y-point.y)*box.height)<Math.max(18,30*box.width/1400));
    if(hit>=0)selected=hit;
    svg.focus({preventScroll:true});showCursor=false;
    if(manual()){render();return;}
    stroke={id:event.pointerId,start:point,hit,points:[home()],moved:false};
    svg.setPointerCapture(event.pointerId);render();
  });
  svg.addEventListener('pointermove',event=>{
    if(!stroke||stroke.id!==event.pointerId)return;
    const point=clampPoint(at(event),selected);
    if(gap(point,stroke.start)>12)stroke.moved=true;
    if(stroke.moved&&gap(point,stroke.points.at(-1))>=8){stroke.points.push(point);if(stroke.points.length>128)stroke.points=simplifyRun(stroke.points,64);render();}
  });
  svg.addEventListener('pointerup',event=>{
    if(!stroke||stroke.id!==event.pointerId)return;
    const finished=stroke;stroke=null;
    const point=clampPoint(at(event),selected);
    if(finished.moved){
      draft[selected]=draft[selected].mode==='hold'?{mode:'hold',points:[point]}:{mode:'route',points:simplifyRun([...finished.points,point])};commit();
    }else if(finished.hit<0)addPoint(point);else render();
  });
  const cancel=()=>{stroke=null;render();};
  svg.addEventListener('pointercancel',cancel);svg.addEventListener('lostpointercapture',cancel);svg.addEventListener('blur',()=>{showCursor=false;cancel();});
  svg.addEventListener('keydown',event=>{
    if(event.ctrlKey||event.metaKey||event.altKey||locked)return;
    const moves={ArrowLeft:[-.02,0],ArrowRight:[.02,0],ArrowUp:[0,-.03],ArrowDown:[0,.03]};
    if(moves[event.key]){event.preventDefault();const [x,y]=moves[event.key];cursor=clampPoint({x:cursor.x+x,y:cursor.y+y},selected);showCursor=true;render();}
    else if(event.key==='Enter'||event.key===' '){event.preventDefault();if(!event.repeat)addPoint(cursor);}
    else if(event.key==='Escape'){event.preventDefault();cancel();}
  });
  render();
  return {root,setDisabled(value){locked=value;stroke=null;render();},setFormation(value){layout=value;cursor=home();stroke=null;render();},
    setReadOnly(){locked=true;stroke=null;render();toolbar.hidden=true;roster.hidden=true;status.hidden=true;
      intro.textContent='Your instructions are fixed for this match. Runs advance across kicks, hold their final point, and restart after goals or halftime.';
      help.textContent='Both boards face right. Team B is mirrored on the live pitch. The human striker and the player taking a kick follow their kick command; the other AI players use these off-ball plans. Choose New Game to edit.';
      svg.setAttribute('role','img');svg.setAttribute('aria-label',`Team ${side.toUpperCase()} saved movement routes`);svg.removeAttribute('tabindex');},
  };
}
