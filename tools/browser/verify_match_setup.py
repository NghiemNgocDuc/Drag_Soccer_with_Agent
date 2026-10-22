"""Verify mandatory tactics setup against a real isolated local Flask server.

Run ``python tools/browser/verify_match_setup.py``. Reports and screenshots go to
the system temporary directory under ``agent-soccer-match-setup``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment

FORMATIONS = ('2-1-1', '1-2-1', '2-0-2', '1-1-2')
STYLES = ('Deep defend', 'Defend', 'Balanced', 'Attack', 'All-out attack')
DIAGNOSTICS = """
window.__setupQA={
 ready:()=>!!gameState && !!pitch.canvas.width,
 state:()=>gameState,
 busy:()=>isAnimating,
 replace:()=>{cancelLocalTurn();gameState={...gameState,match_started:false,
   formation_a:'2-0-2',playstyle_a:2};syncInputs();},
};
"""


def run():
    isolated_environment()
    from flask import redirect, request, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import delete_game, get_game, save_game
    from models.soccer_logic import new_soccer_state

    source = (ROOT / 'templates/game/index_2d.html').read_text(encoding='utf-8')
    source = re.sub(r'(<script type="module">)(.*?)(</script>)',
                    lambda m: m[1] + m[2] + DIAGNOSTICS + m[3], source, flags=re.S)
    application.app.jinja_loader = ChoiceLoader([
        DictLoader({'game/index_2d.html': source}), application.app.jinja_loader])

    @application.app.route('/__setup/login/<name>')
    def login(name):
        session.update(user_id='dev:setup-' + name, username=name)
        delete_game(session['user_id'])
        mode = request.args.get('mode', 'hvai')
        if request.args.get('legacy'):
            state = new_soccer_state(mode=mode, player_count=5, half_length=45)
            state.pop('match_started', None)
        else:
            state = get_game(session['user_id'])
            state['game_mode'] = mode
            # Abandoned setup must remain at kickoff even after a full match time.
            state['start_time'] = time.time() - 1000
        save_game(session['user_id'], state)
        return redirect('/play2d')

    output = Path(tempfile.gettempdir()) / 'agent-soccer-match-setup'
    output.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'errors': [], 'artifacts': str(output)}
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'

    def check(name, passed, evidence=None):
        report['checks'].append({'name': name, 'passed': bool(passed), 'evidence': evidence})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise AssertionError(f'{name}: {evidence}')

    def formation(page, value, side='a'):
        return page.locator(f'input[name="formation-{side}"][value="{value}"]')

    def style(page, level, side='a'):
        return page.locator(f'input[name="setup-playstyle-{side}"][value="{level}"]')

    def ready(page, name, mode='hvai', legacy=False):
        page.goto(base + '/__setup/login/' + name + '?mode=' + mode + ('&legacy=1' if legacy else ''))
        page.wait_for_function('window.__setupQA?.ready()')
        page.wait_for_function("document.querySelectorAll('#model-select option').length >= 15")
        if not legacy:
            page.wait_for_function("document.querySelectorAll('input[name=\"formation-a\"]').length===4 && !document.querySelector('#match-start-btn').disabled")
            page.locator('#match-setup').scroll_into_view_if_needed()

    def start(page):
        with page.expect_response(lambda response: response.url.endswith('/start_match')) as response:
            page.locator('#match-start-btn').click()
        if response.value.ok:
            # AI-versus-AI immediately begins its first turn after kickoff.
            page.wait_for_function('window.__setupQA.state().match_started===true')
        else:
            page.wait_for_function('!window.__setupQA.busy()')
        return response.value

    def reset(page):
        with page.expect_response(lambda response: response.url.endswith('/reset')):
            page.locator('[onclick="resetGame()"]').click()
        page.wait_for_function('!window.__setupQA.busy() && window.__setupQA.state().match_started===false')
        page.locator('#match-setup').scroll_into_view_if_needed()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--disable-webgl'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1100}, reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            ready(page, 'desktop')
            original = context.request.get(base + '/state').json()
            check('Fresh local match opens mandatory setup', page.locator('#match-setup').is_visible() and original['match_started'] is False)
            check('An old setup remains at 00:00 with no period transition', original['match_clock_seconds'] == 0 and original['clock_running'] is False
                  and original['period'] == 'regular_first' and page.locator('#game-clock').inner_text() == '00:00')
            check('Kick is blocked until Start Match', page.locator('#kick-btn').is_disabled())
            check('Four genuine five-a-side formations are available', all(formation(page, value).count() == 1 for value in FORMATIONS))
            check('Balanced mentality and 2-1-1 are the defaults', style(page, 3).is_checked() and formation(page, '2-1-1').is_checked())
            check('Human versus AI exposes only your setup', page.locator('#match-setup-team-b').is_hidden())
            check('All five mentality dots have accessible names', all(page.get_by_role('radio', name=label, exact=True).count() == 1 for label in STYLES))
            check('Retry is hidden after formations load', page.locator('#match-setup-retry').is_hidden())
            mutations = []
            page.on('request', lambda req: mutations.append(req.url.rsplit('/', 1)[-1]) if req.method == 'POST' else None)
            preview_geometries = []
            for value in FORMATIONS:
                formation(page, value).check()
                check('Formation draft selects ' + value, formation(page, value).is_checked())
                preview_geometries.append(page.locator('#setup-preview-a svg text').evaluate_all('nodes=>nodes.map(node=>[node.textContent,node.getAttribute("x"),node.getAttribute("y")])'))
            check('Each formation updates the lineup preview geometry', len({json.dumps(value) for value in preview_geometries}) == 4)
            for level in range(1, 6):
                style(page, level).check()
                check('Mentality draft selects ' + STYLES[level - 1], style(page, level).is_checked())
            current = context.request.get(base + '/state').json()
            check('Draft changes send no mutations and do not move the saved roster', not mutations and all(current[key] == original[key]
                  for key in ('players_a', 'players_b', 'ball', 'kick_count', 'playstyle_a', 'formation_a')), mutations)
            style(page, 5).focus()
            page.keyboard.press('ArrowLeft')
            check('Arrow keys select the adjacent mentality dot', style(page, 4).is_checked()
                  and page.evaluate('document.activeElement.matches(\'input[name="setup-playstyle-a"]:checked\')'))
            formation(page, '1-2-1').check()
            style(page, 5).check()
            page.locator('#match-setup').screenshot(path=str(output / 'setup-desktop.png'))
            page.screenshot(path=str(output / 'desktop.png'))

            page.route('**/start_match', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: start unavailable'})))
            failed = start(page)
            check('A failed start keeps setup and explains the error', failed.status == 503 and page.locator('#match-setup').is_visible()
                  and 'Fixture: start unavailable' in page.locator('#match-setup-status').inner_text())
            check('A failed start preserves draft choices and allows retry', formation(page, '1-2-1').is_checked()
                  and style(page, 5).is_checked() and page.locator('#match-start-btn').is_enabled()
                  and context.request.get(base + '/state').json()['match_started'] is False)
            page.unroute('**/start_match')

            pending = []
            mutations.clear()
            page.route('**/start_match', lambda route: pending.append(route))
            with page.expect_request(lambda req: req.url.endswith('/start_match')):
                page.locator('#match-start-btn').click()
            locked = {selector: page.locator(selector).is_disabled() for selector in (
                '#match-start-btn', 'input[name="formation-a"][value="2-1-1"]',
                'input[name="setup-playstyle-a"][value="1"]', '#kick-btn', '[onclick="resetGame()"]', '#mode-select')}
            check('Pending start locks setup, kick, reset and mode', all(locked.values()), locked)
            page.evaluate("document.querySelector('#match-start-btn').click();window.resetGame();window.setMode('aivai');document.querySelector('#kick-btn').click()")
            page.wait_for_timeout(100)
            check('Duplicate start and conflicting actions send no extra writes', len(pending) == 1 and mutations == ['start_match'], mutations)
            pending.pop().continue_()
            page.wait_for_function('window.__setupQA.state().match_started===true && !window.__setupQA.busy()')
            page.unroute('**/start_match')
            saved = context.request.get(base + '/state').json()
            check('Start atomically applies the chosen formation and mentality', saved['formation_a'] == '1-2-1' and saved['playstyle_a'] == 5
                  and saved['formation_b'] == '2-1-1' and saved['playstyle_b'] == 3 and saved['match_started'] is True)
            check('Selected formation changes the real roster and roles', saved['players_a'] != original['players_a']
                  and saved['player_roles_a'] == ['goalkeeper', 'defender', 'support', 'support', 'attacker'])
            check('Start begins the clock from kickoff', saved['clock_running'] is True and 0 <= saved['match_clock_seconds'] < 120)
            check('Started match shows tactics summary and enables the kick', page.locator('#match-setup').is_hidden()
                  and page.locator('#match-tactics-summary').is_visible() and page.locator('#kick-btn').is_enabled())
            check('Mode cannot replace a started match', page.locator('#mode-select').is_disabled())
            before = saved['kick_count']
            page.reload()
            page.wait_for_function('window.__setupQA?.ready()')
            check('Reload resumes the active match without reopening setup', page.locator('#match-setup').is_hidden()
                  and page.evaluate('window.__setupQA.state().match_started===true')
                  and context.request.get(base + '/state').json()['kick_count'] == before)

            reset(page)
            check('New Match returns to setup with saved tactics prefilled', formation(page, '1-2-1').is_checked()
                  and style(page, 5).is_checked() and page.locator('#kick-btn').is_disabled())
            reset_state = context.request.get(base + '/state').json()
            check('Reset stops the clock and clears the score', reset_state['match_clock_seconds'] == 0 and reset_state['clock_running'] is False
                  and reset_state['score_a'] == 0 and reset_state['score_b'] == 0)

            page.route('**/start_match', lambda route: pending.append(route))
            with page.expect_request(lambda req: req.url.endswith('/start_match')):
                page.locator('#match-start-btn').click()
            stale = dict(saved)
            stale['players_a'] = [dict(player) for player in saved['players_a']]
            stale['players_a'][4]['x'] = 999
            page.evaluate('window.__setupQA.replace()')
            pending.pop().fulfill(status=200, content_type='application/json', body=json.dumps(stale))
            page.wait_for_function('!window.__setupQA.busy()')
            check('A stale start response cannot overwrite replacement setup', page.evaluate("window.__setupQA.state().match_started===false && window.__setupQA.state().formation_a==='2-0-2' && window.__setupQA.state().playstyle_a===2 && window.__setupQA.state().players_a[4].x!==999"))
            page.unroute('**/start_match')

            ready(page, 'both', 'aivai')
            check('AI versus AI exposes both team setups', page.locator('#match-setup-team-b').is_visible())
            formation(page, '2-0-2').check()
            style(page, 2).check()
            formation(page, '1-1-2', 'b').check()
            style(page, 5, 'b').check()
            check('Both teams start successfully', start(page).ok)
            both = context.request.get(base + '/state').json()
            check('Both teams retain independent formation and mentality', both['formation_a'] == '2-0-2' and both['playstyle_a'] == 2
                  and both['formation_b'] == '1-1-2' and both['playstyle_b'] == 5)
            ready(page, 'hotseat', 'hvh')
            check('Hotseat allows both formations before kickoff', page.locator('#match-setup-team-b').is_visible()
                  and formation(page, '1-1-2', 'b').is_enabled())
            check('Hotseat can start normally', start(page).ok and page.locator('#match-setup').is_hidden())

            ready(page, 'legacy', legacy=True)
            check('Existing active saves resume without setup or reset', page.locator('#match-setup').is_hidden()
                  and page.locator('#kick-btn').is_enabled() and context.request.get(base + '/state').json()['match_started'] is True)

            page.route('**/match_setup', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: catalog unavailable'})))
            page.goto(base + '/__setup/login/retry')
            page.wait_for_function('window.__setupQA?.ready()')
            page.wait_for_function("document.querySelector('#match-setup-status').textContent.includes('Could not load formations')")
            check('Failed catalog load exposes retry and blocks kickoff', page.locator('#match-setup-retry').is_visible()
                  and page.locator('#match-start-btn').is_disabled())
            page.unroute('**/match_setup')
            page.locator('#match-setup-retry').click()
            page.wait_for_function("document.querySelectorAll('input[name=\"formation-a\"]').length===4 && !document.querySelector('#match-start-btn').disabled")
            check('Catalog retry restores usable setup and hides retry', formation(page, '2-1-1').is_checked()
                  and page.locator('#match-setup-retry').is_hidden())
            with page.expect_response(lambda response: response.url.endswith('/set_mode')):
                page.locator('#mode-select').select_option('aivai')
            page.wait_for_function('!window.__setupQA.busy() && window.__setupQA.state().game_mode==="aivai"')
            check('Choosing AI versus AI before kickoff reveals both setups', page.locator('#match-setup-team-b').is_visible()
                  and context.request.get(base + '/state').json()['match_started'] is False)
            with page.expect_response(lambda response: response.url.endswith('/set_mode')):
                page.locator('#mode-select').select_option('hvai')
            page.wait_for_function('!window.__setupQA.busy() && window.__setupQA.state().game_mode==="hvai"')
            check('Returning to human play leaves only your setup', page.locator('#match-setup-team-b').is_hidden()
                  and page.locator('#match-start-btn').is_enabled())

            mobile_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, reduced_motion='reduce')
            mobile = mobile_context.new_page()
            mobile.on('pageerror', lambda error: report['errors'].append(str(error)))
            ready(mobile, 'mobile')
            formation(mobile, '1-1-2').check()
            style(mobile, 4).check()
            sizes = mobile.locator('label:has(input[name="setup-playstyle-a"])').evaluate_all('items=>items.map(item=>({width:item.getBoundingClientRect().width,height:item.getBoundingClientRect().height}))')
            check('Mobile mentality dots have 44-pixel touch targets', len(sizes) == 5 and all(size['width'] >= 44 and size['height'] >= 44 for size in sizes), sizes)
            check('Mobile page and setup fit the viewport width', mobile.evaluate("document.documentElement.scrollWidth<=innerWidth && document.querySelector('#match-setup').scrollWidth<=document.querySelector('#match-setup').clientWidth"))
            endpoints = mobile.locator('.setup-playstyle .playstyle-endpoints span').evaluate_all('nodes=>nodes.map(node=>({left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right}))')
            check('Mobile defend and attack labels remain separated', endpoints[1]['left'] - endpoints[0]['right'] >= 100, endpoints)
            mobile.locator('#match-setup').screenshot(path=str(output / 'setup-mobile.png'))
            mobile.screenshot(path=str(output / 'mobile.png'))
            check('Mobile selections start the chosen tactics', start(mobile).ok and mobile_context.request.get(base + '/state').json()['formation_a'] == '1-1-2')
            check('No browser exceptions', not report['errors'], report['errors'])
            browser.close()
    except Exception as error:
        report['exception'] = str(error)
        raise
    finally:
        server.shutdown()
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    results = run()
    print(json.dumps({'passed': len(results['checks']), 'artifacts': results['artifacts']}, indent=2))
