"""Exercise custom positions through real browser inputs and local HTTP kickoff."""
from pathlib import Path
import json
import logging
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


def main():
    isolated_environment()
    from flask import session,redirect
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import delete_game

    @application.app.route('/__lineup/login/<name>')
    def login(name):
        session.update(user_id='dev:lineup-'+name,username=name)
        delete_game(session['user_id'])
        return redirect('/play2d')

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server=make_server('127.0.0.1',0,application.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    output=Path(tempfile.gettempdir())/'agent-soccer-lineup';output.mkdir(exist_ok=True)
    checks=[];errors=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        print(('PASS ' if value else 'FAIL ')+name,flush=True)
        assert value,name
    def ready(page):page.wait_for_function("!document.querySelector('#match-start-btn').disabled")
    def state(page):return page.request.get(base+'/state').json()
    def editor(page,side='a'):return page.locator(f'#match-setup-team-{side} .lineup-editor')
    def open_editor(page,side='a'):
        ready(page);editor(page,side).locator('summary').click()
    def select(page,index,side='a'):editor(page,side).locator(f'[data-player="{index}"]').click()
    def tap_position(page,x,y):
        svg=editor(page).locator('svg');svg.scroll_into_view_if_needed();box=svg.bounding_box()
        page.mouse.click(box['x']+x*2*box['width'],box['y']+y*box['height'])
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/__lineup/login/desktop');open_editor(page)
            check('three role groups show capacity',editor(page).locator('h4').all_text_contents()==['Defenders · 2/2','Midfielders · 1/2','Attackers · 1/2'])
            check('keeper is separate from four selectable outfield players',editor(page).locator('[data-player]').count()==4)
            check('default defender behavior is explained', 'Protect your own half' in editor(page).locator('.lineup-behavior').inner_text())
            select(page,3)
            check('midfield behavior follows the selected role', 'midfield passing option' in editor(page).locator('.lineup-behavior').inner_text())
            check('full defender group cannot accept a third player',editor(page).locator('option[value=defender]').evaluate('(option)=>option.disabled'))
            select(page,1);editor(page).locator('select').select_option('attacker')
            check('changing roles updates default attacker behavior', 'Stay available upfield' in editor(page).locator('.lineup-behavior').inner_text())
            check('moving a player updates both group counts','Defenders · 1/2' in editor(page).inner_text() and 'Attackers · 2/2' in editor(page).inner_text())
            select(page,4);editor(page).locator('select').select_option('defender')
            check('manual player can choose any available role',editor(page).locator('select').input_value()=='defender')
            # Real pointer drag: #5 now starts at x=.175, y=.35 (free slot).
            svg=editor(page).locator('svg');svg.scroll_into_view_if_needed();box=svg.bounding_box()
            selected=svg.locator('circle').nth(4)
            x=float(selected.get_attribute('cx'))/700;y=float(selected.get_attribute('cy'))/875
            page.mouse.move(box['x']+x*box['width'],box['y']+y*box['height']);page.mouse.down()
            page.mouse.move(box['x']+.4*box['width'],box['y']+.2*box['height'],steps=12);page.mouse.up()
            check('drag changes the selected starting position',abs(float(svg.locator('circle').nth(4).get_attribute('cx'))-280)<1)
            check('position edits stay local until kickoff',state(page).get('custom_lineup_a') is None)
            before=svg.locator('circle').nth(4).get_attribute('cy')
            svg.focus();page.keyboard.press('ArrowDown')
            check('keyboard can move a player without submitting kickoff',float(svg.locator('circle').nth(4).get_attribute('cy'))>float(before) and state(page)['match_started'] is False)
            # Escape cancels a captured drag.
            box=svg.bounding_box();page.mouse.move(box['x']+.4*box['width'],box['y']+.22*box['height']);page.mouse.down()
            page.mouse.move(box['x']+.4*box['width'],box['y']+.7*box['height'],steps=5);page.keyboard.press('Escape');page.mouse.up()
            check('Escape restores the last committed position',abs(float(svg.locator('circle').nth(4).get_attribute('cy'))-.22*875)<1)
            page.screenshot(path=str(output/'desktop.png'),full_page=True)
            for width in (320,390,768,1440):
                page.set_viewport_size({'width':width,'height':1050})
                check(f'custom positions fit {width}px',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            page.set_viewport_size({'width':390,'height':1000});page.screenshot(path=str(output/'mobile.png'),full_page=True)
            with page.expect_response('**/start_match') as result:page.locator('#match-start-btn').click()
            saved=result.value.json()
            check('kickoff commits custom positions and stable AI roles',result.value.status==200 and saved['player_roles_a'][1]=='attacker' and saved['player_roles_a'][4]=='defender' and abs(saved['players_a'][4]['x']-280)<1)
            check('manual player remains number five',saved['human_player_idx']==4)
            check('late position changes are rejected',page.request.post(base+'/start_match',data={'custom_lineup_a':saved['custom_lineup_a']}).status==409)
            page.reload();check('reload retains the custom lineup',state(page)['custom_lineup_a']==saved['custom_lineup_a'])
            page.request.post(base+'/reset',data={});page.reload();open_editor(page);select(page,4)
            check('New Game restores editable role assignments',editor(page).locator('select').input_value()=='defender')
            editor(page).get_by_role('button',name='Use formation preset').click()
            check('preset restores its default role groups',editor(page).locator('h4').all_text_contents()==['Defenders · 2/2','Midfielders · 1/2','Attackers · 1/2'])
            page.locator('#mode-select').select_option('aivai');ready(page)
            check('AI versus AI can position both teams',page.locator('.lineup-editor').count()==2)
            open_editor(page,'b');select(page,1,'b');editor(page,'b').locator('select').select_option('attacker')
            with page.expect_response('**/start_match') as result:page.locator('#match-start-btn').click()
            saved_b=result.value.json()
            check('team B positions are mirrored on the real pitch',abs(saved_b['players_b'][1]['x']-(1-saved_b['custom_lineup_b'][1]['x'])*1400)<1)
            touch_context=browser.new_context(viewport={'width':390,'height':1000},has_touch=True,is_mobile=True)
            touch=touch_context.new_page();touch.on('pageerror',lambda error:errors.append(str(error)))
            touch.goto(base+'/__lineup/login/touch');open_editor(touch)
            svg=editor(touch).locator('svg');svg.scroll_into_view_if_needed();box=svg.bounding_box()
            touch.touchscreen.tap(box['x']+.4*box['width'],box['y']+.2*box['height'])
            with touch.expect_response('**/start_match') as result:touch.locator('#match-start-btn').click()
            saved_touch=result.value.json()
            check('touch positioning saves at kickoff',result.value.status==200 and abs(saved_touch['custom_lineup_a'][1]['y']-.2)<.01)
            check('no browser exceptions',not errors)
            browser.close()
    finally:
        server.shutdown();(output/'report.json').write_text(json.dumps({'checks':checks,'errors':errors},indent=2),encoding='utf-8')
    print(f'{len(checks)} checks passed. Artifacts: {output}')


if __name__=='__main__':main()
