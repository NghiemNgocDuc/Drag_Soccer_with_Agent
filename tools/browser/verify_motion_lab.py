"""Motion Lab and learned-control integration checks. No physical camera opens.

Synthetic landmarks exercise capture/review/storage and model plumbing. They
are not evidence that a trained movement classifier works on real people.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment
from tools.browser.verify_metro_motion import CAMERA_HARNESS, FIXTURE_WORKER


def fixture_model(eligible=False):
    # Constant LEFT prediction deliberately disagrees with a RIGHT camera pose.
    # eligible=True solely exercises the explicit-control UI branch; this is
    # never published as a trained model or used as evaluation evidence.
    return {
        'format': 'metro-motion-motion-model', 'version': 1,
        'featureVersion': 'mm-body-v1',
        'labels': ['neutral', 'left', 'right', 'jump', 'duck'],
        'windowSize': 8, 'sampleHz': 15, 'inputSize': 64, 'hiddenSize': 1,
        'mean': [0] * 64, 'scale': [1] * 64,
        'weights1': [[0] * 64], 'bias1': [0],
        'weights2': [[0] for _ in range(5)], 'bias2': [0, 10, 0, 0, 0],
        'threshold': .8,
        'metrics': {'controlEligible': eligible, 'testFixtureOnly': True},
    }


def run(args):
    isolated_environment()
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/metro-motion/'
    output = Path(tempfile.gettempdir()) / f'agent-soccer-motion-lab-{args.channel}'
    output.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'errors': [], 'external': [], 'artifacts': str(output),
              'scope': 'Synthetic camera/model fixtures; no physical webcam or human accuracy claim.'}

    def check(name, condition, evidence=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'evidence': evidence})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)

    def capture_page(context):
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        def route(request):
            if urlparse(request.request.url).hostname not in ('localhost', '127.0.0.1'):
                report['external'].append(request.request.url)
                request.abort()
            else:
                request.continue_()
        page.route('**/*', route)
        page.route('**/js/pose-worker.mjs', lambda route: route.fulfill(body=FIXTURE_WORKER, content_type='text/javascript'))
        return page

    def lab_state(page):
        return page.evaluate('__motionLab.state()')

    def game_state(page):
        return page.evaluate('__metroMotion.state()')

    def import_model(page, data):
        page.locator('#motion-model').set_input_files({'name': 'fixture.json', 'mimeType': 'application/json',
                                                      'buffer': json.dumps(data).encode()})
        page.wait_for_function("__metroMotion.state().modelLoaded")
        page.wait_for_function("!document.querySelector('#model-status').textContent.includes('Checking')")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel, headless=True, args=['--enable-unsafe-swiftshader'])
            report['browserVersion'] = browser.version
            context = browser.new_context(viewport={'width': 1440, 'height': 1050}, accept_downloads=True)
            context.add_init_script(CAMERA_HARNESS)
            page = capture_page(context)
            response = page.goto(base + 'motion-lab.html', wait_until='networkidle')
            page.wait_for_function('window.__motionLab?.state')
            page.locator('#lab-camera-start').wait_for(state='visible')
            check('Motion Lab is publicly served with video-only policy', response.status == 200
                  and response.headers.get('permissions-policy') == 'camera=(self), microphone=()')
            check('Lab does not request camera access on load', page.evaluate('__cameraQA.calls.length===0'))
            check('Recording and download wait for real user actions', page.locator('#lab-record').is_disabled()
                  and page.locator('#download-data').is_disabled())
            for width in (320, 390, 768, 1440):
                page.set_viewport_size({'width': width, 'height': 1000})
                page.wait_for_timeout(80)
                check(f'Lab fits {width}px without horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                page.screenshot(path=str(output / f'lab-{width}.png'), full_page=True)
            page.evaluate("__cameraQA.mode='success'")
            page.locator('#lab-camera-start').click()
            page.wait_for_function("!document.querySelector('#lab-record').disabled", timeout=20000)
            check('Lab calibrates explicit video-only synthetic camera', page.evaluate("__cameraQA.calls.length===1 && __cameraQA.calls[0].audio===false"))
            first = lab_state(page)
            check('Calibration alone does not record clips', first['clipCount'] == 0 and first['phase'] == 'idle', first)
            page.locator('#lab-record').click()
            check('Record starts a visible countdown', lab_state(page)['phase'] == 'countdown')
            page.wait_for_function("__motionLab.state().phase==='review'", timeout=15000)
            check('Capture is reviewed before saving', lab_state(page)['clipCount'] == 0
                  and page.locator('#save-clip').is_disabled())
            page.locator('#review-time').fill('1000')
            page.locator('#range-start').fill('700')
            page.locator('#range-end').fill('2000')
            page.locator('#review-confirm').check()
            page.locator('#save-clip').click()
            page.wait_for_function('__motionLab.state().clipCount===1')
            page.wait_for_function("!document.querySelector('#storage-status').textContent.includes('Saving')")
            check('Reviewed clip saves with one action count', lab_state(page)['clipCount'] == 1)
            with page.expect_download() as downloaded:
                page.locator('#download-data').click()
            dataset_path = output / 'captured-synthetic-landmarks.json'
            downloaded.value.save_as(dataset_path)
            data = json.loads(dataset_path.read_text(encoding='utf8'))
            clip = data['clips'][0]
            check('Export contains bounded landmarks and chosen label interval', data['format'] == 'metro-motion-motion-data'
                  and clip['label'] == 'neutral' and clip['range'] == [700, 2000]
                  and 9 <= len(clip['frames']) <= 90 and len(clip['frames'][0]['landmarks']) == 33)
            check('Export has no image, audio or account identifiers', set(data) == {'format', 'version', 'featureVersion', 'source', 'clips'}
                  and set(clip) == {'id', 'sessionId', 'participantId', 'label', 'range', 'baseline', 'frames'}
                  and all(set(frame) == {'t', 'landmarks'} for frame in clip['frames']))
            # Explicitly mark our generated capture before any offline training.
            data['source'] = 'synthetic'
            dataset_path.write_text(json.dumps(data), encoding='utf8')
            page.reload(wait_until='networkidle')
            page.wait_for_function('__motionLab.state().clipCount===1')
            check('Reviewed library persists through IndexedDB reload', lab_state(page)['clipCount'] == 1
                  and page.evaluate('__cameraQA.calls.length===0'))
            page.locator('#clip-list button').click()
            check('Deleting a saved clip updates the local library', lab_state(page)['clipCount'] == 0
                  and page.locator('#download-data').is_disabled())
            page.locator('#undo-delete').click()
            check('Undo restores the deleted clip', lab_state(page)['clipCount'] == 1)
            page.wait_for_function('__motionLab.state().pendingSaves===0')
            before = lab_state(page)
            page.locator('#new-participant').click()
            after = lab_state(page)
            check('Changing participant creates fresh grouping while retaining clips', before['participantId'] != after['participantId']
                  and before['sessionId'] != after['sessionId'] and after['clipCount'] == 1)
            page.evaluate("__cameraQA.mode='success'")
            page.locator('#lab-camera-start').click()
            page.wait_for_function("!document.querySelector('#lab-record').disabled", timeout=20000)
            page.locator('#lab-record').click()
            page.evaluate("__cameraQA.fixture='missing'")
            page.wait_for_function("__motionLab.state().phase==='idle'", timeout=3000)
            check('Lost tracking aborts capture without saving a partial clip', lab_state(page)['clipCount'] == 1
                  and not page.locator('#lab-review').is_visible())
            page.evaluate("__cameraQA.fixture='upright'")
            page.wait_for_function("!document.querySelector('#lab-record').disabled", timeout=10000)
            page.locator('#lab-record').click()
            page.locator('#lab-cancel').click()
            check('Explicit cancellation discards the pending capture', lab_state(page)['phase'] == 'idle'
                  and lab_state(page)['clipCount'] == 1)
            page.locator('#lab-record').click()
            page.wait_for_function("__motionLab.state().phase==='recording'", timeout=7000)
            page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
            check('Hidden-page handler aborts active capture without saving', lab_state(page)['phase'] == 'idle'
                  and lab_state(page)['clipCount'] == 1 and not lab_state(page)['cameraActive'])
            check('Hidden-page handler releases every synthetic video track', page.evaluate("__cameraQA.tracks.every(t=>t.readyState==='ended')"))
            page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
            check('No coordinates leak through readonly diagnostics', 'landmarks' not in json.dumps(lab_state(page)))

            page.goto(base, wait_until='networkidle')
            page.wait_for_function('window.__metroMotion?.state')
            check('Runner links to Motion Lab', page.locator('a[href="./motion-lab.html"]').count() == 1)
            page.locator('#keyboard-start').click()
            page.wait_for_function("__metroMotion.state().uiPhase==='running'", timeout=10000)
            page.locator('#model-panel summary').focus()
            page.keyboard.press('Space')
            check('Keyboard can expand model details during play without jumping', page.locator('#model-panel').get_attribute('open') is not None
                  and game_state(page)['jumpY'] == 0)
            page.locator('#pause-run').click()
            page.locator('#model-panel').evaluate('(node)=>node.open=true')
            import_model(page, fixture_model())
            check('Import defaults to comparison and leaves camera off', game_state(page)['modelMode'] == 'compare'
                  and not game_state(page)['modelEligible'] and page.evaluate('__cameraQA.calls.length===0'))
            check('Failed evaluation cannot select trained controls', page.locator('#model-mode option[value=control]').is_disabled())
            page.locator('#motion-model').set_input_files({'name': 'bad.json', 'mimeType': 'application/json', 'buffer': b'{broken'})
            page.wait_for_function("document.querySelector('#model-status').textContent.includes('previous model')")
            check('Malformed import preserves previous valid model', game_state(page)['modelLoaded'])
            oversized = b' ' * (2 * 1024 * 1024 + 1)
            page.locator('#motion-model').set_input_files({'name': 'large.json', 'mimeType': 'application/json', 'buffer': oversized})
            page.wait_for_function("document.querySelector('#model-status').textContent.includes('2 MB')")
            check('Oversized model is rejected before parsing', game_state(page)['modelLoaded'])
            page.evaluate("__cameraQA.mode='success'")
            page.locator('#camera-start').click()
            page.wait_for_function('__metroMotion.state().calibrated', timeout=20000)
            page.evaluate("__cameraQA.fixture='right'")
            page.wait_for_function("__metroMotion.state().prediction?.label==='left'", timeout=10000)
            page.locator('#camera-run').click()
            page.wait_for_function("__metroMotion.state().uiPhase==='running' && __metroMotion.state().lane===1", timeout=10000)
            check('Conflicting comparison prediction preserves actual rule controls', game_state(page)['lane'] == 1
                  and game_state(page)['prediction']['label'] == 'left' and game_state(page)['controlSource'] == 'rules')
            import_model(page, fixture_model(eligible=True))
            check('Replacing a model pauses an active run and resets to comparison', game_state(page)['uiPhase'] == 'paused'
                  and game_state(page)['modelMode'] == 'compare')
            page.locator('#model-mode').select_option('control')
            check('Trained controls require an explicit mode selection', game_state(page)['modelMode'] == 'control'
                  and game_state(page)['uiPhase'] == 'paused')
            page.locator('#resume-run').click()
            page.wait_for_function("__metroMotion.state().uiPhase==='running' && __metroMotion.state().lane===-1", timeout=10000)
            check('Explicit eligible control follows the trained prediction', game_state(page)['controlSource'] == 'learned')
            page.evaluate("__cameraQA.fixture='missing'")
            page.wait_for_function("__metroMotion.state().uiPhase==='paused'", timeout=3000)
            check('Trained controls retain tracking-loss pause', game_state(page)['pauseReason'] == 'tracking'
                  and game_state(page)['prediction'] is None)
            page.locator('#model-remove').click()
            check('Removing the model restores standard controls and clears prediction', not game_state(page)['modelLoaded']
                  and game_state(page)['modelMode'] == 'compare' and game_state(page)['prediction'] is None)
            page.locator('#camera-stop').click()
            # A removed model cannot reappear after an older file read resolves.
            import_model(page, fixture_model())
            page.evaluate("()=>{window.__originalFileText=File.prototype.text;File.prototype.text=function(){return new Promise(resolve=>window.__finishFileRead=()=>__originalFileText.call(this).then(resolve))}}")
            page.locator('#motion-model').set_input_files({'name': 'slow.json', 'mimeType': 'application/json', 'buffer': json.dumps(fixture_model(True)).encode()})
            page.locator('#model-remove').click()
            page.evaluate('()=>{window.__finishFileRead();File.prototype.text=__originalFileText}')
            page.wait_for_timeout(150)
            check('Removal fences an older pending model import', not game_state(page)['modelLoaded'])
            page.reload(wait_until='networkidle')
            page.wait_for_function('window.__metroMotion?.state')
            check('Reload keeps camera off and does not persist model files', not game_state(page)['modelLoaded']
                  and page.evaluate('__cameraQA.calls.length===0'))
            page.locator('#model-panel').evaluate('(node)=>node.open=true')
            for width in (320, 768, 1440):
                page.set_viewport_size({'width': width, 'height': 1000})
                check(f'Model panel fits {width}px without overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            page.screenshot(path=str(output / 'runner-model-panel.png'), full_page=True)
            check('Local workflow makes no external requests', not report['external'])
            check('Browser has no JavaScript exceptions', not report['errors'], report['errors'])
            browser.close()
    finally:
        server.shutdown()
        report['passed'] = sum(item['passed'] for item in report['checks'])
        report['failed'] = sum(not item['passed'] for item in report['checks'])
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
        print(json.dumps({key: report[key] for key in ('passed', 'failed', 'artifacts', 'errors')}), flush=True)
    if report['failed'] or report['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='chrome', choices=['chrome', 'msedge'])
    run(parser.parse_args())
