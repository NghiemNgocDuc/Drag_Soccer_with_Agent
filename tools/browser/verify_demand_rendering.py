"""Isolated browser checks for on-demand drawing and graphics resolution.

Runs behavioral checks at 1080p plus one actual 4K drawing-buffer check. All
service credentials are cleared before app import. The temporary server listens
on loopback; test diagnostics are injected only into temporary template copies.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
import re
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import DIAGNOSTICS as COMMON_DIAGNOSTICS, isolated_environment


DIAGNOSTICS = COMMON_DIAGNOSTICS + r"""
window.__demandQA = {
  stats: () => ({
    ...window.__performanceQA.stats(),
    buffer:[renderer.domElement.width, renderer.domElement.height],
    css:[container.clientWidth,container.clientHeight],
    aspect:camera.aspect,
    clock:document.getElementById('game-clock')?.textContent || null,
    ball:window.__ballFX().pos,
  }),
  invalidate: () => controls.dispatchEvent({type:'change'}),
  unchangedPolls: async () => {
    const online = typeof ONLINE !== 'undefined', context = online ? ONLINE : LIVE;
    const previous = {...context}, originalFetch = window.fetch;
    const game = JSON.parse(JSON.stringify(online ? gameState : state));
    const revision = game.online_move_count ?? game.kick_count ?? 0;
    const poll = online ? pollOnline : pollLive;
    let calls = 0;
    const idle = () => new Promise((resolve, reject) => {
      const started = performance.now();
      const check = () => {
        const loop = window.__renderStats().loop;
        if (!loop.pendingFrame && !loop.pendingCallbacks) return resolve();
        if (performance.now() - started > 60000) return reject(new Error('Poll fixture did not settle'));
        setTimeout(check, 25);
      };
      check();
    });
    try {
      Object.assign(context, {active:true, room:'idle-qa', roomId:'idle-qa', side:online?'a':null,
        status:'active', ready:false, lastKick:-1, over:false, polling:false, leaving:false});
      window.fetch = async () => {
        calls++;
        return {ok:true,json:async()=>({game,status:'active',my_side:online?'a':null,
          room_id:'idle-qa',move_count:revision,moves:[]})};
      };
      // Joining legitimately builds the roster. Measure only subsequent polls
      // within the same initialized session, with its revision and roster cache.
      await poll(); await idle();
      const before = renderer.info.render.frame, warmCalls = calls;
      for (let i=0;i<3;i++) { await Promise.all([poll(),poll(),poll()]); await idle(); }
      return {before,after:renderer.info.render.frame,poll:{serialized:calls-warmCalls===3,calls:calls-warmCalls}};
    } finally {
      window.fetch = originalFetch; Object.assign(context, previous);
    }
  },
  setHidden: (hidden) => {
    Object.defineProperty(document,'hidden',{configurable:true,value:hidden});
    document.dispatchEvent(new Event('visibilitychange'));
  },
  setOffscreen: (offscreen) => {
    container.style.position = offscreen ? 'fixed' : '';
    container.style.top = offscreen ? '3000px' : '';
  },
  trajectory: () => {
    const active = typeof gameState !== 'undefined' ? gameState : state;
    const players = team => team.map(p=>({x:p.x,y:p.y}));
    const path = [0,.2,.6].map((t,i)=>({x:700+i*45,y:437.5,z:0,t,
      a:players(active.players_a),b:players(active.players_b),ref:{x:700,y:795}}));
    isAnimating = true;
    return new Promise(resolve=>animateTrajectory3D(path,()=>{
      isAnimating=false;
      controls.dispatchEvent({type:'change'});
      resolve({expected:[90,ballRadius(),0],actual:window.__ballFX().pos});
    }));
  },
  seek: async () => {
    if(typeof gameState !== 'undefined') return null;
    const first=replayData.findIndex(move=>move.trajectory?.length);
    if(first<0) return null;
    currentMove=first;
    window.playNextMove3D();
    await new Promise(resolve=>{
      const wait=()=>isAnimating?setTimeout(wait,50):resolve();wait();
    });
    const last=replayData[first].trajectory.at(-1);
    const expected=replayData[first].scored?[0,0,0]:serverToWorld(last.x,last.y,last.z||0).toArray();
    expected[1]+=ballRadius();
    return {move:currentMove,expected,actual:window.__ballFX().pos};
  },
};
"""


def instrument(source: str) -> str:
    pattern = r'(<script\b[^>]*\btype=[\'"]module[\'"][^>]*>)(.*?)(</script>)'
    result, count = re.subn(pattern, lambda match: match[1] + match[2] + DIAGNOSTICS + match[3], source, flags=re.S)
    if count != 1:
        raise RuntimeError(f"Expected one module script, found {count}")
    return result


def run(args) -> dict:
    isolated_environment()
    from flask import jsonify, redirect, render_template, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import apply_kick, new_soccer_state

    templates = {name: instrument((ROOT / "templates" / name).read_text(encoding="utf-8"))
                 for name in ("game/index_3d.html", "game/replay_3d.html")}
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates), application.app.jinja_loader])
    seed = new_soccer_state(mode="hvh", player_count=3, half_length=9999)
    replay_state = copy.deepcopy(seed)
    trajectory, scored, _desc, _endpoint, _push = apply_kick(replay_state, 2, 0, 100, True)
    replay = [{"mover": "a", "player_idx": 2, "angle": 0, "power": 100,
               "trajectory": trajectory, "scored": scored}]
    goal_seed = new_soccer_state(mode="hvh", player_count=3, half_length=9999)
    goal_seed["ball"] = {"x": 1350.0, "y": 437.5}
    for team in (goal_seed["players_a"], goal_seed["players_b"]):
        for index, player in enumerate(team):
            player.update(x=100.0 + index * 100, y=100.0)
    goal_seed["players_a"][2].update(x=1310.0, y=437.5)

    @application.app.route("/__demand/login")
    def qa_login():
        session["user_id"] = "dev:demand-render-verification"
        session["username"] = "Render QA"
        save_game(session["user_id"], copy.deepcopy(seed))
        return redirect("/play3d")

    @application.app.route("/__performance/goal")
    def qa_goal():
        save_game(session["user_id"], copy.deepcopy(goal_seed))
        return jsonify(copy.deepcopy(goal_seed))

    @application.app.route("/__demand/replay")
    def qa_replay():
        return render_template("game/replay_3d.html", username="Render QA", t={"id": "qa", "name": "QA"},
                               match={"id": "qa", "participant_a": "A", "participant_b": "B", "replay_data": replay},
                               highlights=[], highlight=None, live_room=None, loss_model=None, loss_model_name=None)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report = {"pages": {}, "checks": []}

    def check(name, passed, evidence=None):
        entry = {"name": name, "passed": bool(passed)}
        if evidence is not None:
            entry["evidence"] = evidence
        report["checks"].append(entry)
        print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)

    def stats(page):
        return page.evaluate("window.__demandQA.stats()")

    def settle(page):
        page.wait_for_function("""window.__renderStats && (()=>{
          const loop=window.__renderStats().loop;
          return loop && !loop.pendingFrame && loop.pendingCallbacks===0;
        })()""", timeout=60000)
        page.wait_for_timeout(200)

    def set_quality(page, label, mode):
        if label == "play":
            page.evaluate("mode=>window.setGraphicsQuality(mode)", mode)
        else:
            page.locator("#render-quality").select_option(mode)

    def idle_check(page, label):
        settle(page)
        before = stats(page)
        page.wait_for_timeout(args.idle_ms)
        after = stats(page)
        loop = after["renderStats"]["loop"]
        check(label + ": idle scene submits no frames or animation callbacks",
              before["frames"] == after["frames"] and not loop["pendingFrame"] and loop["pendingCallbacks"] == 0,
              {"before": before["frames"], "after": after["frames"], "loop": loop})
        return before, after

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"])
            context = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
            context.add_init_script("if(!localStorage.getItem('agent-soccer-render-quality')) localStorage.setItem('agent-soccer-render-quality','1080p');")
            for label, path in (("play", "/__demand/login"), ("replay", "/__demand/replay")):
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, target=errors: target.append(str(error)))
                page.goto(base + path, wait_until="networkidle", timeout=60000)
                page.wait_for_function("window.__performanceQA && window.__performanceQA.ready() && window.__renderStats().loop", timeout=60000)
                before, after = idle_check(page, label)
                if label == "play":
                    check("play: DOM match clock advances while the GPU is idle", before["clock"] != after["clock"], [before["clock"], after["clock"]])
                if args.polls_only:
                    unchanged = page.evaluate("window.__demandQA.unchangedPolls()")
                    check(label + ": unchanged live polls preserve the idle GPU",
                          unchanged["poll"]["serialized"] and unchanged["before"] == unchanged["after"], unchanged)
                    check(label + ": no JavaScript exceptions", not errors, errors)
                    report["pages"][label] = {"initial": before, "final": polled, "errors": errors}
                    page.close()
                    continue

                if label == "play":
                    page.evaluate("window.setViewMode('top')")
                    settle(page)
                rect = page.locator("#three-container").bounding_box()
                start_frames = after["frames"]
                x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down(button="right")
                page.mouse.move(x + 80, y + 25, steps=6)
                page.mouse.up(button="right")
                settle(page)
                check(label + ": camera interaction redraws then settles", stats(page)["frames"] > start_frames)
                idle_check(page, label + " after camera movement")

                moved = page.evaluate("window.__demandQA.trajectory()")
                settle(page)
                check(label + ": trajectory draws the authoritative final ball position",
                      all(abs(a - b) < 1e-6 for a, b in zip(moved["actual"], moved["expected"])), moved)
                idle_check(page, label + " after movement")

                page.evaluate("window.__demandQA.pendingMove=window.__demandQA.trajectory(); void 0;")
                page.wait_for_timeout(150)
                page.evaluate("window.__demandQA.setHidden(true)")
                paused_move = stats(page)
                page.wait_for_timeout(900)
                held_move = stats(page)
                check(label + ": hidden trajectory holds its position and queued callback",
                      paused_move["ball"] == held_move["ball"] and held_move["renderStats"]["loop"]["pendingCallbacks"] > 0
                      and not held_move["renderStats"]["loop"]["pendingFrame"],
                      {"before": paused_move["ball"], "after": held_move["ball"], "loop": held_move["renderStats"]["loop"]})
                page.evaluate("window.__demandQA.setHidden(false)")
                resumed_move = page.evaluate("window.__demandQA.pendingMove")
                settle(page)
                check(label + ": hidden trajectory resumes to its correct final position",
                      all(abs(a - b) < 1e-6 for a, b in zip(resumed_move["actual"], resumed_move["expected"])), resumed_move)

                for hidden_kind, method in (("hidden", "setHidden"), ("offscreen", "setOffscreen")):
                    page.evaluate(f"window.__demandQA.{method}(true)")
                    page.wait_for_function("window.__renderStats().loop.suspended", timeout=10000)
                    pause_before = stats(page)
                    page.evaluate("window.__demandQA.invalidate()")
                    page.wait_for_timeout(300)
                    pause_after = stats(page)
                    check(label + f": {hidden_kind} scene pauses rendering",
                          pause_before["frames"] == pause_after["frames"] and not pause_after["renderStats"]["loop"]["pendingFrame"])
                    page.evaluate(f"window.__demandQA.{method}(false)")
                    page.wait_for_function("!window.__renderStats().loop.suspended", timeout=10000)
                    settle(page)
                    check(label + f": {hidden_kind} scene redraws when visible", stats(page)["frames"] > pause_after["frames"])

                set_quality(page, label, "1440p")
                settle(page)
                selected = stats(page)["renderStats"]["quality"]
                check(label + ": 1440p uses the requested drawing-buffer size", selected["mode"] == "1440p" and max(selected["width"], selected["height"]) == 2560, selected)
                page.reload(wait_until="networkidle", timeout=60000)
                page.wait_for_function("window.__demandQA && window.__performanceQA.ready()", timeout=60000)
                settle(page)
                persisted = stats(page)["renderStats"]["quality"]
                check(label + ": selected resolution persists after reload", persisted["mode"] == "1440p", persisted)
                set_quality(page, label, "1080p")
                settle(page)

                batch = page.evaluate("window.__performanceQA.moveBatch()")
                settle(page)
                check(label + ": live queued moves finish sequentially", batch["maxRunning"] == 1 and batch["lastKick"] == 3, batch)
                unchanged = page.evaluate("window.__demandQA.unchangedPolls()")
                check(label + ": unchanged live polls preserve the idle GPU",
                      unchanged["poll"]["serialized"] and unchanged["before"] == unchanged["after"], unchanged)
                if label == "play":
                    goal = page.evaluate("window.__performanceQA.realGoal()")
                    # Active celebration is expected to draw until its finite lifetime ends.
                    settle(page)
                    check("play: scoring redraws the HUD and scoreboard", goal["scoreA"] == 1 and goal["hudA"] == "1" and goal["repeatScoreboardStable"], goal)
                    idle_check(page, "play after goal celebration")
                    if not args.skip_4k:
                        set_quality(page, label, "4k")
                        settle(page)
                        four_k = stats(page)
                        quality = four_k["renderStats"]["quality"]
                        ratio = four_k["buffer"][0] / four_k["buffer"][1]
                        css_ratio = four_k["css"][0] / four_k["css"][1]
                        check("play: actual 4K drawing buffer preserves aspect ratio",
                              quality["mode"] == "4k" and (max(four_k["buffer"]) == 3840 or quality["limited"]) and abs(ratio - css_ratio) < .01,
                              {"buffer": four_k["buffer"], "css": four_k["css"], "quality": quality})
                        set_quality(page, label, "1080p")
                        settle(page)
                else:
                    sought = page.evaluate("window.__demandQA.seek()")
                    settle(page)
                    check("replay: seek/playback draws its final frame", sought and all(abs(a - b) < 1e-6 for a, b in zip(sought["actual"], sought["expected"])), sought)
                    idle_check(page, "replay after playback")

                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshots / f"demand_{label}.png"))
                check(label + ": no JavaScript exceptions", not errors, errors)
                report["pages"][label] = {"initial": before, "final": stats(page), "errors": errors}
                page.close()
            browser.close()
    finally:
        server.shutdown()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idle-ms", type=int, default=1200)
    parser.add_argument("--skip-4k", action="store_true", help="Skip the one actual 4K drawing-buffer check.")
    parser.add_argument("--polls-only", action="store_true", help="Run only idle rendering and unchanged live-poll checks.")
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
