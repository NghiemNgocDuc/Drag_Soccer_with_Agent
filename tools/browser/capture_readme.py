"""Capture real README previews using isolated local fixtures.

Run ``python tools/browser/capture_readme.py``. The app listens on loopback,
external integrations are disabled, and screenshots use the product's normal
pages and controls. Output is written to ``docs/assets/readme``.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.browser.verify_game_performance import isolated_environment


EXAMPLE_CODE = '''# A baseline to extend: reach the ball, then attack the goal.
MODEL_NAME = "Direct play"
DESCRIPTION = "A compact baseline for testing new ideas"

def get_ai_move(state, is_player_a):
    ball = state["ball"]
    players = state["players_a"] if is_player_a else state["players_b"]
    candidates = range(min(3, len(players)))
    player_idx = min(candidates, key=lambda i: math.hypot(
        players[i]["x"] - ball["x"], players[i]["y"] - ball["y"]
    ))
    player = players[player_idx]
    target_x, target_y = ball["x"], ball["y"]
    if math.hypot(player["x"] - ball["x"], player["y"] - ball["y"]) < 55:
        target_x = state["field"]["width"] if is_player_a else 0
        target_y = state["field"]["height"] / 2
    angle = math.degrees(math.atan2(target_y - player["y"],
                                    target_x - player["x"]))
    return player_idx, angle, 78
'''


def capture() -> list[dict]:
    isolated_environment()
    from flask import redirect, session
    from PIL import Image
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    import app as application
    from db.customization import save_customization
    from game.session import new_game_state, save_game
    from models.soccer_logic import inject_player_stats

    destination = ROOT / "docs/assets/readme"
    destination.mkdir(parents=True, exist_ok=True)
    user_id = "dev:readme-orion"
    opponent_id = "dev:readme-harbor"
    settings = {
        "bg_scene": "day", "weather": "clear", "camera_type": "classic",
        "team_a_color": "#0f8b76", "team_b_color": "#f2a633",
        "crowd_palette": "classic", "player_count": 5,
        "player_names": {
            "a": ["Keeper", "Vale", "Reed", "Park", "Blake"],
            "b": ["Keeper", "West", "Avery", "Quinn", "Ellis"],
        },
        "player_colors": {"a": ["#0f8b76"] * 5, "b": ["#f2a633"] * 5},
        "player_stats": {
            "a": [
                {"size": 65, "power": 40, "weight": 60, "agility": 35},
                {"size": 45, "power": 60, "weight": 35, "agility": 60},
                {"size": 40, "power": 70, "weight": 35, "agility": 55},
            ],
            "b": [
                {"size": 60, "power": 45, "weight": 60, "agility": 35},
                {"size": 50, "power": 55, "weight": 45, "agility": 50},
                {"size": 40, "power": 65, "weight": 35, "agility": 60},
            ],
        },
    }
    save_customization(user_id, settings)
    seed = new_game_state(mode="hvh", player_count=5)
    inject_player_stats(seed, settings["player_stats"]["a"], settings["player_stats"]["b"])
    save_game(user_id, copy.deepcopy(seed))

    @application.app.route("/__readme/sign-in")
    def readme_sign_in():
        session["user_id"] = user_id
        session["username"] = "Orion FC"
        return redirect("/lobby")

    # Build an actual room through the same create/join routes used by players.
    creator = application.app.test_client()
    with creator.session_transaction() as auth:
        auth["user_id"] = user_id
        auth["username"] = "Orion FC"
    room_response = creator.post("/online/create", json={"player_count": 5})
    assert room_response.status_code == 200
    room_id = room_response.get_json()["room_id"]
    opponent = application.app.test_client()
    with opponent.session_transaction() as auth:
        auth["user_id"] = opponent_id
        auth["username"] = "Harbor United"
    assert opponent.post(f"/online/{room_id}/join", json={}).status_code == 200

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    images = []
    browser_errors = []
    failed_requests = []

    def save_preview(page, filename, caption, *, full_page=False):
        path = destination / filename
        page.screenshot(path=str(path), full_page=full_page, timeout=60000)
        with Image.open(path) as source:
            # Lossless RGB saves discard redundant alpha without softening text.
            source.convert("RGB").save(path, optimize=True)
        with Image.open(path) as source:
            images.append({"path": path.relative_to(ROOT).as_posix(),
                           "width": source.width, "height": source.height,
                           "bytes": path.stat().st_size, "caption": caption})

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, args=["--disable-webgl"]
            )
            context = browser.new_context(
                viewport={"width": 1600, "height": 1120}, device_scale_factor=1
            )
            context.add_init_script("localStorage.setItem('agent-soccer-2d-quality','auto');")
            page = context.new_page()
            page.on("pageerror", lambda error: browser_errors.append(str(error)))
            page.on("response", lambda response: failed_requests.append(response.url)
                    if response.status >= 400 else None)
            page.goto(base + "/__readme/sign-in", wait_until="networkidle")

            page.goto(base + f"/play3d?room={room_id}", wait_until="networkidle")
            page.wait_for_function("window.__pitch2D?.().players_a.length === 5")
            page.wait_for_function("document.querySelector('.team-a .team-name').textContent === 'Orion FC'")
            canvas = page.locator("#pitch-container canvas")
            bounds = canvas.bounding_box()
            assert bounds
            page.wait_for_timeout(300)
            page.evaluate("window.scrollTo(0,0)")
            save_preview(page, "gameplay.png", "A 5v5 online match on the 2D pitch with shared turn controls.", full_page=True)

            page.locator("#player-select").select_option("4")
            page.wait_for_timeout(300)
            page.evaluate("window.scrollTo(0,0)")
            save_preview(page, "player-view.png", "The selected player is highlighted on the 2D pitch.", full_page=True)

            context.add_init_script("sessionStorage.setItem('pg_preload_code', " + json.dumps(EXAMPLE_CODE) + ");")
            page.goto(base + "/playground", wait_until="networkidle")
            page.locator(".CodeMirror").wait_for()
            with page.expect_response("**/api/models/user/validate") as validated:
                page.get_by_role("button", name="Validate", exact=True).click()
            assert validated.value.json().get("ok"), validated.value.json()
            page.get_by_role("button", name="My Code vs AI", exact=True).click()
            page.locator("#opponent-sel").select_option("greedy")
            page.get_by_role("button", name="Start", exact=True).click()
            page.wait_for_function("document.getElementById('ep-status').textContent.includes('Game started')")
            page.evaluate("window.scrollTo(0,0)")
            save_preview(page, "playground.png", "The Python strategy editor beside an initialized Code vs AI test match.", full_page=True)

            page.set_viewport_size({"width": 1600, "height": 1440})
            page.goto(base + "/customize", wait_until="networkidle")
            assert page.locator('#team-a-stats input[type=range]').count() == 20
            assert page.locator('#team-b-stats input[type=range]').count() == 20
            page.locator(".settings-rail a[href='#player-stats']").click()
            page.wait_for_timeout(400)
            save_preview(page, "team-builder.png", "Point-buy player attributes: Size, Power, Weight, and Agility within a 200-point budget.")
            assert not browser_errors, browser_errors
            assert not failed_requests, failed_requests
            context.close()
            browser.close()
    finally:
        server.shutdown()
    return images


if __name__ == "__main__":
    print(json.dumps(capture(), indent=2))
