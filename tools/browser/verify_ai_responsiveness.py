"""Check complete AI turn cycles, retry recovery, playback, and session changes.

Runs an isolated loopback Flask server. Fixture responses exercise interruption
paths; the final cycle uses the real AI planner and Pymunk simulation. No live
accounts, API keys, Redis, or Supabase services are used.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment
from tools.browser.verify_player_controls import instrument


DIAGNOSTICS = r"""
// This harness checks manual ordering, not elapsed penalty deadlines (covered
// by verify_game_performance). Prevent slow software rendering from auto-kicking.
const aiOriginalKeeperTimer = startKeeperTimer, aiOriginalShootTimer = startPenaltyShootTimer;
startKeeperTimer = () => { aiOriginalKeeperTimer(); clearInterval(keepTimer); keepTimer=null; };
startPenaltyShootTimer = () => { aiOriginalShootTimer(); clearInterval(penaltyShootTimer); penaltyShootTimer=null; };
window.__aiDisplays=[];
const aiOriginalDisplay=displayMove;
displayMove=async(...args)=>{
  const record={key:args[1],speed:args[3]?.speed??animSpeed,start:performance.now()};
  window.__aiDisplays.push(record);
  try{return await aiOriginalDisplay(...args);}
  finally{record.duration=performance.now()-record.start;}
};
window.__aiQA={
  state:()=>({busy:isAnimating,over:gameState.game_over,kick:gameState.kick_count,online:ONLINE.active,animBall:!!animBall}),
  duplicate:()=>{void triggerAI();void triggerAI();void submitKick(0,0,40);},
  join:game=>startOnline({room_id:'ai-session-qa',my_side:'a',status:'active',game,name_a:'New captain',name_b:'New opponent'}),
  real:async()=>{
    cancelLocalTurn();ONLINE.active=false;
    gameState=await fetch('/__ai/real').then(response=>response.json());
    rebuildPlayers();updateHUD(gameState);requestRender();
  },
};
"""


def run(args) -> dict:
    isolated_environment()
    from flask import jsonify, redirect, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    source = instrument((ROOT / "templates/game/index_3d.html").read_text(encoding="utf-8"))
    # Attach after module declarations, never to a classic vendor script.
    marker = "window.__controlsMoves=[];"
    source = source.replace(marker, DIAGNOSTICS + marker, 1)
    application.app.jinja_loader = ChoiceLoader([
        DictLoader({"game/index_3d.html": source}), application.app.jinja_loader,
    ])
    seed = new_soccer_state(mode="hvai", player_count=7, half_length=9999, win_goal_limit=999)

    @application.app.route("/__ai/login")
    def login():
        session["user_id"] = "dev:ai-responsiveness-qa"
        session["username"] = "AI responsiveness QA"
        save_game(session["user_id"], copy.deepcopy(seed))
        return redirect("/play3d")

    @application.app.route("/__ai/real")
    def real():
        next_state = copy.deepcopy(seed)
        save_game(session["user_id"], next_state)
        return jsonify(next_state)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {"checks": []}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(("PASS" if passed else "FAIL") + " " + name, flush=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1280, "height": 1000})
            context.add_init_script("localStorage.setItem('agent-soccer-render-quality','1080p')")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/customization", lambda route: route.fulfill(json={"bg_scene": "day"}))
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/__ai/login", wait_until="networkidle", timeout=60000)
            page.wait_for_function("window.__aiQA && window.__controlsQA.ready()", timeout=60000)
            traffic = []
            settings = {"duration": .08, "fail": None, "fail_at": None, "over_at": None,
                        "penalty_at": None, "join_at": None}

            def control():
                return page.evaluate("window.__playerControlState()")

            def fixture(**options):
                traffic.clear()
                settings.update(duration=.08, fail=None, fail_at=None, over_at=None,
                                penalty_at=None, join_at=None)
                page.evaluate("window.__aiDisplays.length=0;window.__controlsMoves.length=0")
                page.evaluate("options=>window.__controlsQA.fixture({mode:'hvai',...options})", options)

            def ready():
                page.wait_for_function("!window.__playerControlState().busy", timeout=30000)

            def mock_move(route):
                path = urlparse(route.request.url).path
                active = page.evaluate("window.__controlsQA.active()")
                team = "a" if active["is_player_a"] else "b"
                occurrence = sum(item["path"] == path for item in traffic) + 1
                traffic.append({"path": path, "team": team, "busy": control()["busy"]})
                if settings["fail"] == path and occurrence == settings["fail_at"]:
                    route.fulfill(status=503, json={"error": "Teammate temporarily unavailable"})
                    return
                if path == "/goalkeeper_move":
                    route.fulfill(json={"ok": True})
                    return
                index = 1 if path == "/random_move" else 0
                player = active["players_" + team][index]
                frame = {"x": active["ball"]["x"], "y": active["ball"]["y"], "z": 0, "t": 0,
                         "a": copy.deepcopy(active["players_a"]), "b": copy.deepcopy(active["players_b"])}
                result = {"trajectory": [frame, {**frame, "t": settings["duration"]}], "scored": False,
                          "desc": "AI QA kick", "player_idx": index, "mover": team,
                          "angle": 0, "power": 0, "kick_endpoint": {"x": player["x"], "y": player["y"]}}
                active["kick_count"] += 1
                active["is_player_a"] = not active["is_player_a"]
                if settings["over_at"] == len(traffic):
                    active["game_over"] = True
                if settings["penalty_at"] == len(traffic):
                    active["penalty_shootout"] = True
                key = {"/move": "move_result", "/ai_move": "ai_result", "/random_move": "random_result"}[path]
                response = {**active, key: result}
                if settings["join_at"] == len(traffic):
                    page.evaluate("game=>window.__aiQA.join(game)", online_game)
                route.fulfill(json=response)

            for path in ("move", "ai_move", "random_move", "goalkeeper_move"):
                page.route("**/" + path, mock_move)

            fixture()
            settings["duration"] = 5
            page.locator("#kick-btn").click()
            page.evaluate("window.__aiQA.duplicate()")
            ready()
            displays = page.evaluate("window.__aiDisplays")
            check("A human turn completes A/B/A/B exactly once and restores captain control",
                  [item["path"] for item in traffic] == ["/move", "/ai_move", "/random_move", "/ai_move"]
                  and all(item["busy"] for item in traffic) and control()["canKick"]
                  and control()["selectedIndex"] == 0, {"traffic": traffic.copy(), "control": control()})
            check("Automated playback is twice as fast while the human trajectory keeps its speed",
                  [item["speed"] for item in displays] == [1, 2, 2, 2]
                  and displays[0]["duration"] >= 2200
                  and all(1000 <= item["duration"] < 1800 for item in displays[1:]), displays)

            fixture()
            settings.update(fail="/random_move", fail_at=1)
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__aiQA.state().busy && document.getElementById('player-control-status').textContent.includes('Teammate temporarily unavailable')")
            check("A failed teammate leg exposes Retry and blocks a duplicate captain kick",
                  not control()["canKick"] and page.locator("#aivai-btn").is_enabled(), control())
            page.locator("#aivai-btn").click()
            ready()
            check("Retry resumes the teammate and opponent reply without repeating earlier moves",
                  [item["path"] for item in traffic] == ["/move", "/ai_move", "/random_move", "/random_move", "/ai_move"]
                  and control()["canKick"], traffic.copy())

            fixture()
            settings.update(fail="/ai_move", fail_at=2)
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__aiQA.state().busy && document.getElementById('player-control-status').textContent.includes('Teammate temporarily unavailable')")
            page.locator("#aivai-btn").click()
            ready()
            check("A failed final opponent reply retries that reply alone",
                  [item["path"] for item in traffic] == ["/move", "/ai_move", "/random_move", "/ai_move", "/ai_move"]
                  and control()["canKick"], traffic.copy())

            fixture()
            settings["over_at"] = 2
            page.locator("#kick-btn").click()
            ready()
            check("Game over stops the cycle before another teammate or opponent request",
                  [item["path"] for item in traffic] == ["/move", "/ai_move"] and not control()["canKick"], traffic.copy())

            fixture()
            settings["penalty_at"] = 1
            page.locator("#kick-btn").click()
            ready()
            check("Entering penalties stops automatic replies until the human chooses a keeper direction",
                  [item["path"] for item in traffic] == ["/move"]
                  and page.locator("#penalty-keeper-btns").is_visible(), traffic.copy())
            page.locator("#penalty-keeper-btns button").filter(has_text="Left").click()
            ready()
            check("Keeper selection and its AI penalty are a single ordered request chain",
                  [item["path"] for item in traffic] == ["/move", "/goalkeeper_move", "/ai_move"]
                  and control()["canKick"], traffic.copy())

            fixture(mode="aivai")
            settings["over_at"] = 2
            page.evaluate("void window.triggerAI();void window.triggerAI();void window.triggerAI()")
            page.wait_for_function("window.__aiQA.state().over && !window.__aiQA.state().busy", timeout=15000)
            check("AI versus AI continues automatically without duplicate requests",
                  [item["path"] for item in traffic] == ["/ai_move", "/ai_move"], traffic.copy())

            online_game = copy.deepcopy(seed)
            online_game.update(game_mode="hvh", kick_count=42, online_move_count=42)
            page.route("**/online/ai-session-qa/state**", lambda route: route.fulfill(json={
                "room_id": "ai-session-qa", "my_side": "a", "status": "active", "game": online_game,
                "move_count": 42, "moves": [], "name_a": "New captain", "name_b": "New opponent",
            }))
            fixture()
            settings["join_at"] = 2
            page.locator("#kick-btn").click()
            page.wait_for_function("window.__aiQA.state().online && window.__aiQA.state().kick===42 && !window.__aiQA.state().busy", timeout=15000)
            page.wait_for_timeout(400)
            check("Joining an online session during an AI response discards the old state and remaining cycle",
                  len(traffic) == 2 and page.evaluate("window.__aiQA.state().online && window.__aiQA.state().kick===42 && !window.__aiQA.state().busy"), traffic.copy())

            fixture()
            settings["duration"] = 5
            page.locator("#kick-btn").click()
            page.wait_for_function("window.__aiDisplays.length===1 && window.__aiQA.state().busy")
            page.evaluate("game=>window.__aiQA.join(game)", online_game)
            page.wait_for_timeout(500)
            check("Joining during playback cancels the old animation without starting an AI reply",
                  len(traffic) == 1 and page.evaluate("window.__aiQA.state().online && window.__aiQA.state().kick===42 && !window.__aiQA.state().busy && !window.__aiQA.state().animBall"), traffic.copy())

            for path in ("move", "ai_move", "random_move", "goalkeeper_move"):
                page.unroute("**/" + path, mock_move)
            page.evaluate("window.__aiQA.real()")
            before = page.evaluate("window.__aiQA.state().kick")
            page.locator("#kick-btn").click()
            ready()
            after = page.evaluate("window.__controlsQA.active()")
            history = after["move_history"][-4:]
            check("Real AI and physics complete the cycle with a non-captain teammate and ready controls",
                  after["kick_count"] == before + 4 and [item["player"] for item in history] == ["A", "B", "A", "B"]
                  and history[0]["player_idx"] == 0 and history[2]["player_idx"] != 0
                  and after["is_player_a"] and control()["canKick"], {"history": history, "control": control()})
            check("All AI cycle and cancellation checks have zero JavaScript exceptions", not errors, errors)
            browser.close()
    finally:
        server.shutdown()
        output = args.output or Path(tempfile.gettempdir()) / "agent-soccer-ai-responsiveness.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args)
    print(f"{sum(item['passed'] for item in report['checks'])}/{len(report['checks'])} checks passed")
    raise SystemExit(0 if all(item["passed"] for item in report["checks"]) else 1)
