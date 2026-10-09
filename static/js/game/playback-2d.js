import { createTrajectoryPlayback, createTrajectorySampler } from './trajectory-playback.js?v=2';

// The ball, both teams, and the referee share the recorded simulation clock.
export function playTrajectory2D(trajectory, {loop, speed=1, current=()=>true, onFrame, onBounce=()=>{}, onDone}) {
  if (!current() || !trajectory?.length) {onDone();return;}
  if (trajectory.length===1) {onFrame(trajectory[0]);onDone();return;}
  const clock=createTrajectoryPlayback(trajectory,speed), motion=createTrajectorySampler(trajectory);
  let start=null;
  function frame(now) {
    if(!current()){onDone();return;}
    if(start===null)start=now;
    const sample=clock.sample(now-start),a=trajectory[sample.segIdx],b=trajectory[sample.segIdx+1];
    const result=motion.ball(sample);
    for(const side of ['a','b']) {
      if(Array.isArray(a[side])&&Array.isArray(b[side])) result[side]=a[side].map((p,i)=>motion.player(side,i,sample)||p);
    }
    if(a.ref&&b.ref)result.ref={x:a.ref.x+(b.ref.x-a.ref.x)*sample.segFrac,y:a.ref.y+(b.ref.y-a.ref.y)*sample.segFrac};
    for(const point of clock.takeBounces(sample.progress))onBounce(point);
    onFrame(sample.done?trajectory.at(-1):result);
    if(sample.done)onDone();else loop.requestAnimationFrame(frame);
  }
  loop.requestAnimationFrame(frame);
}
