"""Verify decorative site motion without external integrations or game moves.

Run ``python tools/browser/verify_cinematic.py``. A loopback-only Flask server
uses the in-memory development fallback. Screenshots and the report are saved
under the system temporary directory, in ``agent-soccer-cinematic``.
"""
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


def run() -> dict:
    isolated_environment()
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application

    output = Path(tempfile.gettempdir()) / 'agent-soccer-cinematic'
    output.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'errors': [], 'login_motion': {}, 'motion_diagnostics': {}, 'artifacts': str(output)}

    def check(name, passed, evidence=None):
        report['checks'].append({'name': name, 'passed': bool(passed), 'evidence': evidence})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)

    def page_for(context):
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        return page

    def ready(page, path='/'):
        response = page.goto(base + path)
        page.wait_for_selector('[data-motion-toggle]')
        page.wait_for_timeout(750)
        return response

    def still_loops(page):
        """All decorative CSS loops stop when the motion lifecycle pauses them."""
        return page.evaluate('''() => document.getAnimations()
          .filter(animation => animation.effect?.getTiming().iterations === Infinity)
          .every(animation => animation.playState !== 'running')''')

    def login_loops(page):
        return page.evaluate('''() => document.getAnimations().filter(animation =>
          String(animation.animationName||'').startsWith('as-login-')
          && animation.effect?.getTiming().iterations===Infinity).map(animation=>({
            name:animation.animationName,time:animation.currentTime,state:animation.playState,
            transform:getComputedStyle(animation.effect.target).transform
          }))''')

    def login_frozen(page, name):
        page.wait_for_timeout(80)
        page.evaluate('''() => {window.__qaLoginFrozen=document.getAnimations().filter(animation=>
          String(animation.animationName||'').startsWith('as-login-')
          && animation.effect?.getTiming().iterations===Infinity).map(animation=>({animation,time:animation.currentTime}));}''')
        page.wait_for_timeout(200)
        evidence = page.evaluate('''() => window.__qaLoginFrozen.map(({animation,time})=>({
          name:animation.animationName,state:animation.playState,delta:(animation.currentTime??0)-(time??0)
        }))''')
        check(name, still_loops(page) and all(abs(item['delta']) < 1 for item in evidence), evidence)

    def motion_diagnostics(page, label):
        """Measure steady motion, without imposing machine-dependent FPS targets.

        Only this bounded probe schedules animation frames. The separate idle
        assertion below still checks that the application itself has no loop.
        """
        page.evaluate('document.fonts.ready')
        page.wait_for_timeout(250)
        session = page.context.new_cdp_session(page)
        session.send('Performance.enable')
        metrics_before = {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']}
        measured = page.evaluate('''async () => {
          const frames=[],shifts=[],longTasks=[];
          const observers=[];
          for(const [type,sink] of [['layout-shift',shifts],['longtask',longTasks]]) {
            if(!PerformanceObserver.supportedEntryTypes.includes(type))continue;
            const observer=new PerformanceObserver(list=>sink.push(...list.getEntries()));
            observer.observe({type,buffered:false});observers.push(observer);
          }
          await new Promise(resolve=>{
            let previous,request,complete=false;
            const finish=()=>{if(complete)return;complete=true;cancelAnimationFrame(request);resolve();};
            const timeout=setTimeout(finish,3000);
            const frame=time=>{
              if(complete)return;
              if(previous!==undefined)frames.push(time-previous);
              previous=time;
              if(frames.length>=60){clearTimeout(timeout);finish();}
              else request=requestAnimationFrame(frame);
            };
            request=requestAnimationFrame(frame);
          });
          observers.forEach(observer=>{const pending=observer.takeRecords();
            if(pending[0]?.entryType==='layout-shift')shifts.push(...pending);
            else longTasks.push(...pending);observer.disconnect();});
          const sorted=[...frames].sort((a,b)=>a-b);
          const percentile=fraction=>sorted[Math.max(0,Math.ceil(sorted.length*fraction)-1)]??null;
          return {frames:frames.length,frame_ms:{median:percentile(.5),p95:percentile(.95),max:sorted.at(-1)??null},
            layout_shift_value:shifts.reduce((sum,entry)=>sum+entry.value,0),
            long_tasks_ms:longTasks.map(entry=>entry.duration)};
        }''')
        metrics_after = {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']}
        session.detach()
        measured['cdp_delta'] = {name: round(metrics_after.get(name, 0) - metrics_before.get(name, 0), 6)
                                 for name in ('LayoutCount', 'RecalcStyleCount', 'LayoutDuration',
                                              'RecalcStyleDuration', 'TaskDuration', 'ScriptDuration')}
        report['motion_diagnostics'][label] = measured
        # Chromium includes SVG geometry updates in LayoutCount. Keep those
        # costs visible as diagnostics; they do not imply a moving page layout.
        # Field rectangles are checked separately, and CLS captures page shifts.
        check(label + ': steady decorative motion causes no content shifts',
              measured['layout_shift_value'] == 0, measured)

    def verify_login_motion(page, label, *, mobile=False):
        expected = set() if mobile else {'as-login-soccer-float', 'as-login-pass', 'as-login-support'}
        before = login_loops(page)
        before_by_name = {item['name']: item for item in before}
        check(label + ': intended decorative loops run', expected.issubset(before_by_name)
              and all(before_by_name[name]['state'] == 'running' for name in expected)
              and (not mobile or not any(item['state'] == 'running' for item in before)), before)
        form_before = page.locator('#login-form input,#login-form button,#login-form label').evaluate_all('''nodes=>
          nodes.map(node=>{const b=node.getBoundingClientRect();return [b.x,b.y,b.width,b.height];})''')
        page.wait_for_timeout(350)
        after = login_loops(page)
        after_by_name = {item['name']: item for item in after}
        advancing = {name: round((after_by_name.get(name, {}).get('time') or 0)
                                 - (before_by_name.get(name, {}).get('time') or 0), 2) for name in expected}
        check(label + ': every visible loop advances in real time', all(delta > 150 for delta in advancing.values()), advancing)
        check(label + ': sign-in has no ambient background layers',
              page.locator('.as-login-grid,.as-login-glow,.as-login-mark').count() == 0)
        form_after = page.locator('#login-form input,#login-form button,#login-form label').evaluate_all('''nodes=>
          nodes.map(node=>{const b=node.getBoundingClientRect();return [b.x,b.y,b.width,b.height];})''')
        check(label + ': motion keeps login fields and labels still', form_before == form_after,
              {'before': form_before, 'after': form_after})
        raf_before = page.evaluate('window.__qaRAFCalls')
        page.wait_for_timeout(350)
        check(label + ': idle motion uses zero JavaScript animation frames',
              page.evaluate('window.__qaRAFCalls') == raf_before, raf_before)
        motion_diagnostics(page, label)
        if mobile:
            check(label + ': mobile keeps the form visible and decorative motion asleep',
                  not page.locator('.as-login-pitch-art').is_visible()
                  and page.locator('#email').is_visible() and still_loops(page))
        else:
            shot = page.evaluate('''() => {
              const loops=[...document.querySelectorAll('[data-motion-loop]')].map(target=>{
                const style=getComputedStyle(target);
                return {target,name:style.animationName,duration:parseFloat(style.animationDuration)*1000};
              }).filter(loop=>loop.name.startsWith('as-login-'));
              // Seek through CSS so the browser keeps ownership of playback.
              // Animation.play() would override animation-play-state in later checks.
              loops.forEach(({target})=>{
                target.style.animationName='none';target.style.animationPlayState='paused';
                target.style.animationDelay='0s';
              });
              void document.body.offsetWidth;
              loops.forEach(({target,name})=>{target.style.animationName=name;});
              const center=node=>{const b=node.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};};
              const result={};
              result.seams=loops.map(loop=>{
                const at=fraction=>{loop.target.style.animationDelay=`${-loop.duration*fraction}ms`;
                  const style=getComputedStyle(loop.target),matrix=new DOMMatrixReadOnly(style.transform);
                  return {matrix:[matrix.a,matrix.b,matrix.c,matrix.d,matrix.e,matrix.f],opacity:Number(style.opacity)};};
                const before=at(.99999),after=at(.00001);
                loop.target.style.animationDelay='0s';return {name:loop.name,before,after};
              });
              const pass=loops.find(a=>a.name==='as-login-pass'),passBall=pass.target;
              passBall.style.animationDelay='0s';result.passStart=center(passBall);
              passBall.style.animationDelay=`${-pass.duration*.60}ms`;result.passForward=center(passBall);
              passBall.style.animationDelay=`${-pass.duration}ms`;result.passReset=center(passBall);
              return result;
            }''')
            def distance(first, second):
                return ((first['x'] - second['x']) ** 2 + (first['y'] - second['y']) ** 2) ** .5
            check(label + ': soccer pass travels and returns to its starting player',
                  distance(shot['passStart'], shot['passForward']) > 30
                  and distance(shot['passStart'], shot['passReset']) < 1, shot)
            check(label + ': visible loop transforms and opacity meet at the repeat seam', all(
                max(abs(before - after) for before, after in zip(item['before']['matrix'], item['after']['matrix'])) < .02
                and abs(item['before']['opacity'] - item['after']['opacity']) < .002 for item in shot['seams']), shot['seams'])
            page.screenshot(path=str(output / 'login-soccer-motion.png'), full_page=True)
            report['login_motion'][label + '-soccer'] = shot
            # Exercise the real page lifecycle without our deterministic seek styles.
            ready(page, '/login')

        page.locator('[data-motion-toggle]').click()
        login_frozen(page, label + ': Motion off freezes every loop')
        page.locator('[data-motion-toggle]').click()
        page.wait_for_timeout(100)
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
        login_frozen(page, label + ': hidden tabs freeze every loop')
        page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(100)
        page.evaluate('''() => {const spacer=document.createElement('div');spacer.id='qa-motion-spacer';
          spacer.style.height='1500px';spacer.setAttribute('aria-hidden','true');document.body.append(spacer);
          window.scrollTo(0,document.documentElement.scrollHeight);}''')
        page.wait_for_timeout(250)
        check(label + ': all login motion scenes are genuinely offscreen', page.evaluate('''() =>
          [...document.querySelectorAll('.as-login-story [data-motion-scene]')].every(scene=>
            !scene.getClientRects().length || scene.getBoundingClientRect().bottom<0)'''))
        login_frozen(page, label + ': offscreen scenes freeze every loop')
        page.evaluate("document.getElementById('qa-motion-spacer').remove();window.scrollTo(0,0)")
        page.wait_for_timeout(250)
        resumed = login_loops(page)
        check(label + ': returning to the page respects visible artwork',
              still_loops(page) if mobile else any(item['state'] == 'running' for item in resumed), resumed)
        page.emulate_media(reduced_motion='reduce')
        login_frozen(page, label + ': reduced-motion preference freezes every loop')
        check(label + ': reduced motion preserves the usable login form', page.locator('#email').is_visible()
              and page.locator('#password').is_visible() and page.locator('.as-login-submit').is_enabled())
        page.emulate_media(reduced_motion='no-preference')
        page.wait_for_timeout(250)
        report['login_motion'][label] = {'loops': after, 'advance_ms': advancing}

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'

    try:
        with sync_playwright() as playwright:
            launch_options = {'headless': True, 'args': ['--disable-webgl']}
            if not Path(playwright.chromium.executable_path).is_file():
                launch_options['channel'] = 'chrome'
            browser = playwright.chromium.launch(**launch_options)
            desktop = browser.new_context(viewport={'width': 1366, 'height': 900})
            desktop.add_init_script('''window.__qaRAFCalls=0;
              const originalRAF=window.requestAnimationFrame;
              window.requestAnimationFrame=function(callback){window.__qaRAFCalls++;
                return originalRAF.call(this,callback);};''')
            page = page_for(desktop)
            ready(page)
            check('Motion enabled and clearly labelled by default',
                  page.locator('html').evaluate("e=>e.classList.contains('as-motion-enabled')")
                  and page.locator('[data-motion-toggle]').get_attribute('aria-pressed') == 'true'
                  and 'Motion on' in page.locator('[data-motion-toggle]').inner_text())
            page.wait_for_function('''() => [...document.querySelectorAll('[data-motion-scene].is-in-view [data-motion-loop]')]
              .some(element => element.getAnimations({subtree:true})
                .some(animation => animation.playState==='running' && animation.effect?.getTiming().iterations===Infinity))''')
            page.evaluate('''() => {
              const loop=[...document.querySelectorAll('[data-motion-scene].is-in-view [data-motion-loop]')]
                .find(element=>element.getAnimations({subtree:true})
                  .some(animation=>animation.playState==='running' && animation.effect?.getTiming().iterations===Infinity));
              window.__qaLoop=loop;
              window.__qaAnimation=loop.getAnimations({subtree:true})
                .find(animation=>animation.playState==='running' && animation.effect?.getTiming().iterations===Infinity);
              window.__qaAnimationTime=window.__qaAnimation.currentTime;
            }''')
            page.wait_for_timeout(300)
            check('Visible artwork actually animates', page.evaluate(
                'window.__qaAnimation.currentTime > window.__qaAnimationTime + 100'))
            page.wait_for_timeout(700)
            raf_before = page.evaluate('window.__qaRAFCalls')
            page.wait_for_timeout(400)
            check('Idle site motion has no JavaScript frame loop',
                  page.evaluate('window.__qaRAFCalls') == raf_before)
            check('Decorative motion needs no canvas or media player', page.locator('canvas,audio,video').count() == 0)
            page.screenshot(path=str(output / 'landing-desktop.png'), full_page=True)

            page.locator('[data-motion-toggle]').click()
            page.wait_for_timeout(100)
            check('Motion off pauses looping artwork', still_loops(page)
                  and page.locator('[data-motion-toggle]').get_attribute('aria-pressed') == 'false')
            check('Motion preference is saved', page.evaluate("localStorage.getItem('agent-soccer-motion')") == 'off')
            page.reload()
            page.wait_for_selector('[data-motion-toggle]')
            check('Motion stays off after reload', still_loops(page)
                  and not page.locator('html').evaluate("e=>e.classList.contains('as-motion-enabled')"))
            page.locator('[data-motion-toggle]').focus()
            page.keyboard.press('Enter')
            page.wait_for_function("document.documentElement.classList.contains('as-motion-enabled')")
            check('Motion can be enabled with the keyboard',
                  page.locator('[data-motion-toggle]').get_attribute('aria-pressed') == 'true')

            # The compact landing page may fit in a tall desktop. Give this
            # lifecycle check enough scroll distance to hide the entire scene.
            page.evaluate('''() => {const spacer=document.createElement('div');
              spacer.id='qa-hero-spacer';spacer.style.height='1500px';
              spacer.setAttribute('aria-hidden','true');document.body.append(spacer);
              window.scrollTo(0,document.documentElement.scrollHeight);}''')
            page.wait_for_timeout(250)
            check('Offscreen hero pauses its decorative loops', page.evaluate('''() => {
              const scene=document.querySelector('[data-motion-scene]');
              return scene.getBoundingClientRect().bottom < 0 && !scene.classList.contains('is-in-view')
                && [...scene.querySelectorAll('[data-motion-loop]')].flatMap(el=>el.getAnimations({subtree:true}))
                  .filter(a=>a.effect?.getTiming().iterations===Infinity).every(a=>a.playState!=='running');
            }'''))
            page.evaluate("document.getElementById('qa-hero-spacer').remove();window.scrollTo(0,0)")
            page.wait_for_function("document.querySelector('[data-motion-scene]').classList.contains('is-in-view')")
            check('Artwork resumes when scrolled back into view', not still_loops(page))
            page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(100)
            check('Hidden tabs pause decorative loops', still_loops(page)
                  and page.locator('html').evaluate("e=>e.classList.contains('as-motion-paused')"))
            page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(100)
            check('Visible tabs resume motion', not still_loops(page))

            scene = page.locator('[data-motion-scene]').first
            scene_box = scene.bounding_box()
            page.mouse.move(scene_box['x'] + scene_box['width'] * .8, scene_box['y'] + scene_box['height'] * .35)
            page.wait_for_timeout(180)
            check('Mouse movement creates bounded depth', scene.evaluate('''element => {
              const values=[element,...element.querySelectorAll('[data-motion-depth]')].map(el=>{
                const style=getComputedStyle(el);
                return [style.getPropertyValue('--motion-x'),style.getPropertyValue('--motion-y')].map(parseFloat);
              }).flat().filter(Number.isFinite);
              return values.some(value=>Math.abs(value)>.01) && values.every(value=>Math.abs(value)<=30);
            }'''))
            page.mouse.move(2, 2)
            page.wait_for_timeout(350)
            check('Depth resets after leaving the artwork', scene.evaluate('''element =>
              [element,...element.querySelectorAll('[data-motion-depth]')].every(el=>{
                const style=getComputedStyle(el);
                return ['--motion-x','--motion-y'].every(name=>Math.abs(parseFloat(style.getPropertyValue(name))||0)<.01);
              })'''))

            # Retain the pointer interaction while layout coordinates change.
            # The next move must use the resized/scrolled artwork rectangle,
            # without requiring the user to leave and re-enter the artwork.
            page.mouse.move(scene_box['x'] + scene_box['width'] * .25, scene_box['y'] + scene_box['height'] * .5)
            for action in ('resize', 'scroll'):
                if action == 'resize':
                    page.set_viewport_size({'width': 1050, 'height': 900})
                else:
                    page.evaluate('window.scrollBy(0,100)')
                page.wait_for_timeout(400)
                scene.evaluate('''element => {
                  const rect=element.getBoundingClientRect();
                  element.dispatchEvent(new PointerEvent('pointermove', {
                    pointerType:'mouse',clientX:rect.left+rect.width*.7,clientY:rect.top+rect.height*.6
                  }));
                }''')
                page.wait_for_timeout(100)
                values = scene.evaluate('''element => {
                  const style=getComputedStyle(element);
                  return ['--motion-x','--motion-y'].map(name=>parseFloat(style.getPropertyValue(name)));
                }''')
                check(f'Pointer depth stays accurate after {action}',
                      abs((values[0] or 0) - .4) < .02 and abs((values[1] or 0) - .2) < .02, values)
            page.set_viewport_size({'width': 1366, 'height': 900})
            page.evaluate('window.scrollTo(0,0)')
            page.mouse.move(2, 2)

            for name, path in (('landing', '/'), ('login', '/login'), ('register', '/register')):
                response = ready(page, path)
                check(f'{name}: desktop content remains accessible', response.status == 200
                      and page.locator('h1').is_visible()
                      and page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            ready(page, '/login')
            verify_login_motion(page, 'Desktop login')
            page.locator('#email').fill('cinematic@example.test')
            page.locator('#password').fill('cinematic-test')
            page.get_by_role('button', name='Show password', exact=True).click()
            check('Animated login preserves password control', page.locator('#password').get_attribute('type') == 'text')
            page.get_by_role('button', name='Hide password', exact=True).click()
            page.screenshot(path=str(output / 'login-desktop.png'), full_page=True)
            page.get_by_role('button', name='Sign in', exact=True).click()
            page.wait_for_url('**/lobby')
            check('Login form still establishes a development session', page.url.endswith('/lobby'))

            for width in (1366, 390):
                page.set_viewport_size({'width': width, 'height': 900})
                for name, path in (('lobby', '/lobby'), ('play-hub', '/hub/play'), ('workshop-hub', '/hub/workshop')):
                    response = ready(page, path)
                    check(f'{name} {width}: responsive content and working links', response.status == 200
                          and page.locator('h1').is_visible()
                          and page.locator('main a[href]').count() > 0
                          and page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    page.screenshot(path=str(output / f'{name}-{width}.png'), full_page=True)
            page.locator('.as-menu-toggle').click()
            check('Mobile navigation opens with animation enabled', page.locator('.as-menu-toggle').get_attribute('aria-expanded') == 'true')
            page.get_by_label('Soccer AI pages', exact=True).click()
            check('Mobile section links remain reachable', page.locator('.as-nav-menu[open] a[href="/my-models"]').is_visible())
            page.keyboard.press('Escape')
            page.keyboard.press('Escape')
            check('Escape restores mobile menu focus', page.locator('.as-menu-toggle').get_attribute('aria-expanded') == 'false'
                  and page.locator('.as-menu-toggle').evaluate('e=>e===document.activeElement'))
            ready(page, '/hub/play')
            destination = page.locator('main a[href="/play3d"],main a[href="/play2d"]').first
            destination.click()
            page.wait_for_selector('#pitch-container canvas', state='attached')
            check('Animated destination cards still open the playable match', '/play' in page.url
                  and (page.locator('#pitch-container canvas').is_visible() or page.locator('#match-setup').is_visible()))
            desktop.close()

            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            mobile.add_init_script('''window.__qaRAFCalls=0;
              const originalRAF=window.requestAnimationFrame;
              window.requestAnimationFrame=function(callback){window.__qaRAFCalls++;
                return originalRAF.call(this,callback);};''')
            mobile_page = page_for(mobile)
            ready(mobile_page)
            check('Touch landing has no horizontal overflow', mobile_page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            mobile_scene = mobile_page.locator('[data-motion-scene]').first
            mobile_scene.dispatch_event('pointermove', {'pointerType': 'touch', 'clientX': 220, 'clientY': 300})
            check('Touch does not trigger pointer depth movement', mobile_scene.evaluate('''element =>
              [element,...element.querySelectorAll('[data-motion-depth]')].every(el=>{
                const style=getComputedStyle(el);
                return ['--motion-x','--motion-y'].every(name=>Math.abs(parseFloat(style.getPropertyValue(name))||0)<.01);
              })'''))
            mobile_page.screenshot(path=str(output / 'landing-mobile.png'), full_page=True)
            ready(mobile_page, '/login')
            verify_login_motion(mobile_page, 'Mobile login', mobile=True)
            check('Mobile login preserves labelled form fields', mobile_page.get_by_label('Email address', exact=True).is_visible()
                  and mobile_page.get_by_label('Password', exact=True).is_visible()
                  and mobile_page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            mobile_page.screenshot(path=str(output / 'login-mobile.png'), full_page=True)
            mobile.close()

            reduced = browser.new_context(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')
            reduced.add_init_script("localStorage.setItem('agent-soccer-motion','on')")
            reduced_page = page_for(reduced)
            ready(reduced_page)
            check('OS reduced motion overrides a saved on preference', still_loops(reduced_page)
                  and not reduced_page.locator('html').evaluate("e=>e.classList.contains('as-motion-enabled')"))
            check('Reduced motion retains readable page content', reduced_page.locator('h1').is_visible()
                  and reduced_page.locator('.as-hero-actions a').first.is_visible())
            reduced_page.emulate_media(reduced_motion='no-preference')
            reduced_page.wait_for_function("document.documentElement.classList.contains('as-motion-enabled')")
            check('Runtime accessibility preference updates are respected', not still_loops(reduced_page))
            reduced_page.emulate_media(reduced_motion='reduce')
            reduced_page.wait_for_timeout(100)
            check('Runtime reduced motion stops active artwork', still_loops(reduced_page))
            reduced.close()

            no_js = browser.new_context(viewport={'width': 1280, 'height': 900}, java_script_enabled=False)
            static_page = page_for(no_js)
            static_page.goto(base + '/')
            check('Page stays readable when JavaScript is unavailable', static_page.locator('h1').is_visible()
                  and static_page.locator('.as-hero-actions a').first.is_visible()
                  and static_page.locator('.as-destination').first.is_visible())
            no_js.close()
            check('No browser JavaScript errors', not report['errors'], report['errors'])
            browser.close()
    finally:
        server.shutdown()
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    result = run()
    failed = [check for check in result['checks'] if not check['passed']]
    print(json.dumps({'passed': len(result['checks']) - len(failed), 'failed': failed,
                      'artifacts': result['artifacts']}, indent=2))
    raise SystemExit(bool(failed))
