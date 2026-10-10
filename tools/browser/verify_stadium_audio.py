"""Verify generated stadium audio with Chromium and OfflineAudioContext.

Run ``python tools/browser/verify_stadium_audio.py``. No Flask/external services are used.
Signal metrics, a stereo WAV preview, and a report go to the temporary directory.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import wave

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


METRICS = r"""buffer => {
  let energy=0, peak=0, mean=0, finite=true;
  const left=buffer.getChannelData(0), right=buffer.numberOfChannels>1?buffer.getChannelData(1):left;
  let ll=0, rr=0, lr=0;
  for(let ch=0;ch<buffer.numberOfChannels;ch++) {
    const data=buffer.getChannelData(ch);
    for(let i=0;i<data.length;i++) {
      const value=data[i]; finite=finite && Number.isFinite(value);
      energy+=value*value; peak=Math.max(peak,Math.abs(value)); mean+=value;
    }
  }
  for(let i=0;i<left.length;i++) {ll+=left[i]*left[i];rr+=right[i]*right[i];lr+=left[i]*right[i];}
  return {duration:buffer.duration,channels:buffer.numberOfChannels,sampleRate:buffer.sampleRate,
    finite,peak,rms:Math.sqrt(energy/(buffer.length*buffer.numberOfChannels)),
    dc:mean/(buffer.length*buffer.numberOfChannels),correlation:lr/Math.sqrt(ll*rr),
    seam:Math.abs(left[0]-left[left.length-1]),bytes:buffer.length*buffer.numberOfChannels*4};
}"""


def run() -> dict:
    target = Path(tempfile.gettempdir()) / 'agent-soccer-stadium-audio'
    target.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'metrics': {}, 'artifacts': str(target)}

    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--autoplay-policy=no-user-gesture-required'])
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content('<button id="sound-btn" aria-pressed="false">Sound on</button>')
        page.add_script_tag(path=str(ROOT / 'static' / 'js/game/sound.js'))
        quiet = page.evaluate('''async () => {
          const context=new OfflineAudioContext(2,48000,48000);
          SoundManager.attach(context,{goalOnly:true});
          await SoundManager.crowdAmbient();await SoundManager.crowdCheer();await SoundManager.whistle();
          SoundManager._play(context.createBuffer(1,100,48000),1,1,'impact');
          const silent=SoundManager._effects.size===0 && SoundManager._buffers.size===0 && !SoundManager._ambientSource;
          await Promise.all([SoundManager.goal(),SoundManager.goal()]);
          const effects=[...SoundManager._effects];
          const singleGoal=effects.length===1 && effects[0].kind==='goal-whistle';
          const gain=effects[0]?.gain.gain.value;
          const rendered=await context.startRendering();
          let peak=0;for(let ch=0;ch<2;ch++)for(const x of rendered.getChannelData(ch))peak=Math.max(peak,Math.abs(x));
          SoundManager.dispose();return {silent,singleGoal,gain,peak};
        }''')
        check('goal-only mode has no crowd, impact or kickoff sources', quiet['silent'], quiet)
        check('duplicate goals coalesce into one quiet whistle', quiet['singleGoal'], quiet)
        check('goal whistle renders quietly without clipping', 0 < quiet['peak'] < .05 and quiet['gain'] < .06, quiet)
        cooperative = page.evaluate('''async () => {
          const context=new AudioContext();await context.suspend();
          let resumeCalls=0,sourceCount=0;
          const resume=context.resume.bind(context),create=context.createBufferSource.bind(context);
          context.resume=()=>{resumeCalls++;return resume()};
          context.createBufferSource=()=>{sourceCount++;return create()};
          const started=performance.now();
          SoundManager.attach(context);
          const attachMs=performance.now()-started;
          let heartbeats=0;
          const heartbeat=setInterval(()=>heartbeats++,5);
          await Promise.all([...SoundManager._crowdPending.values()]);
          clearInterval(heartbeat);
          const silentPrewarm=context.state==='suspended'&&resumeCalls===0&&sourceCount===0&&
            !SoundManager._ambientSource&&SoundManager._effects.size===0;
          const ready=SoundManager._buffers.has('cheer')&&SoundManager._buffers.has('ambient');
          const beforeAmbient=performance.now();await SoundManager.crowdAmbient();
          const ambientMs=performance.now()-beforeAmbient;
          const beforeGoal=performance.now();await SoundManager.goal();
          const firstGoalMs=performance.now()-beforeGoal;
          return {elapsedMs:performance.now()-started,attachMs,ambientMs,firstGoalMs,silentPrewarm,ready,
            longestChunkMs:SoundManager._synthesisLongestMs,chunks:SoundManager._synthesisChunks,heartbeats};
        }''')
        report['cooperativeSynthesis'] = cooperative
        check('Cold crowd synthesis yields between bounded chunks', cooperative['chunks'] >= 40 and cooperative['heartbeats'] >= 10 and cooperative['longestChunkMs'] < 80, cooperative)
        check('Attach prewarms without audible sources or unlocking audio', cooperative['silentPrewarm'] and cooperative['ready'] and cooperative['attachMs'] < 20, cooperative)
        check('First interactive crowd reaction uses prewarmed audio', cooperative['firstGoalMs'] < 20, cooperative['firstGoalMs'])
        page.evaluate('''() => {
          window.metrics = ''' + METRICS + ''';
          SoundManager.attach(new OfflineAudioContext(2, 44100*8, 44100));
          const start=performance.now();
          window.audioBuffers = {kick:SoundManager.makeKickBuffer(),bounce:SoundManager.makeBounceBuffer(),
            whistle:SoundManager._whistleBuffer(),ambient:SoundManager._crowdBuffer(false),cheer:SoundManager._crowdBuffer(true)};
          window.synthesisMs=performance.now()-start;
        }''')
        inventory = page.evaluate('() => Object.fromEntries(Object.entries(audioBuffers).map(([name,buffer])=>[name,metrics(buffer)]))')
        report['metrics'] = inventory
        report['coldSynthesisMs'] = page.evaluate('synthesisMs')
        for name, info in inventory.items():
            check(f'{name}: finite non-silent bounded samples', info['finite'] and 0.015 < info['rms'] < 0.35 and info['peak'] <= 0.881 and abs(info['dc']) < 1e-5, info)
        for name in ('ambient', 'cheer'):
            info = inventory[name]
            check(f'{name}: independent stereo detail', info['channels'] == 2 and abs(info['correlation']) < 0.90, info['correlation'])
        check('Ambient loop seam has no value discontinuity', inventory['ambient']['seam'] < 1e-6, inventory['ambient']['seam'])
        check('Generated buffer inventory stays under 3 MiB', sum(info['bytes'] for info in inventory.values()) < 3*1024*1024)
        check('Impacts remain short enough for immediate soccer feedback', inventory['kick']['duration'] <= 0.2 and inventory['bounce']['duration'] <= 0.15)
        check('Whistle has a finite stereo stadium tail', inventory['whistle']['channels'] == 2 and 0.7 < inventory['whistle']['duration'] < 1.1)
        whistle_overlap = page.evaluate('''async () => {
          await Promise.all([SoundManager.whistle(),SoundManager.whistle(),SoundManager.whistle()]);
          return [...SoundManager._effects].filter(effect=>effect.kind==='whistle').length;
        }''')
        check('Duplicate whistles coalesce into one source', whistle_overlap == 1, whistle_overlap)
        reuse = page.evaluate('''() => {
          const begin=performance.now();
          let stable=true;
          for(let i=0;i<200;i++) stable=stable && SoundManager.makeKickBuffer()===audioBuffers.kick && SoundManager._crowdBuffer(true)===audioBuffers.cheer;
          return {stable,milliseconds:performance.now()-begin,buffers:SoundManager._buffers.size};
        }''')
        check('Repeated kicks/goals reuse cached synthesis', reuse['stable'] and reuse['buffers'] == 5, reuse)

        # Render the real master graph. Ambient + active cheer + a positional
        # proxy should all be silent while muted, then resume without a new loop.
        rendered = page.evaluate('''async () => {
          SoundManager.dispose();
          const context=new OfflineAudioContext(2,44100*6,44100);
          SoundManager.attach(context);
          await Promise.all([SoundManager.crowdAmbient(),SoundManager.crowdAmbient(),SoundManager.crowdAmbient()]);
          const ambient=SoundManager._ambientSource;
          await Promise.all([SoundManager.goal(),SoundManager.crowdCheer()]);
          const effectsAtGoal=SoundManager._effects.size;
          const proxy=context.createOscillator(), gain=context.createGain();
          gain.gain.value=.035;proxy.frequency.value=190;
          proxy.connect(gain).connect(SoundManager.getOutput());proxy.start();proxy.stop(4.5);
          context.suspend(1);
          let suspension = context.startRendering();
          await new Promise(resolve=>{
            const poll=()=>context.state==='suspended'&&context.currentTime>=1?resolve():setTimeout(poll,1);poll();
          });
          SoundManager.toggleMute();
          const mutedButton={label:document.querySelector('#sound-btn').textContent,
            pressed:document.querySelector('#sound-btn').getAttribute('aria-pressed')};
          context.suspend(2.5);
          await context.resume();
          await new Promise(resolve=>{
            const poll=()=>context.state==='suspended'&&context.currentTime>=2.5?resolve():setTimeout(poll,1);poll();
          });
          SoundManager.toggleMute();
          await context.resume();
          const output=await suspension;
          await new Promise(resolve=>setTimeout(resolve,30));
          const sliceRms=(start,end)=>{
            let sum=0, count=0;
            for(let ch=0;ch<2;ch++){const data=output.getChannelData(ch);
              for(let i=Math.floor(start*44100);i<Math.floor(end*44100);i++){sum+=data[i]*data[i];count++;}}
            return Math.sqrt(sum/count);
          };
          return {before:sliceRms(.4,.9),muted:sliceRms(1.1,2.3),after:sliceRms(2.7,3.5),
            effectsAtGoal,remainingEffects:SoundManager._effects.size,
            sameAmbient:ambient===SoundManager._ambientSource,mutedButton,
            output:metrics(output)};
        }''')
        report['mix'] = rendered
        check('Goal and cheer callbacks coalesce into one crowd reaction', rendered['effectsAtGoal'] == 1)
        check('Master mute silences active effects, ambient, and positional routing', rendered['before'] > 0.01 and rendered['muted'] < 1e-8 and rendered['after'] > 0.01, rendered)
        check('Unmute retains the existing ambient loop', rendered['sameAmbient'])
        check('Mute button communicates its state', rendered['mutedButton'] == {'label': 'Sound off', 'pressed': 'true'})
        check('One-shot nodes disconnect after playback', rendered['remainingEffects'] == 0)
        check('Stadium mix remains finite and unclipped', rendered['output']['finite'] and rendered['output']['peak'] < 1)

        burst = page.evaluate('''async () => {
          const context=new OfflineAudioContext(2,44100*5,44100);
          SoundManager.attach(context);
          const cheer=SoundManager._crowdBuffer(true);
          for(let i=0;i<6;i++)SoundManager._play(cheer,.48,.97+.012*i,'cheer');
          const activeCheers=SoundManager._effects.size;
          const output=await context.startRendering();
          return {activeCheers,output:metrics(output)};
        }''')
        report['burst'] = burst
        check('Fast replay reactions remain bounded and unclipped', burst['activeCheers']==2 and burst['output']['peak']<1 and burst['output']['finite'], burst)

        replaced = page.evaluate('''async () => {
          const old=new OfflineAudioContext(2,44100,44100);
          SoundManager.attach(old);
          let stopped=0,started=0;
          const create=old.createBufferSource.bind(old);
          old.createBufferSource=()=>{const node=create(),start=node.start.bind(node),stop=node.stop.bind(node);
            node.start=(...args)=>{started++;start(...args)};node.stop=(...args)=>{stopped++;stop(...args)};return node};
          await Promise.all([SoundManager.crowdAmbient(),SoundManager.crowdAmbient()]);
          await SoundManager.whistle();
          const source=SoundManager._ambientSource, master=SoundManager.getOutput();
          SoundManager.attach(old);
          const idempotent=source===SoundManager._ambientSource && master===SoundManager.getOutput();
          const next=new OfflineAudioContext(2,44100,44100);
          SoundManager.attach(next);
          const clean=!SoundManager._ambientSource&&SoundManager._effects.size===0&&SoundManager._buffers.size===0;
          await SoundManager.crowdAmbient();
          const bound=SoundManager._ambientSource.context===next;
          for(let i=0;i<20;i++)SoundManager._play(SoundManager.makeKickBuffer(),.2);
          const effectCap=SoundManager._effects.size;
          const pending=SoundManager.crowdAmbient();SoundManager.dispose();await pending;
          const disposed=!SoundManager._ambientSource && !SoundManager.getOutput() && !SoundManager._ctx && SoundManager._effects.size===0;
          return {started,stopped,idempotent,clean,bound,effectCap,disposed};
        }''')
        report['lifecycle'] = replaced
        check('Concurrent ambient calls create only one loop', replaced['started'] == 2, replaced)
        check('Context replacement stops old loop and effects', replaced['stopped'] == 2 and replaced['clean'], replaced)
        check('Repeated attach to the same context is idempotent', replaced['idempotent'])
        check('Replacement sounds use the replacement context', replaced['bound'])
        check('Burst traffic keeps a bounded effect graph', replaced['effectCap'] == 6)
        check('Dispose releases all manager resources', replaced['disposed'])

        canceled = page.evaluate('''async () => {
          const old=new OfflineAudioContext(2,44100,44100);
          SoundManager.attach(old);
          const oldPending=[...SoundManager._crowdPending.values()];
          const next=new OfflineAudioContext(2,44100,44100),created=new WeakSet();
          const create=next.createBuffer.bind(next);
          next.createBuffer=(...args)=>{const buffer=create(...args);created.add(buffer);return buffer};
          SoundManager.attach(next);
          const oldResults=await Promise.all(oldPending);
          await Promise.all([...SoundManager._crowdPending.values()]);
          const replacementOwnsBuffers=[...SoundManager._buffers.values()].every(buffer=>created.has(buffer));
          const canceledReplacement=oldResults.every(result=>result===null);
          const disposed=new OfflineAudioContext(2,44100,44100);SoundManager.attach(disposed);
          const disposePending=[...SoundManager._crowdPending.values()];SoundManager.dispose();
          const disposeResults=await Promise.all(disposePending);
          return {canceledReplacement,replacementOwnsBuffers,canceledDispose:disposeResults.every(result=>result===null),
            noPostDisposeBuffers:SoundManager._buffers.size===0};
        }''')
        report['prewarmCancellation']=canceled
        check('Replacing context cancels in-flight prewarm without stale buffers', canceled['canceledReplacement'] and canceled['replacementOwnsBuffers'], canceled)
        check('Dispose cancels in-flight prewarm and retains no buffers', canceled['canceledDispose'] and canceled['noPostDisposeBuffers'], canceled)

        lazy = page.evaluate('''async () => {
          await SoundManager.crowdAmbient();
          const started=!!SoundManager._ambientSource;
          const ctx=SoundManager._ctx;SoundManager.dispose();await ctx.close();return started;
        }''')
        check('Ambient works without an explicit attach call', lazy)
        check('No browser exceptions', not errors, errors)

        preview = page.evaluate('''async () => {
          SoundManager.dispose();
          const context=new OfflineAudioContext(2,44100*11,44100);
          SoundManager.attach(context);
          await SoundManager.crowdAmbient();
          await SoundManager._crowdBufferAsync(true);
          const first=context.suspend(2),rendering=context.startRendering();
          await first;
          const begin=performance.now();await SoundManager.goal();const warmGoalMs=performance.now()-begin;
          const second=context.suspend(7);await context.resume();await second;
          await SoundManager.whistle();
          const third=context.suspend(8.5);await context.resume();await third;
          SoundManager._play(SoundManager.makeKickBuffer(),.7);
          const fourth=context.suspend(9.2);await context.resume();await fourth;
          SoundManager._play(SoundManager.makeBounceBuffer(),.5);
          await context.resume();window.mixedPreview=await rendering;
          const data=mixedPreview.getChannelData(0);
          const tail=data.slice(Math.floor(10*44100));
          let tailEnergy=0;for(const value of tail)tailEnergy+=value*value;
          return {warmGoalMs,restoredAmbient:SoundManager._ambientGain.gain.value,
            tailRms:Math.sqrt(tailEnergy/tail.length),output:metrics(mixedPreview)};
        }''')
        report['previewMix'] = preview
        check('Warm goal playback uses cached audio promptly', preview['warmGoalMs'] < 20, preview['warmGoalMs'])
        check('Ambient duck restores after a reaction', abs(preview['restoredAmbient']-.1)<1e-6 and preview['tailRms']>.005, preview)
        check('Full stadium preview is finite and unclipped', preview['output']['finite'] and preview['output']['peak']<1, preview['output'])

        # Export the generated source buffers, useful for listening during review.
        page.evaluate('''() => {
          SoundManager.attach(new OfflineAudioContext(2,44100,44100));
          window.previewBuffers={ambient:SoundManager._crowdBuffer(false),cheer:SoundManager._crowdBuffer(true),
            whistle:SoundManager._whistleBuffer(),kick:SoundManager.makeKickBuffer(),bounce:SoundManager.makeBounceBuffer()};
        }''')
        for name in ('ambient', 'cheer', 'whistle', 'kick', 'bounce', 'stadium-mix'):
            encoded = page.evaluate('''name => {
              const buffer=name==='stadium-mix'?mixedPreview:previewBuffers[name],bytes=new Uint8Array(buffer.length*buffer.numberOfChannels*2),view=new DataView(bytes.buffer);
              for(let i=0;i<buffer.length;i++)for(let ch=0;ch<buffer.numberOfChannels;ch++) {
                const value=Math.max(-1,Math.min(1,buffer.getChannelData(ch)[i]));
                view.setInt16((i*buffer.numberOfChannels+ch)*2,Math.round(value*32767),true);
              }
              let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
              return {base64:btoa(binary),channels:buffer.numberOfChannels,sampleRate:buffer.sampleRate};
            }''', name)
            with wave.open(str(target / f'{name}.wav'), 'wb') as output:
                output.setnchannels(encoded['channels'])
                output.setsampwidth(2)
                output.setframerate(encoded['sampleRate'])
                output.writeframes(base64.b64decode(encoded['base64']))
        browser.close()
    report['passed'] = sum(check['passed'] for check in report['checks'])
    report['total'] = len(report['checks'])
    (target / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if report['passed'] != report['total']:
        raise SystemExit(1)
    return report


if __name__ == '__main__':
    run()
