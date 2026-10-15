"""Verify the served opponent picker with real local model selection requests.

Run ``python tools/browser/verify_model_picker.py``. The loopback-only server
uses isolated in-memory storage. Reports and screenshots are written under the
system temporary directory, in ``agent-soccer-model-picker``.
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
    from flask import redirect, session
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    @application.app.route('/__model-picker/login/<name>')
    def login(name):
        session.update(user_id='dev:model-picker-' + name, username=name)
        save_game(session['user_id'], new_soccer_state(mode='hvai', player_count=5, half_length=0))
        return redirect('/play2d')

    output = Path(tempfile.gettempdir()) / 'agent-soccer-model-picker'
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

    def page_for(context):
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        return page

    def ready(page, name, *, catalog=True):
        page.goto(base + '/__model-picker/login/' + name)
        page.wait_for_function("typeof window.openMatchModels === 'function' && !!window.__pitch2D?.().width")
        if catalog:
            page.wait_for_function("document.querySelectorAll('#model-select option').length >= 15")

    def browse(page):
        page.locator('#browse-models-btn').click()
        page.locator('#match-models-modal').wait_for(state='visible')

    def option(page, model):
        return page.locator(f'input[name="opponent-model"][value="{model}"]')

    def layout(page, name):
        dimensions = page.evaluate('''() => {
          const modal=document.querySelector('#match-models-modal');
          const card=modal.querySelector('.online-modal-card');
          const list=document.querySelector('#match-models-list');
          const scroller=modal.querySelector('.model-picker-results') || list;
          const box=card.getBoundingClientRect();
          return {viewport:[innerWidth,innerHeight], box:[box.left,box.top,box.right,box.bottom],
            pageOverflow:document.documentElement.scrollWidth>innerWidth+1,
            cardOverflow:card.scrollWidth>card.clientWidth+1,
            listOverflow:list.scrollWidth>list.clientWidth+1,
            listScrollable:scroller.scrollHeight>scroller.clientHeight+1,
            listHeight:scroller.clientHeight};
        }''')
        left, top, right, bottom = dimensions['box']
        width, height = dimensions['viewport']
        check(name + ': dialog fits the viewport', left >= 0 and top >= 0 and right <= width + 1 and bottom <= height + 1, dimensions)
        check(name + ': no horizontal overflow', not any(dimensions[key] for key in ('pageOverflow', 'cardOverflow', 'listOverflow')), dimensions)
        check(name + ': results have usable scroll space', dimensions['listHeight'] >= 72 and dimensions['listScrollable'], dimensions)
        last = page.locator('input[name="opponent-model"]').last
        last.scroll_into_view_if_needed()
        check(name + ': final model and confirm remain reachable', last.is_visible() and page.locator('#model-picker-apply').is_visible())
        page.screenshot(path=str(output / f'{name}.png'))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--disable-webgl'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            page = page_for(context)
            ready(page, 'desktop')
            catalog = context.request.get(base + '/models').json()
            original_summary = page.locator('#model-summary').inner_text()

            with page.expect_response(lambda response: response.url.endswith('/switch_model')) as changed:
                page.locator('#model-select').select_option('possession')
            page.wait_for_function("previous => document.querySelector('#model-summary').textContent.trim() !== previous", arg=original_summary)
            check('Native selection updates the actual backend', changed.value.ok and context.request.get(base + '/state').json()['ai_model'] == 'possession')
            check('Current opponent has a readable summary', bool(page.locator('#model-summary').inner_text().strip()) and page.locator('#model-summary').inner_text() != original_summary)
            page.locator('#model-summary').scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'opponent-strip-desktop.png'))

            browse(page)
            page.locator('input[name="opponent-model"]').first.wait_for()
            check('Picker opens on the current model', option(page, 'possession').is_checked())
            check('Picker has a labelled dialog', bool(page.locator('#match-models-modal [role="dialog"]').get_attribute('aria-labelledby')))
            check('Picker receives keyboard focus', page.evaluate("!!document.activeElement.closest('#match-models-modal')"))
            page.evaluate('''() => {
              window.__pickerFocusItems=[...document.querySelectorAll('#match-models-modal button,#match-models-modal input,#match-models-modal select,#match-models-modal a[href]')]
                .filter(element=>!element.disabled && element.tabIndex>=0 && element.getClientRects().length);
              window.__pickerFocusItems.at(-1).focus();
            }''')
            page.keyboard.press('Tab')
            check('Tab wraps inside the picker', page.evaluate('document.activeElement===window.__pickerFocusItems[0]'))
            page.keyboard.press('Shift+Tab')
            check('Reverse Tab wraps inside the picker', page.evaluate('document.activeElement===window.__pickerFocusItems.at(-1)'))
            page.locator('#model-search').fill('no model has this exact search phrase')
            check('Search explains an empty result', page.locator('input[name="opponent-model"]').count() == 0 and bool(page.locator('#model-results-count').inner_text().strip()))
            page.locator('#model-search').fill('high press')
            check('Search finds the intended model', option(page, 'high_press').count() == 1)
            option(page, 'high_press').check()
            with page.expect_response(lambda response: response.url.endswith('/switch_model')) as applied:
                page.locator('#model-picker-apply').click()
            page.locator('#match-models-modal').wait_for(state='hidden')
            check('Dialog selection updates backend and native selector', applied.value.ok and context.request.get(base + '/state').json()['ai_model'] == 'high_press' and page.locator('#model-select').input_value() == 'high_press')
            check('Applying restores focus to the opener', page.evaluate("document.activeElement.id === 'browse-models-btn'"))

            browse(page)
            page.locator('#model-search').fill('')
            page.locator('[data-model-filter="all"]').click()
            layout(page, 'picker-desktop')
            focusable = page.locator('#match-models-modal [data-dialog-close]')
            focusable.focus()
            page.keyboard.press('Escape')
            check('Escape closes the picker and restores focus', page.locator('#match-models-modal').is_hidden() and page.evaluate("document.activeElement.id === 'browse-models-btn'"))

            page.locator('#online-btn').click()
            page.locator('#match-models-btn').click()
            page.locator('#match-models-modal').wait_for(state='visible')
            page.keyboard.press('Escape')
            check('Nested picker preserves lobby and restores its opener', page.locator('#online-lobby').is_visible() and page.locator('#match-models-modal').is_hidden() and page.evaluate("document.activeElement.id === 'match-models-btn'"))
            page.keyboard.press('Escape')

            with page.expect_response(lambda response: response.url.endswith('/state')):
                page.locator('#mode-select').select_option('hvh')
            page.locator('#online-btn').click()
            page.locator('#match-models-btn').click()
            option(page, 'high_press').check()
            with page.expect_response(lambda response: response.url.endswith('/set_mode')) as mode_changed:
                page.locator('#model-picker-apply').click()
            page.locator('#match-models-modal').wait_for(state='hidden')
            page.locator('#online-lobby').wait_for(state='hidden')
            started = context.request.get(base + '/state').json()
            check('Lobby selection starts human versus the selected model', mode_changed.value.ok and started['game_mode'] == 'hvai' and started['ai_model'] == 'high_press' and page.locator('#mode-select').input_value() == 'hvai' and page.locator('#model-select').input_value() == 'high_press')

            page.route('**/set_mode', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: match mode unavailable'})))
            page.locator('#online-btn').click()
            page.locator('#match-models-btn').click()
            option(page, 'counter_attack').check()
            page.locator('#model-picker-apply').click()
            page.wait_for_function("document.querySelector('#model-picker-status').classList.contains('is-error') && !document.querySelector('#model-picker-apply').disabled")
            check('Failed mode change preserves the model already committed by the server', page.locator('#match-models-modal').is_visible() and page.locator('#model-select').input_value() == 'counter_attack' and context.request.get(base + '/state').json()['ai_model'] == 'counter_attack')
            check('Failed mode change leaves a usable retry', page.locator('#model-picker-apply').is_enabled())
            page.unroute('**/set_mode')
            page.keyboard.press('Escape')
            page.keyboard.press('Escape')
            with page.expect_response(lambda response: response.url.endswith('/switch_model')):
                page.locator('#model-select').select_option('high_press')
            page.wait_for_function("!document.querySelector('#model-select').disabled")

            pending = []
            page.route('**/switch_model', lambda route: pending.append(route))
            browse(page)
            page.locator('#model-search').fill('')
            option(page, 'counter_attack').check()
            with page.expect_request(lambda request: request.url.endswith('/switch_model')):
                page.locator('#model-picker-apply').evaluate('element => { element.click(); element.click(); }')
            page.wait_for_function("document.querySelector('#model-picker-apply').disabled")
            check('Repeated apply sends one request', len(pending) == 1, len(pending))
            pending[0].fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: selection unavailable'}))
            page.wait_for_function("document.querySelector('#model-picker-status').textContent.includes('Fixture: selection unavailable')")
            check('Failed selection stays open and preserves the previous opponent', page.locator('#match-models-modal').is_visible() and page.locator('#model-select').input_value() == 'high_press' and context.request.get(base + '/state').json()['ai_model'] == 'high_press')
            check('Failed selection allows retry', page.locator('#model-picker-apply').is_enabled())
            page.unroute('**/switch_model')
            page.keyboard.press('Escape')

            joining = []
            page.route('**/api/quick/join', lambda route: joining.append(route))
            page.locator('#online-btn').click()
            with page.expect_request(lambda request: request.url.endswith('/api/quick/join')):
                page.locator('#quick-play-btn').click()
            if page.locator('#match-models-btn').is_enabled():
                page.locator('#match-models-btn').click()
            check('Matchmaking prevents opening a conflicting model selection', page.locator('#match-models-modal').is_hidden())
            joining[0].fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture: search unavailable'}))
            page.locator('#quick-cancel-btn').wait_for(state='hidden')
            page.unroute('**/api/quick/join')
            page.keyboard.press('Escape')

            for name, viewport in (('picker-mobile', {'width': 375, 'height': 812}), ('picker-mobile-keyboard', {'width': 375, 'height': 500}), ('picker-short-landscape', {'width': 812, 'height': 375})):
                page.set_viewport_size(viewport)
                browse(page)
                page.locator('#model-search').fill('')
                page.locator('[data-model-filter="all"]').click()
                layout(page, name)
                page.keyboard.press('Escape')

            custom_context = browser.new_context(viewport={'width': 375, 'height': 812}, reduced_motion='reduce')
            custom = page_for(custom_context)
            custom_id = 'user_model:picker-fixture'
            unsafe_name = '<img src=x onerror="window.__pickerXSS=true"> Custom club'
            unsafe_description = '<script>window.__pickerXSS=true</script> ' + 'LongDescription' * 25
            custom_catalog = catalog + [{'id': custom_id, 'name': unsafe_name, 'desc': unsafe_description, 'type': 'user'}]
            custom.route('**/models', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(custom_catalog)))
            ready(custom, 'custom')
            browse(custom)
            custom.locator('[data-model-filter="user"]').click()
            check('My models filter isolates uploaded models', custom.locator('input[name="opponent-model"]').count() == 1 and option(custom, custom_id).count() == 1)
            check('Custom model text is escaped safely', unsafe_name in custom.locator('#match-models-list').inner_text() and custom.locator('#match-models-list img, #match-models-list script').count() == 0 and not custom.evaluate('Boolean(window.__pickerXSS)'))
            check('Long custom text does not cause horizontal overflow', custom.evaluate("document.querySelector('#match-models-list').scrollWidth <= document.querySelector('#match-models-list').clientWidth + 1"))
            custom.locator('[data-model-filter="builtin"]').click()
            check('Built-in filter excludes uploaded models', option(custom, custom_id).count() == 0 and custom.locator('input[name="opponent-model"]').count() == len(catalog))
            custom.locator('[data-model-filter="all"]').click()
            custom.locator('#model-search').fill('Custom club')
            option(custom, custom_id).check()
            submitted = []

            def accept_custom(route):
                submitted.append(route.request.post_data_json)
                route.fulfill(status=200, content_type='application/json', body=json.dumps({'status': 'Model selected'}))

            custom.route('**/switch_model', accept_custom)
            custom.locator('#model-picker-apply').click()
            custom.locator('#match-models-modal').wait_for(state='hidden')
            check('Custom selection preserves its required ID prefix', submitted == [{'model': custom_id, 'target': 'b'}], submitted)
            check('Custom selection updates the native selector safely', custom.locator('#model-select').input_value() == custom_id and not custom.evaluate('Boolean(window.__pickerXSS)'))

            failure_context = browser.new_context(viewport={'width': 1024, 'height': 768}, reduced_motion='reduce')
            failure = page_for(failure_context)
            available = {'value': False}

            def catalog_response(route):
                route.fulfill(status=200 if available['value'] else 503, content_type='application/json', body=json.dumps(catalog if available['value'] else {'error': 'Fixture: catalog offline'}))

            failure.route('**/models', catalog_response)
            ready(failure, 'retry', catalog=False)
            browse(failure)
            failure.locator('#model-picker-retry').wait_for(state='visible')
            check('Catalog failure provides a visible retry', failure.locator('#model-picker-retry').is_enabled() and bool(failure.locator('#model-picker-status').inner_text().strip()))
            available['value'] = True
            failure.locator('#model-picker-retry').click()
            failure.wait_for_function("document.querySelectorAll('input[name=\"opponent-model\"]').length >= 15")
            check('Retry reloads the catalog successfully', failure.locator('input[name="opponent-model"]').count() == len(catalog))

            check('Picker checks produce no browser exceptions', not report['errors'], report['errors'])
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
    failures = [entry for entry in results['checks'] if not entry['passed']]
    print(json.dumps({'passed': len(results['checks']) - len(failures), 'failed': failures, 'artifacts': results['artifacts']}, indent=2))
    raise SystemExit(bool(failures))
