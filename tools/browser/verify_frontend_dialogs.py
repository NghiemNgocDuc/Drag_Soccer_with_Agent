"""Focused browser checks for game dialogs and feedback request lifetimes.

Uses rendered production markup and the actual dialog functions without starting
the pitch renderer, a game server, or external integrations. Run this file directly.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from flask import Flask, render_template, session
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def game_function(source: str, name: str, *, plain: bool = False) -> str:
    prefix = rf"(?:async\s+)?function {name}" if plain else rf"window\.{name}\s*=\s*(?:async\s+)?function"
    match = re.search(rf"^{prefix}\([^)]*\)\s*\{{", source, re.MULTILINE)
    if not match:
        raise ValueError(f"Missing game function: {name}")
    line_end = source.index("\n", match.start())
    if "}" in source[match.end():line_end]:
        return source[match.start():line_end]
    ending = re.search(r"^\};?\s*$", source[match.end():], re.MULTILINE)
    if not ending:
        raise ValueError(f"Missing game function ending: {name}")
    return source[match.start():match.end() + ending.end()]


def run() -> list[dict]:
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.secret_key = "local-dialog-fixture"
    with app.test_request_context():
        session.update(user_id="dev:dialogs", username="Dialog QA")
        markup = render_template("game/index_2d.html", username="Dialog QA")
    # Keep the production HTML and styles, while excluding renderer initialization.
    markup = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", markup, flags=re.IGNORECASE)
    markup = re.sub(r"<link\b[^>]*>", "", markup, flags=re.IGNORECASE)
    styles = "\n".join((ROOT / "static" / name).read_text(encoding="utf-8")
                       for name in ("css/frontend.css", "css/frontend-game.css"))
    styles = re.sub(r"@import[^;]+;", "", styles)
    source = (ROOT / "templates/game/index_2d.html").read_text(encoding="utf-8")
    functions = [game_function(source, name) for name in
                 ("showOnlineLobby", "closeOnlineLobby", "createOnlineMatch", "openMatchModels", "closeMatchModels",
                  "showHighlights", "closeHighlights", "showPlayerRatings", "closeRatings")]
    functions += [game_function(source, name, plain=True) for name in (
        "detectHighlights", "escM", "onlineJSON", "lobbyStatus", "lobbyBusy",
        "resetLobbyView", "offerResumeRoom", "cancelWaitingRoom", "cancelLobbyWait", "lobbyDelay",
    )]
    results = []

    def check(name, passed, detail=None):
        results.append({"name": name, "passed": bool(passed), "detail": detail})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(markup)
        page.add_style_tag(content=styles)
        page.add_script_tag(path=str(ROOT / "static/js/ui/frontend.js"))
        page.evaluate("() => {window.gameState={}; window.fetch=async()=>({json:async()=>({models:[{id:'greedy',name:'Greedy',type:'builtin',desc:'Test model'}]})});}")
        # Supply the current lobby state and its real helpers, while keeping
        # renderer initialization and ranked matchmaking outside this fixture.
        page.add_script_tag(content="""
          const ONLINE = {active:false}, RANKED = {active:false}, QUICK = {active:false};
          let isAnimating = false;
        """ + re.search(r"^const ONLINE_LOBBY = .*;$", source, re.MULTILINE)[0])
        page.add_script_tag(content="\n".join(functions))

        page.locator("#online-btn").click()
        page.locator("#match-models-btn").click()
        page.wait_for_function("document.querySelector('#match-models-list button')")
        check("Model picker receives focus", page.evaluate("document.activeElement.closest('.online-modal').id") == "match-models-modal")
        check("Dialog IDs are unique", page.evaluate("new Set([...document.querySelectorAll('[id]')].map(el=>el.id)).size===document.querySelectorAll('[id]').length"))
        child = page.locator("#match-models-modal")
        child.get_by_role("link", name="Go to Code Models").focus()
        page.keyboard.press("Tab")
        check("Child Tab wraps within model picker", page.evaluate("document.activeElement===document.querySelector('#match-models-modal a[href]')"))
        page.keyboard.press("Shift+Tab")
        check("Child reverse Tab wraps within model picker", page.evaluate("document.activeElement.textContent.trim()") == "Go to Code Models")
        page.keyboard.press("Escape")
        check("Child Escape preserves parent lobby", child.is_hidden() and page.locator("#online-lobby").is_visible())
        check("Child close restores parent opener", page.evaluate("document.activeElement.id") == "match-models-btn")
        page.keyboard.press("Escape")
        check("Parent Escape restores game opener", page.locator("#online-lobby").is_hidden() and page.evaluate("document.activeElement.id") == "online-btn")

        page.evaluate("() => {window.fetch=async url=>url==='/online/create'?{ok:true,json:async()=>({room_id:'qa123456'})}:url.endsWith('/join')?{ok:true}:new Promise(()=>{});}")
        page.locator("#online-btn").click()
        page.get_by_role("button", name="Create Match", exact=True).click()
        page.wait_for_function("document.getElementById('online-lobby-create').style.display==='flex'")
        check("Invitation replaces hidden create control with useful focus", page.evaluate("document.activeElement.id") == "online-invite-link")
        page.keyboard.press("Escape")
        check("Invitation can close and return to match controls", page.locator("#online-lobby").is_hidden() and page.evaluate("document.activeElement.id") == "online-btn")

        for name, opener, close, modal in (
            ("Highlights", "showHighlights", "closeHighlights", "highlights-modal"),
            ("Ratings", "showPlayerRatings", "closeRatings", "ratings-modal"),
        ):
            page.locator("#online-btn").focus()
            page.evaluate(f"window.{opener}()")
            check(f"{name} opens while lobby is closed", page.locator(f"#{modal}").is_visible())
            check(f"{name} receives focus", page.evaluate("document.activeElement.closest('.online-modal').id") == modal)
            page.keyboard.press("Escape")
            check(f"{name} closes and restores focus", page.locator(f"#{modal}").is_hidden() and page.evaluate("document.activeElement.id") == "online-btn")

        page.add_script_tag(path=str(ROOT / "static/js/ui/feedback.js"))
        page.locator("#online-btn").click()
        page.evaluate("document.getElementById('fb-fab').click()")
        page.locator("#fb-close").focus()
        page.keyboard.press("Shift+Tab")
        check("Feedback above game owns the focus trap", page.evaluate("document.activeElement.id") == "fb-submit")
        page.keyboard.press("Escape")
        check("Feedback above game closes independently", page.locator("#fb-overlay").is_hidden() and page.locator("#online-lobby").is_visible())
        page.evaluate("window.closeOnlineLobby()")
        page.evaluate("() => {window.fetch=()=>new Promise((resolve,reject)=>{window.resolveFeedback=resolve;window.rejectFeedback=reject;});}")
        page.locator("#fb-fab").click()
        page.locator("#fb-title").fill("First report")
        page.locator("#fb-submit").click()
        page.keyboard.press("Tab")
        check("Sending feedback retains keyboard focus", page.evaluate("!!document.activeElement.closest('#fb-overlay')"))
        page.locator("#fb-close").click()
        page.locator("#fb-fab").click()
        page.locator("#fb-title").fill("Second unsent draft")
        page.evaluate("window.resolveFeedback({ok:true,json:async()=>({ok:true})})")
        page.wait_for_timeout(1500)
        check("Stale feedback success preserves reopened draft", page.locator("#fb-overlay").is_visible() and page.locator("#fb-title").input_value() == "Second unsent draft" and page.locator("#fb-msg").is_hidden())

        page.locator("#fb-submit").click()
        page.locator("#fb-close").click()
        page.locator("#fb-fab").click()
        page.locator("#fb-title").fill("Third draft")
        page.evaluate("window.rejectFeedback(new Error('Previous request failed'))")
        page.wait_for_timeout(30)
        check("Stale feedback error preserves reopened draft", page.locator("#fb-msg").is_hidden() and page.locator("#fb-title").input_value() == "Third draft")

        page.locator("#fb-submit").click()
        page.evaluate("window.rejectFeedback(new Error('Connection failed'))")
        page.wait_for_function("document.getElementById('fb-msg').textContent.includes('Connection failed')")
        check("Current feedback failure allows retry", page.locator("#fb-submit").is_enabled())
        page.keyboard.press("Escape")
        check("Feedback close restores focus", page.locator("#fb-overlay").is_hidden() and page.evaluate("document.activeElement.id") == "fb-fab")
        check("Focused checks produce no browser exceptions", not errors, errors)
        browser.close()
    return results


if __name__ == "__main__":
    checks = run()
    failed = [item for item in checks if not item["passed"]]
    print(json.dumps({"passed": len(checks) - len(failed), "failed": failed}, indent=2))
    raise SystemExit(bool(failed))
