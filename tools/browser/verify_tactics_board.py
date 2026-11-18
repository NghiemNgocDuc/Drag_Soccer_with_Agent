"""Real browser drawing and physical soccer movement, on an isolated server."""
from __future__ import annotations
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


def main():
    isolated_environment()
    from flask import session, redirect
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import delete_game

    @application.app.route('/__tactics/login/<name>')
    def login(name):
        session.update(user_id='dev:tactics-'+name,username=name)
        delete_game(session['user_id'])
        return redirect('/play2d')

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server=make_server('127.0.0.1',0,application.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    output=Path(tempfile.gettempdir())/'agent-soccer-tactics'
    output.mkdir(exist_ok=True)
    checks=[];errors=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        print(('PASS ' if value else 'FAIL ')+name,flush=True)
        assert value,name
    def state(page):return page.request.get(base+'/state').json()
    def ready(page):page.wait_for_function("document.querySelector('#match-start-btn') && !document.querySelector('#match-start-btn').disabled")
    def board(page):return page.locator('#match-setup-team-a .tactics-board')
    def open_board(page):
        ready(page);board(page).locator('summary').click()
        board(page).locator('svg').scroll_into_view_if_needed()
    def drag(page,index,target):
        b=board(page);svg=b.locator('svg');svg.scroll_into_view_if_needed()
        box=svg.bounding_box()
        formation=page.request.get(base+'/match_setup').json()['formations'][0]
        start=formation['positions'][index]
        x,y=box['x']+start['x']*box['width'],box['y']+start['y']*box['height']
        page.mouse.move(x,y);page.mouse.down()
        page.mouse.move(box['x']+target[0]*box['width'],box['y']+target[1]*box['height'],steps=24)
        page.mouse.up()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True,args=['--enable-unsafe-swiftshader'])
            context=browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/__tactics/login/desktop');open_board(page)
            check('five numbered players are available',board(page).locator('.tactics-roster button').count()==5)
            check('automatic mode explains the selected defender job', 'Protect your own half' in board(page).locator('.tactics-status').inner_text())
            before=state(page)
            drag(page,1,(.162,.16))
            check('freehand drawing creates a visible run',board(page).locator('.tactics-status').inner_text().startswith('#2:') and 'waypoints' in board(page).locator('.tactics-status').inner_text())
            check('drawing is a local draft',state(page).get('player_plans_a') is None and state(page)['match_started'] is False)
            board(page).get_by_role('button',name='#3 Defender',exact=True).click()
            svg=board(page).locator('svg');svg.scroll_into_view_if_needed();box=svg.bounding_box()
            page.mouse.move(box['x']+.162*box['width'],box['y']+(482.5/875)*box['height'])
            page.mouse.down();page.mouse.move(box['x']+.3*box['width'],box['y']+.7*box['height'],steps=5)
            page.keyboard.press('Escape');page.mouse.up()
            check('Escape discards a partial drawing',board(page).locator('.tactics-roster button').nth(2).get_attribute('data-mode')=='auto')
            board(page).get_by_role('button',name='#2 Defender',exact=True).click()
            page.screenshot(path=str(output/'board-desktop.png'),full_page=True)
            board(page).get_by_role('button',name='#5 You',exact=True).click()
            check('human striker remains manual',board(page).locator('select').is_disabled() and 'your kicks' in board(page).locator('.tactics-status').inner_text())
            board(page).get_by_role('button',name='#1 Keeper',exact=True).click()
            board(page).locator('select').select_option('hold')
            svg=board(page).locator('svg');svg.focus()
            for _ in range(12):page.keyboard.press('ArrowRight')
            page.keyboard.press('Enter')
            check('keyboard creates a bounded keeper hold', 'hold the marked' in board(page).locator('.tactics-status').inner_text())
            # Pointer cancellation must discard a partial new route.
            board(page).get_by_role('button',name='#3 Defender',exact=True).click()
            svg.focus()
            page.keyboard.press('ArrowRight');page.keyboard.press('Enter')
            check('keyboard waypoint does not submit kickoff',state(page)['match_started'] is False and 'waypoints' in board(page).locator('.tactics-status').inner_text())
            board(page).get_by_role('button',name='Clear player',exact=True).click()
            with page.expect_response('**/start_match') as started:
                page.locator('#match-start-btn').click()
            payload=started.value.json()
            check('kickoff saves the drawn route',started.value.status==200 and payload['player_plans_a'][1]['mode']=='route')
            check('keeper hold stays in its legal area',payload['player_plans_a'][0]['points'][0]['x']<=.14)
            check('manual striker instructions are automatic',payload['player_plans_a'][4]['mode']=='auto')
            page.wait_for_function("document.querySelector('#match-setup').hidden")
            check('editor disappears after kickoff',not page.locator('#match-setup').is_visible())
            page.locator('#match-plan-review summary').click()
            check('saved routes remain visible without editable controls',page.locator('#match-plan-review svg').is_visible() and not page.locator('#match-plan-review select').is_visible())
            check('late changes are rejected by the server',page.request.post(base+'/start_match',data={'formation_a':'2-1-1','playstyle_a':3,'player_plans_a':[]}).status==409)
            # Exercise the real move endpoint: human striker misses away from
            # the drawn defender, whose off-ball run is recorded in trajectory.
            before=state(page)
            result=page.request.post(base+'/move',data={'player_idx':4,'angle':270,'power':10})
            after=result.json()
            check('drawn defender moves in actual soccer physics',result.status==200 and after['players_a'][1]['y']<before['players_a'][1]['y']-25)
            undo=page.request.post(base+'/undo',data={}).json()
            check('undo restores route progress',undo['player_plan_progress_a']==before['player_plan_progress_a'])
            page.reload();page.wait_for_selector('#match-plan-review summary')
            check('reload retains the saved instructions',state(page)['player_plans_a']==payload['player_plans_a'])
            page.request.post(base+'/reset',data={});page.reload();open_board(page)
            check('New Game retains editable routes', 'route' in page.locator('#match-setup-team-a .tactics-board .tactics-roster button').nth(1).get_attribute('data-mode'))
            for width in (320,390,768,1440):
                page.set_viewport_size({'width':width,'height':1050});page.wait_for_timeout(80)
                check(f'tactics fit {width}px',page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
            page.set_viewport_size({'width':390,'height':1000})
            page.screenshot(path=str(output/'board-mobile.png'),full_page=True)
            # Sport and mode switches must not leak soccer plans into basketball.
            page.locator('input[name=setup-sport][value=basketball]').check()
            check('basketball has no soccer drawing board',page.locator('#match-setup .tactics-board').count()==0)
            page.locator('input[name=setup-sport][value=soccer]').check()
            check('returning to soccer restores the draft',page.locator('#match-setup-team-a .tactics-board .tactics-roster button').nth(1).get_attribute('data-mode')=='route')
            page.locator('#mode-select').select_option('aivai');ready(page)
            page.wait_for_selector('#match-setup-team-b .tactics-board')
            check('AI versus AI exposes both team boards',page.locator('#match-setup .tactics-board').count()==2)
            page.locator('#match-setup-team-b .tactics-board summary').click()
            page.locator('#match-setup-team-b .tactics-board .tactics-roster button').nth(4).click()
            check('AI striker can receive a drawn instruction',page.locator('#tactics-mode-b').is_enabled())
            touch_context=browser.new_context(viewport={'width':390,'height':1000},has_touch=True,is_mobile=True,reduced_motion='reduce')
            touch=touch_context.new_page();touch.on('pageerror',lambda error:errors.append(str(error)))
            touch.goto(base+'/__tactics/login/touch');open_board(touch)
            box=board(touch).locator('svg').bounding_box()
            touch.touchscreen.tap(box['x']+.3*box['width'],box['y']+.2*box['height'])
            check('touch tap creates a route without starting play', 'waypoints' in board(touch).locator('.tactics-status').inner_text() and state(touch)['match_started'] is False)
            with touch.expect_response('**/start_match') as touch_start:
                touch.locator('#match-start-btn').click()
            check('touch-drawn route is saved at kickoff',touch_start.value.json()['player_plans_a'][1]['mode']=='route')
            touch_context.close()
            check('no browser exceptions',not errors)
            browser.close()
    finally:
        server.shutdown()
        (output/'report.json').write_text(json.dumps({'checks':checks,'errors':errors},indent=2),encoding='utf-8')
    print(f'{len(checks)} checks passed. Artifacts: {output}')

if __name__=='__main__':main()
