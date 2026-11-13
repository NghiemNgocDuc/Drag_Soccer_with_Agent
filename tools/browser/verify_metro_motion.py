"""Isolated browser checks for Metro Motion; never opens a physical camera.

The game and MediaPipe smoke run unmodified. Camera interaction checks use an
explicitly labelled synthetic canvas stream and deterministic landmark worker;
they test lifecycle/input plumbing, not real-person tracking accuracy. The
separate real-worker smoke loads the bundled model and performs real inference.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


CAMERA_HARNESS = r"""
window.__cameraQA={calls:[],tracks:[],requests:[],mode:'deny',fixture:'upright',workerFrames:0};
const qa=window.__cameraQA;
qa.makeStream=()=>{
  const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;
  const context=canvas.getContext('2d');context.fillStyle='#f7f1e7';context.fillRect(0,0,640,480);
  context.fillStyle='#7b2943';context.fillRect(280,70,80,340);
  const stream=canvas.captureStream(20);
  let frame=0;const timer=setInterval(()=>{context.fillStyle=frame++%2?'#7b2943':'#7b2944';context.fillRect(0,0,1,1);},50);
  for(const track of stream.getTracks()){
    const stop=track.stop.bind(track);track.stop=()=>{track.__qaStops=(track.__qaStops||0)+1;stop();
      if(stream.getTracks().every(item=>item.readyState==='ended'))clearInterval(timer);};
    qa.tracks.push(track);
  }
  return stream;
};
navigator.mediaDevices.getUserMedia=constraints=>{
  qa.calls.push(constraints);
  if(qa.mode==='deny')return Promise.reject(new DOMException('Synthetic permission denial','NotAllowedError'));
  if(qa.mode==='pending')return new Promise((resolve,reject)=>qa.requests.push({resolve,reject}));
  return Promise.resolve(qa.makeStream());
};
qa.resolve=()=>{const stream=qa.makeStream();qa.requests.shift().resolve(stream);};
const NativeWorker=window.Worker;
window.Worker=class extends NativeWorker {
  constructor(url,options){
    super(url,options);this.__qaPose=String(url).includes('pose-worker');
    if(this.__qaPose)this.addEventListener('message',({data})=>{
      if(data.type==='pose')qa.lastPose={count:data.landmarks.length,
        visible:data.landmarks.filter(point=>point.visibility>.5).length,
        headToFoot:data.landmarks[27]?.y-data.landmarks[0]?.y};
    });
  }
  postMessage(message,transfer){
    if(this.__qaPose&&message.type==='frame'){
      qa.workerFrames++;super.postMessage({type:'qa-fixture',fixture:qa.fixture});
    }
    return super.postMessage(message,transfer);
  }
};
"""


# Coordinates intentionally represent a known stable standing pose. They are
# generated test input, not model detections or a claim about tracking quality.
FIXTURE_WORKER = r"""
let fixture='upright';
function landmarks(){
  if(fixture==='missing')return [];
  const points=Array.from({length:33},()=>({x:.5,y:.3,z:0,visibility:.99,presence:.99}));
  const put=(i,x,y)=>Object.assign(points[i],{x,y});
  put(0,.5,.12);put(11,.4,.3);put(12,.6,.3);put(13,.36,.43);put(14,.64,.43);
  put(15,.34,.56);put(16,.66,.56);put(23,.43,.56);put(24,.57,.56);
  put(25,.43,.73);put(26,.57,.73);put(27,.43,.91);put(28,.57,.91);
  put(29,.43,.93);put(30,.57,.93);put(31,.4,.94);put(32,.6,.94);
  if(fixture==='left')for(const point of points)point.x+=.2;
  if(fixture==='right')for(const point of points)point.x-=.2;
  if(fixture==='jump')for(const point of points)point.y-=.065;
  if(fixture==='duck'){
    for(let i=0;i<=22;i++)points[i].y+=.13;
    points[23].y+=.07;points[24].y+=.07;
  }
  return points;
}
self.onmessage=event=>{
  const message=event.data;
  if(message.type==='qa-fixture'){fixture=message.fixture;return;}
  if(message.type==='init'){self.postMessage({type:'ready'});return;}
  if(message.type==='frame'){
    message.bitmap?.close();
    self.postMessage({type:'pose',landmarks:landmarks(),timestamp:message.timestamp,id:message.id});
  }
};
"""


def run(args):
    isolated_environment()
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    output = args.screenshots or Path(tempfile.gettempdir()) / ('agent-soccer-metro-motion-qa' + (f'-{args.channel}' if args.channel else ''))
    output.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "errors": [], "failedAssets": [], "external": [], "artifacts": str(output),
              "cameraTests": "synthetic canvas stream + deterministic pose worker; no physical camera",
              "modelSmoke": "real bundled MediaPipe worker and model; no inference mock"}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    def page_for(context):
        page = context.new_page()
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        page.on("response", lambda response: report["failedAssets"].append({"url": response.url, "status": response.status})
                if response.status >= 400 else None)
        def local_only(route):
            if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                report["external"].append(route.request.url)
                route.abort()
            else:
                route.continue_()
        page.route("**/*", local_only)
        return page

    def ready(page):
        response = page.goto(base + "/metro-motion/", wait_until="networkidle", timeout=60000)
        page.wait_for_function("window.__metroMotion?.state", timeout=15000)
        return response

    def state(page):
        return page.evaluate("window.__metroMotion.state()")

    def running(page):
        page.wait_for_function("window.__metroMotion.state().phase==='running'", timeout=15000)

    def lane(page, target):
        current = state(page)["lane"]
        while current != target:
            page.keyboard.press("ArrowRight" if target > current else "ArrowLeft")
            current += 1 if target > current else -1

    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--enable-unsafe-swiftshader"]}
            if args.channel:
                launch['channel'] = args.channel
            elif not Path(playwright.chromium.executable_path).is_file():
                launch["channel"] = "chrome"
            browser = playwright.chromium.launch(**launch)
            report['browserVersion'] = browser.version
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_init_script(CAMERA_HARNESS)
            page = page_for(context)
            response = ready(page)
            check("Public runner loads without a soccer login", response.status == 200)
            private_response = page.request.get(base + '/metro-motion/%2e%2e/README.md')
            check("Runner route cannot serve files outside its public directory", private_response.status == 404)
            check("Camera stays off until explicitly requested", page.evaluate("__cameraQA.calls.length===0"))
            check("Game provides a labelled canvas and keyboard start", bool(page.locator('#runner-canvas').get_attribute('aria-label'))
                  and page.locator('#keyboard-start').is_enabled())
            for width in (320, 390, 768, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(120)
                geometry = page.evaluate("""()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
                  canvas:document.querySelector('#runner-canvas').getBoundingClientRect().toJSON(),
                  start:document.querySelector('#keyboard-start').getBoundingClientRect().toJSON()})""")
                check(f"{width}px layout keeps game and controls within the page", geometry['scroll'] <= width + 1
                      and geometry['canvas']['width'] > 200 and geometry['canvas']['left'] >= -1
                      and geometry['canvas']['right'] <= width + 1, geometry)
                page.screenshot(path=str(output / f"setup-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator('#keyboard-start').click()
            running(page)
            check("Keyboard start enters a playable run without camera permission", state(page)['phase'] == 'running'
                  and page.evaluate('__cameraQA.calls.length===0'))
            page.keyboard.press('ArrowLeft')
            page.wait_for_timeout(180)
            check("Left arrow changes to the left lane", state(page)['lane'] == -1)
            page.keyboard.press('ArrowRight')
            page.keyboard.press('ArrowRight')
            page.wait_for_timeout(180)
            check("Right arrow changes to the right lane", state(page)['lane'] == 1)
            page.keyboard.press('Space')
            page.wait_for_timeout(180)
            check("Space launches a visible jump", state(page)['jumpY'] > .1, state(page)['jumpY'])
            page.wait_for_timeout(1000)
            page.keyboard.down('ArrowDown')
            page.wait_for_timeout(180)
            check("Down arrow holds a duck", state(page)['ducking'])
            page.keyboard.up('ArrowDown')
            page.wait_for_timeout(180)
            check("Releasing Down returns upright", not state(page)['ducking'])
            page.locator('#pause-run').click()
            paused = state(page)
            page.wait_for_timeout(300)
            check("Pause freezes distance and exposes Resume", paused['phase'] == 'paused'
                  and state(page)['distance'] == paused['distance'] and page.locator('#resume-run').is_visible())
            page.locator('#resume-run').click()
            running(page)
            image_before = page.locator('#runner-canvas').evaluate('canvas=>canvas.toDataURL()')
            page.wait_for_timeout(120)
            check("Running canvas draws changing game frames", image_before != page.locator('#runner-canvas').evaluate('canvas=>canvas.toDataURL()'))
            page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
            hidden = state(page)
            page.wait_for_timeout(300)
            check("Hidden tab pauses the run without advancing distance", hidden['phase'] == 'paused'
                  and state(page)['distance'] == hidden['distance'], hidden.get('pauseReason'))
            page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
            check("Returning to the tab waits for explicit Resume", state(page)['phase'] == 'paused')
            page.locator('#resume-run').click()
            running(page)

            # Follow the actual generated route using real keyboard events.
            # No game-state mutation, injected invulnerability, or fake clock.
            passed_rows = set()
            deadline = time.monotonic() + 38
            while time.monotonic() < deadline and len(passed_rows) < 6:
                current = state(page)
                if current['phase'] != 'running':
                    break
                # Remain in the safe lane until the entire train has passed;
                # changing at its front would deliberately run into its side.
                upcoming = sorted((row for row in current['rows'] if row['z'] + 9 > current['distance']), key=lambda row: row['z'])
                if upcoming:
                    lane(page, upcoming[0]['safeLane'])
                passed_rows.update(row['id'] for row in current['rows'] if row['z'] + 9 < current['distance'])
                page.wait_for_timeout(120)
            current = state(page)
            check("Real keyboard play survives six generated obstacle rows", current['phase'] == 'running'
                  and len(passed_rows) >= 6, {"rows": len(passed_rows), "distance": current['distance'], "coins": current['coins']})
            check("Run awards distance score and collected coins", current['score'] > 0 and current['coins'] > 0,
                  {"score": current['score'], "coins": current['coins']})
            page.screenshot(path=str(output / 'keyboard-running.png'), full_page=True)
            # Deliberately remain in a generated obstacle lane until collision.
            if current['phase'] == 'running':
                obstacle_row = next(row for row in sorted(current['rows'], key=lambda item: item['z'])
                                    if row['z'] > current['distance'] + 8 and row['obstacles'])
                lane(page, obstacle_row['obstacles'][0]['lane'])
                page.wait_for_function("window.__metroMotion.state().phase==='over'", timeout=15000)
            check("Natural obstacle collision finishes the run", state(page)['phase'] == 'over' and bool(state(page).get('collision')))
            page.locator('#restart-run').click()
            running(page)
            check("Restart creates a fresh playable run", state(page)['distance'] < 8 and state(page)['coins'] == 0)
            page.locator('#pause-run').click()
            page.screenshot(path=str(output / 'keyboard-paused.png'), full_page=True)
            context.close()

            # No call reaches a webcam: permission denial and delayed responses
            # are synthetic, while the page's controller/lifecycle is unchanged.
            camera_context = browser.new_context(viewport={"width": 1440, "height": 1000})
            camera_context.add_init_script(CAMERA_HARNESS)
            camera_page = page_for(camera_context)
            camera_page.route('**/js/pose-worker.mjs', lambda route: route.fulfill(status=200,
                content_type='text/javascript', body=FIXTURE_WORKER))
            ready(camera_page)
            camera_page.locator('#camera-start').click()
            camera_page.wait_for_function("__cameraQA.calls.length===1")
            camera_page.wait_for_timeout(150)
            check("Denied camera permission leaves an understandable status and keyboard fallback",
                  bool(camera_page.locator('#tracking-status').inner_text().strip()) and camera_page.locator('#keyboard-start').is_enabled())
            check("Camera request never includes audio", camera_page.evaluate("__cameraQA.calls.every(call=>call.audio===false)"))
            camera_page.evaluate("__cameraQA.mode='pending'")
            camera_page.locator('#camera-start').click()
            camera_page.wait_for_function("__cameraQA.requests.length===1")
            camera_page.locator('#camera-stop').click()
            camera_page.evaluate('__cameraQA.resolve()')
            camera_page.wait_for_timeout(200)
            check("Stopping a pending permission request closes a late-arriving stream", camera_page.evaluate(
                "__cameraQA.tracks.length>0&&__cameraQA.tracks.every(track=>track.readyState==='ended')")
                  and not state(camera_page).get('cameraActive'))
            camera_page.evaluate("__cameraQA.mode='success';__cameraQA.fixture='upright'")
            camera_page.locator('#camera-start').click()
            camera_page.wait_for_function("window.__metroMotion.state().calibrated", timeout=15000)
            check("Synthetic full-body landmarks complete steady calibration", state(camera_page)['calibrated']
                  and state(camera_page).get('cameraActive') and camera_page.locator('#camera-run').is_enabled())
            camera_page.locator('#camera-run').click()
            running(camera_page)
            camera_page.evaluate("__cameraQA.fixture='jump'")
            camera_page.wait_for_function("window.__metroMotion.state().jumpY>.1", timeout=3000)
            check("Synthetic body jump launches the actual runner", state(camera_page)['jumpY'] > .1)
            camera_page.evaluate("__cameraQA.fixture='upright'")
            camera_page.wait_for_timeout(900)
            camera_page.evaluate("__cameraQA.fixture='duck'")
            camera_page.wait_for_function("window.__metroMotion.state().ducking", timeout=3000)
            check("Synthetic body squat ducks the actual runner", state(camera_page)['ducking'])
            camera_page.evaluate("__cameraQA.fixture='upright'")
            camera_page.wait_for_function("!window.__metroMotion.state().ducking", timeout=3000)
            camera_page.evaluate("__cameraQA.fixture='left'")
            camera_page.wait_for_function("window.__metroMotion.state().lane===-1", timeout=4000)
            check("Synthetic step left controls the actual game lane", state(camera_page)['lane'] == -1)
            camera_page.evaluate("__cameraQA.fixture='right'")
            camera_page.wait_for_function("window.__metroMotion.state().lane===1", timeout=4000)
            check("Synthetic step right controls the actual game lane", state(camera_page)['lane'] == 1)
            camera_page.evaluate("__cameraQA.fixture='missing'")
            camera_page.wait_for_function("window.__metroMotion.state().phase==='paused'", timeout=5000)
            check("Tracking loss pauses the actual run", state(camera_page)['phase'] == 'paused'
                  and not state(camera_page).get('trackingValid'), state(camera_page).get('pauseReason'))
            camera_page.evaluate("__cameraQA.fixture='upright'")
            camera_page.wait_for_function("window.__metroMotion.state().trackingValid", timeout=5000)
            check("Recovered tracking still waits for explicit Resume", state(camera_page)['phase'] == 'paused')
            camera_page.locator('#recalibrate').click()
            check("Recalibration clears the previous baseline", not state(camera_page)['calibrated'])
            camera_page.wait_for_function("window.__metroMotion.state().calibrated", timeout=15000)
            camera_page.locator('#camera-run').click()
            running(camera_page)
            camera_page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
            camera_page.wait_for_timeout(150)
            check("Hiding camera play pauses the run and releases every camera track", state(camera_page)['phase'] == 'paused'
                  and not state(camera_page)['cameraActive'] and camera_page.evaluate("__cameraQA.tracks.every(track=>track.readyState==='ended')"))
            camera_page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
            calls_before_return = camera_page.evaluate('__cameraQA.calls.length')
            camera_page.wait_for_timeout(200)
            check("Returning never reopens the camera automatically", camera_page.evaluate('__cameraQA.calls.length') == calls_before_return
                  and not state(camera_page)['cameraActive'])
            camera_page.locator('#camera-start').click()
            camera_page.wait_for_function("window.__metroMotion.state().calibrated", timeout=15000)
            camera_page.locator('#camera-stop').click()
            camera_page.wait_for_timeout(150)
            check("Stop camera ends every synthetic media track", camera_page.evaluate(
                "__cameraQA.tracks.every(track=>track.readyState==='ended')") and not state(camera_page).get('cameraActive'))
            camera_page.screenshot(path=str(output / 'camera-stopped.png'), full_page=True)
            report['syntheticCameraFrames'] = camera_page.evaluate('__cameraQA.workerFrames')
            camera_context.close()

            # This uses the real bundled ML runtime/model in its own worker.
            # A blank frame validates executable inference, not pose accuracy.
            model_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            model_page = page_for(model_context)
            ready(model_page)
            model_page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            model_page.locator('#keyboard-start').tap()
            running(model_page)
            model_page.locator('[data-action="right"]').tap()
            check("Mobile touch control switches lanes", state(model_page)['lane'] == 1)
            model_page.locator('[data-action="jump"]').tap()
            model_page.wait_for_timeout(140)
            check("Mobile touch control jumps", state(model_page)['jumpY'] > .1)
            model_page.locator('#pause-run').tap()
            fixture_base64 = base64.b64encode(args.pose_fixture.read_bytes()).decode('ascii') if args.pose_fixture else None
            model_result = model_page.evaluate(r"""async fixture=>{
              const worker=new Worker('./js/pose-worker.mjs');
              const started=performance.now();const messages=[];
              const waitFor=type=>new Promise((resolve,reject)=>{
                const timer=setTimeout(()=>{worker.removeEventListener('message',listener);reject(new Error('Worker '+type+' timeout'));},45000);
                function listener(event){const data=event.data;messages.push({type:data.type,message:data.message});
                  if(data.type==='error'){clearTimeout(timer);worker.removeEventListener('message',listener);reject(new Error(data.message));}
                  if(data.type===type){clearTimeout(timer);worker.removeEventListener('message',listener);resolve(data);}}
                worker.addEventListener('message',listener);
                worker.addEventListener('error',error=>{clearTimeout(timer);reject(new Error(error.message));},{once:true});
              });
              try{
                let pending=waitFor('ready');worker.postMessage({type:'init'});await pending;
                const initializedMs=performance.now()-started;
                const canvas=new OffscreenCanvas(320,240);const ctx=canvas.getContext('2d');ctx.fillStyle='#ddd';ctx.fillRect(0,0,320,240);
                let bitmap=await createImageBitmap(canvas);pending=waitFor('pose');worker.postMessage({type:'frame',bitmap,timestamp:1000,id:1},[bitmap]);
                const blank=await pending;let person=null;
                if(fixture){const blob=await(await fetch('data:image/jpeg;base64,'+fixture)).blob();bitmap=await createImageBitmap(blob);
                  pending=waitFor('pose');worker.postMessage({type:'frame',bitmap,timestamp:2000,id:2},[bitmap]);
                  const result=await pending;person={count:result.landmarks.length,visible:result.landmarks.filter(p=>p.visibility>.5).length};}
                return{initializedMs,elapsedMs:performance.now()-started,blankCount:blank.landmarks.length,person,messages};
              }catch(error){return{error:String(error),messages};}finally{worker.terminate();}
            }""", fixture_base64)
            report['realModel'] = model_result
            check("Real bundled MediaPipe worker initializes and completes blank-frame inference",
                  not model_result.get('error') and model_result.get('blankCount') == 0, model_result)
            if args.pose_fixture:
                check("Real bundled MediaPipe detects a person in the supplied sample image",
                      bool(model_result.get('person')) and model_result['person']['count'] == 33
                      and model_result['person']['visible'] >= 20, model_result.get('person'))
            model_context.close()
            # Exercise the documented standalone command's stdlib HTTP server.
            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
            static_server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler,
                directory=str(ROOT / 'projects/metro-motion/web')))
            threading.Thread(target=static_server.serve_forever, daemon=True).start()
            try:
                standalone = browser.new_context(viewport={"width": 900, "height": 800})
                static_page = page_for(standalone)
                static_page.goto(f'http://127.0.0.1:{static_server.server_port}/')
                static_page.wait_for_function('window.__metroMotion?.state')
                check("Standalone HTTP server loads without Flask or club-only navigation", not static_page.locator('.back-link').is_visible())
                static_page.locator('#keyboard-start').click()
                running(static_page)
                static_page.keyboard.press('ArrowLeft')
                check("Standalone project runs and accepts actual keyboard input", state(static_page)['lane'] == -1)
                standalone.close()
            finally:
                static_server.shutdown()
                static_server.server_close()
            check("All requested local assets load", not report['failedAssets'], report['failedAssets'])
            check("Runner and real ML smoke need no external requests", not report['external'], report['external'])
            check("No browser JavaScript exceptions", not report['errors'], report['errors'])
            browser.close()
    finally:
        server.shutdown()
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--channel', choices=('chrome', 'msedge'), help='Exercise an installed browser explicitly.')
    parser.add_argument('--pose-fixture', type=Path, help='Optional local image containing a person for actual ML smoke; never a webcam.')
    result = run(parser.parse_args())
    failures = [item for item in result['checks'] if not item['passed']]
    print(json.dumps({'passed': len(result['checks']) - len(failures), 'failed': failures, 'artifacts': result['artifacts']}, indent=2))
    raise SystemExit(bool(failures))
