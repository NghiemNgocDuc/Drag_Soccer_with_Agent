"""Focused isolated Chromium checks for frustum culling and physical ball size."""
from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
import re
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment

DIAGNOSTICS = r"""
window.__visibilityQA = {
  ready:()=>typeof gameState!=='undefined'?!!gameState:state.players_a.length>0,
  camera:(away=false)=>{
    if(typeof gameState!=='undefined') window.setViewMode('top');
    controls.enableDamping=false;
    camera.position.set(-1500,790,320);
    controls.target.set(0,0,0);
    if(away) controls.target.copy(camera.position).multiplyScalar(2);
    camera.lookAt(controls.target);controls.update();requestRender();
  },
  stats:()=>{
    const meshes=[];scene.traverse(object=>{if(object.userData.frustumCuller) meshes.push(object)});
    return {...window.__renderStats(), selections:meshes.map(mesh=>mesh.userData.frustumCuller.visibleIndices())};
  },
  ball:(size)=>{
    const active=typeof gameState!=='undefined'?gameState:state;
    active.ball_size=size;active.ball={x:700,y:437.5,z:0};syncBallSize(active);
    squashActive=false;setBallSquash();updateBallMesh(700,437.5,0);
    const normal=window.__ballFX();
    squashActive=true;squashT=sceneNow();tickBallSquash(squashT+75);
    const squashed=window.__ballFX();
    tickBallSquash(squashT+160);
    const restored=window.__ballFX();requestRender();
    return {normal,squashed,restored};
  },
  trajectory:()=>{
    const active=typeof gameState!=='undefined'?gameState:state;
    const players=team=>team.map(p=>({x:p.x,y:p.y}));
    ballMesh.quaternion.identity();updateBallMesh(700,437.5,0);
    const trajectory=[0,1,2].map(i=>({x:700+i*6,y:437.5,z:0,t:i*.1,
      ball_size:i===0?active.ball_size:undefined,a:players(active.players_a),b:players(active.players_b)}));
    isAnimating=true;
    return new Promise(resolve=>animateTrajectory3D(trajectory,()=>{
      isAnimating=false;requestRender();resolve({...window.__ballFX(),angle:ballMesh.quaternion.angleTo(new THREE.Quaternion())});
    }));
  },
  particle:(inside)=>{
    triggerDust(inside?0:10000,inside?0:10000);
    dust.active=false;dustMesh.material.opacity=1;
    window.__dustDraws=0;dustMesh.onBeforeRender=()=>window.__dustDraws++;
    requestRender();
  },
  particleStats:()=>({draws:window.__dustDraws,center:dustMesh.geometry.boundingSphere.center.toArray(),
    radius:dustMesh.geometry.boundingSphere.radius,visible:dustMesh.visible}),
  clearParticle:()=>{dust.active=false;dustMesh.visible=false;requestRender();},
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

    templates = {name:instrument((ROOT/"templates"/name).read_text(encoding="utf-8"))
                 for name in ("game/index_3d.html","game/replay_3d.html")}
    application.app.jinja_loader = ChoiceLoader([DictLoader(templates),application.app.jinja_loader])
    seed = new_soccer_state(mode="hvh",player_count=3,half_length=9999)
    seed["ball_size"] = "large"
    first = {"x":700,"y":437.5,"z":0,"ball_size":"large",
             "a":seed["players_a"],"b":seed["players_b"]}

    @application.app.route("/__visibility/login")
    def login():
        session["user_id"]="dev:visibility-qa";session["username"]="Visibility QA"
        save_game(session["user_id"],copy.deepcopy(seed))
        return redirect("/play3d")

    @application.app.route("/__visibility/replay")
    def replay():
        return render_template("game/replay_3d.html",username="QA",t={"id":"qa","name":"QA"},
            match={"id":"qa","participant_a":"A","participant_b":"B","replay_data":[{"trajectory":[first]}]},
            highlights=[],highlight=None,live_room=None,loss_model=None,loss_model_name=None)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server=make_server("127.0.0.1",0,application.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    report={"checks":[],"pages":{}}
    def check(name,passed,evidence=None):
        report["checks"].append({"name":name,"passed":bool(passed),"evidence":evidence})
        print(("PASS" if passed else "FAIL")+" "+name,flush=True)
    def settle(page):
        page.wait_for_function("window.__renderStats && !window.__renderStats().loop.pendingFrame && window.__renderStats().loop.pendingCallbacks===0",timeout=60000)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--enable-unsafe-swiftshader"])
            context=browser.new_context(viewport={"width":1280,"height":800},device_scale_factor=1)
            context.add_init_script("localStorage.setItem('agent-soccer-render-quality','1080p');")
            for label,path in (("play","/__visibility/login"),("replay","/__visibility/replay")):
                page=context.new_page();errors=[]
                page.on("pageerror",lambda error,target=errors:target.append(str(error)))
                # A viewer's preference must not change this large match ball.
                page.route("**/api/customization",lambda route:route.fulfill(json={"ball_size":"small","bg_scene":"day"}))
                page.goto(f"http://127.0.0.1:{server.server_port}"+path,wait_until="networkidle",timeout=60000)
                page.wait_for_function("window.__visibilityQA && window.__visibilityQA.ready()",timeout=60000)
                settle(page)
                initial=page.evaluate("window.__ballFX()")
                check(label+": match size overrides viewer size",abs(initial["radius"]-14.16)<1e-8,initial)
                page.evaluate("window.__visibilityQA.camera(false)");settle(page)
                before=page.evaluate("window.__visibilityQA.stats()")
                page.evaluate("window.__visibilityQA.camera(true)");settle(page)
                away=page.evaluate("window.__visibilityQA.stats()")
                count=lambda item:sum(len(indices) for indices in item["selections"])
                check(label+": looking away culls crowd instances and reduces draw submissions",
                      count(before)>0 and count(away)==0 and away["drawCalls"]<before["drawCalls"],
                      {"visible":count(before),"away":count(away),"calls":[before["drawCalls"],away["drawCalls"]]})
                page.evaluate("window.__visibilityQA.camera(false)");settle(page)
                returned=page.evaluate("window.__visibilityQA.stats()")
                check(label+": returning camera restores the exact visible fans",before["selections"]==returned["selections"])
                for inside in (False,True):
                    page.evaluate("inside=>window.__visibilityQA.particle(inside)",inside);settle(page)
                    particle=page.evaluate("window.__visibilityQA.particleStats()")
                    check(label+(": relocated particles reenter the frame" if inside else ": particles outside frame submit no draw"),
                          particle["draws"]>0 if inside else particle["draws"]==0,particle)
                page.evaluate("window.__visibilityQA.clearParticle()");settle(page)
                for size,scale in (("small",.82),("normal",1),("large",1.18),(None,1)):
                    result=page.evaluate("size=>window.__visibilityQA.ball(size)",size)
                    radius=12*scale
                    check(label+f": {size or 'legacy'} size survives squash and reset",
                          all(abs(result[phase]["radius"]-radius)<1e-8 for phase in ("normal","squashed","restored"))
                          and all(abs(v-scale)<1e-8 for v in result["restored"]["scale"])
                          and abs(result["normal"]["pos"][1]-radius)<1e-8
                          and result["squashed"]["pos"][1]>0,result)
                    if size is None:
                        continue
                    moved=page.evaluate("window.__visibilityQA.trajectory()");settle(page)
                    check(label+f": {size} trajectory uses effective rolling radius",
                          abs(moved["radius"]-radius)<1e-8 and abs(moved["angle"]-12/radius)<1e-5
                          and abs(moved["pos"][0]-12)<1e-8 and abs(moved["pos"][1]-radius)<1e-8,moved)
                settle(page);idle=page.evaluate("window.__visibilityQA.stats()")
                page.wait_for_timeout(1000);after=page.evaluate("window.__visibilityQA.stats()")
                check(label+": idle camera queues no GPU frames or callbacks",
                      idle["frames"]==after["frames"] and not after["loop"]["pendingFrame"] and after["loop"]["pendingCallbacks"]==0,
                      {"frames":[idle["frames"],after["frames"]],"loop":after["loop"]})
                check(label+": no JavaScript exceptions",not errors,errors)
                report["pages"][label]={"before":before,"away":away,"returned":returned,"errors":errors}
                if args.screenshots:
                    args.screenshots.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(args.screenshots/f"visible_{label}.png"))
                page.close()
            browser.close()
    finally:
        server.shutdown()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path)
    parser.add_argument("--screenshots",type=Path)
    args=parser.parse_args()
    report=run(args)
    print(f"{sum(item['passed'] for item in report['checks'])}/{len(report['checks'])} checks passed")
    raise SystemExit(0 if all(item["passed"] for item in report["checks"]) else 1)
