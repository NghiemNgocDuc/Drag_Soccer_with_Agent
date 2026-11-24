import assert from 'node:assert/strict';
import test from 'node:test';
import {createMatchSetup} from '../../static/js/game/match-setup.js';

// Exercise the real controller lifecycle without requiring a browser for draft
// state transitions. Rendering stays covered by the Playwright setup verifier.
class Element {
  constructor(tag='div') {
    this.tag=tag;this.children=[];this.handlers={};this.dataset={};
    this.classList={toggle:()=>{}};
  }
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(){}
  addEventListener(type,handler){this.handlers[type]=handler;}
  querySelectorAll(selector){
    assert.equal(selector,'input');
    return this.children.flatMap(child=>[...(child.tag==='input'?[child]:[]),...(child.querySelectorAll?.(selector)||[])]);
  }
  focus(){}
}

function harness(t, overrides={}) {
  let state={start_time:100,sport:'soccer',game_mode:'hvai',match_started:false,
    formation_a:'2-1-1',formation_b:'2-1-1',playstyle_a:3,playstyle_b:3,...overrides};
  const ids=['match-setup','setup-teams','match-tactics-summary','match-start-btn',
    'match-setup-status','match-setup-retry','match-options','match-guide','fs-wrap','player-controls'];
  const nodes=Object.fromEntries(ids.map(id=>[id,new Element()]));
  const globals={document:{getElementById:id=>nodes[id],querySelector:()=>new Element(),
    createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)},
    fetch:async()=>({ok:true,json:async()=>({
      sports:[{id:'soccer',name:'Soccer'}],
      formations:[{id:'2-1-1',name:'Balanced',description:'Balanced lineup',
        positions:[{number:1,role:'goalkeeper',x:.062,y:.5}]}],
    })})};
  const originals=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
  t.after(()=>{for(const [key,descriptor] of originals){
    if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
  }});
  const submissions=[];
  const setup=createMatchSetup({getState:()=>state,getOnline:()=>({active:false}),blockedReason:()=>'',
    createBoard:()=>({root:new Element(),setFormation(){},setDisabled(){}}),
    createLineup:()=>({root:new Element(),setFormation(){},setDisabled(){}}),
    onStart:async payload=>submissions.push(payload)});
  return {setup,nodes,submissions,
    update:changes=>{state={...state,...changes};setup.sync();},
    submit:()=>nodes['match-setup'].handlers.submit({preventDefault(){}})};
}

test('local modes submit soccer tactics for exactly the editable teams',async t=>{
  const ui=harness(t);await ui.setup.load();
  for(const game_mode of ['hvh','aivai','hvai']){
    ui.update({game_mode});
    await ui.submit();
    const payload=ui.submissions.at(-1);
    assert.equal(payload.sport,'soccer');
    assert.equal(payload.formation_a,'2-1-1');
    assert.equal(payload.playstyle_a,3);
    assert.equal(Object.hasOwn(payload,'formation_b'),game_mode!=='hvai');
    assert.equal(Object.hasOwn(payload,'player_plans_a'),game_mode!=='hvh');
    assert.equal(Object.hasOwn(payload,'player_plans_b'),game_mode==='aivai');
  }
});

test('kickoff collapses secondary controls once and New Game reopens setup',async t=>{
  const ui=harness(t);await ui.setup.load();
  assert.equal(ui.nodes['match-options'].open,true);
  assert.equal(ui.nodes['fs-wrap'].hidden,true);
  ui.update({match_started:true});
  assert.equal(ui.nodes['match-options'].open,false);
  assert.equal(ui.nodes['fs-wrap'].hidden,false);
  ui.nodes['match-options'].open=true;
  ui.nodes['match-guide'].open=true;
  ui.update({kick_count:1});
  assert.equal(ui.nodes['match-options'].open,true);
  assert.equal(ui.nodes['match-guide'].open,true);
  const before=ui.submissions.length;
  await ui.submit();
  assert.equal(ui.submissions.length,before);
  ui.update({match_started:false,start_time:200});
  assert.equal(ui.nodes['match-options'].open,true);
  assert.equal(ui.nodes['match-guide'].open,false);
  assert.equal(ui.nodes['fs-wrap'].hidden,true);
});
