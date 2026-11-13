"""Verify the served local basketball setup, controls and soccer switch.

Run ``python tools/browser/verify_basketball.py``. Screenshots and a JSON report
are written beneath the system temporary directory, in agent-soccer-basketball.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import re
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment

DIAGNOSTICS = """
window.__basketballQA={
 ready:()=>!!gameState && !!pitch.canvas.width,
 state:()=>gameState,
 busy:()=>isAnimating,
 point:(x,y)=>pitch.clientPoint(x,y),
};
"""

REPLAY_DIAGNOSTICS = """
window.__basketballReplayQA={
 ready:()=>state.players_a.length===5 && !!pitch.canvas.width,
 state:()=>state,
 busy:()=>isAnimating,
 seek:entry=>seekToEntry(entry),
};
"""


def run():
    isolated_environment()
    from flask import redirect, render_template, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import delete_game, get_game
    from models.basketball_logic import HOOP_HEIGHT, _flight_time, _throw_factor, basketball_ai_move

    source = (ROOT / 'templates/game/index_2d.html').read_text(encoding='utf-8')
    source = re.sub(r'(<script type="module">)(.*?)(</script>)',
                    lambda match: match[1] + match[2] + DIAGNOSTICS + match[3], source, flags=re.S)
    replay_source = (ROOT / 'templates/game/replay_2d.html').read_text(encoding='utf-8')
    replay_source = re.sub(r'(<script type="module">)(.*?)(</script>)',
                           lambda match: match[1] + match[2] + REPLAY_DIAGNOSTICS + match[3], replay_source, flags=re.S)
    application.app.jinja_loader = ChoiceLoader([
        DictLoader({'game/index_2d.html': source, 'game/replay_2d.html': replay_source}), application.app.jinja_loader])

    @application.app.route('/__basketball/login/<name>')
    def login(name):
        session.update(user_id='dev:basketball-' + name, username=name)
        delete_game(session['user_id'])
        return redirect('/play2d')

    @application.app.route('/__basketball/replay')
    def replay_fixture():
        # Exercise the real saved engine trajectories and inbound records from
        # the human controls below, using the served replay template.
        moves = get_game(session['user_id'])['move_history']
        return render_template('game/replay_2d.html', username='Basketball QA', t=None,
                               match={'id': 'basketball-ui-fixture', 'replay_data': moves},
                               highlights=[], highlight=None, live_room=None,
                               loss_model=None, loss_model_name=None)

    output = Path(tempfile.gettempdir()) / 'agent-soccer-basketball'
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

    def ready(page, name):
        page.goto(base + '/__basketball/login/' + name)
        page.wait_for_function('window.__basketballQA?.ready()')
        page.wait_for_function('document.querySelectorAll("input[name=setup-sport]").length===2 && !document.querySelector("#match-start-btn").disabled')
        page.locator('#match-setup').scroll_into_view_if_needed()

    def start(page):
        with page.expect_response(lambda response: response.url.endswith('/start_match')) as response:
            page.locator('#match-start-btn').click()
        check('Start request succeeds', response.value.ok, response.value.status)
        page.wait_for_function('window.__basketballQA.state().match_started===true && !window.__basketballQA.busy()')
        return response.value

    def reset(page):
        with page.expect_response(lambda response: response.url.endswith('/reset')):
            page.locator('[onclick="resetGame()"]').click()
        page.wait_for_function('!window.__basketballQA.busy() && window.__basketballQA.state().match_started===false')

    try:
        with sync_playwright() as playwright:
            launch_options = {'headless': True, 'args': ['--disable-webgl']}
            if not Path(playwright.chromium.executable_path).is_file():
                launch_options['channel'] = 'chrome'
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context(viewport={'width': 1440, 'height': 1100}, reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            ready(page, 'desktop')
            soccer = page.locator('input[name="setup-sport"][value="soccer"]')
            basketball = page.locator('input[name="setup-sport"][value="basketball"]')
            check('Soccer stays the default sport', soccer.is_checked())
            writes = []
            page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' else None)
            original = context.request.get(base + '/state').json()
            page.locator('#pass-short').click()
            soccer.focus()
            page.keyboard.press('ArrowRight')
            check('Native sport radios work with arrow keys', basketball.is_checked())
            check('Basketball shows its rules and hides soccer tactics', page.locator('#setup-basketball-title').is_visible()
                  and page.locator('input[name="formation-a"]').count() == 0
                  and '21' in page.locator('.setup-basketball').inner_text())
            saved = context.request.get(base + '/state').json()
            check('Sport remains a local draft until Start', not writes and saved.get('sport', 'soccer') == 'soccer'
                  and saved['players_a'] == original['players_a'])
            page.locator('#match-setup').screenshot(path=str(output / 'setup-desktop.png'))
            response = start(page)
            payload = response.request.post_data_json
            check('Basketball start submits no soccer tactics', payload == {'sport': 'basketball'}, payload)
            state = context.request.get(base + '/state').json()
            check('Basketball starts with five players per side', state['sport'] == 'basketball'
                  and len(state['players_a']) == len(state['players_b']) == 5)
            check('Court and points target replace the soccer pitch presentation',
                  'basketball' in page.locator('#pitch-container canvas').get_attribute('aria-label').lower()
                  and page.locator('#game-clock').inner_text() == 'First to 21'
                  and page.locator('#pitch-fullscreen-btn').inner_text() == 'Enlarge court')
            check('All five players can be selected without a goalkeeper label',
                  page.locator('#player-select option').count() == 5
                  and 'Goalkeeper' not in page.locator('#player-select').inner_text()
                  and page.locator('#squad-role-guide').is_hidden())
            carrier = state['possession']['player_idx']
            check('Ball carrier is selected for a throw', page.locator('#player-select').input_value() == str(carrier)
                  and page.locator('#kick-btn').inner_text() == 'Throw')
            check('Basketball clears and hides the soccer pass presets', page.locator('#pass-presets').is_hidden()
                  and page.locator('#pass-normal').get_attribute('aria-pressed') == 'true'
                  and page.evaluate('window.__playerControlState().power === 80'),
                  {'hidden': page.locator('#pass-presets').is_hidden(),
                   'normal': page.locator('#pass-normal').get_attribute('aria-pressed'),
                   'power': page.locator('#kick-power-value').input_value(),
                   'controls': page.evaluate('window.__playerControlState()')})
            page.locator('#pitch-container canvas').focus()
            for key in ('s', 'a', 'w'):
                page.keyboard.press(key)
            check('Hidden soccer pass shortcuts cannot change basketball power',
                  page.evaluate('window.__playerControlState().power === 80'))
            check('Basketball names positions and the attacking direction',
                  'Point guard' in page.locator('#player-select').inner_text()
                  and 'Ball carrier' in page.locator('#player-select').inner_text()
                  and 'Attack right' in page.locator('#player-control-status').inner_text())
            offball = next(index for index in range(5) if index != carrier)
            page.locator('#player-select').select_option(str(offball))
            check('Selecting an off-ball player offers Move', page.locator('#kick-btn').inner_text() == 'Move')
            page.locator('#speed-slider').evaluate("element=>element.value='3'")
            page.locator('#speed-slider').dispatch_event('input')
            page.locator('#player-select').select_option(str(carrier))
            check('Selecting the carrier manually restores Throw', page.locator('#kick-btn').inner_text() == 'Throw')
            player, hoop = state['players_a'][carrier], state['basketball']['hoops']['A']
            distance = math.hypot(hoop['x'] - player['x'], hoop['y'] - player['y'])
            # The UI range uses whole command points; the real rim has enough
            # tolerance for the nearest supported power without bypassing input.
            shot_power = round(distance / (_throw_factor(player['stats']) * _flight_time(HOOP_HEIGHT)))
            page.locator('#aim-angle').fill(str(round(math.degrees(math.atan2(hoop['y'] - player['y'], hoop['x'] - player['x'])) % 360)))
            page.locator('#kick-power').evaluate('(element,value)=>element.value=String(value)', shot_power)
            page.locator('#kick-power').dispatch_event('input')
            with page.expect_response(lambda reply: reply.url.endswith('/move')) as thrown:
                page.locator('#kick-btn').click()
            check('A human Throw sends the selected carrier through the normal move route', thrown.value.ok
                  and thrown.value.request.post_data_json['player_idx'] == carrier, thrown.value.status)
            made = thrown.value.json()
            result = made['move_result']
            check('The actual shot makes a three-point basket', result['scored'] == 'A' and made['last_points'] == 3
                  and made['score_a'] == 3 and made['score_b'] == 0, {'result': result['desc'], 'power': shot_power})
            inbound = made['possession']
            inbound_player = made['players_b'][inbound['player_idx']]
            check('A made basket gives the conceding side the correct inbound', inbound['side'] == 'B'
                  and made['is_player_a'] is False and made['ball']['x'] == inbound_player['x']
                  and made['ball']['y'] == inbound_player['y'])
            page.wait_for_function('!window.__basketballQA.busy() && window.__basketballQA.state().is_player_a', timeout=30000)
            after_throw = context.request.get(base + '/state').json()
            check('Throw playback, points HUD and the AI reply finish before human input returns',
                  after_throw['kick_count'] == 2 and page.locator('#score-a').inner_text() == '3'
                  and page.locator('#kick-btn').is_enabled())
            page.locator('#fs-wrap').screenshot(path=str(output / 'basket-desktop.png'))
            current_carrier = after_throw['possession']['player_idx'] if (after_throw.get('possession') or {}).get('side') == 'A' else -1
            offball = next(index for index in range(5) if index != current_carrier)
            page.locator('#player-select').select_option(str(offball))
            page.locator('#pitch-container canvas').scroll_into_view_if_needed()
            disc = after_throw['players_a'][offball]
            origin = page.evaluate('(p)=>window.__basketballQA.point(p.x,p.y)', disc)
            pull = page.evaluate('(p)=>window.__basketballQA.point(p.x,p.y+70)', disc)
            with page.expect_response(lambda reply: reply.url.endswith('/move')) as moved:
                page.mouse.move(origin['x'], origin['y'])
                page.mouse.down()
                page.mouse.move(pull['x'], pull['y'], steps=6)
                page.mouse.up()
            check('Dragging an off-ball basketball player submits its move', moved.value.ok
                  and moved.value.request.post_data_json['player_idx'] == offball, moved.value.status)
            page.wait_for_function('!window.__basketballQA.busy() && window.__basketballQA.state().is_player_a', timeout=30000)
            after = context.request.get(base + '/state').json()
            check('Human and AI turns complete and return control', after['kick_count'] == 4
                  and page.locator('#kick-btn').is_enabled())
            page.locator('#fs-wrap').screenshot(path=str(output / 'court-desktop.png'))
            replay = context.new_page()
            replay.on('pageerror', lambda error: report['errors'].append(str(error)))
            replay.goto(base + '/__basketball/replay')
            replay.wait_for_function('window.__basketballReplayQA?.ready()')
            check('Real basketball history opens the Canvas court replay',
                  replay.evaluate("window.__pitch2D().sport==='basketball'")
                  and 'basketball' in replay.locator('#pitch-container canvas').get_attribute('aria-label').lower()
                  and replay.locator('#score-a').inner_text() == '0')
            replay.locator('#btn-next').click()
            replay.wait_for_function('!window.__basketballReplayQA.busy() && document.querySelector("#kick-count").textContent==="1"')
            replay_state = replay.evaluate('window.__basketballReplayQA.state()')
            check('Playing the made-basket replay adds three points exactly once',
                  replay_state['score_a'] == 3 and replay_state['score_b'] == 0
                  and replay.locator('#score-a').inner_text() == '3'
                  and replay.evaluate('window.__pitch2D().last_points') == 3)
            check('Replay applies the recorded basketball inbound instead of a soccer kickoff',
                  replay_state['possession']['side'] == 'B' and replay_state['ball'] == made['ball']
                  and replay_state['players_b'][4]['x'] == made['players_b'][4]['x'])
            replay.locator('.glass-card').screenshot(path=str(output / 'replay-desktop.png'))
            replay.evaluate('window.__basketballReplayQA.seek(0)')
            replay_start = replay.evaluate('window.__basketballReplayQA.state()')
            check('Seeking before the basket resets its points and initial possession',
                  replay.locator('#score-a').inner_text() == '0' and replay_start['score_a'] == 0
                  and replay_start['possession']['side'] == 'A' and replay_start['ball'] == state['ball'])
            replay.evaluate('window.__basketballReplayQA.seek(1)')
            check('Seeking after the basket restores its three points and inbound',
                  replay.locator('#score-a').inner_text() == '3'
                  and replay.evaluate('window.__basketballReplayQA.state().ball') == made['ball'])
            replay.close()
            page.bring_to_front()
            # Finish the actual match through the visible human controls and
            # ordinary AI replies, without injecting a winning state.
            for _ in range(40):
                live = context.request.get(base + '/state').json()
                if live['game_over']:
                    break
                index, angle, power = basketball_ai_move(live, True)
                page.locator('#player-select').select_option(str(index))
                page.locator('#aim-angle').fill(str(angle))
                page.locator('#kick-power').evaluate('(element,value)=>element.value=String(value)', round(power))
                page.locator('#kick-power').dispatch_event('input')
                with page.expect_response(lambda reply: reply.url.endswith('/move')):
                    page.locator('#kick-btn').click()
                page.wait_for_function('!window.__basketballQA.busy() && (window.__basketballQA.state().is_player_a || window.__basketballQA.state().game_over)', timeout=30000)
            final = context.request.get(base + '/state').json()
            check('A real match reaches 21 with a two-point winning margin', final['game_over']
                  and max(final['score_a'], final['score_b']) >= 21
                  and abs(final['score_a'] - final['score_b']) >= 2,
                  {'turns': final['kick_count'], 'score': [final['score_a'], final['score_b']]})
            check('The real basketball result shows the winner and hides soccer ratings',
                  page.locator('#score-a').inner_text() == str(final['score_a'])
                  and page.locator('#winner-banner').is_visible()
                  and page.locator('#player-ratings-btn').is_hidden()
                  and page.locator('#kick-btn').is_disabled())
            page.locator('#fs-wrap').screenshot(path=str(output / 'finished-desktop.png'))
            history = context.new_page()
            history.on('pageerror', lambda error: report['errors'].append(str(error)))
            history.goto(base + '/match-history')
            saved_replay = history.locator('a[href^="/history/watch/"]').first
            check('Completed basketball is available from match history', saved_replay.count() == 1
                  and 'basketball' in history.locator('main').inner_text().lower(),
                  {'url': history.url, 'links': saved_replay.count(), 'text': history.locator('main').inner_text()})
            saved_replay.click()
            history.wait_for_function('window.__basketballReplayQA?.ready()')
            history.evaluate('(turns)=>window.__basketballReplayQA.seek(turns)', final['kick_count'])
            check('The persisted replay restores the final points',
                  history.locator('#score-a').inner_text() == str(final['score_a'])
                  and history.locator('#score-b').inner_text() == str(final['score_b']))
            history.close()
            page.bring_to_front()
            reset(page)
            check('New Game preserves the selected sport as a setup draft', basketball.is_checked())
            soccer.check()
            check('Switching back to soccer restores its formation controls', page.locator('input[name="formation-a"]').count() == 4)
            start(page)
            check('Soccer still starts with its clock and striker controls', page.locator('#kick-btn').inner_text() == 'Kick'
                  and page.locator('#player-select option').count() == 1
                  and context.request.get(base + '/state').json().get('sport', 'soccer') == 'soccer')

            mobile_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, reduced_motion='reduce')
            mobile = mobile_context.new_page()
            mobile.on('pageerror', lambda error: report['errors'].append(str(error)))
            ready(mobile, 'mobile')
            mobile.locator('input[name="setup-sport"][value="basketball"]').check()
            sizes = mobile.locator('.setup-sport-option').evaluate_all('items=>items.map(item=>({width:item.getBoundingClientRect().width,height:item.getBoundingClientRect().height}))')
            check('Mobile sport choices have generous touch targets', len(sizes) == 2 and all(size['width'] >= 44 and size['height'] >= 44 for size in sizes), sizes)
            check('Basketball setup fits a phone viewport', mobile.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            mobile.locator('#match-setup').screenshot(path=str(output / 'setup-mobile.png'))
            mobile.locator('#mode-select').select_option('hvh')
            mobile.wait_for_function('window.__basketballQA.state().game_mode === "hvh" && !window.__basketballQA.busy()')
            start(mobile)
            check('Mobile basketball starts with usable controls', mobile.locator('#kick-btn').is_enabled()
                  and mobile.locator('#player-select option').count() == 5)
            mobile.locator('#fs-wrap').screenshot(path=str(output / 'court-mobile.png'))
            with mobile.expect_response(lambda reply: reply.url.endswith('/move')):
                mobile.locator('#kick-btn').click()
            mobile.wait_for_function('!window.__basketballQA.busy() && !window.__basketballQA.state().is_player_a')
            check('Local two-player basketball hands Team B all five players with its own direction',
                  mobile.locator('#player-select option').count() == 5
                  and 'Point guard' in mobile.locator('#player-select').inner_text()
                  and 'Attack left' in mobile.locator('#player-control-status').inner_text()
                  and mobile.locator('#kick-btn').is_enabled())
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
