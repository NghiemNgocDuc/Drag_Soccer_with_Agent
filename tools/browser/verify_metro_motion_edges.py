"""Adversarial Metro Motion UI checks, with no physical-camera access.

Uses real browser controls and the shipped app. Synthetic streams/landmarks
exercise lifecycle failures; the optional photo also exercises the complete
real camera-to-MediaPipe pipeline. No game-state mutations or invulnerability.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_metro_motion import CAMERA_HARNESS, FIXTURE_WORKER
from tools.browser.verify_game_performance import isolated_environment


def run(args):
    isolated_environment()
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    output = Path(tempfile.gettempdir()) / f'agent-soccer-metro-motion-edges-{args.channel}'
    output.mkdir(parents=True, exist_ok=True)
    report = {'channel': args.channel, 'checks': [], 'exceptions': [], 'artifacts': str(output),
              'physicalCameraUsed': False}

    def check(name, passed, evidence=None):
        report['checks'].append({'name': name, 'passed': bool(passed), 'evidence': evidence})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)

    def snapshot(page):
        return page.evaluate('window.__metroMotion.state()')

    def phase(page, expected):
        page.wait_for_function('(expected)=>window.__metroMotion.state().uiPhase===expected', arg=expected, timeout=15000)

    def start(page):
        page.locator('#keyboard-start').click()
        phase(page, 'running')

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel, headless=True,
                args=['--enable-unsafe-swiftshader'])
            report['browserVersion'] = browser.version

            def new_page(script='', worker=FIXTURE_WORKER, **options):
                context = browser.new_context(viewport={'width': 1280, 'height': 900}, **options)
                context.add_init_script(CAMERA_HARNESS + '\n' + script)
                page = context.new_page()
                page.on('pageerror', lambda error: report['exceptions'].append(str(error)))
                if worker is not None:
                    page.route('**/js/pose-worker.mjs', lambda route: route.fulfill(status=200,
                        content_type='text/javascript', body=worker))
                page.goto(base + '/metro-motion/')
                page.wait_for_function('window.__metroMotion?.state')
                return context, page

            def scenario(name, body):
                try:
                    body()
                except Exception as error:
                    check(name + ' completes', False, str(error))

            def serving():
                context, page = new_page()
                try:
                    for path, mime in (('js/app.mjs', 'text/javascript'), ('js/pose-worker.mjs', 'text/javascript'),
                                       ('vendor/mediapipe/wasm/vision_wasm_internal.wasm', 'application/wasm')):
                        response = page.request.head(base + '/metro-motion/' + path)
                        check(f'Public asset {path} has the correct browser MIME', response.status == 200 and response.headers.get('content-type', '').startswith(mime))
                    response = page.request.head(base + '/metro-motion/')
                    check('Runner response allows its camera and disables the microphone', response.headers.get('permissions-policy') == 'camera=(self), microphone=()')
                    blocked = [page.request.get(base + '/metro-motion/' + path).status for path in ('%2e%2e%5cREADME.md', '%2e%2e%2fREADME.md', 'no-such-file.mjs')]
                    check('Encoded parent paths and absent files return 404', blocked == [404, 404, 404], blocked)
                    response = page.request.get(base + '/metro-motion/vendor/mediapipe/pose_landmarker_lite.task', headers={'Range': 'bytes=0-15'})
                    check('Model downloads support bounded byte-range responses', response.status == 206 and len(response.body()) == 16)
                    vendor = ROOT / 'projects/metro-motion/web/vendor/mediapipe'
                    manifest = json.loads((vendor / 'checksums.json').read_text(encoding='utf-8-sig'))
                    valid = all((vendor / item['file']).stat().st_size == item['bytes'] and hashlib.sha256((vendor / item['file']).read_bytes()).hexdigest() == item['sha256'] for item in manifest)
                    check('Every bundled model/runtime asset matches its recorded size and checksum', valid, {'files': len(manifest)})
                finally:
                    context.close()
            scenario('Public route and bundled asset integrity', serving)

            def countdown_and_focus():
                context, page = new_page()
                try:
                    page.locator('#keyboard-start').click()
                    page.locator('#pause-run').click()
                    paused = snapshot(page)
                    page.wait_for_timeout(350)
                    check('Pausing the initial countdown freezes a not-yet-started run',
                          paused['uiPhase'] == 'paused' and snapshot(page)['distance'] == 0)
                    page.keyboard.press('p')
                    check('P resumes after the Pause button receives keyboard focus', snapshot(page)['uiPhase'] == 'countdown')
                    if snapshot(page)['uiPhase'] != 'countdown':
                        page.locator('#resume-run').click()
                    phase(page, 'running')
                    page.keyboard.down('p')
                    page.keyboard.down('p')
                    check('Holding P does not repeatedly toggle pause and resume', snapshot(page)['uiPhase'] == 'paused')
                    page.keyboard.up('p')
                    if snapshot(page)['uiPhase'] != 'paused':
                        page.locator('#pause-run').click()
                    page.locator('#difficulty').select_option('normal')
                    check('Pace changes do not alter the current run', snapshot(page)['difficulty'] == 'gentle')
                    page.locator('#keyboard-start').click()
                    phase(page, 'running')
                    check('A new run applies the selected Normal pace', snapshot(page)['difficulty'] == 'normal' and snapshot(page)['speed'] >= 14)
                    page.keyboard.press('a')
                    page.keyboard.press('a')
                    page.keyboard.press('a')
                    check('Repeated A input stays within the leftmost lane', snapshot(page)['lane'] == -1)
                    page.keyboard.press('d')
                    page.keyboard.press('d')
                    page.keyboard.press('d')
                    check('Repeated D input stays within the rightmost lane', snapshot(page)['lane'] == 1)
                    page.locator('#fullscreen-run').click()
                    page.wait_for_function('Boolean(document.fullscreenElement)')
                    page.keyboard.press('ArrowLeft')
                    check('Fullscreen entry preserves keyboard play', snapshot(page)['lane'] == 0)
                    page.keyboard.press('p')
                    check('Pause shortcut works immediately after fullscreen entry', snapshot(page)['uiPhase'] == 'paused')
                    page.evaluate('document.exitFullscreen()')
                    page.wait_for_function('!document.fullscreenElement')
                    check('Exiting fullscreen preserves run state', snapshot(page)['uiPhase'] == 'paused')
                finally:
                    context.close()
            scenario('Countdown, focus, fullscreen and pace', countdown_and_focus)

            def touch_release():
                context, page = new_page(is_mobile=True, has_touch=True)
                try:
                    page.set_viewport_size({'width': 390, 'height': 844})
                    check('Touch actions are disabled before a run', page.locator('[data-action=duck]').is_disabled())
                    start(page)
                    duck = page.locator('[data-action=duck]')
                    duck.focus()
                    page.keyboard.down('Enter')
                    check('Keyboard activation of the on-screen Duck button ducks', snapshot(page)['ducking'])
                    page.keyboard.press('Tab')
                    page.keyboard.up('Enter')
                    check('Moving focus away from held Duck releases it', not snapshot(page)['ducking'])
                    duck.scroll_into_view_if_needed()
                    box = duck.bounding_box()
                    cdp = context.new_cdp_session(page)
                    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': box['x'] + box['width']/2, 'y': box['y'] + box['height']/2, 'id': 7}]})
                    check('Actual touch contact holds Duck', snapshot(page)['ducking'])
                    cdp.send('Input.dispatchTouchEvent', {'type': 'touchCancel', 'touchPoints': []})
                    check('Cancelled touch cannot leave Duck held', not snapshot(page)['ducking'])
                    page.locator('[data-action=left]').tap()
                    page.keyboard.press('ArrowRight')
                    check('Touch lane controls can hand back to keyboard input', snapshot(page)['lane'] == 0)
                    page.keyboard.down('ArrowDown')
                    page.keyboard.down('s')
                    page.keyboard.up('ArrowDown')
                    check('Releasing one of two held duck keys keeps the other active', snapshot(page)['ducking'])
                    page.keyboard.up('s')
                    check('Releasing both duck keys returns upright', not snapshot(page)['ducking'])
                    page.locator('#pause-run').click()
                    check('Touch actions are disabled while paused', page.locator('[data-action=jump]').is_disabled())
                finally:
                    context.close()
            scenario('Touch and focus release', touch_release)

            def navigation_and_idle():
                context, page = new_page("""
                  window.__rafQA={count:0};const originalRAF=window.requestAnimationFrame;
                  window.requestAnimationFrame=callback=>originalRAF.call(window,t=>{__rafQA.count++;callback(t);});
                """)
                try:
                    page.wait_for_timeout(200)
                    before = page.evaluate('__rafQA.count')
                    page.wait_for_timeout(300)
                    check('Setup does not run a continuous animation loop', page.evaluate('__rafQA.count') == before)
                    start(page)
                    page.keyboard.down('ArrowDown')
                    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
                    parked = snapshot(page)
                    page.wait_for_timeout(250)
                    check('Synthetic persisted pagehide pauses gameplay and clears held input', parked['uiPhase'] == 'paused' and not parked['ducking'])
                    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
                    page.wait_for_timeout(300)
                    check('Synthetic persisted pageshow waits for explicit resume', snapshot(page)['uiPhase'] == 'paused' and snapshot(page)['distance'] == parked['distance'])
                    if snapshot(page)['uiPhase'] == 'running':
                        page.locator('#pause-run').click()
                    page.wait_for_timeout(200)
                    before = page.evaluate('__rafQA.count')
                    page.wait_for_timeout(300)
                    check('Paused page does not run a continuous animation loop', page.evaluate('__rafQA.count') == before)
                    copied = page.evaluate('()=>{const s=__metroMotion.state();s.distance=999;s.rows.length=0;return __metroMotion.state();}')
                    check('Diagnostics return a copy and cannot mutate the game', copied['distance'] != 999 and len(copied['rows']) > 0)
                finally:
                    context.close()
            scenario('Navigation lifecycle and idle rendering', navigation_and_idle)

            def storage():
                for saved in ('NaN', 'Infinity', '-20', '123'):
                    context, page = new_page(f"localStorage.setItem('metro-motion-best',{json.dumps(saved)});")
                    try:
                        check(f'Saved best {saved} is handled safely', page.locator('#best-score').inner_text() == ('123' if saved == '123' else '0'))
                    finally:
                        context.close()
                context, page = new_page("""
                  Storage.prototype.getItem=()=>{throw new DOMException('Unavailable','SecurityError');};
                  Storage.prototype.setItem=()=>{throw new DOMException('Full','QuotaExceededError');};
                """)
                try:
                    start(page)
                    page.keyboard.press('ArrowLeft')
                    phase(page, 'over')
                    check('Blocked storage still permits play, scoring and collision', int(page.locator('#best-score').inner_text()) > 0)
                    page.locator('#restart-run').click()
                    phase(page, 'running')
                    check('Blocked storage still permits restarting', snapshot(page)['distance'] < 5)
                finally:
                    context.close()
                context, page = new_page()
                try:
                    start(page)
                    page.keyboard.press('ArrowLeft')
                    phase(page, 'over')
                    best = page.locator('#best-score').inner_text()
                    page.reload()
                    page.wait_for_function('window.__metroMotion?.state')
                    check('Earned personal best survives a reload', best != '0' and page.locator('#best-score').inner_text() == best)
                finally:
                    context.close()
            scenario('Storage failures and persistence', storage)

            def camera_transitions():
                context, page = new_page("__cameraQA.mode='success';")
                try:
                    start(page)
                    page.wait_for_timeout(250)
                    page.locator('#camera-start').click()
                    parked = snapshot(page)['distance']
                    phase(page, 'ready-camera')
                    check('Keyboard-to-camera calibration preserves and pauses the run', snapshot(page)['phase'] == 'paused' and snapshot(page)['distance'] == parked)
                    page.evaluate("__cameraQA.fixture='missing'")
                    page.wait_for_function('!__metroMotion.state().trackingValid')
                    check('Missing body prevents starting a calibrated camera run', page.locator('#camera-run').is_disabled())
                    page.evaluate("__cameraQA.fixture='upright'")
                    page.wait_for_function('__metroMotion.state().trackingValid')
                    page.locator('#camera-run').click()
                    phase(page, 'running')
                    page.keyboard.press('ArrowRight')
                    check('Keyboard movement cannot override body-control mode', snapshot(page)['lane'] == 0)
                    page.evaluate("__cameraQA.fixture='left'")
                    page.wait_for_function('__metroMotion.state().lane===-1')
                    page.locator('#pause-run').click()
                    parked = snapshot(page)
                    page.evaluate("__cameraQA.fixture='right'")
                    page.wait_for_timeout(350)
                    check('Body movement during pause does not move the game', snapshot(page)['lane'] == parked['lane'] and snapshot(page)['distance'] == parked['distance'])
                    page.locator('#keyboard-start').click()
                    phase(page, 'running')
                    check('Camera-to-keyboard switch closes every camera track', page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")') and not snapshot(page)['cameraActive'])
                    check('Camera-to-keyboard switch starts a fresh centered run', snapshot(page)['mode'] == 'keyboard' and snapshot(page)['lane'] == 0 and snapshot(page)['distance'] < 5)
                finally:
                    context.close()
                context, page = new_page("__cameraQA.mode='pending';")
                try:
                    page.locator('#camera-start').click()
                    page.wait_for_function('__cameraQA.requests.length===1')
                    page.locator('#keyboard-start').click()
                    page.evaluate('__cameraQA.resolve()')
                    phase(page, 'running')
                    check('Switching modes during camera permission closes the late stream', snapshot(page)['mode'] == 'keyboard' and page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")'))
                finally:
                    context.close()
            scenario('Camera mode transitions', camera_transitions)

            def camera_failure():
                for label, worker in (
                    ('initialization', "self.onmessage=()=>self.postMessage({type:'error',message:'Model unavailable in this test.'});"),
                    ('inference', "self.onmessage=e=>{if(e.data.type==='init')self.postMessage({type:'ready'});else if(e.data.type==='frame'){e.data.bitmap.close();self.postMessage({type:'error',message:'Inference failed in this test.'});}};"),
                ):
                    context, page = new_page("__cameraQA.mode='success';", worker=worker)
                    try:
                        page.locator('#camera-start').click()
                        page.wait_for_function("document.querySelector('#tracking-status').textContent==='Camera unavailable.'")
                        check(f'Worker {label} failure releases the stream and offers retry', page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")') and page.locator('#camera-start').is_visible() and page.locator('#keyboard-start').is_enabled())
                    finally:
                        context.close()
                context, page = new_page("__cameraQA.mode='success';")
                try:
                    page.locator('#camera-start').click()
                    phase(page, 'ready-camera')
                    page.evaluate("__cameraQA.tracks[0].dispatchEvent(new Event('ended'))")
                    page.wait_for_function('!__metroMotion.state().cameraActive')
                    check('Disconnecting a running camera releases tracks and allows retry', page.locator('#camera-start').is_visible() and page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")'))
                    page.locator('#camera-start').click()
                    phase(page, 'ready-camera')
                    check('Camera can reconnect after disconnect and recalibrate', snapshot(page)['calibrated'])
                finally:
                    context.close()
                context, page = new_page("__cameraQA.mode='success';", worker=None)
                try:
                    page.route('**/pose_landmarker_lite.task', lambda route: route.fulfill(status=404, body='Missing model fixture'))
                    page.locator('#camera-start').click()
                    page.wait_for_function("document.querySelector('#tracking-status').textContent==='Camera unavailable.'", timeout=45000)
                    check('Actual missing-model failure closes the camera and retains keyboard fallback', page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")') and page.locator('#keyboard-start').is_enabled())
                finally:
                    context.close()
            scenario('Camera failures and retry', camera_failure)

            def layouts():
                context, page = new_page(device_scale_factor=3, reduced_motion='reduce')
                try:
                    for width, height in ((320, 568), (667, 375), (1024, 768), (2560, 1440)):
                        page.set_viewport_size({'width': width, 'height': height})
                        page.wait_for_timeout(150)
                        bounds = page.evaluate("""()=>{const c=document.querySelector('#runner-canvas'),o=document.querySelector('.overlay-card'),r=o.getBoundingClientRect(),s=document.querySelector('.stage').getBoundingClientRect();return{scroll:document.documentElement.scrollWidth,pixels:c.width*c.height,edge:Math.max(c.width,c.height),overlayFits:r.top>=s.top&&r.bottom<=s.bottom,transition:getComputedStyle(document.querySelector('.button')).transitionDuration};}""")
                        check(f'{width}×{height} at DPR 3 keeps layout and canvas bounded', bounds['scroll'] <= width + 1 and bounds['pixels'] <= 2600000 and bounds['edge'] <= 3072 and bounds['overlayFits'], bounds)
                        check(f'{width}px reduced motion disables button transitions', bounds['transition'] == '0s')
                    page.set_viewport_size({'width': 3840, 'height': 2160})
                    page.locator('#fullscreen-run').click()
                    page.wait_for_function('Boolean(document.fullscreenElement)')
                    page.wait_for_timeout(200)
                    large = page.locator('#runner-canvas').evaluate('c=>({pixels:c.width*c.height,edge:Math.max(c.width,c.height)})')
                    check('4K fullscreen at DPR 3 respects the actual canvas resource caps', large['pixels'] <= 2600000 and large['edge'] <= 3072, large)
                    page.set_viewport_size({'width': 568, 'height': 320})
                    page.locator('#keyboard-start').click()
                    phase(page, 'running')
                    check('568×320 landscape fullscreen Start can be scrolled to and clicked', snapshot(page)['phase'] == 'running')
                    page.locator('#pause-run').click()
                    page.locator('#resume-run').click()
                    phase(page, 'running')
                    check('Short landscape fullscreen Resume stays reachable', snapshot(page)['phase'] == 'running')
                    page.keyboard.press('ArrowLeft')
                    phase(page, 'over')
                    page.screenshot(path=str(output / 'landscape-fullscreen.png'))
                    page.locator('#restart-run').click()
                    phase(page, 'running')
                    check('Short landscape fullscreen result can restart', snapshot(page)['distance'] < 5)
                    page.evaluate('document.exitFullscreen()')
                    page.evaluate("()=>{document.querySelector('#game').requestFullscreen=()=>Promise.reject(new Error('Fullscreen unavailable in test'));}")
                    page.locator('#fullscreen-run').click()
                    page.wait_for_function("!document.querySelector('#notice').hidden")
                    check('Denied fullscreen gives feedback while keeping the run playable', snapshot(page)['phase'] == 'running' and 'unavailable' in page.locator('#notice').inner_text())
                    page.keyboard.press('ArrowRight')
                    check('Denied fullscreen also preserves actual keyboard movement', snapshot(page)['lane'] == 1)
                finally:
                    context.close()
            scenario('High-density, reduced-motion and short-screen layout', layouts)

            if args.pose_fixture:
                def actual_camera_pipeline():
                    context, page = new_page("__cameraQA.mode='success';", worker=None)
                    try:
                        photo = base64.b64encode(args.pose_fixture.read_bytes()).decode()
                        page.evaluate("""async photo=>{
                          const img=new Image();img.src='data:image/jpeg;base64,'+photo;await img.decode();
                          const original=__cameraQA.makeStream;
                          __cameraQA.makeStream=()=>{
                            const stream=original(),track=stream.getVideoTracks()[0];track.stop();
                            const width=__cameraQA.width||1280,height=__cameraQA.height||720;
                            const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
                            const ctx=canvas.getContext('2d');const scale=Math.min(width/img.width,height/img.height);
                            ctx.fillStyle='#ddd';ctx.fillRect(0,0,width,height);ctx.drawImage(img,(width-img.width*scale)/2,(height-img.height*scale)/2,img.width*scale,img.height*scale);
                            const photoStream=canvas.captureStream(15),photoTrack=photoStream.getVideoTracks()[0];
                            let frame=0;const timer=setInterval(()=>{ctx.fillStyle=frame++%2?'#ddd':'#dde';ctx.fillRect(0,0,1,1);},67);
                            const stop=photoTrack.stop.bind(photoTrack);photoTrack.stop=()=>{clearInterval(timer);stop();};__cameraQA.tracks.push(photoTrack);return photoStream;
                          };
                        }""", photo)
                        page.locator('#camera-start').click()
                        page.wait_for_function('__metroMotion.state().trackingValid', timeout=45000)
                        check('Actual camera capture, bitmap transfer and ML worker detect the photo body', snapshot(page)['trackingValid'] and snapshot(page)['cameraActive'])
                        geometry = page.evaluate("""()=>{const v=document.querySelector('#camera-video'),o=document.querySelector('#pose-overlay');return{video:[v.videoWidth,v.videoHeight],overlay:[o.width,o.height]};}""")
                        check('Pose overlay uses the actual widescreen camera aspect ratio', geometry['overlay'][0]*geometry['video'][1] == geometry['video'][0]*geometry['overlay'][1], geometry)
                        page.locator('#camera-stop').click()
                        check('Real inference pipeline stops and releases its synthetic video stream', page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")'))
                        page.evaluate('__cameraQA.width=720;__cameraQA.height=1280;__cameraQA.lastPose=null')
                        page.locator('#camera-start').click()
                        # A landscape photograph fitted into portrait video may
                        # be too small for standing calibration. Check actual ML
                        # detection and alignment, separately from control framing.
                        page.wait_for_function('__cameraQA.lastPose && document.querySelector("#pose-overlay").width===720', timeout=45000)
                        detection = page.evaluate('__cameraQA.lastPose')
                        check('Real pose model detects the person inside portrait video', detection['count'] == 33 and detection['visible'] >= 20, detection)
                        geometry = page.evaluate("""()=>{const v=document.querySelector('#camera-video'),o=document.querySelector('#pose-overlay');return{video:[v.videoWidth,v.videoHeight],overlay:[o.width,o.height],videoFit:getComputedStyle(v).objectFit,overlayFit:getComputedStyle(o).objectFit};}""")
                        check('Portrait synthetic video keeps its pose overlay aligned', geometry['video'] == [720, 1280] and geometry['overlay'][0]*geometry['video'][1] == geometry['video'][0]*geometry['overlay'][1] and geometry['videoFit'] == geometry['overlayFit'] == 'contain', geometry)
                        page.locator('#camera-stop').click()
                        check('Restarting the real model for a different aspect ratio leaves no camera tracks', page.evaluate('__cameraQA.tracks.every(t=>t.readyState==="ended")'))
                    finally:
                        context.close()
                scenario('Actual camera-to-model pipeline', actual_camera_pipeline)

            check('All adversarial scenarios finish without browser exceptions', not report['exceptions'], report['exceptions'])
            browser.close()
    finally:
        server.shutdown()
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', choices=('chrome', 'msedge'), default='chrome')
    parser.add_argument('--pose-fixture', type=Path)
    report = run(parser.parse_args())
    failures = [item for item in report['checks'] if not item['passed']]
    print(json.dumps({'passed': len(report['checks'])-len(failures), 'failed': failures, 'artifacts': report['artifacts']}, indent=2))
    raise SystemExit(bool(failures))
