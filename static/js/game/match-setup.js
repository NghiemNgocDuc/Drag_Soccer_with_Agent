// Pre-match drafts stay local until one explicit kickoff request commits them.
import { createTacticsBoard, emptyPlans } from './tactics-board.js';
import { createLineupEditor } from './lineup-editor.js';
export const PLAYSTYLES = [
  ['Deep defend', 'Drop deeper, stay compact and protect your goal.'],
  ['Defend', 'Keep cover behind the ball and offer safer passes.'],
  ['Balanced', 'Balance defensive cover with forward support.'],
  ['Attack', 'Push higher and wider to create forward passing options.'],
  ['All-out attack', 'Commit more players forward, leaving less cover behind.'],
];

const svgNS = 'http://www.w3.org/2000/svg';
const text = (tag, value, className) => {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
};

function pitchPreview(formation, human) {
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', '0 0 280 190');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', formation.id + ' formation, ' + formation.positions.map(p => `number ${p.number} ${p.role}`).join(', '));
  const shape = (tag, attrs) => {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    svg.append(node);
    return node;
  };
  shape('rect', {x:1,y:1,width:278,height:188,rx:12,fill:'#103f38'});
  shape('path', {d:'M14 14H266V176H14Z M14 60H46V130H14',stroke:'#b9d6cc','stroke-opacity':'.55','stroke-width':1.2,fill:'none'});
  shape('path', {d:'M266 68A27 27 0 0 0 266 122',stroke:'#b9d6cc','stroke-opacity':'.55',fill:'none'});
  for (const player of formation.positions) {
    // Expand the team's own half so the actual formation is easy to read.
    const x = 14 + player.x * 504, y = 14 + player.y * 162;
    const you = human && player.number === 5;
    if (you) shape('circle', {cx:x,cy:y,r:16,fill:'none',stroke:'#fde68a','stroke-width':2});
    shape('circle', {cx:x,cy:y,r:12,fill:player.number === 1?'#fbbf24':'#d9f5f7',stroke:'#071e25','stroke-width':1.5});
    const number = shape('text', {x,y:y+4,'text-anchor':'middle',fill:'#072631','font-size':12,'font-weight':800});
    number.textContent = player.number;
    if (you) { const label = shape('text',{x,y:y-21,'text-anchor':'middle',fill:'#fde68a','font-size':9,'font-weight':800}); label.textContent='YOU'; }
  }
  return svg;
}

