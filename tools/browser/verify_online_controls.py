"""Isolated browser verification for online turns, joining, and room lifecycle.

External integrations are disabled before Flask imports. The loopback-only
server injects diagnostics into a template copy; product templates are untouched.
Real room endpoints and physics run with two independent browser sessions.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


INIT = r"""
localStorage.setItem('agent-soccer-render-quality','1080p');
window.__onlineIntervals=new Map();
const onlineQASetInterval=window.setInterval.bind(window);
const onlineQAClearInterval=window.clearInterval.bind(window);
window.setInterval=(callback,delay,...args)=>{
  const id=onlineQASetInterval(callback,delay,...args);
  window.__onlineIntervals.set(id,{id,delay,name:callback.name||'',stack:new Error().stack});
  return id;
};
window.clearInterval=id=>{window.__onlineIntervals.delete(id);return onlineQAClearInterval(id);};
const OnlineQANativePeer=window.RTCPeerConnection;
window.RTCPeerConnection=class extends OnlineQANativePeer {
  constructor(configuration={}){super({...configuration,iceServers:[]});}
};
"""

DIAGNOSTICS = r"""
window.__onlinePlayback=[];
const onlineQAAnimation=animateOnlineMove;
animateOnlineMove=async(move,game,...rest)=>{
  window.__onlinePlayback.push({stage:'start',source:'queued',revision:move?.kick_count,
    roomId:ONLINE.roomId,mover:move?.mover,index:move?.player_idx});
  try{return await onlineQAAnimation(move,game,...rest);}
  finally{window.__onlinePlayback.push({stage:'end',source:'queued',revision:move?.kick_count,roomId:ONLINE.roomId});}
};
const onlineQADisplay=displayMove;
displayMove=async(data,key,...rest)=>{
  if(ONLINE.active)window.__onlinePlayback.push({stage:'start',source:'human',
    revision:data?.[key]?.kick_count??data?.online_move_count,roomId:ONLINE.roomId,
    mover:data?.[key]?.mover,index:data?.[key]?.player_idx});
  return await onlineQADisplay(data,key,...rest);
};
const onlineQAFetch=window.fetch.bind(window);
window.__onlineQA={
  snapshot:()=>({
    online:{active:ONLINE.active,roomId:ONLINE.roomId,side:ONLINE.side,lastKick:ONLINE.lastKick,
      polling:ONLINE.polling,generation:ONLINE.generation,ready:ONLINE.ready,status:ONLINE.status,
      waitingRoomId:ONLINE_LOBBY.waitRoom,pollTimer:ONLINE.pollTimer,
      chatTimer:ONLINE.chat?._timer,chatDestroyed:ONLINE.chat?._destroyed},
    game:gameState?{count:gameState.online_move_count??gameState.kick_count,
      kickCount:gameState.kick_count,turnA:gameState.is_player_a,over:gameState.game_over,
      winner:gameState.winner,mode:gameState.game_mode,
      playersA:gameState.players_a.length,playersB:gameState.players_b.length,
      ball:{...gameState.ball}}:null,
    meshes:{a:playerMeshesA.length,b:playerMeshesB.length},
    control:window.__playerControlState?.(),render:window.__renderStats?.(),
    playback:window.__onlinePlayback.slice(),
    intervals:[...window.__onlineIntervals.values()],
    chatPanels:document.querySelectorAll('#match-chat-wrap .chat-panel').length,
    busy:isAnimating,
  }),
  poll:()=>pollOnline(),
  pausePoll:()=>{if(ONLINE.pollTimer){clearInterval(ONLINE.pollTimer);ONLINE.pollTimer=null;}},
  resumePoll:()=>{if(ONLINE.active&&!ONLINE.pollTimer)ONLINE.pollTimer=setInterval(pollOnline,1500);},
  restart:async()=>{
    const response=await onlineQAFetch('/online/'+ONLINE.roomId+'/state?since_kick=-1');
    const data=await response.json();return await startOnline(data);
  },
  clearPlayback:()=>{window.__onlinePlayback=[];},
  keepVoiceTracks:()=>{window.__onlineVoiceTracks=VOICE.localStream?.getTracks()??[];},
  holdNext:(pattern,method='GET')=>{
    window.__onlineHeld={ready:false,armed:true,pattern,method,data:null};
    window.fetch=(input,init={})=>{
      const url=typeof input==='string'?input:input.url;
      const kind=(init.method||input.method||'GET').toUpperCase();
      const held=window.__onlineHeld;
      if(!held.armed||!url.includes(pattern)||kind!==method)return onlineQAFetch(input,init);
      held.armed=false;
      return onlineQAFetch(input,init).then(async response=>{
        held.data=await response.clone().json();held.ready=true;
        return new Promise(resolve=>{held.release=()=>{window.fetch=onlineQAFetch;resolve(response);};});
      });
    };
  },
  release:()=>{window.__onlineHeld?.release?.();},
};
"""

SPECTATOR_DIAGNOSTICS = r"""
window.__spectatorPlayback=[];
const onlineQASpectatorAnimation=animateTrajectory3D;
animateTrajectory3D=(trajectory,...rest)=>{
  if(LIVE.room)window.__spectatorPlayback.push({revision:LIVE.lastKick,length:trajectory?.length});
  return onlineQASpectatorAnimation(trajectory,...rest);
};
window.__spectatorQA={
  snapshot:()=>({live:window.__liveState(),busy:isAnimating,polling:LIVE.polling,
    players:{a:playerMeshesA.length,b:playerMeshesB.length},
    scene:{info:window.__sceneInfo(),sun: sunMesh.position.toArray(),sky:!!skyDome.material.map},
    playback:window.__spectatorPlayback.slice(),intervals:[...window.__onlineIntervals.values()]}),
  poll:()=>pollLive(),
  pausePoll:()=>{if(LIVE.pollTimer){clearInterval(LIVE.pollTimer);LIVE.pollTimer=null;}},
  resumePoll:()=>{if(!LIVE.over&&!LIVE.pollTimer)LIVE.pollTimer=setInterval(pollLive,1500);},
  clearPlayback:()=>{window.__spectatorPlayback=[];},
};
"""


def instrument(source: str, diagnostics=DIAGNOSTICS) -> str:
    pattern = r'(<script\b[^>]*\btype=[\'\"]module[\'\"][^>]*>)(.*?)(</script>)'
    result, count = re.subn(pattern, lambda match: match[1] + match[2] + diagnostics + match[3], source, flags=re.S)
    if count != 1:
        raise RuntimeError("Expected exactly one module script")
    return result


def run(args) -> dict:
    isolated_environment()
    from flask import jsonify, redirect, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    templates = {
        "game/index_3d.html": instrument((ROOT / "templates/game/index_3d.html").read_text(encoding="utf-8")),
        "game/replay_3d.html": instrument((ROOT / "templates/game/replay_3d.html").read_text(encoding="utf-8"), SPECTATOR_DIAGNOSTICS),
    }
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates), application.app.jinja_loader])

    @application.app.route("/__online/login/<label>")
    def qa_login(label):
        session["user_id"] = f"dev:online-qa-{label}"
        session["username"] = f"Online QA {label.upper()}"
        save_game(session["user_id"], new_soccer_state(mode="hvh", player_count=3, half_length=9999))
        return redirect("/play3d")

    @application.app.route("/__online/inspect/<room_id>")
    def qa_room(room_id):
        room = application._get_room(room_id)
        return jsonify(room or {"missing": True})

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    destination = args.screenshots or Path(tempfile.gettempdir()) / "agent-soccer-online-qa"
    destination.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "artifacts": str(destination), "snapshots": {}, "requests": {}}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(("PASS" if passed else "FAIL") + " " + name, flush=True)

    def snapshot(page):
        return page.evaluate("window.__onlineQA.snapshot()")

    def settled(page, count=None, room_id=None):
        page.wait_for_function("""options=>{
          const s=window.__onlineQA?.snapshot();return s?.online.active&&s.game&&!s.busy&&!s.online.polling
            &&(options.count===null||s.game.count===options.count)
            &&(!options.room||s.online.roomId===options.room);
        }""", arg={"count": count, "room": room_id}, timeout=90000, polling=250)

    def local(page):
        page.wait_for_function("window.__onlineQA&& !window.__onlineQA.snapshot().online.active && !window.__onlineQA.snapshot().busy", timeout=90000)

    def room(context, room_id):
        return context.request.get(base + "/__online/inspect/" + room_id).json()

    def wait_room(context, room_id, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = room(context, room_id)
            if predicate(result):
                return result
            time.sleep(.05)
        return result

    def begin_waiting(page):
        previous = page.locator("#online-invite-link").input_value()
        page.locator("#online-btn").click()
        page.locator("#online-lobby").get_by_role("button", name="Create Match", exact=True).click()
        page.wait_for_function("previous=>{const value=document.getElementById('online-invite-link').value;return value.length>0&&value!==previous;}", arg=previous, timeout=30000)
        return page.locator("#online-invite-link").input_value().split("room=")[-1].split("/")[-1]

    def kick(page, index=2, power=6):
        page.wait_for_function("window.__playerControlState?.().canKick", timeout=90000)
        page.locator("#player-select").select_option(str(index))
        page.locator("#aim-angle").fill("0")
        page.locator("#kick-power").evaluate("(node,value)=>{node.value=String(value);node.dispatchEvent(new Event('input',{bubbles:true}));}", power)
        page.locator("#kick-btn").scroll_into_view_if_needed()
        target = page.locator("#kick-btn").evaluate("""node=>{const r=node.getBoundingClientRect();
          const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return {clear:hit?.closest('#kick-btn')===node,hit:hit?.outerHTML,
            kick:r.toJSON(),chat:document.getElementById('match-chat-wrap').getBoundingClientRect().toJSON()};}""")
        check("The enabled online Kick action is unobstructed", target["clear"], target)
        if not target["clear"]:
            page.screenshot(path=str(destination / "online_blocked_kick.png"), full_page=True)
            raise AssertionError("The visible Kick action is covered by another element")
        page.locator("#kick-btn").click()

    def api_kick(context, room_id, index=2):
        state = context.request.get(base + f"/online/{room_id}/state").json()
        return context.request.post(base + f"/online/{room_id}/move", data={
            "player_idx": index, "angle": 0, "power": 0,
            "expected_move_count": state["move_count"],
        })

    def timers(page):
        s = snapshot(page)
        return {"poll": sum(item["name"] == "pollOnline" for item in s["intervals"]),
                "chat": sum("/static/js/ui/chat.js" in item["stack"] for item in s["intervals"]),
                "panels": s["chatPanels"]}

    def spectator_settled(page, count):
        page.wait_for_function("count=>{const s=window.__spectatorQA?.snapshot();return s?.live.lastKick===count&&!s.busy&&!s.polling;}", arg=count, timeout=90000, polling=250)

    def spectator_timers(page):
        intervals = page.evaluate("window.__spectatorQA.snapshot().intervals")
        return {"poll": sum(item["name"] == "pollLive" for item in intervals),
                "chat": sum(item["name"] == "pollLiveChat" for item in intervals)}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
            contexts, pages, errors, external, posts, dialogs, console_messages = {}, {}, {}, {}, {}, {}, {}

            def create_browser(label, mobile=False, storage=None, initial=None, spectator=False):
                options = {"viewport": {"width": 375 if mobile else 1280, "height": 1000},
                           "is_mobile": mobile, "has_touch": mobile, "permissions": ["microphone"]}
                if storage:
                    options["storage_state"] = storage
                context = browser.new_context(**options)
                context.add_init_script(INIT)
                page = context.new_page()
                contexts[label], pages[label] = context, page
                errors[label], external[label], posts[label], dialogs[label] = [], [], [], []
                console_messages[label] = []
                page.on("pageerror", lambda error: errors[label].append(str(error)))
                page.on("console", lambda message: console_messages[label].append({"type": message.type, "text": message.text, "location": message.location}) if message.type in ("error", "warning") else None)
                page.on("request", lambda request: posts[label].append({"path": urlparse(request.url).path,
                    "method": request.method, "body": request.post_data_json})
                    if request.method == "POST" and re.search(r"/online/[^/]+/(move|leave)$", urlparse(request.url).path) else None)
                page.on("dialog", lambda dialog: (dialogs[label].append({"type": dialog.type, "message": dialog.message}), dialog.accept()))

                def local_only(route):
                    if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                        external[label].append(route.request.url);route.abort()
                    else:
                        route.continue_()
                context.route("**/*", local_only)
                page.goto(base + (initial or "/__online/login/" + label), wait_until="domcontentloaded", timeout=60000)
                if spectator:
                    page.wait_for_function("window.__spectatorQA?.snapshot().live.lastKick>=0", timeout=90000)
                else:
                    page.wait_for_function("window.__onlineQA?.snapshot().game", timeout=90000)
                    page.evaluate("window.setViewMode('top')")
                return context, page

            ca, a = create_browser("a")
            cb, b = create_browser("b")
            if args.layout_only:
                created = ca.request.post(base + "/online/create", data={}).json()
                layout_room = created["room_id"]
                joined = cb.request.post(base + f"/online/{layout_room}/join", data={})
                check("The visual pass uses a real joined room", joined.status == 200)
                a.goto(base + "/play3d?room=" + layout_room, wait_until="domcontentloaded")
                settled(a, 0, layout_room)
                a.evaluate("window.setViewMode('top')")
                cm, m = create_browser("mobile", mobile=True, storage=ca.storage_state(), initial="/play3d?room=" + layout_room)
                settled(m, 0, layout_room)
                for label, page in (("desktop", a), ("mobile", m)):
                    layout = page.evaluate("""()=>{const header=document.querySelector('.as-page-header');
                      const badge=document.getElementById('online-bar'),pitch=document.getElementById('fs-wrap');
                      const css=getComputedStyle(badge);return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                        header:header.getBoundingClientRect().toJSON(),badge:badge.getBoundingClientRect().toJSON(),
                        pitch:pitch.getBoundingClientRect().toJSON(),position:css.position,transform:css.transform};}""")
                    check(label + " headline, room badge and pitch reserve separate space",
                          layout["header"]["bottom"] <= layout["badge"]["top"] and layout["badge"]["bottom"] <= layout["pitch"]["top"] and layout["position"] == "static" and layout["transform"] == "none", layout)
                    check(label + " room badge fits without horizontal overflow", layout["scrollWidth"] <= layout["width"] + 1 and layout["badge"]["left"] >= 0 and layout["badge"]["right"] <= layout["width"] + 1, layout)
                    page.locator("#match-chat-wrap .chat-toggle").click()
                    chat_layout = page.evaluate("""()=>({chat:document.getElementById('match-chat-wrap').getBoundingClientRect().toJSON(),
                      controls:document.getElementById('player-controls').getBoundingClientRect().toJSON()})""")
                    check(label + " expanded chat reserves its own space", chat_layout["chat"]["top"] >= chat_layout["controls"]["bottom"], chat_layout)
                    page.locator("#match-chat-wrap .chat-toggle").click()
                    page.locator("#kick-btn").scroll_into_view_if_needed()
                    hit = page.locator("#kick-btn").evaluate("node=>{const r=node.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('#kick-btn')===node;}")
                    check(label + " Kick remains an unobstructed touch and click target", hit)
                    voice_layouts = []
                    for expanded in (False, True):
                        selector = "#voice-panel" if expanded else "#voice-btn"
                        if expanded:
                            page.locator("#voice-btn").click()
                            page.wait_for_function("window.__voiceState().enabled&&window.__voiceState().panelShown", timeout=15000)
                        voice_layout = page.locator(selector).evaluate("""node=>{
                          const voice=node.getBoundingClientRect(),css=getComputedStyle(node);
                          const selectors=['#kick-btn','#aim-cancel','#player-controls','.as-header','.as-control-panel'];
                          const bounds=selectors.map(selector=>({selector,rect:document.querySelector(selector).getBoundingClientRect().toJSON()}));
                          const overlaps=bounds.filter(({rect})=>Math.min(voice.right,rect.right)-Math.max(voice.left,rect.left)>0.5
                            &&Math.min(voice.bottom,rect.bottom)-Math.max(voice.top,rect.top)>0.5).map(({selector})=>selector);
                          return {voice:voice.toJSON(),position:css.position,width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                            overlaps,bounds};}""")
                        voice_layouts.append(voice_layout)
                        check(label + (" expanded voice panel" if expanded else " voice button") + " has no rectangle overlap with player controls, actions or navigation",
                              not voice_layout["overlaps"] and voice_layout["voice"]["width"] > 0 and voice_layout["voice"]["height"] > 0 and voice_layout["position"] in ("static", "relative"), voice_layout)
                        check(label + (" expanded voice panel" if expanded else " voice button") + " fits without horizontal overflow",
                              voice_layout["scrollWidth"] <= voice_layout["width"] + 1 and voice_layout["voice"]["left"] >= 0 and voice_layout["voice"]["right"] <= voice_layout["width"] + 1, voice_layout)
                        if expanded:
                            page.screenshot(path=str(destination / ("online_" + label + "_voice.png")), full_page=True)
                            page.evaluate("window.__onlineQA.keepVoiceTracks()")
                            page.locator("#voice-end-btn").click()
                            page.wait_for_function("!window.__voiceState().enabled&&!window.__voiceState().hasPc", timeout=10000)
                            check(label + " voice disconnect restores its button and stops the microphone", page.locator("#voice-btn").is_visible() and page.evaluate("window.__onlineVoiceTracks.length>0&&window.__onlineVoiceTracks.every(track=>track.readyState==='ended')"))
                    page.screenshot(path=str(destination / ("online_" + label + ".png")), full_page=True)
                    report["snapshots"][label] = {"layout": layout, "voiceLayouts": voice_layouts, "online": snapshot(page)}
                report["blocked_optional_assets"] = {label: [url for url in urls if urlparse(url).hostname == "media.giphy.com"] for label, urls in external.items()}
                integrations = {label: [url for url in urls if urlparse(url).hostname != "media.giphy.com"] for label, urls in external.items()}
                check("The final visual pass has no JavaScript exceptions", not any(errors.values()), errors)
                check("The final visual pass calls no external integrations", not any(integrations.values()), integrations)
                report["console_messages"] = console_messages
                for context in contexts.values():
                    context.close()
                browser.close()
                return report
            check("Both isolated accounts begin with independent local three-player teams", snapshot(a)["game"]["playersA"] == 3 and snapshot(b)["game"]["playersA"] == 3)

            cancelled = begin_waiting(a)
            check("Create Match exposes its real waiting room and invitation", room(ca, cancelled)["status"] == "waiting")
            a.get_by_role("button", name="Close online lobby", exact=True).click()
            a.wait_for_function("!document.getElementById('online-lobby').classList.contains('visible')")
            cancelled_state = wait_room(ca, cancelled, lambda state: state.get("status") == "cancelled" or state.get("missing"))
            check("Closing the waiting lobby cancels the server room", cancelled_state.get("status") == "cancelled" or cancelled_state.get("missing"), cancelled_state.get("status"))
            check("Waiting cancellation leaves usable local controls and no room/chat timers", not snapshot(a)["online"]["active"] and timers(a) == {"poll": 0, "chat": 0, "panels": 0}, timers(a))

            a.locator("#online-btn").click()
            a.evaluate("window.__onlineQA.holdNext('/online/create','POST')")
            a.locator("#online-lobby").get_by_role("button", name="Create Match", exact=True).click()
            a.wait_for_function("window.__onlineHeld?.ready", timeout=30000)
            late_room = a.evaluate("window.__onlineHeld.data.room_id")
            a.get_by_role("button", name="Close online lobby", exact=True).click()
            a.evaluate("window.__onlineQA.release()")
            late_state = wait_room(ca, late_room, lambda state: state.get("status") == "cancelled" or state.get("missing"))
            check("An in-flight create completed after Close is cancelled instead of reopening", late_state.get("status") == "cancelled" or late_state.get("missing"), late_state.get("status"))
            check("Late creation leaves the local game and lobby lifecycle clear", not snapshot(a)["online"]["active"] and not a.locator("#online-lobby").evaluate("node=>node.classList.contains('visible')"))

            first = begin_waiting(a)
            b.goto(base + "/join/" + first, wait_until="domcontentloaded", timeout=60000)
            settled(a, 0, first);settled(b, 0, first)
            initial_a, initial_b = snapshot(a), snapshot(b)
            online_count = len(room(ca, first)["game"]["players_a"])
            check("The /join deep link opens and joins the exact room", initial_b["online"]["side"] == "b" and initial_b["online"]["roomId"] == first and first in b.url, b.url)
            check("Both clients bootstrap and rebuild the authoritative online roster", online_count != 3 and initial_a["game"]["playersA"] == online_count and initial_b["game"]["playersA"] == online_count and initial_a["meshes"] == {"a": online_count, "b": online_count} and initial_b["meshes"] == {"a": online_count, "b": online_count})
            check("Joining starts no historical animation and only Team A may kick", not initial_a["playback"] and not initial_b["playback"] and initial_a["control"]["canKick"] and not initial_b["control"]["canKick"])
            check("Active clients have exactly one room poll and match-chat timer", timers(a) == {"poll": 1, "chat": 1, "panels": 1} and timers(b) == {"poll": 1, "chat": 1, "panels": 1}, {"a": timers(a), "b": timers(b)})
            a.screenshot(path=str(destination / "online_desktop.png"), full_page=True)
            for expanded in (False, True):
                if expanded:
                    a.locator("#match-chat-wrap .chat-toggle").click()
                layout = a.evaluate("""()=>({chat:document.getElementById('match-chat-wrap').getBoundingClientRect().toJSON(),
                  controls:document.getElementById('player-controls').getBoundingClientRect().toJSON(),
                  collapsed:document.querySelector('#match-chat-wrap .chat-panel').classList.contains('collapsed')})""")
                check("Expanded match chat reserves space below player controls" if expanded else "Collapsed match chat reserves space below player controls",
                      layout["chat"]["top"] >= layout["controls"]["bottom"] and layout["collapsed"] != expanded, layout)
            a.locator("#match-chat-wrap .chat-toggle").click()
            a.evaluate("window.__onlineQA.restart()")
            settled(a, 0, first)
            check("Repeated room bootstrap replaces its poll/chat lifecycle", timers(a) == {"poll": 1, "chat": 1, "panels": 1}, timers(a))

            print("Checking bounded local WebRTC connection...", flush=True)
            b.locator("#voice-btn").click()
            b.wait_for_function("window.__voiceState().enabled", timeout=15000)
            a.locator("#voice-btn").click()
            try:
                a.wait_for_function("window.__voiceState().connected", timeout=25000, polling=250)
                b.wait_for_function("window.__voiceState().connected", timeout=25000, polling=250)
                voice_connected = True
            except Exception:
                voice_connected = False
            voice_a, voice_b = a.evaluate("window.__voiceState()"), b.evaluate("window.__voiceState()")
            check("Two fake microphones form a real local host-only WebRTC connection", voice_connected, {"a": voice_a, "b": voice_b})
            a.locator("#voice-mic-btn").click()
            b.wait_for_function("window.__voiceState().oppMuted", timeout=10000)
            check("Mute stops the local microphone and reaches the opponent", a.evaluate("window.__voiceState().micMuted") and b.locator("#voice-opp-muted").is_visible())
            a.locator("#voice-mic-btn").click()
            b.wait_for_function("!window.__voiceState().oppMuted", timeout=10000)
            check("Unmute restores the microphone and clears the opponent badge", not a.evaluate("window.__voiceState().micMuted") and not b.locator("#voice-opp-muted").is_visible())
            report["voice"] = {"transport": "real Chromium peers with fake microphones and host-only ICE; no external STUN/TURN", "a": voice_a, "b": voice_b}

            path = f"**/online/{first}/move"
            a.route(path, lambda route: route.fulfill(status=503, json={"error": "QA move unavailable. Try again."}))
            kick(a)
            a.wait_for_function("window.__playerControlState?.().canKick&&!window.__onlineQA.snapshot().busy", timeout=30000)
            check("A failed online kick restores controls with understandable feedback", "QA move unavailable" in a.locator("#player-control-status").inner_text() and snapshot(a)["game"]["count"] == 0)
            a.unroute(path)

            kick(a)
            settled(a, 1, first);settled(b, 1, first)
            sent = [request for request in posts["a"] if request["path"].endswith("/move")][-1]["body"]
            check("A real Team A kick sends its selected player and current revision", sent["player_idx"] == 2 and sent["expected_move_count"] == 0, sent)
            check("The real kick hands control to Team B on both clients", not snapshot(a)["control"]["canKick"] and snapshot(b)["control"]["canKick"] and snapshot(b)["control"]["team"] == "b")
            kick(b)
            settled(a, 2, first);settled(b, 2, first)
            sent_b = [request for request in posts["b"] if request["path"].endswith("/move")][-1]["body"]
            check("A real Team B kick uses the chosen Team B index", sent_b["player_idx"] == 2 and sent_b["expected_move_count"] == 1 and room(ca, first)["last_move"]["mover"] == "b", sent_b)
            authoritative = ca.request.get(base + f"/online/{first}/state").json()
            check("Both render states finish at the server ball and revision", snapshot(a)["game"]["ball"] == authoritative["game"]["ball"] and snapshot(b)["game"]["ball"] == authoritative["game"]["ball"] and authoritative["move_count"] == 2)

            before = copy.deepcopy(room(ca, first))
            stale = ca.request.post(base + f"/online/{first}/move", data={"player_idx": 2, "angle": 0, "power": 0, "expected_move_count": 0})
            check("A stale command receives conflict without changing the room", stale.status == 409 and room(ca, first) == before, stale.json())

            a.evaluate("()=>{window.__onlineQA.pausePoll();window.__onlineQA.holdNext('/online/'+window.__onlineQA.snapshot().online.roomId+'/state');window.__onlineQA.poll();}")
            a.wait_for_function("window.__onlineHeld?.ready", timeout=30000)
            kick(a, index=1)
            a.wait_for_function("window.__onlineQA.snapshot().game.count===3&&!window.__onlineQA.snapshot().busy", timeout=90000)
            a.evaluate("window.__onlineQA.release()")
            settled(a, 3, first);settled(b, 3, first)
            check("A late same-room snapshot cannot roll back a newer accepted kick", snapshot(a)["game"]["count"] == 3 and not snapshot(a)["game"]["turnA"])
            a.evaluate("window.__onlineQA.resumePoll()")

            cs, s = create_browser("spectator", initial="/spectate/" + first, spectator=True)
            spectator_settled(s, 3)
            spectator_initial = s.evaluate("window.__spectatorQA.snapshot()")
            check("A real spectator joins current state without playing room history", not spectator_initial["playback"] and spectator_initial["players"] == {"a": online_count, "b": online_count}, spectator_initial["live"])
            environment = spectator_initial["scene"]
            check("A logged-out spectator initializes the day environment without account customization", environment["info"]["scene"] == "day" and environment["sky"] and abs(sum(value * value for value in environment["sun"]) ** .5 - 2250) < 1 and environment["sun"][1] > 0, environment)
            check("Spectator state and chat each start one polling timer", spectator_timers(s) == {"poll": 1, "chat": 1}, spectator_timers(s))

            # Freeze delivery, rather than physics, to exercise real cached moves.
            a.evaluate("window.__onlineQA.pausePoll();window.__onlineQA.clearPlayback()")
            b.evaluate("window.__onlineQA.pausePoll()")
            s.evaluate("window.__spectatorQA.pausePoll();window.__spectatorQA.clearPlayback()")
            for context in (cb, ca, cb):
                response = api_kick(context, first)
                check("A queued real kick is accepted", response.status == 200, response.status)
            a.evaluate("()=>{window.__onlineQA.poll();}")
            settled(a, 6, first)
            queued = [event["revision"] for event in snapshot(a)["playback"] if event["stage"] == "start"]
            check("Cached real moves play in order exactly once", queued == [4, 5, 6], queued)
            a.evaluate("()=>{window.__onlineQA.poll();}")
            settled(a, 6, first)
            check("An identical poll never replays already consumed moves", [event["revision"] for event in snapshot(a)["playback"] if event["stage"] == "start"] == queued)
            a.evaluate("window.__onlineQA.resumePoll()")
            b.evaluate("()=>{window.__onlineQA.resumePoll();window.__onlineQA.poll();}")
            settled(b, 6, first)
            s.evaluate("()=>{window.__spectatorQA.poll();}")
            spectator_settled(s, 6)
            spectator_queue = s.evaluate("window.__spectatorQA.snapshot().playback")
            check("The real spectator plays every new queued move once in order", [move["revision"] for move in spectator_queue] == [4, 5, 6], spectator_queue)
            s.evaluate("()=>{window.__spectatorQA.poll();}")
            spectator_settled(s, 6)
            check("An unchanged spectator poll never duplicates an animation", s.evaluate("window.__spectatorQA.snapshot().playback") == spectator_queue)

            fixture = cs.request.get(base + f"/online/{first}/state?since_kick=6").json()
            fixture["move_count"] = fixture["game"]["online_move_count"] = 7
            fixture["game"]["penalty_shootout"] = True
            frame = {"x": fixture["game"]["ball"]["x"], "y": fixture["game"]["ball"]["y"], "z": 0,
                     "a": fixture["game"]["players_a"], "b": fixture["game"]["players_b"], "t": 0}
            penalty_move = {"kick_count": 7, "mover": "a", "player_idx": 0, "angle": 0, "power": 0,
                            "is_penalty": True, "trajectory": [frame, {**frame, "t": .02}], "scored": None, "desc": "QA penalty"}
            fixture["moves"], fixture["last_move"] = [penalty_move], penalty_move
            state_pattern = f"**/online/{first}/state?*"
            s.route(state_pattern, lambda route: route.fulfill(json=fixture))
            s.evaluate("()=>{window.__spectatorQA.poll();}")
            spectator_settled(s, 7)
            check("Spectator shootout revisions advance while regular kick_count is unchanged", fixture["game"]["kick_count"] == 6 and s.evaluate("window.__liveState().lastKick") == 7 and s.evaluate("window.__spectatorQA.snapshot().playback.at(-1).revision") == 7)
            fixture["status"] = "done";fixture["game"]["game_over"] = True;fixture["game"]["winner"] = "B"
            fixture["name_b"] = "<b>QA winner</b>";fixture["moves"] = []
            s.evaluate("()=>{window.__spectatorQA.poll();}")
            s.wait_for_function("window.__liveState().over", timeout=30000)
            check("Spectator completion uses the authoritative winner with a tied score", fixture["game"]["score_a"] == fixture["game"]["score_b"] and "<b>QA winner</b> wins!" in s.locator("#live-msg").inner_text())
            check("Winner names remain escaped and completion stops both spectator timers", s.locator("#live-msg b").count() == 0 and spectator_timers(s) == {"poll": 0, "chat": 0}, spectator_timers(s))
            s.screenshot(path=str(destination / "online_spectator_finished.png"), full_page=True)

            # Delay a valid old-room response until after leaving and joining another.
            a.evaluate("()=>{window.__onlineQA.keepVoiceTracks();window.__onlineQA.pausePoll();window.__onlineQA.holdNext('/online/'+window.__onlineQA.snapshot().online.roomId+'/state');window.__onlineQA.poll();}")
            a.wait_for_function("window.__onlineHeld?.ready", timeout=30000)
            a.locator("#online-leave-btn").click()
            local(a)
            old_finished = room(ca, first)
            check("Back to local confirms an active forfeit with an uppercase winner", old_finished["status"] == "done" and old_finished["game"]["game_over"] and old_finished["game"]["winner"] == "B", {"status": old_finished["status"], "winner": old_finished["game"]["winner"]})
            check("Leaving cleans polling, chat, URL, voice and local mode controls", timers(a) == {"poll": 0, "chat": 0, "panels": 0} and "room=" not in a.url and a.locator("#mode-select").is_enabled() and not a.evaluate("window.__voiceState().enabled"), timers(a))
            check("Leaving stops every previously captured microphone track and peer", a.evaluate("window.__onlineVoiceTracks.length>0&&window.__onlineVoiceTracks.every(track=>track.readyState==='ended')&&!window.__voiceState().hasPc"))
            b.evaluate("()=>{window.__onlineQA.poll();}")
            settled(b, 6, first)
            check("The opponent sees the authoritative finished winner and cannot kick", snapshot(b)["game"]["winner"] == "B" and snapshot(b)["game"]["over"] and not snapshot(b)["control"]["canKick"] and b.locator("#winner-banner").is_visible())
            check("Finished-room delivery stops opponent gameplay, chat and voice", timers(b) == {"poll": 0, "chat": 0, "panels": 1} and not b.evaluate("window.__voiceState().enabled||window.__voiceState().hasPc"), timers(b))
            cf, f = create_browser("finished", storage=ca.storage_state(), initial="/play3d?room=" + first)
            settled(f, 6, first)
            finished_bootstrap = snapshot(f)
            check("Reloading a finished room bootstraps the authoritative roster, score and winner", finished_bootstrap["game"]["playersA"] == online_count and finished_bootstrap["game"]["winner"] == "B" and finished_bootstrap["game"]["over"] and not finished_bootstrap["playback"], finished_bootstrap["game"])
            check("Finished-room bootstrap leaves no gameplay or chat polling and cannot kick", timers(f) == {"poll": 0, "chat": 0, "panels": 1} and not finished_bootstrap["control"]["canKick"] and f.locator("#winner-banner").is_visible(), timers(f))
            f.screenshot(path=str(destination / "online_finished_reload.png"), full_page=True)
            f.locator("#online-leave-btn").click();local(f)
            b.locator("#online-leave-btn").click();local(b)

            second = begin_waiting(a)
            b.goto(base + "/play3d?room=" + second, wait_until="domcontentloaded", timeout=60000)
            settled(a, 0, second);settled(b, 0, second)
            a.evaluate("window.__onlineQA.release()")
            a.wait_for_timeout(300)
            check("A late previous-room state never overwrites the new room", snapshot(a)["online"]["roomId"] == second and snapshot(a)["game"]["count"] == 0 and not snapshot(a)["game"]["over"] and snapshot(a)["control"]["canKick"], snapshot(a)["game"])
            check("Leave and rejoin keep one poll/chat lifecycle", timers(a) == {"poll": 1, "chat": 1, "panels": 1} and timers(b) == {"poll": 1, "chat": 1, "panels": 1}, {"a": timers(a), "b": timers(b)})

            cm, m = create_browser("mobile", mobile=True, storage=ca.storage_state(), initial="/play3d?room=" + second)
            settled(m, 0, second)
            mobile_layout = m.evaluate("""()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
              controls:document.getElementById('player-controls').getBoundingClientRect().toJSON(),
              leave:document.getElementById('online-leave-btn').getBoundingClientRect().toJSON(),
              font:getComputedStyle(document.getElementById('aim-angle')).fontSize,
              options:[...document.getElementById('player-select').options].map(option=>option.value)})""")
            check("375px online controls fit without horizontal overflow", mobile_layout["scrollWidth"] <= 376 and mobile_layout["controls"]["x"] >= 0 and mobile_layout["controls"]["right"] <= 376, mobile_layout)
            check("Mobile selection exposes legal players and a usable leave action", mobile_layout["options"] == [str(index) for index in range(online_count)] and mobile_layout["leave"]["height"] >= 44 and float(mobile_layout["font"][:-2]) >= 16, mobile_layout)
            m.locator("#player-select").select_option("2")
            check("Mobile selects the intended online player without accidentally firing", snapshot(m)["control"]["selectedIndex"] == 2 and not [request for request in posts["mobile"] if request["path"].endswith("/move")])
            m.screenshot(path=str(destination / "online_mobile.png"), full_page=True)

            a.screenshot(path=str(destination / "online_desktop.png"), full_page=True)
            a.evaluate("window.__onlineQA.clearPlayback();window.__onlineQA.holdNext('/online/'+window.__onlineQA.snapshot().online.roomId+'/move','POST')")
            kick(a)
            a.wait_for_function("window.__onlineHeld?.ready", timeout=30000)
            a.locator("#online-leave-btn").click();local(a)
            a.evaluate("window.__onlineQA.release()")
            a.wait_for_timeout(300)
            late_move = snapshot(a)
            check("A delayed move response after leaving cannot animate or restore the old room", not late_move["online"]["active"] and not late_move["busy"] and late_move["game"]["playersA"] == 3 and not late_move["game"]["over"] and not late_move["playback"], late_move["game"])
            check("Leaving during a request still clears every room/chat timer", timers(a) == {"poll": 0, "chat": 0, "panels": 0}, timers(a))
            report["snapshots"]["desktop"] = snapshot(a)
            report["snapshots"]["mobile"] = snapshot(m)
            report["snapshots"]["spectator"] = s.evaluate("window.__spectatorQA.snapshot()")
            report["requests"] = posts
            check("Online browser sessions have no JavaScript exceptions", not any(errors.values()), errors)
            optional_assets = {label: [url for url in urls if urlparse(url).hostname == "media.giphy.com"]
                               for label, urls in external.items()}
            integration_requests = {label: [url for url in urls if urlparse(url).hostname != "media.giphy.com"]
                                    for label, urls in external.items()}
            report["blocked_optional_assets"] = optional_assets
            check("The isolated online pages fetch no external integrations", not any(integration_requests.values()), integration_requests)
            report["dialogs"] = dialogs
            report["console_messages"] = console_messages
            for context in contexts.values():
                context.close()
            browser.close()
    finally:
        server.shutdown()
        output = args.output or destination / ("layout_report.json" if args.layout_only else "report.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--layout-only", action="store_true", help="Verify the final online page layout and refresh desktop/mobile screenshots")
    report = run(parser.parse_args())
    print(f"{sum(item['passed'] for item in report['checks'])}/{len(report['checks'])} checks passed")
    raise SystemExit(0 if all(item["passed"] for item in report["checks"]) else 1)
