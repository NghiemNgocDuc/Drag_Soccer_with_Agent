"""Render a reviewable soccer-only UI draft without deleting game code.

Template overrides and the soccer-only catalog are confined to this isolated
preview process. Production routes, game files, and Git remotes are untouched.
"""
from pathlib import Path
import json
import logging
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


def drafts():
    read=lambda name:(ROOT/'templates'/name).read_text(encoding='utf-8')
    nav=read('shared/_ui.html')
    nav=nav.replace("('/packs','Make a game'),",'')
    nav=nav.replace("('workshop','Workshop'", "('workshop','Soccer AI'")

    hub=read('hubs/hub_play.html')
    start=hub.index('<div class="play-hub-section-heading">')
    end=hub.index('</div><div class="as-callout',start)
    hub=hub[:start]+'''<div class="play-hub-section-heading"><div><p>YOUR NEXT MOVE</p><h2>One pitch. Your game.</h2></div><span>Play, plan, and compete.</span></div>
<div class="as-grid play-hub-grid" aria-label="Soccer match options">
<a href="/play2d" class="as-destination play-destination-primary">{{ icon('ball') }}<span class="play-card-index">01 / PLAY</span><h2>Play soccer</h2><p>Five players. One ball. Take on a built-in AI opponent or challenge a friend.</p><span>Start a match {{ icon('arrow-right',16) }}</span></a>
<a href="/play2d#match-setup" class="as-destination">{{ icon('diagram') }}<span class="play-card-index">02 / PLAN</span><h2>Your team. Your tactics.</h2><p>Place your defenders, midfielders and attackers. Draw their runs, then watch your plan unfold.</p><span>Set your game plan {{ icon('arrow-right',16) }}</span></a>
<a href="/spectate" class="as-destination">{{ icon('eye') }}<span class="play-card-index">03 / WATCH</span><h2>Watch the next move</h2><p>Follow live soccer matches and see how other teams find space and create chances.</p><span>Watch live {{ icon('arrow-right',16) }}</span></a>
{% for href,glyph,title,description,action in [('/lobby','users','Your locker room','Prepare your player build and bring your squad together.','Get match ready'),('/hub/compete','trophy','Play for the win','Compete in soccer tournaments and climb the rankings.','Explore competition'),('/hub/workshop','code','Build your soccer AI','Code a soccer agent, test its decisions, and improve your team.','Open the soccer workshop')] %}
<a href="{{ href }}" class="as-destination">{{ icon(glyph) }}<h2>{{ title }}</h2><p>{{ description }}</p><span>{{ action }} {{ icon('arrow-right',16) }}</span></a>{% endfor %}
'''+hub[end:]
    game=read('game/index_2d.html').replace('Five-a-side soccer and basketball, with every shot resolved by physics.','Five-a-side soccer. Set your shape, draw your runs, and make every kick count.').replace('Choose your sport, then get your team ready.','Choose your formation, assign your roles, and get your team ready.')
    game=game.replace('<fieldset class="setup-sport-picker">','<fieldset class="setup-sport-picker" hidden>')
    game=game.replace('</head>','<style>.setup-sport-picker[hidden]{display:none!important}</style></head>')
    return {'shared/_ui.html':nav,'hubs/hub_play.html':hub,'game/index_2d.html':game}


def main():
    isolated_environment()
    from flask import session,redirect,request
    from jinja2 import ChoiceLoader,DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import delete_game
    output=ROOT/'docs'/'previews'/'soccer-only'
    output.mkdir(parents=True,exist_ok=True)
    proposed=drafts()
    for name,source in proposed.items():
        target=output/'templates'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source,encoding='utf-8')
    application.app.jinja_loader=ChoiceLoader([DictLoader(proposed),application.app.jinja_loader])

    @application.app.route('/__soccer_preview/login')
    def login():
        session.update(user_id='dev:soccer-preview',username='Coach')
        delete_game(session['user_id'])
        return redirect('/hub/play')

    @application.app.after_request
    def soccer_catalog(response):
        if request.path=='/match_setup' and response.is_json:
            data=response.get_json();data['sports']=[sport for sport in data.get('sports',[]) if sport['id']=='soccer']
            response.set_data(json.dumps(data))
        return response

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server=make_server('127.0.0.1',0,application.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1,reduced_motion='reduce')
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/__soccer_preview/login');page.evaluate('document.fonts.ready')
            page.screenshot(path=str(output/'01-soccer-home.png'),full_page=True)
            assert page.locator('a[href="/packs"],a[href="/metro-motion/"]').count()==0
            page.goto(base+'/play2d');page.wait_for_function("!document.querySelector('#match-start-btn').disabled")
            assert not page.locator('.setup-sport-picker').is_visible()
            page.screenshot(path=str(output/'02-match-setup.png'),full_page=True)
            page.locator('.lineup-editor summary').click()
            page.locator('.lineup-editor').screenshot(path=str(output/'03-player-roles.png'))
            page.locator('.lineup-editor summary').click()
            page.locator('.tactics-board summary').click()
            svg=page.locator('#match-setup .tactics-pitch');svg.scroll_into_view_if_needed();box=svg.bounding_box()
            page.mouse.click(box['x']+.3*box['width'],box['y']+.25*box['height'])
            page.locator('#match-setup .tactics-board').screenshot(path=str(output/'04-drawn-tactics.png'))
            page.locator('#match-start-btn').click();page.wait_for_function("document.querySelector('#match-setup').hidden")
            page.evaluate('scrollTo(0,0)');page.screenshot(path=str(output/'05-soccer-match.png'),full_page=True)
            page.locator('.matchday-stage').screenshot(path=str(output/'07-player-redesign.png'))
            page.goto(base+'/hub/play');page.set_viewport_size({'width':390,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(output/'06-mobile-home.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown()
    (output/'README.md').write_text('# Soccer-only UI preview\n\nReview draft only. The template overrides run in an isolated local server. No game files have been deleted and nothing has been pushed.\n\nThis draft removes basketball, Metro Motion and the general game builder from navigation while retaining the soccer AI workshop and built-in soccer opponents, team customization, roles, drawn tactics, online matches, spectating, and competition.\n\nRun `python tools/browser/preview_soccer_only.py` to regenerate the screenshots.\n',encoding='utf-8')
    print(f'Preview rendered with no browser exceptions: {output}')


if __name__=='__main__':main()