export function createMatchSetup({getState, getOnline, blockedReason, onStart, createBoard=createTacticsBoard, createLineup=createLineupEditor}) {
  const root = document.getElementById('match-setup');
  const teams = document.getElementById('setup-teams');
  const summary = document.getElementById('match-tactics-summary');
  const start = document.getElementById('match-start-btn');
  const status = document.getElementById('match-setup-status');
  const retry = document.getElementById('match-setup-retry');
  const review = document.getElementById('match-plan-review');
  let catalog = [], loading = false, saving = false, error = '', stamp = '', built = '';
  let drafts = {}, panels = new Map();
  let planDrafts = {a:emptyPlans(),b:emptyPlans()}, planStamp='';
  let reviewStamp='', wasPending;
  let lineupDrafts={a:null,b:null};

  function formationFor(value) { return catalog.find(item => item.id === value) || catalog[0]; }
  function withLineup(formation,lineup){return lineup?{...formation,positions:lineup.map((p,i)=>({...p,number:i+1}))}:formation;}
  function refreshPanel(side) {
    const panel = panels.get(side), draft = drafts[side];
    if (!panel || !draft) return;
    const formation = withLineup(formationFor(draft.formation),lineupDrafts[side]);
    panel.board?.setFormation(formation);
    for (const input of panel.forms) input.checked = input.value === draft.formation;
    for (const input of panel.styles) input.checked = Number(input.value) === draft.level;
    panel.preview.replaceChildren(pitchPreview(formation, side === 'a' && getState().game_mode === 'hvai'));
    panel.roles.textContent = formation.positions.map(p => `#${p.number} ${p.role === 'attacker'?'Attacker':p.role === 'support'?'Midfielder':p.role === 'defender'?'Defender':'Keeper'}`).join(' · ');
    panel.style.dataset.level = String(draft.level);
    panel.name.textContent = PLAYSTYLES[draft.level-1][0];
    panel.help.textContent = PLAYSTYLES[draft.level-1][1];
  }

  function build(sides) {
    panels = new Map(); teams.replaceChildren();
    for (const side of sides) {
      const panel = text('section', '', 'setup-team');
      panel.id=`match-setup-team-${side}`;
      panel.setAttribute('aria-label', `Team ${side.toUpperCase()} tactics`);
      panel.append(text('h3', sides.length === 1 ? 'Your lineup' : `Team ${side.toUpperCase()}`));
      const layout = text('div', '', 'setup-formation-layout');
      const visual = text('div', '', 'setup-visual');
      const preview = text('div', '', 'setup-pitch'); preview.id = `setup-preview-${side}`;
      const roles = text('p', '', 'setup-roles');
      visual.append(preview, roles);
      const field = text('fieldset', '', 'setup-formations'); field.append(text('legend','Formation'));
      const forms = [];
      for (const formation of catalog) {
        const label = text('label', '', 'setup-formation-option');
        const input = document.createElement('input'); input.type='radio'; input.name=`formation-${side}`; input.value=formation.id;
        const copy = text('span', '', 'setup-formation-copy');
        copy.append(text('strong', formation.id + ' · ' + formation.name), text('small', formation.description));
        label.append(input,copy); field.append(label); forms.push(input);
        input.addEventListener('change',()=>{drafts[side].formation=input.value;lineupDrafts[side]=null;panels.get(side)?.lineup?.setFormation(formationFor(input.value));refreshPanel(side);});
      }
      layout.append(visual,field); panel.append(layout);
      const lineup=createLineup({side,formation:formationFor(drafts[side].formation),lineup:lineupDrafts[side],human:side==='a'&&getState().game_mode==='hvai',onChange:value=>{lineupDrafts[side]=value;refreshPanel(side);}});
      panel.append(lineup.root);
      const style = text('fieldset','','playstyle-control setup-playstyle');
      style.hidden = getState().game_mode === 'hvh';
      style.append(text('legend','Playstyle'));
      const track = text('div','','playstyle-track'), styles=[];
      PLAYSTYLES.forEach(([name],index)=>{
        const label=text('label',''),input=document.createElement('input');
        input.type='radio';input.name=`setup-playstyle-${side}`;input.value=String(index+1);
        label.append(input,text('span',name,'playstyle-sr-only'));track.append(label);styles.push(input);
        input.addEventListener('change',()=>{drafts[side].level=Number(input.value);refreshPanel(side);});
      });
      const ends=text('div','','playstyle-endpoints');ends.setAttribute('aria-hidden','true');ends.append(text('span','Defend'),text('span','Attack'));
      const copy=text('div','','playstyle-copy'),name=text('strong',''),help=text('p','');
      help.id=`setup-playstyle-help-${side}`;style.setAttribute('aria-describedby',help.id);
      copy.append(name,help);style.append(track,ends,copy);panel.append(style);teams.append(panel);
      let board=null;
      if(getState().game_mode!=='hvh' && getState().team_movement!==false){
        board=createBoard({side,formation:formationFor(drafts[side].formation),plans:planDrafts[side],
          human:side==='a'&&getState().game_mode==='hvai',onChange:plans=>{planDrafts[side]=plans;}});
        panel.append(board.root);
      }
      panels.set(side,{forms,styles,preview,roles,style,name,help,board,lineup});refreshPanel(side);
    }
  }

  function sync() {
    const state=getState(), online=getOnline().active;
    const pending=!!state && state.match_started === false && !state.penalty_shootout && !online;
    // Close secondary settings once at kickoff; later refreshes preserve the
    // player's disclosure choice. New Game makes setup options visible again.
    if (state && pending !== wasPending) {
      const options=document.getElementById('match-options');
      if(options) options.open=pending;
      const guide=document.getElementById('match-guide');
      if(guide) guide.open=false;
      wasPending=pending;
    }
    root.hidden=!pending;
    document.getElementById('fs-wrap').hidden=!state || pending;
    document.getElementById('player-controls').hidden=!state || pending;
    summary.hidden=!state || pending || online || !!state.penalty_shootout;
    if(review){
      const sides=state?.game_mode==='hvai'?['a']:['a','b'];
      const planned=sides.filter(side=>Array.isArray(state?.['player_plans_'+side])&&state['player_plans_'+side].some(p=>p.mode!=='auto'));
      review.hidden=!state||pending||online||state.penalty_shootout||!planned.length;
      if(!review.hidden&&catalog.length){
        const key=JSON.stringify(planned.map(side=>[side,state['formation_'+side],state['custom_lineup_'+side],state['player_plans_'+side]]));
        if(key!==reviewStamp){
          reviewStamp=key;review.replaceChildren();
          for(const side of planned){
            const board=createBoard({side,formation:withLineup(formationFor(state['formation_'+side]),state['custom_lineup_'+side]),plans:state['player_plans_'+side],human:side==='a'&&state.game_mode==='hvai',onChange:()=>{}});
            board.root.querySelector('summary').textContent=`Team ${side.toUpperCase()} · saved movement plan`;
            board.setReadOnly();review.append(board.root);
          }
        }
      }
    }
    if (!summary.hidden) {
      const sides=state.game_mode==='hvai'?['a']:['a','b'];
      summary.textContent=sides.map(side=>`${state.game_mode==='hvai'?'Your team':'Team '+side.toUpperCase()}: ${state['custom_lineup_'+side] ? 'Custom '+['defender','support','attacker'].map(role=>state['custom_lineup_'+side].filter(p=>p.role===role).length).join('-') : state['formation_'+side] || '2-1-1'}${state.game_mode==='hvh'?'':' · '+PLAYSTYLES[(state['playstyle_'+side]||3)-1]?.[0]}`).join('  /  ')+' · Choose new tactics with New Game.';
    }
    if (!pending) { stamp=''; built=''; planStamp=''; return; }
    const nextStamp=[state.start_time,state.sport,state.game_mode,state.formation_a,state.formation_b,state.playstyle_a,state.playstyle_b].join(':');
    if (stamp!==nextStamp && catalog.length) {
      stamp=nextStamp; built='';error='';
      // Mode changes rebuild the team panels within the same setup. Keep its
      // unsaved plans and positions until a new match/reset establishes another draft.
      const nextPlanStamp=[state.start_time,state.sport].join(':');
      if(planStamp!==nextPlanStamp){
        for(const side of ['a','b']){
          const saved=state['player_plans_'+side];
          planDrafts[side]=Array.isArray(saved)&&saved.length===5?structuredClone(saved):emptyPlans();
          lineupDrafts[side]=state['custom_lineup_'+side]?structuredClone(state['custom_lineup_'+side]):null;
        }
        planStamp=nextPlanStamp;
      }
      drafts=Object.fromEntries(['a','b'].map(side=>[side,{formation:formationFor(state['formation_'+side]).id,level:Number.isInteger(state['playstyle_'+side])&&state['playstyle_'+side]>=1&&state['playstyle_'+side]<=5?state['playstyle_'+side]:3}]));
    }
    const sides=state.game_mode==='hvai'?['a']:['a','b'];
    document.querySelector('.setup-match-tag').textContent=state.game_mode==='hvai'?'5-a-side · You are #5':'5-a-side';
    const buildStamp=stamp;
    if(catalog.length && built!==buildStamp){build(sides);built=buildStamp;}
    const reason=blockedReason();
    const disabled=saving||loading||!!reason||!catalog.length;
    root.setAttribute('aria-busy',String(saving||loading));
    for (const input of teams.querySelectorAll('input')) input.disabled=disabled;
    for (const panel of panels.values()) panel.board?.setDisabled(disabled);
    for (const panel of panels.values()) panel.lineup?.setDisabled(disabled);
    start.disabled=disabled;start.textContent=saving?'Starting match…':'Start match';
    retry.hidden=!!catalog.length||loading;retry.disabled=saving||!!reason;
    status.textContent=saving?'Starting your match…':error||reason||(loading?'Loading formations…':'Your choices apply at kickoff. The clock is stopped until you start.');
    status.classList.toggle('is-error',!!error);
  }

  async function load() {
    if (loading||catalog.length) return;
    loading=true;error='';sync();
    try {
      const response=await fetch('/match_setup');
      const data=await response.json();
      if(!response.ok||!Array.isArray(data.formations)||!data.formations.length)throw new Error('Could not load formations. Try again.');
      catalog=data.formations;
    }catch(failure){error='Could not load formations. Try again.';}
    finally{loading=false;sync();}
  }
  root.addEventListener('submit',async event=>{
    event.preventDefault();
    if(start.disabled||saving||getState()?.match_started!==false)return;
    const payload={sport:'soccer'};
    payload.custom_lineup_a=lineupDrafts.a;
    if(getState().game_mode!=='hvai')payload.custom_lineup_b=lineupDrafts.b;
    Object.assign(payload,{formation_a:drafts.a.formation,playstyle_a:drafts.a.level});
    if(getState().game_mode!=='hvai')Object.assign(payload,{formation_b:drafts.b.formation,playstyle_b:drafts.b.level});
    if(getState().game_mode!=='hvh'&&getState().team_movement!==false){
      payload.player_plans_a=structuredClone(planDrafts.a);
      if(getState().game_mode==='hvai')payload.player_plans_a[4]={mode:'auto',points:[]};
      else payload.player_plans_b=structuredClone(planDrafts.b);
    }
    saving=true;error='';sync();
    try{await onStart(payload);}
    catch(failure){if(failure.name!=='AbortError')error=failure.message||'Could not start the match. Try again.';}
    finally{saving=false;sync();if(!root.hidden && error)start.focus();}
  });
  retry.addEventListener('click',()=>void load());
  return {sync,load};
}
