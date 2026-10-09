"""Isolated Chromium checks for game rendering and trajectory playback.

Run ``python tools/browser/verify_game_performance.py --baseline`` to render the HEAD
templates, or omit --baseline to verify the working tree. Integrations are
disabled before importing the app. The temporary server listens on loopback
only; diagnostics are injected into its template copies, never production.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def isolated_environment() -> None:
    os.environ["DEV_MODE"] = "1"
    os.environ["SECRET_KEY"] = "performance-verification-only"
    os.environ["UPSTASH_REDIS_URL"] = "redis://127.0.0.1:1"
    for name in (
        "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
        "RESEND_API_KEY", "POSTHOG_API_KEY", "SENTRY_DSN", "PINECONE_API_KEY",
        "CLERK_SECRET_KEY", "CLERK_PUBLISHABLE_KEY", "PRODUCTBRIDGE_API_KEY",
        "OPENAI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY",
    ):
        os.environ[name] = ""


DIAGNOSTICS = r"""
window.__performanceQA = {
  ready: () => typeof gameState !== 'undefined' ? !!gameState : state.players_a.length > 0,
  stats: () => ({
    frames: renderer.info.render.frame,
    calls: renderer.info.render.calls,
    triangles: renderer.info.render.triangles,
    geometries: renderer.info.memory.geometries,
    textures: renderer.info.memory.textures,
    scoreboardVersion: window._scoreboardTex ? window._scoreboardTex.version : null,
    crowd: window.__crowdInfo ? window.__crowdInfo() : null,
    renderStats: window.__renderStats ? window.__renderStats() : null,
    playerPositions: [...playerMeshesA, ...playerMeshesB].map(mesh => mesh.position.toArray()),
    spriteAudience: (() => {let count=0;scene.traverse(object=>{
      if(object.isInstancedMesh && object.visible && object.material.uniforms?.uSpriteSheet) count+=object.count;
    });return count;})(),
  }),
  trajectory: (fps) => {
    const originalRAF = window.requestAnimationFrame;
    const animationLoop = typeof renderLoop !== 'undefined' ? renderLoop :
      (typeof demandLoop !== 'undefined' ? demandLoop : null);
    const originalDemandRAF = animationLoop?.requestAnimationFrame;
    const queue = [];
    let complete = false, frames = 0, timestamp = 100;
    const active = typeof gameState !== 'undefined' ? gameState : state;
    const a = active.players_a.map(p => ({x:p.x, y:p.y}));
    const b = active.players_b.map(p => ({x:p.x, y:p.y}));
    const trajectory = Array.from({length:31}, (_, i) => ({
      x:700+i*3, y:437.5, z:Math.max(0, 30*Math.sin(i/30*Math.PI)), t:i*.05,
      a:a.map(p => ({...p})), b:b.map(p => ({...p})), ref:{x:700, y:795}
    }));
    try {
      const queueFrame = callback => {queue.push(callback); return queue.length;};
      if (animationLoop) animationLoop.requestAnimationFrame = queueFrame;
      else window.requestAnimationFrame = queueFrame;
      animateTrajectory3D(trajectory, () => {complete = true;});
      while (!complete && queue.length && frames < 1000) {
        const callbacks = queue.splice(0);
        callbacks.forEach(callback => callback(timestamp));
        frames++;
        if (!complete) timestamp += 1000/fps;
      }
      return {fps, complete, frames, elapsedMs:timestamp-100, ball:window.__ballFX().pos};
    } finally {
      if (animationLoop) animationLoop.requestAnimationFrame = originalDemandRAF;
      window.requestAnimationFrame = originalRAF;
    }
  },
  synchronizedKick: async (fps, mode='normal') => {
    const live = typeof gameState !== 'undefined';
    const loop = live ? renderLoop : demandLoop, originalRAF = loop.requestAnimationFrame;
    const original = JSON.parse(JSON.stringify(live ? gameState : state));
    const oldBusy = isAnimating, oldOnline = live ? {...ONLINE} : null;
    const oldReplay = live ? null : [...replayData], oldMove = live ? null : currentMove;
    const oldAuto = live ? null : autoPlayOn, oldGoal = SoundManager.goal;
    const active = live ? gameState : state;
    const trajectory = [0,.02,.2,.65,1].map(t=>({t,x:700+680*t,y:437.5,z:0,
      a:active.players_a.map((p,i)=>({...p,x:i===0?200+100*t:p.x})),
      b:active.players_b.map(p=>({...p})),ref:active.referee}));
    const move={mover:'a',player_idx:0,angle:0,power:80,trajectory,
      kick_endpoint:{x:300,y:trajectory[0].a[0].y},scored:mode==='goal'?'A':null,desc:'Playback QA'};
    let queue=[], complete=false, time=0, maxPlayerError=0, maxBallError=0;
    const goalTimes=[], samples=[];
    try {
      loop.requestAnimationFrame = callback => {queue.push(callback);return queue.length;};
      SoundManager.goal=()=>goalTimes.push(time);
      active.players_a=trajectory[0].a.map(p=>({...p}));
      active.ball={x:700,y:437.5,z:0};active.penalty_shootout=false;
      isAnimating=false;
      let task;
      if(live) {
        if(mode==='online') {
          Object.assign(ONLINE,{active:true,leaving:false});
          task=animateOnlineMove(move,active);
        } else task=animateComputerResult({move_result:move},'move_result','a',()=>true,1);
      } else if(mode==='goal') {
        replayData.splice(0,replayData.length,move);currentMove=0;autoPlayOn=false;
        window.playNextMove3D();
      } else task=new Promise(resolve=>animateTrajectory3D(trajectory,resolve));
      task?.then(()=>{complete=true;});
      for(let frame=0;frame<=Math.ceil(fps*1.4);frame++) {
        time=frame*1000/fps;
        const batch=queue;queue=[];batch.forEach(callback=>callback(time));
        await Promise.resolve();await Promise.resolve();
        if(time<=1000) {
          const expected=200+100*time/1000;
          maxPlayerError=Math.max(maxPlayerError,Math.abs(playerMeshesA[0].position.x+W/2-expected));
          maxBallError=Math.max(maxBallError,Math.abs(ballMesh.position.x+W/2-(700+680*time/1000)));
          if(time===0 || Math.abs(time-1000)<.01) samples.push({time,playerX:playerMeshesA[0].position.x+W/2});
        }
        if(!live&&mode==='goal') complete=!isAnimating&&currentMove===1;
        if(complete&&time>=1000)break;
      }
      return {fps,mode,complete,maxPlayerError,maxBallError,goalTimes,samples,
        pose:playerMeshesA[0].userData.avatar?.pose,queued:queue.length};
    } finally {
      loop.requestAnimationFrame=originalRAF;SoundManager.goal=oldGoal;isAnimating=oldBusy;
      if(live) {gameState=original;Object.assign(ONLINE,oldOnline);animBall=null;updatePlayerPositions();}
      else {Object.assign(state,original);replayData.splice(0,replayData.length,...oldReplay);
        currentMove=oldMove;autoPlayOn=oldAuto;updatePlayerPositions(state.players_a,state.players_b);}
      updateBallMesh(original.ball.x,original.ball.y,original.ball.z||0);
    }
  },
  pollOverlap: async (delayMs = 0) => {
    const online = typeof ONLINE !== 'undefined';
    const context = online ? ONLINE : LIVE;
    const oldContext = {...context};
    const oldFetch = window.fetch;
    const originalAnimating = isAnimating;
    const response = {game:JSON.parse(JSON.stringify(typeof gameState !== 'undefined' ? gameState : state)),
      status:'active',my_side:online?ONLINE.side:null,room_id:'performance'};
    const pending = [];
    let calls = 0;
    try {
      context.active = true; context.roomId = 'performance'; context.room = 'performance';
      context.over = false; context.polling = false;
      context.lastKick = response.game.kick_count || 0;
      isAnimating = false;
      window.fetch = () => {calls++; return new Promise(resolve => pending.push(() => resolve({ok:true, json:async () => response})));};
      const poll = online ? pollOnline : pollLive;
      const results = [poll(), poll(), poll()];
      if (delayMs) {await new Promise(resolve => setTimeout(resolve, delayMs)); results.push(poll());}
      pending.splice(0).forEach(resolve => resolve());
      await Promise.race([Promise.all(results),new Promise((_,reject)=>setTimeout(()=>reject(new Error('Poll fixture did not finish')),5000))]);
      return {calls, serialized:calls===1};
    } finally {
      window.fetch = oldFetch; Object.assign(context, oldContext); isAnimating = originalAnimating;
    }
  },
  rebuildMemory: async () => {
    const result = [];
    // Use a fixed camera so culling does not change which geometries are first
    // uploaded between resets. Warm caches before comparing repeated rebuilds.
    const previousView = typeof currentView !== 'undefined' ? currentView : null;
    if (previousView !== null) applyViewMode('top');
    for (let i = 0; i < 5; i++) {
      if (typeof gameState !== 'undefined') await window.resetGame();
      else rebuildPlayers(state.players_a, state.players_b);
      renderer.render(scene, camera);
      if (i > 0) result.push({geometries:renderer.info.memory.geometries, textures:renderer.info.memory.textures});
    }
    if (previousView !== null) applyViewMode(previousView);
    return result;
  },
  penaltyTimer: () => {
    if (typeof gameState === 'undefined') return null;
    const original = window.setInterval;
    const originalAuto = autoPenaltyKick;
    let callback, automatic=0;
    const states=[];
    try {
      autoPenaltyKick = () => {automatic++;};
      window.setInterval = fn => {callback=fn; return -1;};
      startKeeperTimer();
      states.push(document.getElementById('penalty-keeper-timer').textContent);
      for (let i=0;i<5;i++) {callback(); states.push(document.getElementById('penalty-keeper-timer').textContent);}
      return {states, automatic, cleared:keepTimer===null};
    } finally {window.setInterval=original; autoPenaltyKick=originalAuto;}
  },
  realGoal: async () => {
    if (typeof gameState === 'undefined') return null;
    gameState = await fetch('/__performance/goal').then(response => response.json());
    rebuildPlayers(); updateHUD(gameState);
    await submitKick(2, 0, 40);
    const afterGoal = window._scoreboardTex.version;
    updateHUD(gameState);
    return {scoreA:gameState.score_a, scoreB:gameState.score_b,
      hudA:document.getElementById('score-a').textContent,
      hudB:document.getElementById('score-b').textContent,
      scoreboardScore:window._scoreboardScore,
      repeatScoreboardStable:window._scoreboardTex.version===afterGoal};
  },
  moveBatch: async () => {
    const online=typeof ONLINE !== 'undefined';
    const context=online?ONLINE:LIVE, previous={...context};
    const originalFetch=window.fetch, originalAnimation=animateTrajectory3D;
    const previousAnimating=isAnimating;
    const active=JSON.parse(JSON.stringify(online?gameState:state));
    const positions=players=>players.map(p=>({x:p.x,y:p.y}));
    const moves=[1,2,3].map(i=>({mover:i===2?'b':'a',player_idx:0,kick_count:i,
      trajectory:[0,1].map(j=>({x:700+i*10+j,y:437.5,z:0,t:j*.18,
        a:positions(active.players_a),b:positions(active.players_b)}))}));
    const events=[]; let running=0,maxRunning=0;
    try {
      Object.assign(context,{active:true,room:'performance',roomId:'performance',lastKick:0,over:false,polling:false});
      isAnimating=false; active.kick_count=3;active.online_move_count=3;
      window.fetch=async()=>({ok:true,json:async()=>({game:active,status:'active',moves,
        move_count:3,my_side:online?ONLINE.side:null,room_id:'performance'})});
      animateTrajectory3D=(path,onDone)=>{
        const marker=path[0].x; events.push('start:'+marker); running++; maxRunning=Math.max(maxRunning,running);
        setTimeout(()=>{events.push('end:'+marker);running--;onDone();},25);
      };
      await (online?pollOnline():pollLive());
      return {events,maxRunning,lastKick:context.lastKick};
    } finally {
      window.fetch=originalFetch; animateTrajectory3D=originalAnimation;
      Object.assign(context,previous);isAnimating=previousAnimating;
    }
  }
};
"""


def instrument(source: str) -> str:
    pattern = r'(<script\b[^>]*\btype=[\'"]module[\'"][^>]*>)(.*?)(</script>)'
    result, count = re.subn(pattern, lambda m: m[1] + m[2] + DIAGNOSTICS + m[3], source, flags=re.S)
    if count != 1:
        raise RuntimeError(f"Expected one module script, found {count}")
    return result


def load_head_template(name: str) -> str:
    """Keep visual baselines readable across the folder reorganization."""
    for candidate in (name, Path(name).name):
        result = subprocess.run(["git", "show", f"HEAD:templates/{candidate}"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if result.returncode == 0:
            source = result.stdout
            asset_paths = {}
            for folder in ("css", "js/game", "js/ui", "images"):
                for asset in (ROOT / "static" / folder).iterdir():
                    if asset.is_file():
                        target = asset.relative_to(ROOT / "static").as_posix()
                        asset_paths[asset.name] = target
                        source = source.replace(f"/static/{asset.name}", f"/static/{target}")
            source = re.sub(r"\bfilename(\s*=\s*)(['\"])([^'\"]+)\2",
                            lambda match: f"filename{match[1]}{match[2]}{asset_paths.get(match[3], match[3])}{match[2]}",
                            source)
            for partial in (ROOT / "templates/shared").glob("*.html"):
                for quote in ("'", '"'):
                    source = source.replace(f"{quote}{partial.name}{quote}",
                                            f"{quote}shared/{partial.name}{quote}")
            return source
    raise RuntimeError(f"No HEAD template found for {name}")


def run(args) -> dict:
    isolated_environment()
    from flask import jsonify, redirect, render_template, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state, apply_kick

    templates = {}
    for name in ("game/index_3d.html", "game/replay_3d.html"):
        if args.baseline:
            source = load_head_template(name)
        else:
            source = (ROOT / "templates" / name).read_text(encoding="utf-8")
        templates[name] = instrument(source)
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates), application.app.jinja_loader])

    seed = new_soccer_state(player_count=7, half_length=9999)
    replay_state = copy.deepcopy(seed)
    trajectory, scored, _desc, _endpoint, _push = apply_kick(replay_state, 6, 0, 100, True)
    replay = [{"mover":"a", "player_idx":6, "angle":0, "power":100, "trajectory":trajectory, "scored":scored}]
    goal_seed = new_soccer_state(mode="hvh", player_count=3, half_length=9999)
    goal_seed["ball"] = {"x":1350.0, "y":437.5}
    for players in (goal_seed["players_a"], goal_seed["players_b"]):
        for i, player in enumerate(players):
            player.update(x=100.0 + i*100, y=100.0)
    goal_seed["players_a"][2].update(x=1310.0, y=437.5)

    @application.app.route("/__performance/login")
    def qa_login():
        session["user_id"] = "dev:performance-verification"
        session["username"] = "Performance QA"
        save_game(session["user_id"], copy.deepcopy(seed))
        return redirect("/play3d")

    @application.app.route("/__performance/goal")
    def qa_goal():
        save_game(session["user_id"], copy.deepcopy(goal_seed))
        return jsonify(copy.deepcopy(goal_seed))

    @application.app.route("/__performance/replay")
    def qa_replay():
        return render_template("game/replay_3d.html", username="Performance QA", t={"id":"performance", "name":"QA"},
                               match={"id":"performance", "participant_a":"A", "participant_b":"B", "replay_data":replay},
                               highlights=[], highlight=None, live_room=None, loss_model=None, loss_model_name=None)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report = {"baseline":args.baseline, "pages":{}, "checks":[]}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"])
            context = browser.new_context(viewport={"width":1280, "height":800}, device_scale_factor=1)
            if not args.baseline:
                context.add_init_script("localStorage.setItem('agent-soccer-render-quality','1080p')")
            for label, path in (("play", "/__performance/login"), ("replay", "/__performance/replay")):
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + path, wait_until="networkidle", timeout=60000)
                page.wait_for_function("window.__performanceQA && window.__performanceQA.ready()", timeout=60000)
                # Let compilation and initial scene construction settle before the sample.
                page.wait_for_timeout(1200)
                before = page.evaluate("window.__performanceQA.stats()")
                print(label + ': ready', flush=True)
                sample = page.evaluate("""(duration) => new Promise(resolve => {
                  const start = performance.now(); let frames=0;
                  function tick(timestamp) {frames++; if (timestamp-start < duration) requestAnimationFrame(tick);
                    else resolve({frames, elapsedMs:timestamp-start, fps:1000*frames/(timestamp-start)});}
                  requestAnimationFrame(tick);
                })""", args.sample_ms)
                after = page.evaluate("window.__performanceQA.stats()")
                screenshot = None
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    screenshot = str(args.screenshots / f"{'baseline' if args.baseline else 'current'}_{label}.png")
                    page.screenshot(path=screenshot)
                trajectories = [page.evaluate("fps => window.__performanceQA.trajectory(fps)", fps) for fps in (60, 30, 10)]
                print(label + ': playback sampled', flush=True)
                poll = page.evaluate("window.__performanceQA.pollOverlap()")
                delayed_poll = page.evaluate("window.__performanceQA.pollOverlap(1600)") if not args.baseline else None
                memory = page.evaluate("window.__performanceQA.rebuildMemory()") if not args.baseline else None
                print(label + ': resources checked', flush=True)
                penalty = page.evaluate("window.__performanceQA.penaltyTimer()") if not args.baseline else None
                move_batch = page.evaluate("window.__performanceQA.moveBatch()") if not args.baseline else None
                goal = page.evaluate("Promise.race([window.__performanceQA.realGoal(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('Goal playback did not finish')),15000))])") if not args.baseline and label == "play" else None
                synchronized = []
                if not args.baseline:
                    for fps in (10, 30, 60, 120):
                        for mode in ("normal", "online") if label == "play" else ("normal", "goal"):
                            synchronized.append(page.evaluate("([fps,mode])=>window.__performanceQA.synchronizedKick(fps,mode)", [fps, mode]))
                report["pages"][label] = {"before":before, "after":after, "sample":sample, "trajectories":trajectories,
                                          "poll":poll, "delayedPoll":delayed_poll, "rebuildMemory":memory,
                                          "penalty":penalty, "goal":goal, "moveBatch":move_batch, "synchronized":synchronized, "errors":errors, "screenshot":screenshot}
                report["checks"].append({"name":label+": no browser exceptions", "passed":not errors})
                report["checks"].append({"name":label+": trajectory completes at 60/30/10 fps", "passed":all(t["complete"] for t in trajectories)})
                durations = [t["elapsedMs"] for t in trajectories]
                report["checks"].append({"name":label+": playback independent of frame rate", "passed":max(durations)-min(durations) <= 101})
                if not args.baseline:
                    report["checks"].append({"name":label+": ball and kicker share the physical timeline at 10/30/60/120 FPS",
                        "passed":all(row["complete"] and row["maxPlayerError"] < .001 and row["maxBallError"] < .001 and row["queued"] == 0 for row in synchronized)})
                    if label == "replay":
                        report["checks"].append({"name":"replay: goal celebration starts at contact and reset begins at the final roster",
                            "passed":all(row["goalTimes"] == [1000] and abs(row["samples"][-1]["playerX"] - 300) < .001
                                          for row in synchronized if row["mode"] == "goal")})
                    report["checks"].append({"name":label+": polls serialize", "passed":poll["serialized"]})
                    report["checks"].append({"name":label+": delayed polls serialize", "passed":delayed_poll["serialized"]})
                    report["checks"].append({"name":label+": rebuilding players releases GPU resources", "passed":memory[0] == memory[-1]})
                    report["checks"].append({"name":label+": idle players preserve server positions", "passed":before["playerPositions"] == after["playerPositions"]})
                    report["checks"].append({"name":label+": queued moves animate in order", "passed":move_batch["maxRunning"] == 1 and move_batch["lastKick"] == 3 and move_batch["events"] == ['start:710','end:710','start:720','end:720','start:730','end:730']})
                    if label == "play":
                        report["checks"].append({"name":"play: idle scoreboard texture stable", "passed":after["scoreboardVersion"] == before["scoreboardVersion"]})
                        report["checks"].append({"name":"play: crowd has no duplicated fallback", "passed":after["crowd"] is not None and after["crowd"]["fallbackCount"] == 0})
                        report["checks"].append({"name":"play: keeper countdown auto-kicks once", "passed":penalty["automatic"] == 1 and penalty["cleared"] and penalty["states"][0] == 'Goalkeeper: 5s' and penalty["states"][-1] == ''})
                        report["checks"].append({"name":"play: real goal refreshes HUD and scoreboard", "passed":goal["scoreA"] == 1 and goal["scoreB"] == 0 and goal["hudA"] == '1' and goal["hudB"] == '0' and goal["repeatScoreboardStable"]})
                page.close()
            browser.close()
    finally:
        server.shutdown()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--sample-ms", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    report = run(args)
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    raise SystemExit(0 if all(check["passed"] for check in report["checks"]) else 1)
