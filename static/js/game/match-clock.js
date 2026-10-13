// The accelerated clock is independent of canvas rendering (20 match seconds/s).
export function matchClockView(state, nowMs = Date.now()) {
  if (state.half_length === 0) return {text:`First to ${state.win_goal_limit || 5}`, title:'Untimed match', due:false};
  const half = (state.half_length || 45) * 3;
  const boundaries = {regular_first:half, regular_second:half*2, et_first:half*2+45, et_second:half*2+90};
  const period = state.period || 'regular_first';
  const running = state.clock_running ?? (!state.game_over && !state.penalty_shootout);
  const boundary = state.next_period_at ?? boundaries[period] ?? half*2+90;
  const elapsed = Math.max(0, nowMs/1000 - (state.start_time ?? nowMs/1000));
  const stopped = state.match_clock_seconds ?? ((state.clock_stopped_at ??
    (period.startsWith('et_') || state.penalty_shootout ? half*2+90 : half*2))*20);
  const seconds = Math.max(0, Math.floor(running ? Math.min(elapsed,boundary)*20 : stopped));
  const labels = {regular_first:'First half',regular_second:'Second half',et_first:'Extra time · first half',et_second:'Extra time · second half',penalties:'Penalty shootout'};
  return {text:`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`,
    title:state.game_over?'Full time':labels[period], due:running && elapsed >= boundary};
}
