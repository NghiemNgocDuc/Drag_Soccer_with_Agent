"""Focused isolated live/replay stadium visual, audio, and idle-render QA.

The local server disables external integrations. Diagnostics are injected into
template copies, and screenshots/reports default to the system temporary folder.
"""
from __future__ import annotations

import argparse
import copy
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

DIAGNOSTICS = r"""
window.__stadiumCheerStarts=[];
const stadiumOriginalPlay=SoundManager._play;
SoundManager._play=function(...args) {
  if(args[3]==='cheer') window.__stadiumCheerStarts.push({wallMs:performance.now(),contextTime:audioCtx.currentTime});
  return stadiumOriginalPlay.apply(this,args);
};
window.__stadiumQA = {
  referee:()=>({visible:refereeMesh.visible,position:refereeMesh.position.toArray(),
    official:refereeMesh.userData.avatar.kit.official,
    stride:refereeMesh.userData.avatar.pose.stride,
    arm:refereeMesh.userData.avatar.arms[1].rotation.x,
    color:refereeMesh.userData.avatar.kit.jersey}),
  refereeClose:()=>{
    if(typeof gameState!=='undefined') window.setViewMode('top');
    updateRefereeMesh(700,437.5);
    refereeMesh.rotation.y=0;
    controls.enableDamping=false;
    camera.position.set(48,35,95);controls.target.set(0,23,0);
    camera.lookAt(controls.target);controls.update();requestRender();
  },
  refereeWalk:()=>{
    updateRefereeMesh(702,440.5,16);
    return {actual:refereeMesh.position.toArray(),expected:serverToWorld(702,440.5).toArray(),
      stride:refereeMesh.userData.avatar.pose.stride};
  },
  refereeWhistle:()=>playRefereeWhistle(),
  refereeHide:()=>{updateRefereeMesh(-100,-100);return refereeMesh.visible;},
  ready:()=>typeof gameState!=='undefined'?!!gameState:state.players_a.length>0,
  audience:()=>typeof crowdData!=='undefined'?crowdData:replayCrowd,
  camera:(view='broadcast')=>{
    if(typeof gameState!=='undefined') window.setViewMode('top');
    controls.enableDamping=false;
    if(view==='close') { camera.position.set(0,95,H/2-170); controls.target.set(0,35,H/2+85); }
    else if(view==='side_close') { camera.position.set(-W/2+180,100,180); controls.target.set(-W/2-80,40,180); }
    else { camera.position.set(-1500,790,320); controls.target.set(0,0,0); }
    if(view==='away') controls.target.copy(camera.position).multiplyScalar(2);
    camera.lookAt(controls.target);controls.update();requestRender();
  },
  stats:()=>{
    const data=window.__stadiumQA.audience();
    const geometry=data?.mesh.geometry;
    const attributes={};
    for(const [key,value] of Object.entries(geometry?.attributes||{})) {
      if(key==='position'||key==='normal'||key==='uv') continue;
      attributes[key]={itemSize:value.itemSize,count:value.count,sample:Array.from(value.array.slice(0,24))};
    }
    const texture=data?.uniforms.uSpriteSheet?.value;
    return {...window.__renderStats(),audience:{
      total:data?.culler.stats().total,visible:data?.culler.stats().visible,
      indices:data?.culler.visibleIndices(),attributes,
      texture:{width:texture?.image?.width,height:texture?.image?.height,version:texture?.version},
      cheer:typeof replayCheerPhase!=='undefined'?replayCheerPhase:data?.uniforms.uCheerPhase?.value,
      info:window.__crowdInfo?window.__crowdInfo():null,
      detail:typeof data?.stats==='function'?data.stats():null,
    },audio:{state:audioCtx.state,muted:SoundManager.isMuted(),ambient:!!SoundManager._ambientSource,
      master:typeof SoundManager.getOutput==='function'?SoundManager.getOutput()?.gain.value:null,
      ambientGain:SoundManager._ambientGain?.gain.value??null,
      ambientChannels:SoundManager._ambientSource?.buffer.numberOfChannels??null,
      effects:SoundManager._effects?.size??null,
      cheerReady:SoundManager._buffers?.has('cheer')??false,
      synthesisChunks:SoundManager._synthesisChunks??null,synthesisLongestMs:SoundManager._synthesisLongestMs??null,
      wallMs:performance.now(),contextTime:audioCtx.currentTime,
      cheerStarts:window.__stadiumCheerStarts,
      whistle:!!window._whistlePlayed,activation:navigator.userActivation?.hasBeenActive,
      events:window.__stadiumEvents??[],
      sharedContext:typeof listener!=='undefined'?listener.context===audioCtx:null,
      outputContext:typeof SoundManager.getOutput==='function'?SoundManager.getOutput()?.context===audioCtx:null,
      contexts:window.__stadiumContexts??null}};
  },
  paletteProbe:()=>{
    const data=window.__stadiumQA.audience(), geometry=data.mesh.geometry.clone();
    geometry.setAttribute('aSpriteIndex',new THREE.InstancedBufferAttribute(new Float32Array([0]),1));
    geometry.setAttribute('aBobOffset',new THREE.InstancedBufferAttribute(new Float32Array([.3]),1));
    geometry.setAttribute('aSection',new THREE.InstancedBufferAttribute(new Float32Array([1]),1));
    const material=data.mesh.material.clone();
    material.uniforms.uSpriteSheet.value=data.uniforms.uSpriteSheet.value;
    material.uniforms.uCamRight.value.set(1,0,0);material.uniforms.uCamUp.value.set(0,1,0);
    material.uniforms.uTime.value=0;material.uniforms.uCheerPhase.value=0;
    material.uniforms.uPaletteMode.value=2;
    const mesh=new THREE.InstancedMesh(geometry,material,1);mesh.setMatrixAt(0,new THREE.Matrix4());mesh.frustumCulled=false;
    const probeScene=new THREE.Scene();probeScene.add(mesh);
    const probeCamera=new THREE.OrthographicCamera(-13,13,38,0,.1,100);probeCamera.position.set(0,0,10);probeCamera.lookAt(0,0,0);
    const target=new THREE.WebGLRenderTarget(128,128), previous=renderer.getRenderTarget(), clearColor=new THREE.Color();
    renderer.getClearColor(clearColor);const clearAlpha=renderer.getClearAlpha();
    const images=[];
    try {
      renderer.setRenderTarget(target);renderer.setClearColor(0,0);
      for(const color of ['#13b970','#a431dd']) {
        material.uniforms.uTeamA.value.set(color);renderer.clear();renderer.render(probeScene,probeCamera);
        const bytes=new Uint8Array(128*128*4);renderer.readRenderTargetPixels(target,0,0,128,128,bytes);images.push(bytes);
      }
      let changed=0,natural=0,preserved=0,opaque=0;
      for(let i=0;i<images[0].length;i+=4) {
        const [r,g,b,a]=images[0].slice(i,i+4);if(a<128)continue;opaque++;
        const difference=Math.max(...[0,1,2].map(channel=>Math.abs(images[0][i+channel]-images[1][i+channel])));
        if(difference>8)changed++;
        if(r>g*1.08 && r>b*1.08 && r>35) {natural++;if(difference<=2)preserved++;}
      }
      return {changed,natural,preserved,opaque,textureResolution:[data.uniforms.uSpriteSheet.value.image.width,data.uniforms.uSpriteSheet.value.image.height]};
    } finally {
      renderer.setRenderTarget(previous);renderer.setClearColor(clearColor,clearAlpha);
      target.dispose();geometry.dispose();material.dispose();requestRender();
    }
  },
  cheer:()=>{ SoundManager.goal();triggerCrowdCheer();return {time:performance.now(),phase:typeof replayCheerPhase!=='undefined'?replayCheerPhase:window.__stadiumQA.audience().uniforms.uCheerPhase.value}; },
};
"""


