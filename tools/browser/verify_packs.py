"""Isolated browser regressions for the table studio and match-day layouts."""
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


def main():
    isolated_environment()
    from flask import session, redirect
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright
    import app as application

    @application.app.route('/__packs_review_login')
    def login():
        session.update(user_id='dev:pack-review', username='Table reviewer')
        return redirect('/packs')

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    output = Path(tempfile.gettempdir()) / 'agent-soccer-pack-review'
    output.mkdir(exist_ok=True)
    checks, errors = [], []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        assert passed, name

    def idle(page):
        page.wait_for_function("document.querySelector('.pack-layout').getAttribute('aria-busy') === 'false'")

    def saved(page):
        return page.request.get(base + '/api/packs/state').json()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/__packs_review_login')
            page.wait_for_function("document.querySelector('#pack-status').textContent.includes('Preview ready')")
            idle(page)
            check('initial table is a non-playable preview', page.locator('#pack-mode').inner_text() == 'Preview')
            page.locator('#pack-canvas').focus()
            page.keyboard.press('Enter')
            check('preview keyboard cannot create a move', saved(page)['active'] is False)
            check('empty canonical bumpers display zero', page.locator('#pack-bumpers').input_value() == '0')
            check('hidden pocket field stays hidden', not page.locator('#pack-pocket-label').is_visible())

            with page.expect_response('**/api/packs/start') as start:
                page.locator('#pack-play').click()
            idle(page)
            initial = start.value.json()
            check('preset material and color survive start', initial['spec']['ball']['mass'] == 1.05 and initial['spec']['felt'] == '#0f6e78')
            check('starting enables the table', page.locator('#pack-mode').inner_text() == 'Match live')

            page.locator('#pack-canvas').scroll_into_view_if_needed()
            box = page.locator('#pack-canvas').bounding_box()
            # A blank-table click must not count as a shot.
            page.mouse.click(box['x'] + box['width'] * .6, box['y'] + box['height'] * .2)
            check('clicking away from the mallet cannot shoot', saved(page)['shot'] == 0)
            view, pos = initial['geometry']['view'], initial['bodies']['a']
            x = box['x'] + (pos[0] - view['x']) / view['w'] * box['width']
            y = box['y'] + (pos[1] - view['y']) / view['h'] * box['height']
            with page.expect_response('**/api/packs/move'):
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + box['width'] * .30, y, steps=8)
                page.mouse.up()
            idle(page)
            played = saved(page)
            check('real drag resolves a human and bot turn', played['shot'] >= 2)
            page.reload()
            page.wait_for_function("document.querySelector('#pack-status').textContent.includes('restored')")
            check('reload resumes score and revision', saved(page)['shot'] == played['shot'] and page.locator('#pack-mode').inner_text() == 'Match live')

            page.get_by_role('button', name='Pocket table', exact=True).click()
            idle(page)
            check('switching preset returns to preview', page.locator('#pack-mode').inner_text() == 'Preview')
            check('pocket family hides mallet and gravity controls', not page.locator('#pack-mallet-label').is_visible() and not page.locator('#pack-gravity-label').is_visible())
            page.locator('#pack-canvas').focus()
            page.keyboard.press('Enter')
            check('new preview cannot shoot at the saved match', saved(page)['shot'] == played['shot'])
            page.locator('#pack-resume').click()
            idle(page)
            check('resume restores the saved table family', page.locator('#pack-family').input_value() == 'strike')
            page.locator('#pack-name').fill('A new draft')
            check('editing suspends shooting until preview or start', page.locator('#pack-mode').inner_text() == 'Preview')
            check('editing does not replace saved state', saved(page)['geometry']['name'] == 'Air hockey')
            page.locator('#pack-resume').click()
            idle(page)

            # Commit the shot, then lose the response: never automatically replay it.
            def lose_response(route):
                route.fetch()
                route.abort('failed')
            page.route('**/api/packs/move', lose_response)
            before = saved(page)['shot']
            page.locator('#pack-canvas').focus()
            page.keyboard.press('Enter')
            idle(page)
            page.wait_for_function("document.querySelector('#pack-status').textContent.includes('before shooting again')")
            committed = saved(page)['shot']
            check('lost response suspends controls after server commit', committed > before and page.locator('#pack-mode').inner_text() == 'Preview')
            page.keyboard.press('Enter')
            check('lost response is not automatically resubmitted', saved(page)['shot'] == committed)
            page.unroute('**/api/packs/move', lose_response)
            page.locator('#pack-resume').click()
            idle(page)
            check('explicit resume recovers the committed shot', page.locator('#pack-mode').inner_text() == 'Match live')

            page.get_by_text('Import a table description', exact=True).click()
            page.locator('#pack-json').fill('{broken')
            page.locator('#pack-update').click()
            idle(page)
            check('invalid JSON has visible feedback', 'not valid JSON' in page.locator('#pack-status').inner_text())
            page.locator('#pack-json').fill('')
            page.locator('#pack-resume').click()
            idle(page)
            page.get_by_text('Import a table description', exact=True).click()

            for width in (320, 390, 768, 1024, 1440):
                page.set_viewport_size({'width': width, 'height': 1050})
                page.wait_for_timeout(100)
                check(f'studio fits {width}px', page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                check(f'canvas keeps aspect ratio at {width}px', page.evaluate("(() => { const c=document.querySelector('canvas'); const b=c.getBoundingClientRect(); return Math.abs(b.width/b.height-c.width/c.height)<.02; })()"))
                if width in (390, 1440):
                    page.screenshot(path=str(output / f'studio-{width}.png'), full_page=True)
            for path in ('/hub/play', '/play2d'):
                page.goto(base + path)
                page.wait_for_timeout(400)
                for width in (320, 390, 768, 1440):
                    page.set_viewport_size({'width': width, 'height': 1050})
                    page.wait_for_timeout(80)
                    check(f'{path} fits {width}px', page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                page.screenshot(path=str(output / ('hub.png' if path == '/hub/play' else 'match-setup.png')), full_page=True)
            check('no JavaScript exceptions', not errors)
            browser.close()
    finally:
        server.shutdown()
        (output / 'report.json').write_text(json.dumps({'checks': checks, 'errors': errors}, indent=2), encoding='utf-8')
    print(f'{len(checks)} checks passed. Artifacts: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
