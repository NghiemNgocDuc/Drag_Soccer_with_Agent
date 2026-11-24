import test from 'node:test';
import assert from 'node:assert/strict';
import {matchClockView} from '../../static/js/game/match-clock.js';
const base={half_length:45,start_time:1000};
test('setup time never advances the clock or requests a period transition',()=>{
  const view=matchClockView({...base,match_started:false,clock_running:true},99999999);
  assert.deepEqual(view,{text:'00:00',title:'Before kickoff',due:false});
  assert.equal(matchClockView({...base,match_started:true},1001000).text,'00:20');
});
test('MM:SS clock reaches regulation and both extra-time boundaries without decimals',()=>{
  for(const [period,elapsed,text] of [['regular_first',135,'45:00'],['regular_second',270,'90:00'],['et_first',315,'105:00'],['et_second',360,'120:00']]){
    const view=matchClockView({...base,period},(1000+elapsed)*1000);
    assert.equal(view.text,text);assert.equal(view.due,true);
    assert.equal(matchClockView({...base,period},(1001+elapsed)*1000).text,text);
  }
});
test('terminal clock stays frozen during penalties and after regulation win',()=>{
  assert.equal(matchClockView({...base,period:'penalties',penalty_shootout:true},9000000).text,'120:00');
  const view=matchClockView({...base,game_over:true,match_clock_seconds:5400},9000000);
  assert.equal(view.text,'90:00');assert.equal(view.due,false);
});
test('clock advances in whole seconds and keeps explicit untimed practice',()=>{
  assert.equal(matchClockView(base,1001234).text,'00:24');
  assert.equal(matchClockView({...base,half_length:0},1001234).text,'First to 5');
});
