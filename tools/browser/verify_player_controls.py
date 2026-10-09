"""Isolated live/replay avatar and turn-based control browser verification.

External integrations are disabled before importing the app. The temporary
loopback server injects diagnostics into template copies only. Most input checks
use short fixture responses; the final hotseat move uses the real backend and
physics. Screenshots and the JSON report default to the system temporary folder.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import math
from pathlib import Path
import re
import sys
import tempfile
import threading
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


DIAGNOSTICS = r"""
window.__controlsMoves=[];
if(typeof displayMove!=='undefined') {
  const controlsOriginalDisplay=displayMove;
  displayMove=async(...args)=>{
    window.__controlsMoves.push({stage:'start',key:args[1],busy:isAnimating});
    try{return await controlsOriginalDisplay(...args);}
    finally{window.__controlsMoves.push({stage:'end',key:args[1],busy:isAnimating});}
  };
}
window.__controlsQA = {
  ready:()=>typeof gameState!=='undefined'?!!gameState:state.players_a.length>0,
  active:()=>JSON.parse(JSON.stringify(typeof gameState!=='undefined'?gameState:state)),
  cameraState:()=>({render:window.__renderStats(),control:window.__playerControlState?.(),
    camera:camera.position.toArray(),target:controls.target.toArray(),orbit:controls.enabled,
    damping:controls.enableDamping,pan:controls.enablePan,rotate:controls.enableRotate,
    penalty:typeof gameState!=='undefined'&&gameState.penalty_shootout}),
  stats:()=>({
    render:window.__renderStats(),
    control:window.__playerControlState?window.__playerControlState():null,
    players:[...playerMeshesA,...playerMeshesB].map(player=>({
      position:player.position.toArray(),scale:player.scale.toArray(),
      userData:{radius:player.userData.radius,top:player.userData.top,selected:player.userData.ring?.visible,
        appearance:player.userData.avatar?.appearance,number:player.userData.avatar?.number,
        pose:player.userData.avatar?.pose},
    })),
    resources:(()=>{
      const geometry=new Set(),material=new Set(),textures=new Set();let meshes=0,triangles=0;
      for(const player of [...playerMeshesA,...playerMeshesB]) player.traverse(child=>{
        if(child.geometry){geometry.add(child.geometry);meshes++;triangles+=(child.geometry.index?.count??child.geometry.attributes.position?.count??0)/3;}
        for(const value of Array.isArray(child.material)?child.material:[child.material]) {
          if(!value)continue;material.add(value);if(value.map)textures.add(value.map);
        }
      });
      return {geometry:geometry.size,material:material.size,textures:textures.size,meshes,triangles,
        gpuGeometry:renderer.info.memory.geometries,gpuTextures:renderer.info.memory.textures};
    })(),
  }),
  fixture:(options={})=>{
    if(typeof gameState==='undefined')return null;
    cancelLocalTurn();
    if(!window.__controlsSeed)window.__controlsSeed=JSON.parse(JSON.stringify(gameState));
    if(keepTimer){clearInterval(keepTimer);keepTimer=null;}
    if(penaltyShootTimer){clearInterval(penaltyShootTimer);penaltyShootTimer=null;}
    const next=JSON.parse(JSON.stringify(window.__controlsSeed));
    next.game_mode=options.mode??'hvh';next.is_player_a=options.turnA??true;
    next.game_over=!!options.over;next.penalty_shootout=!!options.penalty;
    next.power_cap=options.cap??100;
    next.kick_count=(window.__controlsFixtureCount=(window.__controlsFixtureCount??0)+1);
    if(options.penalty){next.penalty_kick_num=0;next.penalty_kicks=[];next.penalty_a_score=0;next.penalty_b_score=0;next.ball={x:1106,y:437.5,z:0};}
    if(options.size!==undefined){next.players_a[0].stats={...next.players_a[0].stats,size:options.size};}
    Object.assign(ONLINE,{active:!!options.online,ready:!!options.online,status:options.online?'active':null,
      leaving:false,connectionError:'',side:Object.hasOwn(options,'side')?options.side:'a',roomId:'controls-qa',polling:false});
    isAnimating=!!options.busy;
    gameState=next;rebuildPlayers();updateHUD(next);
    // Penalty deadlines are exercised by the existing playback harness. Keep
    // these input checks deterministic without automatic timer-driven moves.
    if(keepTimer){clearInterval(keepTimer);keepTimer=null;}
    if(penaltyShootTimer){clearInterval(penaltyShootTimer);penaltyShootTimer=null;}
    document.getElementById('mode-select').value=next.game_mode;
    window.setViewMode('top');controls.enableDamping=false;
    if(next.penalty_shootout) {
      const position=next.is_player_a?serverToWorld(PENALTY_SPOT_X_A-320,H/2+110):serverToWorld(PENALTY_KEEPER_X_B-110,H/2);
      position.y=next.is_player_a?520:420;camera.position.copy(position);
      controls.target.copy(serverToWorld(next.is_player_a?W-20:PENALTY_SPOT_X_B,H/2));controls.target.y=12;
    } else { camera.position.set(0,1100,400);controls.target.set(0,0,0); }
    camera.lookAt(controls.target);controls.update();requestRender();
    return window.__playerControlState?window.__playerControlState():null;
  },
  project:(team,index=0,height=12)=>{
    const active=typeof gameState!=='undefined'?gameState:state;
    const player=team==='ball'?active.ball:active['players_'+team][index];
    const position=serverToWorld(player.x,player.y,height);camera.updateMatrixWorld();position.project(camera);
    const rect=renderer.domElement.getBoundingClientRect();
    return {x:rect.left+(position.x+1)*rect.width/2,y:rect.top+(1-position.y)*rect.height/2};
  },
  closeCamera:(team='a',index=0)=>{
    const active=typeof gameState!=='undefined'?gameState:state;
    const player=active['players_'+team][index],position=serverToWorld(player.x,player.y);
    if(typeof gameState!=='undefined')window.setViewMode('top');
    controls.enableDamping=false;controls.minDistance=40;camera.position.copy(position).add(new THREE.Vector3(75,60,100));
    controls.target.copy(position).add(new THREE.Vector3(0,25,0));camera.lookAt(controls.target);controls.update();requestRender();
  },
  draw:()=>{camera.updateMatrixWorld();renderer.render(scene,camera);return window.__controlsQA.stats();},
  realHotseat:async()=>{
    gameState=await fetch('/__controls/real-hotseat').then(response=>response.json());
    ONLINE.active=false;isAnimating=false;rebuildPlayers();updateHUD(gameState);requestRender();
    return window.__controlsQA.active();
  },
  realHvai:async()=>{
    gameState=await fetch('/__controls/real-hvai').then(response=>response.json());
    ONLINE.active=false;isAnimating=false;rebuildPlayers();updateHUD(gameState);requestRender();
    return window.__controlsQA.active();
  },
  penaltyConvergence:()=>{
    camera.position.set(0,1100,400);controls.target.set(0,0,0);controls.enableDamping=false;
    camera.lookAt(controls.target);controls.update();
    let moving=true,steps=0;
    for(;moving&&steps<1000;steps++)moving=updatePenaltyCamera(gameState);
    return {moving,steps,camera:camera.position.toArray(),target:controls.target.toArray()};
  },
};
"""


def instrument(source: str) -> str:
    pattern = r'(<script\b[^>]*\btype=[\'\"]module[\'\"][^>]*>)(.*?)(</script>)'
    result, count = re.subn(pattern, lambda m: m[1] + m[2] + DIAGNOSTICS + m[3], source, flags=re.S)
    if count != 1:
        raise RuntimeError("Expected exactly one module script")
    return result


def run(args) -> dict:
    isolated_environment()
    from flask import jsonify, redirect, render_template, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    templates = {name: instrument((ROOT / "templates" / name).read_text(encoding="utf-8"))
                 for name in ("game/index_3d.html", "game/replay_3d.html")}
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates), application.app.jinja_loader])
    seed = new_soccer_state(mode="hvh", player_count=7, half_length=9999)
    first = {"x": 700, "y": 437.5, "z": 0, "a": seed["players_a"], "b": seed["players_b"]}

    @application.app.route("/__controls/login")
    def login():
        session["user_id"] = "dev:player-controls-qa"
        session["username"] = "Controls QA"
        save_game(session["user_id"], copy.deepcopy(seed))
        return redirect("/play3d")

    @application.app.route("/__controls/replay")
    def replay():
        return render_template("game/replay_3d.html", username="QA", t={"id": "qa", "name": "QA"},
                               match={"id": "qa", "participant_a": "A", "participant_b": "B",
                                      "replay_data": [{"trajectory": [first]}]},
                               highlights=[], highlight=None, live_room=None,
                               loss_model=None, loss_model_name=None)

    @application.app.route("/__controls/real-hotseat")
    def real_hotseat():
        next_state = copy.deepcopy(seed)
        next_state["is_player_a"] = False
        save_game(session["user_id"], next_state)
        return jsonify(next_state)

    @application.app.route("/__controls/real-hvai")
    def real_hvai():
        next_state = copy.deepcopy(seed)
        next_state["game_mode"] = "hvai"
        save_game(session["user_id"], next_state)
        return jsonify(next_state)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    destination = args.screenshots or Path(tempfile.gettempdir()) / "agent-soccer-player-controls"
    destination.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "pages": {}, "artifacts": str(destination)}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(("PASS" if passed else "FAIL") + " " + name, flush=True)

    def settle(page):
        try:
            page.wait_for_function("window.__renderStats && !window.__renderStats().loop.pendingFrame && window.__renderStats().loop.pendingCallbacks===0", timeout=60000)
        except Exception:
            report["lastFailure"] = page.evaluate("window.__controlsQA.cameraState()")
            print("FAILED SETTLE " + json.dumps(report["lastFailure"]), flush=True)
            raise

    def control(page):
        return page.evaluate("window.__playerControlState()")

    def fixture(page, **options):
        page.evaluate("options=>window.__controlsQA.fixture(options)", options)
        settle(page)
        return control(page)

    def focus_pitch(page):
        page.locator("#three-container canvas").focus()

    def point(page, team="a", index=0, height=12):
        return page.evaluate("args=>window.__controlsQA.project(...args)", [team, index, height])

    def input_value(page, selector, value):
        page.locator(selector).evaluate("(node,value)=>{node.value=String(value);node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));}", value)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1280, "height": 1000}, device_scale_factor=1)
            context.add_init_script("localStorage.setItem('agent-soccer-render-quality','1080p')")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/customization", lambda route: route.fulfill(json={"bg_scene": "day"}))
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/__controls/login", wait_until="networkidle", timeout=60000)
            page.wait_for_function("window.__controlsQA && window.__controlsQA.ready() && window.__playerControlState", timeout=60000)
            settle(page)
            page.evaluate("window.__controlsQA.closeCamera('a',3)")
            settle(page)
            page.locator("#three-container").screenshot(path=str(destination / "play_player_detail.png"))
            print("CAPTURED player close-up " + str(destination / "play_player_detail.png"), flush=True)
            posts = []
            responses = []
            traffic = []

            def mock_move(route):
                path = urlparse(route.request.url).path
                if route.request.method != "POST":
                    route.continue_()
                    return
                active = page.evaluate("window.__controlsQA.active()")
                payload = route.request.post_data_json or {}
                traffic.append({"path": path, "body": payload, "busy": control(page)["busy"]})
                if path == "/goalkeeper_move":
                    route.fulfill(json={"ok": True})
                    return
                if path.endswith("/move") or path in ("/ai_move", "/random_move"):
                    manual = path.endswith("/move")
                    if manual:
                        posts.append({"path": path, "body": payload})
                    team = "a" if active["is_player_a"] else "b"
                    index = int(payload.get("player_idx", 0)) if manual else 1 if path == "/random_move" else 0
                    player = active["players_" + team][index]
                    frame = {"x": active["ball"]["x"], "y": active["ball"]["y"], "z": 0, "t": 0,
                             "a": copy.deepcopy(active["players_a"]), "b": copy.deepcopy(active["players_b"])}
                    result = {"trajectory": [frame, {**frame, "t": .08}], "scored": False,
                              "desc": "Fixture kick", "player_idx": index,
                              "angle": payload.get("angle", 0), "power": payload.get("power", 0),
                              "kick_endpoint": {"x": player["x"], "y": player["y"]}, "mover": team}
                    active["kick_count"] += 1
                    active["is_player_a"] = not active["is_player_a"]
                    result_key = "move_result" if manual else "ai_result" if path == "/ai_move" else "random_result"
                    response = {"game": active, "move_result": result} if "/online/" in path else {**active, result_key: result}
                else:
                    response = active
                responses.append(response)
                route.fulfill(json=response)

            page.route("**/move", mock_move)
            page.route("**/ai_move", mock_move)
            page.route("**/random_move", mock_move)
            page.route("**/goalkeeper_move", mock_move)

            state = fixture(page)
            check("Hotseat selection exposes all legal team A players", state["team"] == "a" and state["allowedIndices"] == list(range(7)), state)
            page.locator("#player-select").select_option("3")
            check("Player selector updates the controlled player", control(page)["selectedIndex"] == 3)
            avatars = page.evaluate("window.__controlsQA.stats().players")
            selected = [index for index, item in enumerate(avatars) if item["userData"].get("selected")]
            check("Selection highlights one athlete without inflating its physical size", selected == [3] and all(item["scale"] == [1, 1, 1] for item in avatars), avatars)
            focus_pitch(page)
            page.keyboard.press("q")
            forward = control(page)["selectedIndex"]
            page.keyboard.press("Shift+q")
            check("Q and Shift+Q select adjacent legal players", forward == 4 and control(page)["selectedIndex"] == 3)
            before = control(page)
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowDown")
            after = control(page)
            check("Arrow keys aim and adjust power without firing", len(posts) == 0 and after["angle"] != before["angle"] and after["basePower"] < before["basePower"], {"before": before, "after": after})
            input_value(page, "#kick-power", 57)
            input_value(page, "#aim-angle", 33)
            expected = control(page)
            focus_pitch(page)
            page.keyboard.press("Enter")
            page.wait_for_function("!window.__playerControlState().busy", timeout=15000)
            check("Enter submits the selected player, displayed angle, and power once", len(posts) == 1 and posts[-1]["body"]["player_idx"] == 3 and abs(posts[-1]["body"]["angle"] - expected["angle"]) < .01 and abs(posts[-1]["body"]["power"] - expected["power"]) < .01, {"expected": expected, "posts": posts})

            state = fixture(page, turnA=False)
            page.locator("#player-select").select_option("2")
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__playerControlState().busy", timeout=15000)
            check("Hotseat team B submits its selected player", state["team"] == "b" and posts[-1]["body"]["player_idx"] == 2, {"state": state, "post": posts[-1]})

            fixture(page)
            count = len(posts)
            captain = point(page, height=30)
            page.mouse.click(captain["x"], captain["y"])
            settle(page)
            check("Clicking a player head selects without an accidental kick", len(posts) == count and not control(page)["dragging"], control(page))
            captain = point(page)
            page.mouse.move(captain["x"], captain["y"])
            page.mouse.down()
            page.mouse.move(captain["x"] + 3, captain["y"] + 2)
            page.mouse.up()
            settle(page)
            check("A small pointer movement stays below the kick threshold", len(posts) == count)
            page.mouse.click(captain["x"], captain["y"], button="right")
            settle(page)
            check("Right mouse camera gestures never begin a kick", len(posts) == count and not control(page)["dragging"])

            fixture(page)
            captain = point(page)
            page.mouse.move(captain["x"], captain["y"])
            page.mouse.down()
            page.mouse.move(captain["x"] - 55, captain["y"])
            dragging = control(page)
            page.keyboard.press("Escape")
            page.mouse.up()
            settle(page)
            check("Escape cancels a drag without submitting", dragging["dragging"] and not control(page)["dragging"] and len(posts) == count)

            for cancellation in ("pointercancel", "lostpointercapture"):
                fixture(page)
                captain = point(page)
                page.mouse.move(captain["x"], captain["y"])
                page.mouse.down()
                page.mouse.move(captain["x"] - 55, captain["y"])
                page.locator("#three-container canvas").dispatch_event(cancellation, {"pointerId": 1, "pointerType": "mouse"})
                page.mouse.up()
                settle(page)
                check(cancellation + " cancels safely without submitting", len(posts) == count and not control(page)["dragging"])

            fixture(page)
            captain = point(page)
            page.mouse.move(captain["x"], captain["y"])
            page.mouse.down()
            page.mouse.move(captain["x"] - 55, captain["y"])
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            page.mouse.up()
            settle(page)
            check("Window focus loss cancels a pending kick", len(posts) == count and not control(page)["dragging"])

            fixture(page)
            captain = point(page)
            # Trusted Chromium touch events have active pointer identities, so
            # setPointerCapture follows its real browser contract. Synthetic
            # touch pointerdown would fail that contract before the test begins.
            touch = context.new_cdp_session(page)
            touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{**captain, "id": 1}]})
            pulled = {"x": captain["x"] - 55, "y": captain["y"], "id": 1}
            touch.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [pulled]})
            touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pulled, {**captain, "id": 2}]})
            touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            touch.detach()
            settle(page)
            check("A second touch cancels aiming rather than kicking", len(posts) == count and not control(page)["dragging"])

            fixture(page)
            captain = point(page)
            touch = context.new_cdp_session(page)
            touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{**captain, "id": 1}]})
            touch.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": captain["x"] - 60, "y": captain["y"] + 10, "id": 1}]})
            touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            touch.detach()
            page.wait_for_function("!window.__playerControlState().busy", timeout=15000)
            check("One-finger touch drag submits exactly one intentional kick", len(posts) == count + 1 and not control(page)["dragging"])
            count = len(posts)

            fixture(page)
            captain = point(page)
            page.mouse.move(captain["x"], captain["y"])
            page.mouse.down()
            page.mouse.move(captain["x"] - 60, captain["y"] - 20)
            aimed = control(page)
            page.mouse.up()
            page.wait_for_function("!window.__playerControlState().busy", timeout=15000)
            check("An intentional drag release fires one finite kick", len(posts) == count + 1 and all(isinstance(posts[-1]["body"][key], (int, float)) for key in ("angle", "power")) and aimed["dragging"], {"aimed": aimed, "post": posts[-1]})
            count = len(posts)

            fixture(page)
            focus_pitch(page)
            page.keyboard.down(" ")
            page.keyboard.down(" ")
            page.keyboard.up(" ")
            page.wait_for_function("!window.__playerControlState().busy", timeout=15000)
            check("Holding the kick key does not submit repeated kicks", len(posts) == count + 1)
            count = len(posts)

            fixture(page)
            page.locator("#aim-angle").focus()
            angle = control(page)["angle"]
            page.keyboard.press("q")
            page.keyboard.press("Enter")
            page.keyboard.press("Space")
            page.keyboard.press("ArrowRight")
            check("Editable fields keep typing and native arrow behavior", len(posts) == count and not control(page)["dragging"] and control(page)["angle"] == angle)
            page.evaluate("""()=>{const field=document.createElement('div');field.id='qa-editable';field.contentEditable='true';field.textContent='Editable';document.body.append(field);field.focus();}""")
            page.keyboard.press("Enter")
            page.keyboard.press("q")
            check("Contenteditable text never dispatches a match action", len(posts) == count)
            page.evaluate("document.getElementById('qa-editable').remove()")
            page.locator("#sound-btn").focus()
            page.keyboard.press("Enter")
            check("Focused buttons retain their own Enter action", len(posts) == count)
            page.evaluate("window.showOnlineLobby(true)")
            page.keyboard.press("ArrowRight")
            page.keyboard.press("Enter")
            page.keyboard.press("q")
            check("An open dialog blocks game keyboard actions", len(posts) == count)
            page.evaluate("window.closeOnlineLobby()")

            fixture(page, mode="hvai")
            state = control(page)
            focus_pitch(page)
            page.keyboard.press("q")
            check("Human versus AI honestly limits manual control to captain zero", state["allowedIndices"] == [0] and control(page)["selectedIndex"] == 0, state)
            for label, options in (("AI opponent turn", {"mode": "hvai", "turnA": False}),
                                   ("AI versus AI", {"mode": "aivai"}),
                                   ("Finished match", {"over": True}),
                                   ("Busy playback", {"busy": True}),
                                   ("Online opponent turn", {"online": True, "side": "b", "turnA": True}),
                                   ("Online spectator", {"online": True, "side": None, "turnA": True})):
                state = fixture(page, **options)
                focus_pitch(page)
                page.keyboard.press("Enter")
                page.keyboard.press("Space")
                pnt = point(page)
                page.mouse.click(pnt["x"], pnt["y"])
                check(label + " blocks manual kicks", not state["canKick"] and len(posts) == count and not control(page)["dragging"], state)

            fixture(page, online=True, side="b", turnA=False)
            page.locator("#player-select").select_option("4")
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__playerControlState().busy", timeout=15000)
            check("Online team B dispatches its legal selected index to the room", len(posts) == count + 1 and posts[-1]["path"] == "/online/controls-qa/move" and posts[-1]["body"]["player_idx"] == 4, posts[-1])
            count = len(posts)

            fixture(page, mode="hvai", penalty=True)
            convergence = page.evaluate("window.__controlsQA.penaltyConvergence()")
            settle(page)
            check("Penalty camera converges to a finite resting view", not convergence["moving"] and convergence["steps"] < 300, convergence)
            ball_before = control(page)["ball"]
            ball = point(page, "ball", height=0)
            page.mouse.move(ball["x"], ball["y"])
            page.mouse.down()
            page.mouse.move(ball["x"] - 55, ball["y"] + 15)
            aiming = control(page)
            page.keyboard.press("Escape")
            page.mouse.up()
            check("Penalty aiming keeps the authoritative ball stationary", aiming["dragging"] and aiming["ball"] == ball_before and control(page)["ball"] == ball_before and len(posts) == count, {"before": ball_before, "aiming": aiming})
            fixture(page, mode="hvai", turnA=False, penalty=True)
            focus_pitch(page)
            page.keyboard.press("Enter")
            check("Penalty goalkeeper view cannot dispatch a shooter kick", not control(page)["canKick"] and len(posts) == count)
            keeper_start = len(traffic)
            page.locator("#penalty-keeper-btns button").filter(has_text="Left").click()
            page.locator("#three-container").scroll_into_view_if_needed()
            page.wait_for_function("!window.__playerControlState().busy && window.__playerControlState().canKick", timeout=15000)
            keeper_traffic = traffic[keeper_start:]
            check("Visible keeper choice submits its direction before the AI shot", [item["path"] for item in keeper_traffic] == ["/goalkeeper_move", "/ai_move"] and keeper_traffic[0]["body"] == {"direction": "left"}, keeper_traffic)

            fixture(page)
            captain = point(page)
            page.mouse.move(captain["x"], captain["y"])
            page.mouse.down()
            page.mouse.move(captain["x"] - 55, captain["y"])
            fixture(page, mode="hvai", turnA=False)
            page.mouse.up()
            check("A turn or mode change cancels an in-progress drag", len(posts) == count and not control(page)["dragging"])

            fixture(page, cap=50)
            input_value(page, "#kick-power", 100)
            check("Power preview respects the server's configured cap", control(page)["power"] <= 50 and "50" in page.locator("#kick-power-value").inner_text(), control(page))

            sizes = []
            for size in (0, 50, 100):
                fixture(page, size=size)
                sizes.append(page.evaluate("window.__controlsQA.stats().players[0].userData.radius"))
            check("Avatar size zero, default, and maximum match physics radii", sizes == [12, 20, 28], sizes)

            fixture(page, mode="hvai")
            chain_start = len(traffic)
            page.evaluate("window.__controlsMoves.length=0")
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__playerControlState().busy", timeout=20000)
            chain_traffic = traffic[chain_start:]
            chain_moves = page.evaluate("window.__controlsMoves")
            check("Captain, opponent, ally, and opponent reply stay busy until the captain regains control", [item["path"] for item in chain_traffic] == ["/move", "/ai_move", "/random_move", "/ai_move"] and all(item["busy"] for item in chain_traffic) and len(chain_moves) == 8 and all(item["busy"] for item in chain_moves) and control(page)["canKick"] and control(page)["selectedIndex"] == 0, {"requests": chain_traffic, "moves": chain_moves, "control": control(page)})

            fixture(page)
            def reject_move(route):
                route.fulfill(status=400, json={"error": "Fixture move rejection"})
            page.route("**/move", reject_move)
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__playerControlState().busy && document.getElementById('player-control-status').textContent.includes('Fixture move rejection')", timeout=15000)
            check("A rejected human move unlocks controls with an accessible error", page.locator("#kick-btn").is_enabled() and page.locator("#player-control-status").get_attribute("role") == "status" and page.locator("#player-control-status").get_attribute("data-error") == "true" and not page.locator("#canvas-overlay").evaluate("node=>node.classList.contains('visible')"))
            page.unroute("**/move", reject_move)

            fixture(page, mode="hvai", turnA=False)
            def reject_ai(route):
                route.fulfill(status=503, json={"error": "Fixture AI rejection"})
            page.route("**/ai_move", reject_ai)
            page.locator("#aivai-btn").click()
            page.wait_for_function("!window.__playerControlState().busy && document.getElementById('player-control-status').textContent.includes('Fixture AI rejection')", timeout=15000)
            check("A rejected AI request clears its overlay and exposes retry status", page.locator("#aivai-btn").is_visible() and page.locator("#player-control-status").get_attribute("data-error") == "true" and not page.locator("#canvas-overlay").evaluate("node=>node.classList.contains('visible')"))
            page.unroute("**/ai_move", reject_ai)

            fixture(page)
            resources = []
            for _ in range(5):
                page.evaluate("window.__controlsQA.fixture({});window.__controlsQA.draw()")
                settle(page)
                resources.append(page.evaluate("window.__controlsQA.stats().resources"))
            check("Repeated team rebuilds keep GPU geometry and texture counts stable", len({(item["gpuGeometry"], item["gpuTextures"]) for item in resources[1:]}) == 1, resources)
            check("Avatar geometry is shared across fourteen players", resources[-1]["geometry"] < resources[-1]["meshes"] / 2, resources[-1])
            avatars = page.evaluate("window.__controlsQA.stats().players")
            check("Players have varied natural appearances and unique shirt numbers", len({item["userData"]["appearance"]["skin"] for item in avatars}) >= 4 and len({item["userData"]["number"] for item in avatars[:7]}) == 7, avatars)
            report["pages"]["play"] = page.evaluate("window.__controlsQA.stats()")

            page.evaluate("window.__controlsQA.closeCamera('a',3)")
            settle(page)
            page.locator("#three-container").screenshot(path=str(destination / "play_player_detail.png"))
            fixture(page)
            page.screenshot(path=str(destination / "play_controls_desktop.png"), full_page=True)
            settle(page)
            before = page.evaluate("window.__renderStats().frames")
            page.wait_for_timeout(700)
            after = page.evaluate("window.__renderStats()")
            check("Idle players and controls schedule zero render frames", before == after["frames"] and not after["loop"]["pendingFrame"] and after["loop"]["pendingCallbacks"] == 0, {"before": before, "after": after})

            page.unroute("**/move", mock_move)
            page.unroute("**/ai_move", mock_move)
            page.unroute("**/random_move", mock_move)
            page.unroute("**/goalkeeper_move", mock_move)
            page.evaluate("window.__controlsQA.realHotseat()")
            settle(page)
            real_before = page.evaluate("window.__controlsQA.active()")
            page.locator("#player-select").select_option("2")
            input_value(page, "#aim-angle", 180)
            input_value(page, "#kick-power", 40)
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__playerControlState().busy", timeout=20000)
            real_after = page.evaluate("window.__controlsQA.active()")
            moved_b = math.hypot(real_after["players_b"][2]["x"] - real_before["players_b"][2]["x"],
                                 real_after["players_b"][2]["y"] - real_before["players_b"][2]["y"])
            # Full-state JSON rounds positions to one decimal place, whereas
            # the raw fixture seed retains formation fractions. Compare within
            # the serialization precision rather than treating rounding as a
            # stationary opponent moving.
            static_a = all(abs(after_player[axis] - before_player[axis]) <= .051
                           for before_player, after_player in zip(real_before["players_a"], real_after["players_a"])
                           for axis in ("x", "y"))
            check("Real hotseat backend moves team B and hands the turn to A", real_after["kick_count"] == real_before["kick_count"] + 1 and real_after["is_player_a"] is True and moved_b > 1 and static_a, {"before": real_before, "after": real_after, "movedB": moved_b, "staticA": static_a})
            page.evaluate("window.__controlsQA.realHvai()")
            settle(page)
            page.locator("#aim-angle").fill("0")
            input_value(page, "#kick-power", 40)
            real_hvai_before = page.evaluate("window.__controlsQA.active().kick_count")
            page.locator("#kick-btn").click()
            page.wait_for_function("!window.__playerControlState().busy", timeout=30000)
            real_hvai_after = page.evaluate("window.__controlsQA.active()")
            history = real_hvai_after["move_history"]
            check("A real human-versus-AI cycle runs A/B/A/B physics and restores captain controls", real_hvai_after["kick_count"] == real_hvai_before + 4 and [item["player"] for item in history[-4:]] == ["A", "B", "A", "B"] and history[-4]["player_idx"] == 0 and history[-2]["player_idx"] != 0 and real_hvai_after["is_player_a"] and control(page)["canKick"], {"kick_count": real_hvai_after["kick_count"], "history": history[-4:], "control": control(page)})
            poses = page.evaluate("window.__controlsQA.stats().players.map(player=>player.userData.pose)")
            check("Player animation resets every limb pose after the real kick chain", all(pose == {"stride": 0, "kick": 0, "lean": 0} for pose in poses), poses)
            check("Gameplay controls run without JavaScript exceptions", not errors, errors)

            mobile = browser.new_context(viewport={"width": 375, "height": 900}, is_mobile=True, has_touch=True)
            mobile.add_init_script("localStorage.setItem('agent-soccer-render-quality','1080p')")
            mobile_page = mobile.new_page()
            mobile_errors = []
            mobile_page.on("pageerror", lambda error: mobile_errors.append(str(error)))
            mobile_page.route("**/api/customization", lambda route: route.fulfill(json={"bg_scene": "day"}))
            mobile_page.goto(base + "/__controls/login", wait_until="networkidle", timeout=60000)
            mobile_page.wait_for_function("window.__controlsQA && window.__controlsQA.ready()", timeout=60000)
            settle(mobile_page)
            metrics = mobile_page.evaluate("""()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,
                controls:document.getElementById('player-controls').getBoundingClientRect().toJSON(),
                kick:document.getElementById('kick-btn').getBoundingClientRect().toJSON(),
                assistantPosition:getComputedStyle(document.getElementById('bot-btn')).position})""")
            check("375px mobile controls fit without horizontal overflow", not metrics["overflow"] and metrics["kick"]["width"] >= 44 and metrics["kick"]["height"] >= 44, metrics)
            check("Mobile assistant remains in the page flow so it cannot cover player inputs", metrics["assistantPosition"] == "static", metrics)
            fixture(mobile_page)
            mobile_before = mobile_page.evaluate("window.__controlsQA.active().kick_count")
            captain = point(mobile_page)
            mobile_page.touchscreen.tap(captain["x"], captain["y"])
            check("A mobile tap selects without an accidental shot", mobile_page.evaluate("window.__controlsQA.active().kick_count") == mobile_before and not control(mobile_page)["dragging"])
            mobile_page.screenshot(path=str(destination / "play_controls_mobile.png"), full_page=True)
            check("Mobile player controls run without JavaScript exceptions", not mobile_errors, mobile_errors)
            mobile.close()

            replay_page = context.new_page()
            replay_errors = []
            replay_page.on("pageerror", lambda error: replay_errors.append(str(error)))
            replay_page.route("**/api/customization", lambda route: route.fulfill(json={"bg_scene": "day"}))
            replay_page.goto(base + "/__controls/replay", wait_until="networkidle", timeout=60000)
            replay_page.wait_for_function("window.__controlsQA && window.__controlsQA.ready()", timeout=60000)
            settle(replay_page)
            replay_page.evaluate("window.__controlsQA.closeCamera('a',3)")
            settle(replay_page)
            replay_page.locator("#three-container").screenshot(path=str(destination / "replay_player_detail.png"))
            replay_resources = replay_page.evaluate("window.__controlsQA.stats().resources")
            check("Replay uses the same shared avatar resources", replay_resources["geometry"] == resources[-1]["geometry"] and replay_resources["meshes"] == resources[-1]["meshes"], replay_resources)
            settle(replay_page)
            replay_before = replay_page.evaluate("window.__renderStats().frames")
            replay_page.wait_for_timeout(700)
            replay_after = replay_page.evaluate("window.__renderStats()")
            check("Replay avatars settle with zero idle render frames", replay_before == replay_after["frames"] and not replay_after["loop"]["pendingFrame"], {"before": replay_before, "after": replay_after})
            check("Replay avatars run without JavaScript exceptions", not replay_errors, replay_errors)
            report["pages"]["replay"] = replay_page.evaluate("window.__controlsQA.stats()")
            browser.close()
    finally:
        server.shutdown()
        output = args.output or destination / "report.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    report = run(args)
    print(f"{sum(item['passed'] for item in report['checks'])}/{len(report['checks'])} checks passed")
    raise SystemExit(0 if all(item["passed"] for item in report["checks"]) else 1)
