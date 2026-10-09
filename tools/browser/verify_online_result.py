"""Focused browser checks for delayed ranked results after a finished room.

Creates and finishes a real isolated room; only delayed rating responses use
fixtures. Product files are untouched and external integrations are disabled.
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
from tools.browser.verify_online_controls import DIAGNOSTICS, INIT, instrument
from tools.browser.verify_game_performance import isolated_environment

RESULT_DIAGNOSTICS = DIAGNOSTICS + r"""
window.__rankedQA={
  begin:data=>startOnline(data),
  snapshot:()=>({delta:RANKED.delta,attempts:ONLINE.resultAttempts,
    timer:!!ONLINE.resultTimer,controller:!!ONLINE.resultController,
    online:window.__onlineQA.snapshot(),voice:window.__voiceState()}),
  retry:()=>{stopOnlineResultRetries();return retryOnlineResult(ONLINE.roomId,ONLINE.generation);},
};
"""


def run(args):
    isolated_environment()
    from flask import redirect, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    source = (ROOT / "templates/game/index_3d.html").read_text(encoding="utf-8")
    application.app.jinja_loader = ChoiceLoader([
        DictLoader({"game/index_3d.html": instrument(source, RESULT_DIAGNOSTICS)}),
        application.app.jinja_loader,
    ])

    @application.app.route("/__ranked_qa/login/<label>")
    def login(label):
        session["user_id"] = "dev:ranked-result-qa-" + label
        session["username"] = "Ranked QA " + label.upper()
        save_game(session["user_id"], new_soccer_state(mode="hvh", player_count=3))
        return redirect("/play3d")

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    destination = args.screenshots or Path(tempfile.gettempdir()) / "agent-soccer-online-result-qa"
    destination.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "artifacts": str(destination)}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            ca = browser.new_context(viewport={"width": 1280, "height": 1000})
            cb = browser.new_context()
            ca.add_init_script(INIT)
            page = ca.new_page()
            errors, external, states = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())

            def local_only(route):
                if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                    external.append(route.request.url)
                    route.abort()
                else:
                    route.continue_()
            ca.route("**/*", local_only)
            page.goto(base + "/__ranked_qa/login/a", wait_until="domcontentloaded")
            page.wait_for_function("window.__rankedQA?.snapshot().online.game", timeout=90000)
            page.evaluate("window.setViewMode('top')")
            cb.request.get(base + "/__ranked_qa/login/b")
            created = ca.request.post(base + "/online/create", data={}).json()
            room_id = created["room_id"]
            joined = cb.request.post(base + f"/online/{room_id}/join", data={})
            ended = ca.request.post(base + f"/online/{room_id}/leave", data={})
            check("The retry fixture starts from a real joined and forfeited room", joined.status == 200 and ended.status == 200)
            fixture = ca.request.get(base + f"/online/{room_id}/state?since_kick=-1").json()
            fixture.update({"ranked": True, "ranked_result": None})
            fixture["moves"] = []
            rating = {
                "winner": "B", "player_a": {"delta": -20, "rating_after": 1180},
                "player_b": {"delta": 20, "rating_after": 1220},
            }

            def respond(route):
                states.append({"path": route.request.url, "result": bool(fixture.get("ranked_result"))})
                route.fulfill(json=fixture)
            page.route(f"**/online/{room_id}/state?*", respond)
            page.evaluate("data=>window.__rankedQA.begin(data)", fixture)
            first = page.evaluate("window.__rankedQA.snapshot()")
            check("A pending ranked result displays feedback and schedules one retry", first["timer"] and first["attempts"] == 0 and "Updating rating" in page.locator("#winner-banner").inner_text(), first)
            check("Completion stops gameplay, chat and voice while rating is pending", not first["online"]["control"]["canKick"] and first["online"]["online"]["pollTimer"] is None and first["online"]["online"]["chatTimer"] is None and not first["voice"]["enabled"])
            fixture["ranked_result"] = rating
            page.wait_for_function("window.__rankedQA.snapshot().delta", timeout=12000)
            success = page.evaluate("window.__rankedQA.snapshot()")
            check("The scheduled retry applies the delayed server rating to the winner banner", success["attempts"] == 1 and "-20 rating (1180)" in page.locator("#winner-banner").inner_text(), success)
            check("Receiving a rating ends all result retry work", not success["timer"] and not success["controller"] and len(states) == 1, states)
            page.screenshot(path=str(destination / "online_rating_complete.png"), full_page=True)

            # Advance the same production retry function without six wall-clock waits.
            fixture["ranked_result"] = None
            page.evaluate("data=>window.__rankedQA.begin(data)", fixture)
            before = len(states)
            for _ in range(6):
                page.evaluate("window.__rankedQA.retry()")
            exhausted = page.evaluate("window.__rankedQA.snapshot()")
            check("Pending storage retries stop after six attempts with recovery advice", exhausted["attempts"] == 6 and not exhausted["timer"] and not exhausted["controller"] and len(states) - before == 6 and "Reopen this match" in page.locator("#winner-banner").inner_text(), exhausted)

            fixture["ranked_result"] = None
            page.evaluate("data=>window.__rankedQA.begin(data)", fixture)
            page.locator("#online-leave-btn").click()
            page.wait_for_function("!window.__rankedQA.snapshot().online.online.active", timeout=30000)
            exited = page.evaluate("window.__rankedQA.snapshot()")
            check("Leaving cancels the pending result timer and request controller", not exited["timer"] and not exited["controller"] and not exited["online"]["online"]["active"])

            page.evaluate("data=>window.__rankedQA.begin(data)", fixture)
            fixture["ranked_result"] = copy.deepcopy(rating)
            page.evaluate("()=>{window.__onlineQA.holdNext('/online/'+window.__onlineQA.snapshot().online.roomId+'/state');window.__rankedQA.retry();}")
            page.wait_for_function("window.__onlineHeld?.ready", timeout=10000)
            page.locator("#online-leave-btn").click()
            page.wait_for_function("!window.__onlineQA.snapshot().online.active&&!window.__onlineQA.snapshot().busy", timeout=30000)
            page.evaluate("window.__onlineQA.release()")
            page.wait_for_timeout(100)
            late = page.evaluate("window.__rankedQA.snapshot()")
            check("A result delivered after leaving cannot alter the local game", not late["online"]["online"]["active"] and late["delta"] is None and not late["timer"] and not late["controller"] and late["online"]["game"]["playersA"] == 3, late)
            check("The ranked result browser has no JavaScript exceptions", not errors, errors)
            report["blocked_optional_assets"] = [url for url in external if urlparse(url).hostname == "media.giphy.com"]
            integration_requests = [url for url in external if urlparse(url).hostname != "media.giphy.com"]
            check("The focused pass calls no external integrations", not integration_requests, integration_requests)
            report["requests"] = states
            ca.close();cb.close();browser.close()
    finally:
        server.shutdown()
        output = args.output or destination / "report.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--output", type=Path)
    result = run(parser.parse_args())
    print(f"{sum(item['passed'] for item in result['checks'])}/{len(result['checks'])} checks passed")
    raise SystemExit(0 if all(item["passed"] for item in result["checks"]) else 1)
