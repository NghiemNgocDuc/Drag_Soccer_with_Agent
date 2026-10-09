"""Exercise the served Canvas views without WebGL or external integrations.

Run python tools/browser/verify_2d.py. Screenshots/report go to the system temp
directory, under agent-soccer-2d. Diagnostics are injected only in this server.
"""
from __future__ import annotations

import copy
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
window.__qa2D={
 ready:()=>typeof gameState!=='undefined'?!!gameState:state.players_a.length>0,
 active:()=>typeof gameState!=='undefined'?gameState:state,
 busy:()=>isAnimating,
 revision:()=>typeof gameState!=='undefined'?gameState.kick_count:LIVE.lastKick,
 point:(side,index)=>{const s=window.__qa2D.active(),p=s['players_'+side][index];return pitch.clientPoint(p.x,p.y)},
 seek:target=>seekToEntry(target),
 online:()=>typeof ONLINE!=='undefined'?ONLINE:null,
 invalidate:()=>pitch.loop.invalidate(),
};
"""


def main():
    isolated_environment()
    from flask import redirect, render_template, request, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import apply_kick, new_soccer_state, _setup_penalty_positions

    templates = {}
    for name in ('game/index_2d.html', 'game/replay_2d.html'):
        source = (ROOT / 'templates' / name).read_text(encoding='utf-8')
        templates[name] = re.sub(r'(<script type="module">)(.*?)(</script>)',
                                lambda m: m[1] + m[2] + DIAGNOSTICS + m[3], source, flags=re.S)
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates), application.app.jinja_loader])
    seed = new_soccer_state(mode='hvh', player_count=7, half_length=9999)
    replay_seed = copy.deepcopy(seed)
    trajectory, scored, description, endpoint, _ = apply_kick(replay_seed, 6, 0, 100, True)
    move = {'trajectory': trajectory, 'scored': scored, 'desc': description,
            'kick_endpoint': endpoint, 'mover': 'a', 'player_idx': 6, 'angle': 0, 'power': 100}
    clip = {'id': 'qa', 'type': 'fast', 'label': 'Opening move', 'start': 0, 'end': 1}

    @application.app.route('/__2d/login/<name>')
    def login(name):
        session['user_id'] = 'dev:canvas-' + name
        session['username'] = name
        game = new_soccer_state(mode='hvh', player_count=int(request.args.get('count', 7)), half_length=9999)
        game['game_mode'] = request.args.get('mode', 'hvh')
        if request.args.get('penalty'):
            game['penalty_shootout'] = True
            _setup_penalty_positions(game, True)
        save_game(session['user_id'], game)
        return redirect('/play2d')

    @application.app.route('/__2d/replay')
    def replay():
        return render_template('game/replay_2d.html', username='QA', t={'id': 'qa', 'name': 'QA'},
                               match={'id': 'qa', 'participant_a': 'A', 'participant_b': 'B',
                                      'replay_data': [{'trajectory': []}, move]},
                               highlights=[clip], highlight=clip if request.args.get('clip') else None,
                               live_room=None, loss_model=None, loss_model_name=None)

    @application.app.route('/__2d/loss')
    def loss():
        return render_template('game/replay_2d.html', username='QA', t=None, match=None,
                               highlights=[], highlight=None, live_room=None,
                               loss_model='qa', loss_model_name='QA model',
                               loss_tag_meta={'neutral': {'cls': '', 'emoji': '', 'label': 'Neutral', 'description': 'Fixture decision'}})

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    out = Path(tempfile.gettempdir()) / 'agent-soccer-2d'
    out.mkdir(exist_ok=True)
    checks, errors, resources = [], [], []

    def check(name, condition, detail=None):
        checks.append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(f'{name}: {detail}')

    def page_for(context):
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: resources.append(req.url))
        return page

    def ready(page):
        page.wait_for_function('window.__qa2D?.ready()')
        page.locator('#pitch-container').scroll_into_view_if_needed()

    def idle(page):
        page.wait_for_function('!window.__qa2D.busy()')
        page.wait_for_timeout(1500)
        before = page.evaluate('window.__pitch2D()')
        page.wait_for_timeout(350)
        after = page.evaluate('window.__pitch2D()')
        check('idle rendering sleeps', after['frames'] == before['frames'], [before['frames'], after['frames']])
        check('cached pitch stays cached', after['backgroundBuilds'] == before['backgroundBuilds'])

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--disable-webgl'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1100})
            page = page_for(context)
            page.goto(base + '/__2d/login/desktop')
            ready(page)
            check('Canvas renders 7v7', page.evaluate('window.__pitch2D().players_a.length===7 && window.__pitch2D().width>0'))
            check('no horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            idle(page)

            page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));window.__qa2D.invalidate()")
            before_frames = page.evaluate('window.__pitch2D().frames')
            page.wait_for_timeout(350)
            check('hidden canvas stops drawing', page.evaluate('window.__pitch2D().frames') == before_frames)
            page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function('(n)=>window.__pitch2D().frames>n', arg=before_frames)
            check('visible canvas resumes drawing', True)
            page.screenshot(path=str(out / 'desktop.png'), full_page=True)
            point = page.evaluate("window.__qa2D.point('a',6)")
            page.mouse.click(point['x'], point['y'])
            check('click selects player', page.evaluate('window.__playerControlState().selectedIndex===6'))
            before = page.evaluate('window.__qa2D.active().kick_count')
            page.mouse.move(point['x'], point['y'])
            page.mouse.down()
            page.mouse.move(point['x'] - 80, point['y'], steps=5)
            page.mouse.up()
            page.wait_for_function('(n)=>!window.__qa2D.busy() && window.__qa2D.active().kick_count>n', arg=before)
            check('drag performs real move', page.evaluate('!window.__qa2D.active().is_player_a'))
            authoritative = context.request.get(base + '/state').json()
            check('live ball reaches authoritative result', page.evaluate('window.__pitch2D().ball.x') == authoritative['ball']['x'])
            page.locator('#pitch-container canvas').focus()
            page.keyboard.press('q')
            page.keyboard.press('ArrowUp')
            page.keyboard.press('Enter')
            page.wait_for_function('(n)=>!window.__qa2D.busy() && window.__qa2D.active().kick_count>n', arg=before + 1)
            check('keyboard plays opposite team', page.evaluate('window.__qa2D.active().is_player_a'))
            page.evaluate("window.setGraphicsQuality('4k')")
            check('4K resolution available', page.evaluate('Math.max(window.__pitch2D().width,window.__pitch2D().height)===3840'))
            page.evaluate("window.setGraphicsQuality('auto')")
            idle(page)

            page.goto(base + '/__2d/login/ai?mode=hvai')
            ready(page)
            before = page.evaluate('window.__qa2D.active().kick_count')
            page.locator('#pitch-container canvas').focus()
            page.keyboard.press('Enter')
            page.wait_for_function('(n)=>!window.__qa2D.busy() && window.__qa2D.active().kick_count>=n+4', arg=before, timeout=40000)
            check('human and three AI legs finish', page.evaluate('window.__qa2D.active().is_player_a'))

            replay_page = page_for(context)
            replay_page.goto(base + '/__2d/replay')
            ready(replay_page)
            replay_page.evaluate('window.__qa2D.seek(0)')
            check('seeking start retains roster', replay_page.evaluate('window.__pitch2D().players_a.length===7'))
            replay_page.evaluate('window.toggleAutoPlay()')
            replay_page.wait_for_function('window.__hlState().current===2 && !window.__qa2D.busy()')
            last = move['trajectory'][-1]
            check('replay reaches physics endpoint', replay_page.evaluate('window.__pitch2D().ball.x') == last['x'])
            check('full replay stops autoplay at the end', replay_page.evaluate('!window.__hlState().auto') and replay_page.locator('#btn-next').is_disabled())
            idle(replay_page)
            replay_page.screenshot(path=str(out / 'replay.png'), full_page=True)
            replay_page.goto(base + '/__2d/replay?clip=1')
            ready(replay_page)
            replay_page.wait_for_function('window.__hlState().current===2 && !window.__hlState().auto')
            check('highlight clip finishes', 'clip finished' in replay_page.locator('#hl-banner').inner_text())

            loss_page = page_for(context)
            fixture_match = {'match_id': 'match', 'opponent': 'B', 'score_for': 0, 'score_against': 1, 'result': 'loss', 'turn_count': 1}
            def loss_data(route):
                path = route.request.url.split('/api/loss/models/qa')[-1]
                data = {'n_matches': 0}
                if path == '/matches':
                    data = {'matches': [fixture_match]}
                elif path == '/matches/match':
                    data = {'match': fixture_match, 'traces': [{'turn': 0, 'mover': 'a', 'outcome_tag': 'neutral', 'state': seed,
                            'decision': {'player_idx': 6, 'angle': 0, 'power': 100}}]}
                elif path.endswith('/playback'):
                    data = move
                route.fulfill(json=data)
            loss_page.route('**/api/loss/models/qa/**', loss_data)
            loss_page.goto(base + '/__2d/loss')
            ready(loss_page)
            check('loss review renders traced squad', loss_page.evaluate('window.__lossState().tracedTurns===1 && window.__pitch2D().players_a.length===7'))
            loss_page.evaluate('window.playLossTurn()')
            loss_page.wait_for_function('window.__qa2D.busy()')
            loss_page.wait_for_function('!window.__qa2D.busy()')
            check('loss playback restores decision snapshot', loss_page.evaluate('window.__pitch2D().ball.x') == seed['ball']['x'])

            opponent = browser.new_context(viewport={'width': 1440, 'height': 1100})
            opponent_page = page_for(opponent)
            opponent_page.goto(base + '/__2d/login/opponent')
            ready(opponent_page)
            response = context.request.post(base + '/online/create', data={'player_count': 7})
            check('online room created', response.ok)
            room = response.json()['room_id']
            page.goto(base + '/play2d?room=' + room)
            opponent_page.goto(base + '/play2d?room=' + room)
            ready(page)
            ready(opponent_page)
            page.wait_for_function("window.__qa2D.online().status==='active' && window.__qa2D.online().ready")
            spectator = browser.new_context(viewport={'width': 1440, 'height': 1100})
            spectator_page = page_for(spectator)
            spectator_page.goto(base + '/spectate/' + room)
            ready(spectator_page)
            check('guest spectator gets full roster', spectator_page.evaluate('window.__pitch2D().players_a.length===7'))
            check('spectator has no kick control', spectator_page.locator('#kick-btn').count() == 0)
            page.locator('#pitch-container canvas').focus()
            page.keyboard.press('Enter')
            for target in [page, opponent_page, spectator_page]:
                target.wait_for_function("!window.__qa2D.busy() && window.__qa2D.revision()>=1", timeout=20000)
            authoritative = context.request.get(base + '/online/' + room + '/state').json()['game']
            for target in [page, opponent_page, spectator_page]:
                check('online view matches authoritative ball', target.evaluate('window.__pitch2D().ball.x') == authoritative['ball']['x'])
            opponent_page.locator('#pitch-container canvas').focus()
            opponent_page.keyboard.press('Enter')
            for target in [page, opponent_page, spectator_page]:
                target.wait_for_function("!window.__qa2D.busy() && window.__qa2D.revision()>=2", timeout=20000)
            check('opponent controls work', page.evaluate('window.__qa2D.active().is_player_a'))
            idle(spectator_page)

            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
            mobile_page = page_for(mobile)
            mobile_page.goto(base + '/__2d/login/mobile')
            ready(mobile_page)
            point = mobile_page.evaluate("window.__qa2D.point('a',6)")
            mobile_page.touchscreen.tap(point['x'], point['y'])
            check('touch selects player', mobile_page.evaluate('window.__playerControlState().selectedIndex===6'))
            check('mobile fits viewport', mobile_page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            mobile_page.screenshot(path=str(out / 'mobile.png'), full_page=True)
            mobile_page.goto(base + '/__2d/login/mobile?count=11')
            ready(mobile_page)
            check('11v11 roster renders', mobile_page.evaluate('window.__pitch2D().players_a.length===11 && window.__pitch2D().players_b.length===11'))
            page.goto(base + '/__2d/login/penalty?penalty=1')
            ready(page)
            check('penalty referee stays hidden', page.evaluate('window.__pitch2D().referee===null'))
            check('penalty restricts selection to kicker', page.evaluate('JSON.stringify(window.__playerControlState().allowedIndices)==="[0]"'))
            page.locator('#view-settings-btn').click()
            check('resolution dialog opens', page.locator('#view-settings-modal').is_visible())
            page.keyboard.press('Escape')
            check('resolution dialog closes', not page.locator('#view-settings-modal').is_visible())
            page.locator('#sound-btn').click()
            check('mute control works', page.evaluate('SoundManager.isMuted()'))
            check('no WebGL assets fetched', not any('/vendor/three' in url or '/textures/' in url for url in resources))
            check('zero JavaScript exceptions', not errors, errors)
            browser.close()
    finally:
        server.shutdown()
        (out / 'report.json').write_text(json.dumps({'checks': checks, 'errors': errors}, indent=2), encoding='utf-8')
        print(json.dumps({'passed': sum(c['passed'] for c in checks), 'checks': len(checks), 'errors': errors, 'artifacts': str(out)}))


if __name__ == '__main__':
    main()
