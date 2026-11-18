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
    'match-setup-status','match-setup-retry','setup-sports','fs-wrap','player-controls'];
  const nodes=Object.fromEntries(ids.map(id=>[id,new Element()]));
  const globals={document:{getElementById:id=>nodes[id],querySelector:()=>new Element(),
    createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)},
    fetch:async()=>({ok:true,json:async()=>({
      sports:[{id:'soccer',name:'Soccer'},{id:'basketball',name:'Basketball'}],
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
  const radio=value=>nodes['setup-sports'].querySelectorAll('input').find(input=>input.value===value);
  return {setup,nodes,submissions,radio,
    choose:value=>{const input=radio(value);input.checked=true;input.handlers.change();},
    update:changes=>{state={...state,...changes};setup.sync();},
    submit:()=>nodes['match-setup'].handlers.submit({preventDefault(){}})};
}

test('basketball setup draft survives local mode changes and starts without soccer settings',async t=>{
  const ui=harness(t);await ui.setup.load();
  ui.choose('basketball');
  for(const game_mode of ['hvh','aivai','hvai']){
    ui.update({game_mode});
    assert.equal(ui.radio('basketball').checked,true);
    assert.equal(ui.radio('soccer').checked,false);
    assert.equal(ui.nodes['setup-teams'].querySelectorAll('input').length,0);
  }
  await ui.submit();
  assert.deepEqual(ui.submissions,[{sport:'basketball'}]);
});

test('new setup and resumed setup restore the saved sport instead of leaking an old draft',async t=>{
  const ui=harness(t);await ui.setup.load();
  ui.choose('basketball');
  ui.update({start_time:200});
  assert.equal(ui.radio('soccer').checked,true);
  ui.update({sport:'basketball',start_time:300});
  assert.equal(ui.radio('basketball').checked,true);
  ui.choose('soccer');
  ui.update({match_started:true});
  ui.update({match_started:false});
  assert.equal(ui.radio('basketball').checked,true);
});
