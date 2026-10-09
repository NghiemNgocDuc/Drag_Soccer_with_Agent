"""Isolated browser checks for the Workshop frontend.

Uses real in-memory routes for editing, search, likes, comments, test-match
setup and customization. Expensive Arena simulations are replaced by a small
browser response fixture so their result controls can be checked quickly.
Screenshots and JSON diagnostics are written outside the repository.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


def run():
    isolated_environment()
    from flask import redirect, session
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright
    import app as application
    from db.user_models import create_model, update_model, get_user_models
    from db.profiles import _MEM_USERS
    from user_models.runner import TEMPLATE

    user = "dev:frontend-workshop-verification"
    _MEM_USERS[user] = "Workshop QA"
    model = create_model(user, "Touchline strategy", "A local verification strategy", TEMPLATE)
    update_model(model["id"], user, is_public=True)

    @application.app.route("/__workshop/login")
    def qa_login():
        session["user_id"] = user
        session["username"] = "Workshop QA"
        return redirect("/hub/workshop")

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    folder = Path(tempfile.mkdtemp(prefix="agent-soccer-workshop-"))
    report = {"screenshots": str(folder), "pages": [], "checks": []}

    def check(name, condition, details=None):
        report["checks"].append({"name": name, "passed": bool(condition), "details": details})
        if not condition:
            print(f"FAIL: {name}: {details}", flush=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept("Saved from workshop verification") if dialog.type == "prompt" else dialog.accept())
            page.goto(base + "/__workshop/login")
            paths = ["/hub/workshop", "/my-models", "/playground", "/arena", "/community", "/community/" + model["id"], "/customize"]
            for width in (1280, 375):
                page.set_viewport_size({"width": width, "height": 900})
                for path in paths:
                    count = len(errors)
                    response = page.goto(base + path)
                    page.wait_for_timeout(450)
                    dimensions = page.evaluate("""() => ({width:innerWidth, scroll:document.documentElement.scrollWidth, overflow:[...document.querySelectorAll('main *')].filter(el=>{const r=el.getBoundingClientRect();return r.width && (r.right>innerWidth+1 || r.left<-1) && getComputedStyle(el).position!=='absolute';}).slice(0,12).map(el=>({tag:el.tagName,cls:el.className,id:el.id,width:el.getBoundingClientRect().width}))})""")
                    key = path.strip("/").replace("/", "-")
                    page.screenshot(path=str(folder / f"{key}-{width}.png"), full_page=True)
                    report["pages"].append({"path": path, "width": width, "status": response.status, "dimensions": dimensions, "errors": errors[count:]})
                    check(f"{path} {width}px loads", response.status == 200)
                    check(f"{path} {width}px has no JS exceptions", len(errors) == count, errors[count:])
                    check(f"{path} {width}px fits viewport", dimensions["scroll"] <= width + 1, dimensions)
                    check(f"{path} {width}px has shared navigation", page.locator(".as-header").count() == 1)

            page.set_viewport_size({"width": 375, "height": 900})
            page.goto(base + "/my-models")
            page.get_by_role("button", name="New", exact=True).click()
            check("Model editor opens on mobile", page.locator("#editor-panel").is_visible())
            check("CodeMirror initializes", page.locator(".CodeMirror").count() == 1)
            page.get_by_role("button", name="Add a resource link").click()
            page.get_by_label("Resource title").fill("Reference")
            page.get_by_label("Resource URL").fill("https://example.com/reference")
            page.locator("#f-name").fill("Saved from workshop verification")
            page.locator("#f-desc").fill("Browser save flow")
            page.get_by_role("button", name="Validate code", exact=True).click()
            page.wait_for_function("document.querySelector('#validate-result').textContent.includes('test move')", timeout=20000)
            check("Model validates through actual API", "test move" in page.locator("#validate-result").inner_text())
            check("Mobile editor fits viewport", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
            page.screenshot(path=str(folder / "model-editor-375.png"), full_page=True)
            with page.expect_navigation():
                page.get_by_role("button", name="Save Model", exact=True).click()
            saved = [m for m in get_user_models(user) if m["name"] == "Saved from workshop verification"]
            check("Model save preserves resource links", bool(saved) and saved[0]["links"] == [{"title":"Reference", "url":"https://example.com/reference"}], saved)
            page.locator("#card-" + model["id"]).press("Enter")
            page.wait_for_function("document.querySelector('#f-name').value === 'Touchline strategy'")
            check("Keyboard model card opens saved strategy", page.locator("#editor-panel").is_visible())

            page.goto(base + "/community")
            page.wait_for_selector("#list article")
            check("Public model appears in community", "Touchline strategy" in page.locator("#list").inner_text())
            page.locator("#q").fill("does-not-exist")
            page.locator("#q").press("Enter")
            page.wait_for_function("document.querySelector('#list').textContent.includes('No strategies match')")
            check("Community search shows useful empty state", page.locator("#list .workshop-empty").count() == 1)
            page.get_by_role("button", name="Clear", exact=True).click()
            page.wait_for_selector("#list article")
            check("Community Clear restores results", "Touchline strategy" in page.locator("#list").inner_text())
            page.goto(base + "/community/" + model["id"])
            page.locator("#like-btn").click()
            page.wait_for_function("document.querySelector('#like-count').textContent === '1'")
            check("Community Like updates state", page.locator("#like-btn").get_attribute("aria-pressed") == "true")
            page.locator("#comment-input").fill("A useful local strategy review.")
            with page.expect_navigation():
                page.get_by_role("button", name="Post comment").click()
            check("Community comment persists", "A useful local strategy review." in page.locator("#comments").inner_text())

            page.goto(base + "/playground")
            check("Playground editor initializes", page.locator(".CodeMirror").count() == 1)
            check("Playground strategy text is rendered", bool(page.locator(".CodeMirror-code").inner_text().strip()))
            check("Playground editor status fits inside panel", page.evaluate("document.querySelector('#ep-status').getBoundingClientRect().bottom <= document.querySelector('.editor-pane').getBoundingClientRect().bottom + 1"))
            page.get_by_role("button", name="Start", exact=True).click()
            page.wait_for_function("document.querySelector('#ep-status').textContent.includes('Game started.')", timeout=15000)
            check("Playground starts real test match", "Game started." in page.locator("#ep-status").inner_text())
            page.get_by_role("button", name="My Code vs AI", exact=True).click()
            check("Playground mode exposes step controls", page.locator("#autoplay-bar").is_visible())
            page.locator('button[onclick="resetGame()"]').click()
            page.wait_for_function("document.querySelector('#ep-status').textContent.includes('Match reset.')")
            check("Playground reset updates status", "Match reset." in page.locator("#ep-status").inner_text())

            page.goto(base + "/arena")
            fixture = {"n_games":2, "wins_a":1, "wins_b":0, "draws":1, "total_goals":3, "avg_kicks":10, "win_rate_a":50, "win_rate_b":0, "avg_score_a":1, "avg_score_b":.5, "avg_stats_a":{}, "avg_stats_b":{}, "games":[{"winner":"A", "score_a":1,"score_b":0,"kick_count":10,"stats_a":{},"stats_b":{},"move_history":[{"trajectory":[{"x":700,"y":437},{"x":1300,"y":750}]}]}]}
            page.route("**/api/arena/battle", lambda route: route.fulfill(json=fixture))
            page.get_by_role("button", name="Run battle", exact=True).click()
            page.wait_for_selector("#results-section .summary-grid")
            check("Arena renders result fixture", page.locator("#results-section .stat-card").count() == 5)
            page.get_by_role("tab", name="Ball heatmap").click()
            page.wait_for_selector("#heatmap-content canvas")
            check("Arena result tab remains functional", page.locator("#tab-heatmap").is_visible())
            check("Arena heatmap renders current whole pitch", page.locator("#heatmap-content canvas").get_attribute("width") == "1400")
            page.get_by_role("tab", name="Battle results").click()
            page.get_by_role("button", name="Show/hide individual game details").click()
            check("Arena individual game details expand", page.locator("#game-details .game-detail").count() == 1)
            page.route("**/api/arena/matrix", lambda route: route.fulfill(json={"models":[],"matrix":[{"model_a":"Greedy","model_b":"Potential field","wins_a":1,"wins_b":0,"draws":1,"win_rate_a":50,"avg_score_a":1,"avg_score_b":.5}]}))
            page.get_by_role("button", name="Run full matrix", exact=True).click()
            page.wait_for_selector("#matrix-content table")
            check("Arena matrix fixture renders in results tab", page.locator("#tab-matrix").is_visible() and "Potential field" in page.locator("#matrix-content").inner_text())
            check("Arena results fit mobile viewport", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))

            page.goto(base + "/customize")
            check("Custom stat sliders render", page.locator(".stat-player-card input[type=range]").count() == 24)
            page.locator("#pname-a0").fill("Local keeper")
            page.locator("#team_a_color").fill("#123456")
            page.locator("#team_a_color").dispatch_event("input")
            with page.expect_response("**/customize/save") as response:
                page.locator("#save-btn").click()
            payload = response.value.request.post_data_json
            check("Customize save preserves player names", payload["player_names"]["a"][0] == "Local keeper")
            check("Customize save preserves colors and stats", payload["team_a_color"] == "#123456" and len(payload["player_stats"]["a"]) == 3 and response.value.json().get("ok"), payload.get("team_a_color"))
            page.get_by_role("button", name="Reset to Defaults").click()
            check("Customize reset updates color value", page.locator("#team_a_color").input_value() == "#3b82f6")
            check("Interaction flows have no JS exceptions", not errors, errors)
            browser.close()
    finally:
        server.shutdown()
    report["passed"] = sum(c["passed"] for c in report["checks"])
    report["failed"] = sum(not c["passed"] for c in report["checks"])
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed":report["passed"], "failed":report["failed"], "screenshots":str(folder), "failures":[c for c in report["checks"] if not c["passed"]]}, indent=2), flush=True)
    return report


if __name__ == "__main__":
    result = run()
    raise SystemExit(bool(result["failed"]))
