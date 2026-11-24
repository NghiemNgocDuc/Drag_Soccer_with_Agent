"""Check the game-first layout and capture desktop/mobile review images.

Uses an isolated local Flask server and installed Chrome, without external data.
"""
from pathlib import Path
import json
import logging
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


def main():
    isolated_environment()
    from flask import redirect, session
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import delete_game

    @application.app.route('/__focus/login')
    def login():
        session.update(user_id='dev:match-focus', username='Coach')
        delete_game(session['user_id'])
        return redirect('/play2d')

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    out = ROOT / 'docs' / 'previews' / 'match-focus'
    out.mkdir(parents=True, exist_ok=True)
    errors, checks = [], []

    def check(name, condition):
        assert condition, name
        checks.append(name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100}, reduced_motion='reduce')
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/__focus/login')
            page.wait_for_function("!document.querySelector('#match-start-btn').disabled")
            check('setup exposes match settings', page.locator('#model-select').is_visible())
            page.locator('#match-start-btn').click()
            page.wait_for_function("document.querySelector('#match-setup').hidden && window.__pitch2D().width > 0")
            page.evaluate('document.fonts.ready')
            page.evaluate('scrollTo(0, 0)')
            check('settings collapse after kickoff', not page.locator('#match-options').evaluate('(el)=>el.open'))
            check('game plan starts collapsed', not page.locator('#match-guide').evaluate('(el)=>el.open'))
            canvas = page.locator('#pitch-container').bounding_box()
            hud = page.locator('.hud').bounding_box()
            check('pitch uses at least 95 percent of desktop width', canvas['width'] >= 1440 * .95)
            check('pitch starts near the top', canvas['y'] <= 160)
            check('scoreboard is compact', hud['height'] <= 86)
            check('kick controls remain visible', page.locator('#kick-btn').is_visible())
            check('kickoff selects the human player', page.locator('#player-select').input_value() == '4')
            page.screenshot(path=str(out / 'desktop.png'), full_page=True)
            page.locator('#fs-wrap').screenshot(path=str(out / 'pitch.png'))

            # Real keyboard activation of disclosures and settings dialogs.
            summary = page.locator('#match-options > summary')
            summary.focus()
            page.keyboard.press('Enter')
            check('keyboard opens settings', page.locator('#model-select').is_visible())
            page.locator('#view-settings-btn').click()
            check('display settings remain reachable', page.locator('#view-settings-modal').is_visible())
            page.locator('#view-settings-modal button', has_text='Close').click()
            summary.click()
            page.locator('#match-guide > summary').click()
            check('roles and keyboard help remain reachable', page.locator('#squad-role-list').is_visible()
                  and page.locator('#player-control-help').is_visible())
            page.locator('#match-guide > summary').click()

            for width, height in [(390, 900), (768, 1024), (1440, 560)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.evaluate('scrollTo(0, 0)')
                check(f'no horizontal overflow at {width}x{height}', page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                check(f'pitch keeps its shape at {width}x{height}', page.locator('#pitch-container').evaluate('(el)=>Math.abs(el.clientWidth/el.clientHeight - 1552/1027) < .015'))
                if width == 390:
                    page.screenshot(path=str(out / 'mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1100})
            with page.expect_response(lambda r: r.url.endswith('/move')) as moved:
                page.locator('#kick-btn').click()
            check('first button kick submits player five', moved.value.ok and moved.value.request.post_data_json['player_idx'] == 4)
            page.wait_for_function('!window.__renderStats().isAnimating && !document.querySelector("#kick-btn").disabled')
            for fallback in (False, True):
                if fallback:
                    page.locator('#match-start-btn').click()
                    page.wait_for_function("document.querySelector('#match-setup').hidden")
                    page.evaluate("Object.defineProperty(document,'fullscreenEnabled',{configurable:true,value:false})")
                page.locator('#pitch-fullscreen-btn').click()
                page.wait_for_function("document.fullscreenElement || document.querySelector('#fs-wrap').classList.contains('pitch-expanded')")
                check(f'enlarged view retains kick controls ({fallback=})', page.locator('#kick-btn').is_visible())
                page.locator('#kick-btn').click()
                page.wait_for_function('!window.__renderStats().isAnimating && !document.querySelector("#kick-btn").disabled')
                check(f'kick updates preserve the enlarged-view label ({fallback=})',
                      page.locator('#pitch-fullscreen-btn').inner_text() == 'Exit enlarged view')
                page.locator('button', has_text='New Game').click()
                page.wait_for_function("!document.querySelector('#match-setup').hidden")
                check(f'new game exits enlarged view ({fallback=})', page.evaluate("!document.fullscreenElement && !document.querySelector('#fs-wrap').classList.contains('pitch-expanded')"))
                check(f'new game restores setup settings ({fallback=})', page.locator('#model-select').is_visible())
            check('no browser exceptions', not errors)
            browser.close()
    finally:
        server.shutdown()
    (out / 'checks.json').write_text(json.dumps({'passed': len(checks), 'checks': checks, 'errors': errors}, indent=2) + '\n', encoding='utf-8')
    print(f'{len(checks)} match-focus checks passed. Preview: {out}')


if __name__ == '__main__':
    main()
