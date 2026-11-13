"""Check the served cream/burgundy theme on desktop and mobile.

Run ``python tools/browser/verify_warm_theme.py``. The loopback server uses
isolated in-memory storage. Screenshots and a JSON report are written to
``agent-soccer-warm-theme`` in the system temporary directory. Contrast checks
cover shared tokens and sampled solid-background text, not a full WCAG audit.
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

PUBLIC = ('/', '/login', '/register', '/forgot-password', '/reset-password', '/about', '/workflow', '/spectate')
MEMBER = ('/lobby', '/hub/play', '/hub/workshop', '/hub/compete', '/hub/social', '/hub/learn',
          '/play2d', '/customize', '/my-models', '/playground', '/learn', '/research', '/analytics',
          '/profile', '/match-history', '/achievements', '/leaderboard', '/leaderboard/ranked',
          '/tournaments', '/arena', '/community', '/messages', '/clans', '/feedback', '/loss-analysis',
          '/__warm-theme/replay')
CAPTURE = {'/': 'landing', '/login': 'login', '/lobby': 'lobby', '/hub/play': 'play-hub',
           '/hub/workshop': 'workshop', '/hub/social': 'social', '/hub/compete': 'compete',
           '/profile': 'profile', '/play2d': 'soccer', '/__warm-theme/replay': 'replay'}


def rgb(value):
    if value.startswith('#'):
        value = value.lstrip('#')
        if len(value) == 3:
            value = ''.join(character * 2 for character in value)
        return [int(value[offset:offset + 2], 16) for offset in (0, 2, 4)]
    return [float(number) for number in re.findall(r'[\d.]+', value)[:3]]


def contrast(foreground, background):
    def luminance(color):
        channels = [channel / 255 for channel in rgb(color)]
        linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + .05) / (dark + .05)


PAGE_STATE = r'''() => {
  const root=getComputedStyle(document.documentElement), body=getComputedStyle(document.body);
  const header=document.querySelector('.as-header');
  return {bodyClass:document.body.className,background:body.backgroundColor,
    bg:root.getPropertyValue('--as-bg').trim(),primary:root.getPropertyValue('--as-primary').trim(),
    header:header?getComputedStyle(header).backgroundColor:null,
    width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
    heading:document.querySelector('h1')?.textContent.trim(),title:document.title};
}'''

TEXT_SAMPLES = r'''() => {
  const out=[], parse=value=>(value.match(/[\d.]+/g)||[]).map(Number);
  const composite=(front,back)=>{
    const a=front[3]??1;return [0,1,2].map(i=>Math.round(front[i]*a+back[i]*(1-a)));
  };
  for(const el of document.querySelectorAll('a,button,label,p,span,h1,h2,h3,li,td,th,input,select,textarea')) {
    const cs=getComputedStyle(el), box=el.getBoundingClientRect();
    if(!box.width||!box.height||cs.visibility!=='visible'||Number(cs.opacity)<.7||el.disabled
      ||el.matches('input[type=radio],input[type=checkbox],input[type=range],input[type=color],input[type=hidden],input[type=file]')
      ||el.closest('[aria-hidden="true"],svg,canvas')||!el.innerText?.trim() && !el.matches('input,select,textarea'))continue;
    if(el.children.length&&!Array.from(el.childNodes).some(node=>node.nodeType===3&&node.textContent.trim()))continue;
    let layers=[],skip=false;
    for(let ancestor=el;ancestor;ancestor=ancestor.parentElement){
      const style=getComputedStyle(ancestor);
      if(style.backgroundImage!=='none'){skip=true;break;}
      const color=parse(style.backgroundColor);layers.push(color);
      if((color[3]??1)>=1)break;
    }
    if(skip)continue;
    let bg=[255,255,255];for(const layer of layers.reverse())bg=composite(layer,bg);
    const fg=composite(parse(cs.color),bg),size=parseFloat(cs.fontSize),weight=parseInt(cs.fontWeight)||400;
    out.push({selector:el.id?'#'+el.id:el.tagName.toLowerCase()+'.'+[...el.classList].join('.'),
      text:(el.innerText||el.placeholder||el.value||'').trim().slice(0,90),
      foreground:`rgb(${fg.join(',')})`,background:`rgb(${bg.join(',')})`,
      threshold:size>=24||size>=18.66&&weight>=700?3:4.5});
    if(out.length>=180)break;
  }
  return out;
}'''


def run():
    isolated_environment()
    from flask import redirect, render_template, session
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import get_game, save_game
    from models.soccer_logic import new_soccer_state

    @application.app.route('/__warm-theme/login/<name>')
    def login(name):
        session.update(user_id='dev:warm-theme-' + name, username='Theme QA')
        state = new_soccer_state(mode='hvai', player_count=5, half_length=0)
        state['match_started'] = False
        save_game(session['user_id'], state)
        return redirect('/lobby')

    @application.app.route('/__warm-theme/replay')
    def replay_fixture():
        state = get_game(session['user_id'])
        first = {'x': state['ball']['x'], 'y': state['ball']['y'], 'z': 0, 't': 0,
                 'a': state['players_a'], 'b': state['players_b'], 'ref': state.get('referee')}
        move = {'mover': 'a', 'player_idx': 4, 'angle': 0, 'power': 0, 'scored': None,
                'desc': 'Theme replay fixture', 'trajectory': [first, {**first, 't': .1}]}
        return render_template('game/replay_2d.html', username='Theme QA', t=None,
                               match={'id': 'theme-replay', 'replay_data': [move]}, highlights=[],
                               highlight=None, live_room=None, loss_model=None, loss_model_name=None)

    output = Path(tempfile.gettempdir()) / 'agent-soccer-warm-theme'
    output.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'pages': [], 'errors': [], 'contrast_findings': [], 'contrast_samples': 0, 'artifacts': str(output),
              'scope': 'Shared tokens, rendered pages, selected interactions and sampled solid-background text; not a full WCAG audit.'}
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'

    def check(name, passed, evidence=None):
        report['checks'].append({'name': name, 'passed': bool(passed), 'evidence': evidence})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)

    def sample(page, route, viewport):
        for sample in page.evaluate(TEXT_SAMPLES):
            report['contrast_samples'] += 1
            ratio = contrast(sample['foreground'], sample['background'])
            if ratio + .005 < sample['threshold']:
                report['contrast_findings'].append({'route': route, 'viewport': viewport, **sample, 'ratio': round(ratio, 2)})

    def scores(page, name):
        styles = page.locator('.team-score').evaluate_all('''nodes=>nodes.map(el=>{
          const cs=getComputedStyle(el);return {id:el.id,color:cs.color,fill:cs.webkitTextFillColor,background:cs.backgroundImage};
        })''')
        check(name + ': scoreboard uses visible solid text', len(styles) == 2
              and all(style['fill'] == style['color'] and style['background'] == 'none' for style in styles), styles)
        ratios = [round(contrast(style['fill'], '#5a2339'), 2) for style in styles]
        check(name + ': large score text contrasts with brightest HUD stop', all(ratio >= 3 for ratio in ratios), ratios)

    try:
        with sync_playwright() as playwright:
            launch_options = {'headless': True, 'args': ['--disable-webgl']}
            if not Path(playwright.chromium.executable_path).is_file():
                launch_options['channel'] = 'chrome'
            browser = playwright.chromium.launch(**launch_options)
            for viewport, dimensions in (('desktop', {'width': 1440, 'height': 1050}), ('mobile', {'width': 390, 'height': 844})):
                for member, routes in ((False, PUBLIC), (True, MEMBER)):
                    context = browser.new_context(viewport=dimensions, reduced_motion='reduce')
                    page = context.new_page()
                    page.on('pageerror', lambda error, v=viewport: report['errors'].append({'viewport': v, 'error': str(error)}))
                    if member:
                        page.goto(base + '/__warm-theme/login/' + viewport)
                    for route in routes:
                        try:
                            response = page.goto(base + route, wait_until='networkidle', timeout=20000)
                            page.evaluate('document.fonts.ready')
                            state = page.evaluate(PAGE_STATE)
                            record = {'route': route, 'viewport': viewport, 'status': response.status, **state}
                            report['pages'].append(record)
                            check(viewport + ' ' + route + ': shared warm theme and layout',
                                  response.ok and 'as-app' in state['bodyClass']
                                  and state['bg'].lower() == '#f7f1e7' and state['primary'].lower() == '#7b2943'
                                  and rgb(state['background']) == rgb('#f7f1e7')
                                  and state['scrollWidth'] <= dimensions['width'] + 1, record)
                            if state['header'] and route != '/login':
                                check(viewport + ' ' + route + ': burgundy navigation',
                                      rgb(state['header'])[0] > rgb(state['header'])[1] * 1.2, state['header'])
                            sample(page, route, viewport)
                            if route == '/__warm-theme/replay':
                                scores(page, viewport + ' replay')
                            if route in CAPTURE:
                                page.screenshot(path=str(output / f'{CAPTURE[route]}-{viewport}.png'), full_page=True)
                        except Exception as error:
                            check(viewport + ' ' + route + ': page loads', False, str(error))
                    if member:
                        page.goto(base + '/play2d', wait_until='networkidle')
                        page.wait_for_function("typeof window.openMatchModels==='function' && document.querySelector('#match-start-btn') && !document.querySelector('#match-start-btn').disabled")
                        page.locator('#browse-models-btn').click()
                        page.locator('#match-models-modal input[name="opponent-model"]').first.wait_for()
                        model_style = page.locator('#match-models-modal .online-modal-card').evaluate('(el)=>({background:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color,box:el.getBoundingClientRect().toJSON()})')
                        box = model_style['box']
                        check(viewport + ': model picker fits and uses cream surface',
                              rgb(model_style['background'])[0] > 240 and box['x'] >= -1 and box['right'] <= dimensions['width'] + 1, model_style)
                        sample(page, 'model-picker', viewport)
                        page.screenshot(path=str(output / f'model-picker-{viewport}.png'))
                        page.keyboard.press('Escape')
                        page.locator('#online-btn').click()
                        page.locator('#online-lobby').wait_for(state='visible')
                        sample(page, 'online-lobby', viewport)
                        page.screenshot(path=str(output / f'online-lobby-{viewport}.png'))
                        page.keyboard.press('Escape')
                        page.locator('#match-start-btn').click()
                        page.locator('#fs-wrap').wait_for(state='visible')
                        check(viewport + ': actual soccer pitch loads', page.evaluate("window.__pitch2D().sport==='soccer'"))
                        scores(page, viewport + ' soccer')
                        sample(page, 'soccer-active', viewport)
                        page.screenshot(path=str(output / f'soccer-active-{viewport}.png'), full_page=True)
                        page.locator('[onclick="resetGame()"]').click()
                        page.locator('#match-setup').wait_for(state='visible')
                        page.locator('input[name="setup-sport"][value="basketball"]').check()
                        page.locator('#match-start-btn').click()
                        page.wait_for_function("window.__pitch2D?.().sport==='basketball'")
                        check(viewport + ': real basketball court and controls load', 'First to 21' in page.locator('#game-clock').inner_text())
                        scores(page, viewport + ' basketball')
                        sample(page, 'basketball-active', viewport)
                        page.screenshot(path=str(output / f'basketball-{viewport}.png'), full_page=True)
                    else:
                        page.goto(base + '/login', wait_until='networkidle')
                        page.locator('#email').focus()
                        page.keyboard.press('Tab')
                        focused = page.evaluate('''() => {const el=document.activeElement, cs=getComputedStyle(el);return {id:el.id,href:el.getAttribute('href'),style:cs.outlineStyle,width:cs.outlineWidth,color:cs.outlineColor,background:cs.backgroundColor}}''')
                        check(viewport + ': keyboard reaches recovery link with visible indicator',
                              focused['href'] == '/forgot-password' and focused['style'] == 'solid' and float(focused['width'].replace('px', '')) >= 2, focused)
                        if focused['style'] == 'solid':
                            ratio = contrast(focused['color'], '#fffdf8')
                            check(viewport + ': focus indicator contrasts with cream surface', ratio >= 3, round(ratio, 2))
                    context.close()
            browser.close()
        for foreground, background, threshold, name in (
            ('#33252b', '#f7f1e7', 4.5, 'body text'), ('#6f5b60', '#f7f1e7', 4.5, 'muted text'),
            ('#7b2943', '#fffdf8', 4.5, 'links'), ('#fffdf8', '#7b2943', 4.5, 'primary button text'),
            ('#e6cdd0', '#33252b', 4.5, 'navigation text'), ('#9e3758', '#fffdf8', 3, 'focus indicator'),
            ('#a58b7e', '#fffdf8', 3, 'input border')):
            ratio = contrast(foreground, background)
            check(name + ': token contrast', ratio >= threshold, round(ratio, 2))
        check('No browser JavaScript exceptions', not report['errors'], report['errors'])
        check('Sampled solid-background text meets contrast threshold', not report['contrast_findings'],
              {'count': len(report['contrast_findings']), 'examples': report['contrast_findings'][:8]})
    finally:
        server.shutdown()
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Report: ' + str(output / 'report.json'), flush=True)
    return report


if __name__ == '__main__':
    result = run()
    raise SystemExit(0 if all(item['passed'] for item in result['checks']) else 1)