def instrument(source):
    pattern = r'(<script\b[^>]*\btype=[\'"]module[\'"][^>]*>)(.*?)(</script>)'
    result, count = re.subn(pattern, lambda m: m[1]+m[2]+DIAGNOSTICS+m[3], source, flags=re.S)
    if count != 1:
        raise RuntimeError("Expected exactly one module script")
    return result


def run(args):
    isolated_environment()
    from flask import redirect, render_template, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    def template(name):
        source=(ROOT/"templates"/name).read_text(encoding="utf-8")
        if args.baseline and "getOutput()" not in (ROOT/"static"/"js/game/sound.js").read_text(encoding="utf-8"):
            # Keep visual baselines bootable while the independent audio agent
            # is changing the shared output API in the working tree.
            source=source.replace("listener.getInput().connect(SoundManager.getOutput());", "listener.getInput().connect(audioCtx.destination);")
        return instrument(source)
    templates = {name:template(name) for name in ("game/index_3d.html","game/replay_3d.html")}
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates),application.app.jinja_loader])
    frozen = {name:(ROOT/"static"/name).read_text(encoding="utf-8")
              for name in ("js/game/stadium-crowd.js","js/game/sound.js")} if args.baseline else {}
    seed = new_soccer_state(mode="hvh",player_count=3,half_length=9999)
    first = {"x":700,"y":437.5,"z":0,"a":seed["players_a"],"b":seed["players_b"]}

    @application.app.route("/__stadium/login")
    def login():
        session["user_id"]="dev:stadium-qa";session["username"]="Stadium QA"
        save_game(session["user_id"],copy.deepcopy(seed))
        return redirect("/play3d")

    @application.app.route("/__stadium/replay")
    def replay():
        return render_template("game/replay_3d.html",username="QA",t={"id":"qa","name":"QA"},
            match={"id":"qa","participant_a":"A","participant_b":"B","replay_data":[{"trajectory":[first]}]},
            highlights=[],highlight=None,live_room=None,loss_model=None,loss_model_name=None)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server=make_server("127.0.0.1",0,application.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    destination=args.screenshots or Path(tempfile.gettempdir())/"agent-soccer-stadium-qa"
    destination.mkdir(parents=True,exist_ok=True)
    report={"checks":[],"pages":{},"screenshots":str(destination)}
    def check(name,passed,evidence=None):
        report["checks"].append({"name":name,"passed":bool(passed),"evidence":evidence})
        print(("PASS" if passed else "FAIL")+" "+name,flush=True)
    def settle(page):
        page.wait_for_function("window.__renderStats && !window.__renderStats().loop.pendingFrame && window.__renderStats().loop.pendingCallbacks===0",timeout=60000)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--enable-unsafe-swiftshader"])
            context=browser.new_context(viewport={"width":1280,"height":800},device_scale_factor=1)
            context.add_init_script("""
              localStorage.setItem('agent-soccer-render-quality','1080p');
              window.__stadiumContexts=0;
              window.__stadiumEvents=[];
              for(const type of ['pointerdown','pointerup','click','keydown','touchstart']) {
                window.addEventListener(type,event=>window.__stadiumEvents.push({type,trusted:event.isTrusted,
                  active:navigator.userActivation.isActive,time:performance.now()}),true);
              }
              const NativeAudioContext=window.AudioContext;
              window.AudioContext=new Proxy(NativeAudioContext,{construct(target,args){
                window.__stadiumContexts++;return Reflect.construct(target,args);
              }});
            """)
            for label,path in (("play","/__stadium/login"),("replay","/__stadium/replay")):
                page=context.new_page();errors=[];requests=[]
                def pageerror(error):
                    errors.append(str(error))
                    print(f"ERROR {label}: {error}",flush=True)
                page.on("pageerror",pageerror)
                page.on("response",lambda response,target=requests:target.append({"url":response.url,"status":response.status}) if response.status>=400 else None)
                page.route("**/api/customization",lambda route:route.fulfill(json={"bg_scene":"day","crowd_palette":"classic"}))
                def frozen_route(content):
                    return lambda route:route.fulfill(body=content,content_type="text/javascript")
                for name,source in frozen.items():
                    page.route("**/static/"+name,frozen_route(source))
                page.goto(f"http://127.0.0.1:{server.server_port}"+path,wait_until="networkidle",timeout=60000)
                page.wait_for_function("window.__stadiumQA && window.__stadiumQA.ready()",timeout=60000)
                if args.unlock_only:
                    states={"initial":page.evaluate("window.__stadiumQA.stats().audio")}
                    page.locator("#sound-btn").click();page.wait_for_timeout(1000)
                    states["sound_button"]=page.evaluate("window.__stadiumQA.stats().audio")
                    page.keyboard.press("Shift");page.wait_for_timeout(1000)
                    states["keyboard"]=page.evaluate("window.__stadiumQA.stats().audio")
                    page.locator("#sound-btn").click();page.wait_for_timeout(1000)
                    states["unmute"]=page.evaluate("window.__stadiumQA.stats().audio")
                    check(label+": trusted Sound button gesture resumes context",states["sound_button"]["state"]=="running",states)
                    report["pages"][label]=states
                    page.close()
                    continue
                settle(page)
                if not args.baseline:
                    page.evaluate("window.__stadiumQA.refereeClose()");settle(page)
                    ref=page.evaluate("window.__stadiumQA.referee()")
                    check(label+": human referee uses the official kit",ref["visible"] and ref["official"],ref)
                    page.locator("#three-container").screenshot(path=str(destination/f"{label}_referee_close.png"))
                    walk=page.evaluate("window.__stadiumQA.refereeWalk()")
                    check(label+": referee gait preserves exact server coordinates",walk["actual"]==walk["expected"] and walk["stride"]!=0,walk)
                    page.evaluate("window.__stadiumQA.refereeClose()");settle(page)
                    check(label+": referee stops walking after a snapshot",page.evaluate("window.__stadiumQA.referee().stride")==0)
                    check(label+": penalty coordinates hide the referee",page.evaluate("window.__stadiumQA.refereeHide()") is False)
                    page.evaluate("window.__stadiumQA.refereeClose()");settle(page)
                page.evaluate("window.__stadiumQA.camera('broadcast')");settle(page)
                before=page.evaluate("window.__stadiumQA.stats()")
                page.screenshot(path=str(destination/f"{'baseline_' if args.baseline else ''}{label}_broadcast.png"))
                page.evaluate("window.__stadiumQA.camera('close')");settle(page)
                close=page.evaluate("window.__stadiumQA.stats()")
                page.screenshot(path=str(destination/f"{'baseline_' if args.baseline else ''}{label}_crowd_close.png"))
                if not args.baseline:
                    page.evaluate("window.__stadiumQA.camera('side_close')");settle(page)
                    page.locator("#three-container").screenshot(path=str(destination/f"{label}_crowd_detail.png"))
                    page.evaluate("window.__setScene('night');window.__stadiumQA.camera('side_close')");settle(page)
                    page.locator("#three-container").screenshot(path=str(destination/f"{label}_crowd_detail_night.png"))
                    page.evaluate("window.__stadiumQA.camera('broadcast')");settle(page)
                    page.screenshot(path=str(destination/f"{label}_broadcast_night.png"))
                    page.evaluate("window.__setScene('day');window.__stadiumQA.camera('broadcast')");settle(page)
                    check(label+": 4K graphics option remains available",page.locator('select[aria-label="Graphics resolution"] option[value="4k"]').count()==1)
                print(f"CAPTURED {label} crowd at {destination}",flush=True)
                page.evaluate("window.__stadiumQA.camera('away')");settle(page)
                away=page.evaluate("window.__stadiumQA.stats()")
                check(label+": off-camera crowd submits zero instances",
                      before["audience"]["visible"]>0 and away["audience"]["visible"]==0,
                      {"broadcast":before["audience"]["visible"],"away":away["audience"]["visible"],"calls":[before["drawCalls"],away["drawCalls"]]})
                page.evaluate("window.__stadiumQA.camera('broadcast')");settle(page)
                returned=page.evaluate("window.__stadiumQA.stats()")
                check(label+": camera return restores the same fans",before["audience"]["indices"]==returned["audience"]["indices"])
                if not args.baseline:
                    check(label+": culling preserves every per-fan attribute",before["audience"]["attributes"]==returned["audience"]["attributes"])
                    detail=returned["audience"]["detail"]
                    check(label+": detailed atlas supplies eight people and two poses",bool(detail and detail["atlasLoaded"] and min(detail["atlasResolution"])>=1024 and detail["variants"]==8 and detail["poses"]==2),detail)
                    palette=page.evaluate("window.__stadiumQA.paletteProbe()");settle(page)
                    check(label+": garment palettes preserve natural skin and hair tones",palette["changed"]>50 and palette["natural"]>30 and palette["preserved"]/max(1,palette["natural"])>.98,palette)
                idle=page.evaluate("window.__stadiumQA.stats()")
                page.wait_for_timeout(800);after=page.evaluate("window.__stadiumQA.stats()")
                check(label+": idle crowd queues no GPU frames or callbacks",idle["frames"]==after["frames"] and not after["loop"]["pendingFrame"] and after["loop"]["pendingCallbacks"]==0,{"frames":[idle["frames"],after["frames"]],"loop":after["loop"]})
                if not args.baseline:
                    page.locator("#sound-btn").click()
                    page.wait_for_function("SoundManager._ctx.state==='running' && SoundManager._ambientSource && SoundManager.getOutput().gain.value<.0001",timeout=15000)
                    muted=page.evaluate("window.__stadiumQA.stats().audio")
                    page.locator("#sound-btn").click()
                    page.wait_for_function("SoundManager.getOutput().gain.value>.9999",timeout=5000)
                    audible=page.evaluate("window.__stadiumQA.stats().audio")
                    page.wait_for_function("SoundManager._effects.size===0",timeout=5000)
                    page.evaluate("window.__stadiumQA.refereeWhistle()")
                    page.wait_for_function("window.__stadiumQA.referee().arm < -0.1",timeout=5000)
                    check(label+": whistle raises the referee hand",True)
                    settle(page)
                    check(label+": whistle gesture returns to rest",abs(page.evaluate("window.__stadiumQA.referee().arm"))<.001)
                    check(label+": gesture unlocks one audio context and mute toggles",audible["state"]=="running" and muted["muted"] and not audible["muted"],{"muted":muted,"audible":audible})
                    check(label+": spatial and stadium audio share one context",audible["sharedContext"] is True and audible["outputContext"] is True and audible["contexts"]==1,audible)
                    check(label+": one master mutes and restores the whole mix",muted["master"]==0 and audible["master"]==1,{"muted":muted["master"],"audible":audible["master"]})
                    check(label+": gesture starts stereo stadium ambience",audible["ambient"] and audible["ambientChannels"]==2,audible)
                    page.wait_for_function("SoundManager._effects.size===0",timeout=5000)
                    reaction=page.evaluate("window.__stadiumQA.cheer()")
                    check(label+": goal immediately activates audience reaction",reaction["phase"]>0,reaction)
                    page.wait_for_function("SoundManager._effects.size===1 && SoundManager._ambientGain.gain.value<.036",timeout=15000)
                    cheering=page.evaluate("window.__stadiumQA.stats()")
                    scheduling=cheering["audio"]["cheerStarts"][-1]["wallMs"]-reaction["time"]
                    check(label+": duplicate goal calls coalesce and duck ambience",cheering["audio"]["effects"]==1 and abs(cheering["audio"]["ambientGain"]-0.035)<0.001,{**cheering["audio"],"firstCheerObservedMs":cheering["audio"]["wallMs"]-reaction["time"],"cheerSchedulingMs":scheduling})
                    check(label+": prepared cheer starts promptly with the goal",scheduling<1000,{"cheerSchedulingMs":scheduling,"prepared":audible["cheerReady"]})
                    settle(page)
                    completed=page.evaluate("window.__stadiumQA.stats()")
                    page.wait_for_timeout(800);rested=page.evaluate("window.__stadiumQA.stats()")
                    check(label+": goal reaction ends and returns to idle",completed["audience"]["cheer"]==0 and completed["frames"]==rested["frames"],{"cheer":completed["audience"]["cheer"],"frames":[completed["frames"],rested["frames"]]})
                    page.wait_for_function("SoundManager._effects.size===0 && SoundManager._ambientGain.gain.value>=.099",timeout=12000)
                    restored=page.evaluate("window.__stadiumQA.stats().audio")
                    check(label+": cheer nodes end and ambience restores",restored["effects"]==0 and abs(restored["ambientGain"]-0.10)<.001,restored)
                check(label+": no JavaScript exceptions or failed requests",not errors and not requests,{"errors":errors,"requests":requests})
                report["pages"][label]={"broadcast":before,"close":close,"away":away,"errors":errors}
                if not args.baseline:
                    page.reload(wait_until="networkidle",timeout=60000)
                    page.wait_for_function("window.__stadiumQA && window.__stadiumQA.ready()",timeout=60000)
                    page.keyboard.press("Shift")
                    page.wait_for_function("SoundManager._ctx.state==='running' && SoundManager._ambientSource",timeout=15000)
                    keyboard=page.evaluate("window.__stadiumQA.stats().audio")
                    check(label+": trusted keyboard gesture starts stadium sound",keyboard["state"]=="running" and keyboard["ambient"] and keyboard["contexts"]==1 and any(event["type"]=="keydown" and event["trusted"] for event in keyboard["events"]),keyboard)
                    report["pages"][label]["keyboardAudio"]=keyboard
                page.close()
            browser.close()
    finally:
        server.shutdown()
    output=args.output or destination/("baseline.json" if args.baseline else "report.json")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline",action="store_true")
    parser.add_argument("--unlock-only",action="store_true")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--screenshots",type=Path)
    args=parser.parse_args()
    report=run(args)
    print(f"{sum(item['passed'] for item in report['checks'])}/{len(report['checks'])} checks passed")
    raise SystemExit(0 if all(item["passed"] for item in report["checks"]) else 1)
