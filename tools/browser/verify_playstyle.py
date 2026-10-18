"""Exercise the five-dot mentality control against an isolated local server.

Run ``python tools/browser/verify_playstyle.py``. Reports and screenshots go to
the system temporary directory under ``agent-soccer-playstyle``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment

DIAGNOSTICS = """
window.__mentalityQA={
 ready:()=>!!gameState && !!pitch.canvas.width,
 state:()=>gameState,
 busy:()=>isAnimating,
 guard:(kind,value)=>{if(kind==='online')ONLINE.active=value;
   else if(kind==='matchmaking')QUICK.joining=value;
   else if(kind==='busy')isAnimating=value;
   else gameState[kind]=value;
   syncInputs();},
 replace:()=>{cancelLocalTurn();gameState={...gameState,playstyle_a:2};syncInputs();},
};
"""


def run():
    isolated_environment()
    from flask import redirect, request, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    source = (ROOT / 'templates/game/index_2d.html').read_text(encoding='utf-8')
    source = re.sub(r'(<script type="module">)(.*?)(</script>)',
                    lambda m: m[1] + m[2] + DIAGNOSTICS + m[3], source, flags=re.S)
    application.app.jinja_loader = ChoiceLoader([
        DictLoader({'game/index_2d.html': source}), application.app.jinja_loader])

    @application.app.route('/__playstyle/login/<name>')
    def login(name):
        session.update(user_id='dev:playstyle-' + name, username=name)
        state = new_soccer_state(mode=request.args.get('mode', 'hvai'), player_count=5, half_length=0)
        save_game(session['user_id'], state)
        return redirect('/play2d')

    output = Path(tempfile.gettempdir()) / 'agent-soccer-playstyle'
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

    def radio(page, level):
        return page.locator(f'input[name="team-playstyle"][value="{level}"]')

    def ready(page, name, mode='hvai'):
        page.goto(base + '/__playstyle/login/' + name + '?mode=' + mode)
        page.wait_for_function('window.__mentalityQA?.ready()')
        page.wait_for_function("document.querySelectorAll('#model-select option').length >= 15")
        page.locator('#playstyle-control').scroll_into_view_if_needed()

    def choose(page, level):
        with page.expect_response(lambda response: response.url.endswith('/set_playstyle')) as saved:
            radio(page, level).check()
        page.wait_for_function("!window.__mentalityQA.busy() && document.querySelector('#playstyle-control').getAttribute('aria-busy') === 'false'")
        return saved.value

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--disable-webgl'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            ready(page, 'desktop')
            original = context.request.get(base + '/state').json()
            check('Balanced is the initial mentality', radio(page, 3).is_checked() and original['playstyle_a'] == 3 and original['playstyle_b'] == 3)
            check('Five dots have accessible names and a group label', page.get_by_role('group', name='Team mentality').count() == 1
                  and all(page.get_by_role('radio', name=label, exact=True).count() == 1 for label in ('Deep defend', 'Defend', 'Balanced', 'Attack', 'All-out attack')))
            check('Human match exposes only your team', page.locator('#playstyle-team-choice').is_hidden() and page.locator('#playstyle-team-label').inner_text() == 'Your team')
            for level, label in enumerate(('Deep defend', 'Defend', 'Balanced', 'Attack', 'All-out attack'), start=1):
                saved = choose(page, level)
                state = context.request.get(base + '/state').json()
                check(f'Level {level} saves to the backend and displays {label}', saved.ok and state['playstyle_a'] == level and state['playstyle_b'] == 3 and radio(page, level).is_checked() and page.locator('#playstyle-name').inner_text() == label)
                check(f'Level {level} does not move players or reset the match', all(state[key] == original[key] for key in ('players_a', 'players_b', 'ball', 'kick_count', 'player_roles_a', 'player_roles_b')))
            page.reload()
            page.wait_for_function('window.__mentalityQA?.ready()')
            check('Reload retains the selected mentality', radio(page, 5).is_checked())
            with page.expect_response(lambda response: response.url.endswith('/reset')):
                page.locator('[onclick="resetGame()"]').click()
            page.wait_for_function('window.__mentalityQA.state().playstyle_a === 5')
            check('New Game retains the selected mentality', context.request.get(base + '/state').json()['playstyle_a'] == 5 and radio(page, 5).is_checked())
            radio(page, 5).focus()
            with page.expect_response(lambda response: response.url.endswith('/set_playstyle')):
                page.keyboard.press('ArrowLeft')
            page.wait_for_function("document.querySelector('#playstyle-control').getAttribute('aria-busy') === 'false'")
            check('Keyboard arrows select and save the adjacent dot', radio(page, 4).is_checked() and context.request.get(base + '/state').json()['playstyle_a'] == 4)
            check('Keyboard focus remains usable after saving', page.evaluate('document.activeElement.matches(\'input[name="team-playstyle"]:checked\')'))
            page.locator('#playstyle-control').screenshot(path=str(output / 'control-desktop.png'))
            page.screenshot(path=str(output / 'desktop.png'))

            page.route('**/set_playstyle', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: mentality unavailable'})))
            choose(page, 1)
            check('Failed saves restore the committed dot with an error', radio(page, 4).is_checked() and page.locator('#playstyle-status').inner_text() == 'Fixture: mentality unavailable' and page.locator('#playstyle-status').evaluate("el => el.classList.contains('is-error')") and context.request.get(base + '/state').json()['playstyle_a'] == 4)
            page.unroute('**/set_playstyle')

            pending, mutation_requests = [], []
            page.on('request', lambda req: mutation_requests.append(req.url.rsplit('/', 1)[-1]) if req.method == 'POST' else None)
            page.route('**/set_playstyle', lambda route: pending.append(route))
            with page.expect_request(lambda req: req.url.endswith('/set_playstyle')):
                radio(page, 2).check()
            locked = {selector: page.locator(selector).is_disabled() for selector in ('input[name="team-playstyle"][value="1"]', '#kick-btn', '[onclick="resetGame()"]', '#mode-select', '#model-select', '#browse-models-btn')}
            check('Pending save disables dots, kick, reset, mode and opponent controls', all(locked.values()), locked)
            page.evaluate("window.resetGame();window.setMode('aivai');window.switchModel('possession');document.querySelector('#kick-btn').click();document.querySelector('input[name=\"team-playstyle\"][value=\"5\"]').click()")
            page.wait_for_timeout(150)
            check('Pending save rejects duplicate requests and conflicting actions', len(pending) == 1 and mutation_requests == ['set_playstyle'], mutation_requests)
            pending.pop().fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: retry available'}))
            page.wait_for_function("document.querySelector('#playstyle-status').textContent.includes('Fixture: retry available')")
            check('Failed pending save unlocks controls for retry', page.locator('#kick-btn').is_enabled() and page.locator('#model-select').is_enabled() and radio(page, 4).is_checked())
            page.unroute('**/set_playstyle')

            for endpoint, action in (('reset', "window.resetGame()"), ('undo', "window.undoMove()"), ('set_mode', "window.setMode('aivai')")):
                transitions = []
                page.route('**/' + endpoint, lambda route: transitions.append(route))
                with page.expect_request(lambda req: req.url.endswith('/' + endpoint)):
                    page.evaluate('void ' + action)
                before = len(mutation_requests)
                radio(page, 1).evaluate('element=>element.click()')
                check(f'Pending {endpoint} locks mentality and prevents racing saves', radio(page, 1).is_disabled() and page.locator('#kick-btn').is_disabled() and len(mutation_requests) == before)
                transitions.pop().fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: transition unavailable'}))
                page.wait_for_function('!window.__mentalityQA.busy()')
                check(f'Failed {endpoint} keeps the selected mentality and unlocks controls', radio(page, 4).is_checked() and radio(page, 4).is_enabled() and page.locator('#mode-select').input_value() == 'hvai')
                page.unroute('**/' + endpoint)

            for kind, value, text in (('online', True, 'local AI matches'), ('matchmaking', True, 'matchmaking'), ('penalty_shootout', True, 'shootout'), ('game_over', True, 'new match'), ('busy', True, 'between turns'), ('game_mode', 'hvh', 'AI match')):
                before = len(mutation_requests)
                page.evaluate('([kind,value])=>window.__mentalityQA.guard(kind,value)', [kind, value])
                radio(page, 1).evaluate('element=>element.click()')
                check(f'{kind} disables mentality changes with an explanation', radio(page, 1).is_disabled() and text in page.locator('#playstyle-status').inner_text() and len(mutation_requests) == before)
                page.evaluate('([kind,value])=>window.__mentalityQA.guard(kind,value)', [kind, 'hvai' if kind == 'game_mode' else False])

            page.route('**/set_playstyle', lambda route: pending.append(route))
            with page.expect_request(lambda req: req.url.endswith('/set_playstyle')):
                radio(page, 1).check()
            old_state = context.request.get(base + '/state').json()
            old_state.update(playstyle_a=1)
            old_state['players_a'][4]['x'] = 100
            # Swap the local session before a delayed settings response arrives.
            page.evaluate('window.__mentalityQA.replace()')
            pending.pop().fulfill(status=200, content_type='application/json', body=json.dumps(old_state))
            page.wait_for_function("document.querySelector('#playstyle-control').getAttribute('aria-busy') === 'false'")
            check('An outdated save cannot overwrite the replacement session', page.evaluate('window.__mentalityQA.state().playstyle_a===2 && window.__mentalityQA.state().players_a[4].x!==100') and radio(page, 2).is_checked())
            page.unroute('**/set_playstyle')

            ready(page, 'both', 'aivai')
            check('AI versus AI exposes both team choices', page.locator('#playstyle-team-choice').is_visible() and page.locator('#playstyle-team-label').is_hidden())
            choose(page, 2)
            page.locator('#playstyle-team').select_option('b')
            check('Switching teams shows that team\'s saved level', radio(page, 3).is_checked())
            choose(page, 5)
            state = context.request.get(base + '/state').json()
            check('AI sides save independently', state['playstyle_a'] == 2 and state['playstyle_b'] == 5)
            page.locator('#playstyle-team').select_option('a')
            check('Switching back restores team A\'s level', radio(page, 2).is_checked())

            mobile_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, reduced_motion='reduce')
            mobile = mobile_context.new_page()
            mobile.on('pageerror', lambda error: report['errors'].append(str(error)))
            ready(mobile, 'mobile')
            choose(mobile, 5)
            sizes = mobile.locator('.playstyle-track label').evaluate_all('items=>items.map(item=>({width:item.getBoundingClientRect().width,height:item.getBoundingClientRect().height}))')
            check('Mobile dots have usable touch targets', all(size['width'] >= 44 and size['height'] >= 44 for size in sizes), sizes)
            check('Mobile page and mentality control fit the width', mobile.evaluate("document.documentElement.scrollWidth<=innerWidth && document.querySelector('#playstyle-control').scrollWidth<=document.querySelector('#playstyle-control').clientWidth"))
            mobile.locator('#playstyle-control').screenshot(path=str(output / 'control-mobile.png'))
            mobile.screenshot(path=str(output / 'mobile.png'))
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
