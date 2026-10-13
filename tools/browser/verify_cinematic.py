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
    report = {'checks': [], 'errors': [], 'artifacts': str(output)}

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
        return page.evaluate('''() => [...document.querySelectorAll('[data-motion-loop]')]
          .flatMap(element => element.getAnimations({subtree:true}))
          .filter(animation => animation.effect?.getTiming().iterations === Infinity)
          .every(animation => animation.playState !== 'running')''')

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--disable-webgl'])
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

            # A tall desktop can still show the last pixels of the hero at the
            # document end. Use a short pane so this is genuinely offscreen.
            page.set_viewport_size({'width': 1366, 'height': 700})
            page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
            page.wait_for_timeout(250)
            check('Offscreen hero pauses its decorative loops', page.evaluate('''() => {
              const scene=document.querySelector('[data-motion-scene]');
              return scene.getBoundingClientRect().bottom < 0 && !scene.classList.contains('is-in-view')
                && [...scene.querySelectorAll('[data-motion-loop]')].flatMap(el=>el.getAnimations({subtree:true}))
                  .filter(a=>a.effect?.getTiming().iterations===Infinity).every(a=>a.playState!=='running');
            }'''))
            page.set_viewport_size({'width': 1366, 'height': 900})
            page.evaluate('window.scrollTo(0,0)')
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
            page.get_by_label('Workshop pages', exact=True).click()
            check('Mobile section links remain reachable', page.locator('.as-nav-menu[open] a[href="/my-models"]').is_visible())
            page.keyboard.press('Escape')
            page.keyboard.press('Escape')
            check('Escape restores mobile menu focus', page.locator('.as-menu-toggle').get_attribute('aria-expanded') == 'false'
                  and page.locator('.as-menu-toggle').evaluate('e=>e===document.activeElement'))
            ready(page, '/hub/play')
            destination = page.locator('main a[href="/play3d"],main a[href="/play2d"]').first
            destination.click()
            page.wait_for_selector('#pitch-container canvas')
            check('Animated destination cards still open the playable match', '/play' in page.url and page.locator('#pitch-container canvas').is_visible())
            desktop.close()

            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
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
