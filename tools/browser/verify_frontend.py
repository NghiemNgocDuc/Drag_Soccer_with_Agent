"""Isolated browser checks for the shared frontend, auth forms, and game entry.

Run ``python tools/browser/verify_frontend.py``. External integrations are disabled before
importing Flask. Reports and screenshots go to the system temporary directory.
The synthetic replay and sign-in endpoints exist only in this test process.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


def run() -> dict:
    isolated_environment()
    from flask import redirect, render_template, session
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state, apply_kick

    target = Path(tempfile.gettempdir()) / "agent-soccer-frontend-previews"
    target.mkdir(parents=True, exist_ok=True)
    report: dict = {"checks": [], "pages": [], "artifacts": str(target)}

    def check(label, condition, detail=None):
        report["checks"].append({"name": label, "passed": bool(condition), "detail": detail})

    for filename in sorted((ROOT / "templates").rglob("*.html")):
        application.app.jinja_env.get_template(filename.relative_to(ROOT / "templates").as_posix())
    check("All Jinja templates compile", True)

    seed = new_soccer_state(mode="hvh", player_count=3, half_length=9999)
    replay_state = copy.deepcopy(seed)
    trajectory, scored, *_ = apply_kick(replay_state, 2, 0, 75, True)
    replay = [{"mover": "a", "player_idx": 2, "angle": 0, "power": 75,
               "trajectory": trajectory, "scored": scored}]

    @application.app.route("/__frontend/login")
    def frontend_login():
        session["user_id"] = "dev:frontend-qa"
        session["username"] = "Frontend QA"
        save_game(session["user_id"], copy.deepcopy(seed))
        return redirect("/lobby")

    @application.app.route("/__frontend/replay")
    def frontend_replay():
        return render_template("game/replay_3d.html", username="Frontend QA",
                               t={"id": "qa", "name": "QA cup"},
                               match={"id": "qa", "participant_a": "A", "participant_b": "B", "replay_data": replay},
                               highlights=[], highlight=None, live_room=None,
                               loss_model=None, loss_model_name=None)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            for width in (1280, 768, 375):
                context = browser.new_context(viewport={"width": width, "height": 900})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for name, path in (("landing", "/"), ("login", "/login"), ("register", "/register"),
                                   ("forgot", "/forgot-password"), ("reset", "/reset-password")):
                    errors.clear()
                    response = page.goto(base + path, wait_until="networkidle")
                    metrics = page.evaluate("""() => ({
                      overflow: document.documentElement.scrollWidth > innerWidth + 1,
                      h1: !!document.querySelector('h1'),
                      main: !!document.getElementById('as-content'),
                      header: getComputedStyle(document.querySelector('.as-header')).backgroundColor,
                      bodyAnimation: getComputedStyle(document.body).animationName,
                      navBlur: getComputedStyle(document.querySelector('.as-nav')).backdropFilter
                    })""")
                    check(f"{name} {width}: responsive shell", response.status == 200 and not metrics["overflow"] and metrics["h1"] and metrics["main"], metrics)
                    check(f"{name} {width}: no browser exception", not errors, list(errors))
                    check(f"{name} {width}: lightweight background", metrics["bodyAnimation"] == "none" and metrics["navBlur"] == "none", metrics)
                    if name == "landing":
                        check(f"Guest landing {width}: registration navigation", page.locator('.as-account-menu').count() == 0 and page.locator('.as-hero-actions a[href="/register"]').count() == 1)
                    page.screenshot(path=str(target / f"{name}-{width}.png"), full_page=True)
                    report["pages"].append({"page": name, "width": width, **metrics})
                if width == 375:
                    page.goto(base + "/", wait_until="networkidle")
                    page.locator(".as-menu-toggle").click()
                    check("Mobile navigation opens", page.locator(".as-menu-toggle").get_attribute("aria-expanded") == "true")
                    page.get_by_label("Workshop pages", exact=True).click()
                    check("Mobile section menu opens", page.locator('.as-nav-menu[open] a[href="/my-models"]').is_visible())
                    page.keyboard.press("Escape")
                    check("Escape closes section menu", page.locator(".as-nav-menu[open]").count() == 0)
                    page.keyboard.press("Escape")
                    check("Escape closes mobile navigation", page.locator(".as-menu-toggle").get_attribute("aria-expanded") == "false")
                context.close()

            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_init_script("localStorage.setItem('agent-soccer-render-quality','1080p')")
            page = context.new_page()
            page.goto(base + "/login", wait_until="networkidle")
            page.locator("#password").fill("frontend-password")
            page.get_by_role("button", name="Show password", exact=True).click()
            check("Password visibility toggle works", page.locator("#password").get_attribute("type") == "text")
            page.get_by_role("button", name="Hide password", exact=True).click()
            check("Password can be hidden again", page.locator("#password").get_attribute("type") == "password")
            page.keyboard.press("Tab")
            page.locator(".as-skip").focus()
            page.keyboard.press("Enter")
            check("Skip link focuses main content", page.evaluate("document.activeElement.id") == "as-content")

            page.goto(base + "/register")
            page.locator("#username").fill("Frontend tester")
            page.locator("#email").fill("frontend@example.test")
            page.locator("#password").fill("test-password")
            page.locator("#confirm").fill("test-password")
            page.get_by_role("button", name="Create account", exact=False).click()
            page.wait_for_url("**/lobby")
            check("Registration form reaches the locker room", page.url.endswith("/lobby"))
            page.goto(base + "/auth/logout")
            page.goto(base + "/login")
            page.locator("#email").fill("frontend@example.test")
            page.locator("#password").fill("test-password")
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/lobby")
            check("Login form reaches the locker room", page.url.endswith("/lobby"))

            page.goto(base + "/auth/logout")
            recovery_requests = []
            def recovery(route):
                recovery_requests.append(route.request.post_data_json)
                route.fulfill(json={"ok": True})
            page.route("**/api/auth/forgot-password", recovery)
            page.goto(base + "/forgot-password")
            page.locator("#email").fill("invalid")
            page.locator("#submit-btn").click()
            check("Recovery validates email before sending", "valid email" in page.locator("#msg").inner_text() and not recovery_requests)
            page.locator("#email").fill("recover@example.test")
            page.locator("#submit-btn").click()
            page.locator("#sent-wrap").wait_for(state="visible")
            check("Recovery preserves request and confirmation", recovery_requests == [{"email": "recover@example.test"}])

            resets = []
            def reset(route):
                resets.append(route.request.post_data_json)
                route.fulfill(json={"ok": True})
            page.route("**/api/auth/reset-password", reset)
            page.goto(base + "/reset-password")
            check("Invalid reset link shows recovery action", page.locator("#expired-wrap").is_visible())
            page.goto("about:blank")
            page.goto(base + "/reset-password#access_token=qa-access&refresh_token=qa-refresh")
            page.locator("#password").fill("new-password")
            page.locator("#confirm").fill("different")
            check("Reset requires matching passwords", page.locator("#submit-btn").is_disabled())
            page.locator("#confirm").fill("new-password")
            page.locator("#submit-btn").click()
            page.locator("#done-wrap").wait_for(state="visible")
            check("Reset preserves tokens and clears URL fragment", resets == [{"access_token": "qa-access", "refresh_token": "qa-refresh", "password": "new-password"}] and not page.evaluate("location.hash"))

            page.goto(base + "/__frontend/login", wait_until="networkidle")
            for width in (1280, 768, 375):
                page.set_viewport_size({"width": width, "height": 900})
                for name, path in (("lobby", "/lobby"), ("play-hub", "/hub/play"),
                                   ("game", "/play3d"), ("replay", "/__frontend/replay")):
                    response = page.goto(base + path, wait_until="networkidle")
                    if name in ("game", "replay"):
                        page.wait_for_selector("#three-container canvas")
                    overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                    check(f"{name} {width}: responsive page", response.status == 200 and not overflow)
                    page.screenshot(path=str(target / f"{name}-{width}.png"), full_page=name not in ("game", "replay"), timeout=60000)
                    if name == "play-hub":
                        check(f"Play hub {width}: direct links avoid nested renderer", page.locator("iframe").count() == 0 and page.locator('.as-destination[href="/play3d"]').count() == 1)
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(base + "/play3d", wait_until="networkidle")
            page.wait_for_selector("#three-container canvas")
            page.locator("#pass-short").click()
            check("Pass controls expose selected state", page.locator("#pass-short").get_attribute("aria-pressed") == "true" and page.locator("#pass-normal").get_attribute("aria-pressed") == "false")
            page.locator("#sound-btn").click()
            check("Sound control shows mute state", page.locator("#sound-btn").get_attribute("aria-pressed") == "true" and page.locator("#sound-btn").inner_text() == "Sound off")
            page.locator("#view-settings-btn").click()
            page.locator("#graphics-quality").select_option("4k")
            check("4K graphics option remains available", page.locator("#graphics-quality").input_value() == "4k")
            page.locator("#view-settings-modal").get_by_role("button", name="Close", exact=True).click()
            check("View dialog restores focus", page.evaluate("document.activeElement.id") == "view-settings-btn")
            page.locator("#online-btn").click()
            check("Online lobby focuses the join field", page.evaluate("document.activeElement.id") == "online-join-code")
            page.keyboard.press("Escape")
            check("Online lobby can be dismissed before creating a room", page.locator("#online-lobby").is_hidden() and page.evaluate("document.activeElement.id") == "online-btn")
            page.evaluate("""() => {
              navigator.clipboard.writeText=async text=>{window.__copiedInvite=text;};
              window.showOnlineLobby(true);
              document.getElementById('online-lobby-create').style.display='flex';
              document.getElementById('online-invite-link').value='https://example.test/join/qa';
            }""")
            page.locator("#online-copy-btn").click()
            check("Invitation copying preserves the close controls", page.evaluate("window.__copiedInvite") == "https://example.test/join/qa" and page.locator('#online-lobby-create button').count() == 2)
            page.get_by_role("button", name="Close online lobby", exact=True).click()
            page.locator("#mode-select").select_option("aivai")
            page.wait_for_function("document.getElementById('aivai-btn').style.display !== 'none'")
            check("AI versus AI controls remain available", page.locator("#aivai-btn").is_visible())
            page.goto(base + "/profile", wait_until="networkidle")
            page.locator("#fb-fab").click()
            check("Feedback dialog opens and focuses title", page.locator("#fb-overlay").is_visible() and page.evaluate("document.activeElement.id") == "fb-title")
            page.keyboard.press("Escape")
            check("Feedback Escape restores focus", page.locator("#fb-overlay").is_hidden() and page.evaluate("document.activeElement.id") == "fb-fab")
            feedback_requests = []
            def feedback(route):
                feedback_requests.append(route.request.post_data_json)
                route.fulfill(json={"ok": True})
            page.route("**/api/feedback", feedback)
            page.locator("#fb-fab").click()
            page.locator("#fb-title").fill("QA feedback")
            page.locator("#fb-desc").fill("Keyboard form check")
            page.locator("#fb-submit").click()
            page.wait_for_function("document.getElementById('fb-msg').textContent.includes('Thanks')")
            check("Feedback uses existing API payload", feedback_requests == [{"title": "QA feedback", "description": "Keyboard form check"}])
            context.close()
            browser.close()
    finally:
        server.shutdown()
        (target / "frontend-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run()
    failed = [item for item in result["checks"] if not item["passed"]]
    print(json.dumps({"passed": len(result["checks"]) - len(failed), "failed": failed, "artifacts": result["artifacts"]}, indent=2))
    raise SystemExit(bool(failed))
