"""Focused isolated browser verification for the redesigned login page.

Run ``python tools/browser/verify_login.py``. External integrations are disabled before
Flask imports, and the temporary server listens on loopback only. Registration
and login exercise the existing development fallback and form/session flow;
development mode does not validate account passwords. Reports and screenshots
default to the system temporary directory.
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


def run(args) -> dict:
    isolated_environment()
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    destination = args.screenshots or Path(tempfile.gettempdir()) / "agent-soccer-login-qa"
    destination.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "pages": {}, "artifacts": str(destination),
              "authMode": "isolated development fallback; form/session behavior only"}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(("PASS" if passed else "FAIL") + " " + name, flush=True)

    def metrics(page):
        return page.evaluate("""()=>({
          overflow:document.documentElement.scrollWidth>innerWidth+1,
          width:innerWidth,height:innerHeight,deviceScale:devicePixelRatio,scrollWidth:document.documentElement.scrollWidth,
          scrollHeight:document.documentElement.scrollHeight,
          layout:document.querySelector('.as-login-layout').getBoundingClientRect().toJSON(),
          footer:document.querySelector('.as-login-footer').getBoundingClientRect().toJSON(),
          artwork:document.querySelector('.as-login-pitch-art').getBoundingClientRect().toJSON(),
          artworkMaxHeight:getComputedStyle(document.querySelector('.as-login-pitch-art')).maxHeight,
          form:document.querySelector('form[action="/auth/login"]').getBoundingClientRect().toJSON(),
          email:document.getElementById('email').getBoundingClientRect().toJSON(),
          password:document.getElementById('password').getBoundingClientRect().toJSON(),
          submit:document.querySelector('form[action="/auth/login"] button[type="submit"]').getBoundingClientRect().toJSON(),
          fonts:[...document.querySelectorAll('#email,#password')].map(node=>getComputedStyle(node).fontSize),
          canvas:document.querySelectorAll('canvas').length,
          raf:window.__loginRAF??null,
          focus:document.activeElement.id,
        })""")

    def text_contrast(page):
        # Check ordinary login text; use the brightest forest gradient stop as
        # a conservative background for the story panel, excluding SVG artwork.
        return page.evaluate("""()=>{
          const color=value=>{const parts=value.match(/[\\d.]+/g)?.map(Number)||[];
            return [parts[0]||0,parts[1]||0,parts[2]||0,parts.length>3?parts[3]:1];};
          const blend=(front,back)=>front.slice(0,3).map((channel,i)=>channel*front[3]+back[i]*(1-front[3]));
          const luminance=value=>value.map(channel=>{const c=channel/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;})
            .reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
          const background=node=>{if(node.closest('.as-login-story'))return [40,83,69];
            const chain=[];for(let p=node;p;p=p.parentElement)chain.unshift(p);
            return chain.reduce((bg,p)=>blend(color(getComputedStyle(p).backgroundColor),bg),[255,255,255]);};
          const selectors=['.as-brand','.as-brand-sub','.as-login-nav-action>span','.as-login-nav-action>a',
            '.as-login-story .as-eyebrow','.as-login-story h2','.as-login-story h2 span',
            '.as-login-story-copy>p:last-child','.as-login-pitch-caption span','.as-login-play-note',
            '.as-login-features>span','.as-login-heading .as-eyebrow','.as-login-heading h1',
            '.as-login-heading>p:last-child','.field label','.as-login-field-label a','.as-password-toggle',
            '.as-login-submit','.as-login-register','.as-login-register a','.as-demo-account summary',
            '.as-login-demo-content>p','.auth-demo-row>span','.auth-demo-row code','.auth-demo-row button',
            '#use-demo','.as-login-footer a','.as-login-footer>span','.as-login-error','#toast.show'];
          const records=[];
          for(const selector of selectors)for(const node of document.querySelectorAll(selector)){
            if(!node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))continue;
            const style=getComputedStyle(node),bg=background(node),fg=blend(color(style.color),bg);
            const values=[luminance(fg),luminance(bg)].sort((a,b)=>a-b);
            const font=parseFloat(style.fontSize),weight=parseFloat(style.fontWeight);
            records.push({selector,text:node.textContent.trim().slice(0,70),foreground:style.color,
              background:bg,ratio:(values[1]+.05)/(values[0]+.05),threshold:font>=24||(font>=18.66&&weight>=700)?3:4.5});
          }
          for(const node of document.querySelectorAll('#email,#password')){
            const style=getComputedStyle(node,'::placeholder'),bg=background(node),fg=blend(color(style.color),bg);
            const values=[luminance(fg),luminance(bg)].sort((a,b)=>a-b);
            records.push({selector:'#'+node.id+'::placeholder',foreground:style.color,
              background:bg,ratio:(values[1]+.05)/(values[0]+.05),threshold:4.5});
          }
          return records;
        }""")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width in (320, 375, 768, 1280, 1440, 1920, 720):
                zoom_equivalent = width == 720
                desktop_fit = width in (1280, 1440, 1920)
                context = browser.new_context(
                    viewport={"width": width, "height": 480 if zoom_equivalent else 900 if desktop_fit else 960},
                    device_scale_factor=2 if zoom_equivalent else 1,
                )
                context.add_init_script("""
                  window.__loginRAF=0;
                  const loginOriginalRAF=window.requestAnimationFrame;
                  window.requestAnimationFrame=function(callback){window.__loginRAF++;return loginOriginalRAF.call(this,callback);};
                """)
                page = context.new_page()
                errors, failed, external, posts = [], [], [], []
                page.on("pageerror", lambda error, target=errors: target.append(str(error)))
                page.on("response", lambda response, target=failed: target.append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
                page.on("request", lambda request, target=posts: target.append(request.url) if request.method == "POST" and urlparse(request.url).path == "/auth/login" else None)

                def local_only(route, target=external):
                    if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                        target.append(route.request.url)
                        route.abort()
                    else:
                        route.continue_()
                page.route("**/*", local_only)
                response = page.goto(base + "/login", wait_until="networkidle", timeout=60000)
                page.wait_for_selector("#password + button.as-password-toggle")
                initial = metrics(page)
                check(f"{width}px login renders with its dedicated stylesheet", response.status == 200 and page.locator('link[href="/static/css/frontend-login.css"]').count() == 1)
                check(f"{width}px layout has no horizontal overflow", not initial["overflow"] and initial["form"]["x"] >= 0 and initial["form"]["right"] <= width + 1, initial)
                check(f"{width}px inputs and sign-in action remain usable", min(initial["email"]["height"], initial["password"]["height"], initial["submit"]["height"]) >= 44 and (width > 600 or all(float(value[:-2]) >= 16 for value in initial["fonts"])), initial)
                if desktop_fit:
                    check(f"{width}x900 desktop illustration is capped and its footer fits", initial["artworkMaxHeight"] == '270px' and initial["artwork"]["height"] <= 270 and initial["footer"]["bottom"] <= 900, initial)
                check(f"{width}px native login form contract is preserved", page.locator('form[action="/auth/login"][method="POST"] #email[name="email"][type="email"][required][autocomplete="email"]').count() == 1 and page.locator('form[action="/auth/login"] #password[name="password"][required][autocomplete="current-password"]').count() == 1 and page.locator('label[for="email"]').count() == 1 and page.locator('label[for="password"]').count() == 1)
                hero_color = page.locator('.as-login-story .as-eyebrow').evaluate("node=>getComputedStyle(node).color")
                check(f"{width}px story eyebrow uses the readable lime color", hero_color == 'rgb(216, 239, 129)', hero_color)
                link_targets = page.locator('.as-login-field-label a,.as-login-register a,.as-login-footer a').evaluate_all("nodes=>nodes.map(node=>({text:node.textContent.trim(),height:node.getBoundingClientRect().height}))")
                check(f"{width}px recovery, registration, and home links have 44px targets", all(item['height'] >= 44 for item in link_targets), link_targets)
                check(f"{width}px login avoids autofocus and mobile keyboard jumps", initial["focus"] not in ("email", "password") and page.locator("[autofocus]").count() == 0)
                check(f"{width}px password visibility button has an accessible label", page.get_by_role("button", name="Show password", exact=True).count() == 1)
                page.screenshot(path=str(destination / f"login_{width}.png"), full_page=True)

                page.locator("#password").fill("login-verification-password")
                page.get_by_role("button", name="Show password", exact=True).click()
                check(f"{width}px Show password reveals the existing value", page.locator("#password").get_attribute("type") == "text" and page.locator("#password").input_value() == "login-verification-password" and page.get_by_role("button", name="Hide password", exact=True).get_attribute("aria-pressed") == "true")
                page.get_by_role("button", name="Hide password", exact=True).click()
                check(f"{width}px Hide password restores privacy without submitting", page.locator("#password").get_attribute("type") == "password" and page.get_by_role("button", name="Show password", exact=True).get_attribute("aria-pressed") == "false" and not posts)

                page.locator("#email").fill("")
                page.locator("#password").fill("")
                page.locator('form[action="/auth/login"] button[type="submit"]').click()
                check(f"{width}px empty credentials trigger native validation", not posts and page.locator("#email").evaluate("node=>!node.validity.valid"))
                page.locator("#email").fill("invalid-address")
                page.locator("#password").fill("login-verification-password")
                page.locator('form[action="/auth/login"] button[type="submit"]').click()
                check(f"{width}px invalid email stays in the form", not posts and page.locator("#email").evaluate("node=>node.validity.typeMismatch"))

                page.locator("#email").focus()
                trail = []
                for _ in range(18):
                    trail.append(page.evaluate("""()=>({id:document.activeElement.id,tag:document.activeElement.tagName,
                      label:document.activeElement.getAttribute('aria-label'),href:document.activeElement.getAttribute('href'),
                      type:document.activeElement.getAttribute('type'),visible:document.activeElement.getClientRects().length>0})"""))
                    page.keyboard.press("Tab")
                check(f"{width}px keyboard reaches every core login action", any(item["id"] == "email" for item in trail) and any(item["id"] == "password" for item in trail) and any(item["label"] in ("Show password", "Hide password") for item in trail) and any(item["type"] == "submit" for item in trail) and any(item["href"] == "/forgot-password" for item in trail) and any(item["href"] == "/register" for item in trail) and all(item["visible"] for item in trail), trail)
                page.locator("#email").focus()
                page.keyboard.press("Tab")
                focus_style = page.evaluate("""()=>({outline:getComputedStyle(document.activeElement).outlineStyle,shadow:getComputedStyle(document.activeElement).boxShadow,active:document.activeElement.id})""")
                check(f"{width}px keyboard focus has a visible indicator", focus_style["outline"] != "none" or focus_style["shadow"] != "none", focus_style)
                page.locator(".as-skip").focus()
                page.keyboard.press("Enter")
                check(f"{width}px skip link moves focus to main content", page.evaluate("document.activeElement.id") == "as-content")

                demo = page.locator('details:has(button[aria-label="Copy demo email"])')
                demo.locator("summary").click()
                page.evaluate("()=>{window.__loginCopies=[];navigator.clipboard.writeText=async text=>{window.__loginCopies.push(text)};}")
                page.get_by_role("button", name="Copy demo email", exact=True).click()
                page.wait_for_function("/copied/i.test(document.getElementById('toast').textContent)")
                check(f"{width}px demo email copies with status feedback", page.evaluate("window.__loginCopies") == ["edward@umass.edu"] and page.locator("#toast").get_attribute("role") == "status", page.evaluate("window.__loginCopies"))
                page.get_by_role("button", name="Copy demo password", exact=True).click()
                page.wait_for_function("window.__loginCopies.length===2")
                check(f"{width}px demo password copy preserves the demo value", page.evaluate("window.__loginCopies") == ["edward@umass.edu", "123456"])
                page.evaluate("()=>{navigator.clipboard.writeText=async()=>{throw new DOMException('Unavailable','NotAllowedError')};}")
                page.get_by_role("button", name="Copy demo email", exact=True).click()
                page.wait_for_function("document.getElementById('toast').textContent.includes('unavailable')")
                check(f"{width}px clipboard failure leaves an understandable fallback", "Select" in page.locator("#toast").inner_text())
                page.locator("#use-demo").click()
                check(f"{width}px one-click demo fills both fields without submitting", page.locator("#email").input_value() == "edward@umass.edu" and page.locator("#password").input_value() == "123456" and not posts and page.evaluate("document.activeElement.matches('.as-login-submit')") and "filled in" in page.locator("#toast").inner_text())
                check(f"{width}px demo fill preserves a hidden password", page.locator("#password").get_attribute("type") == "password" and page.get_by_role("button", name="Show password", exact=True).get_attribute("aria-pressed") == "false")
                page.get_by_role("button", name="Show password", exact=True).click()
                page.locator("#use-demo").click()
                check(f"{width}px demo fill also preserves a revealed password", page.locator("#password").get_attribute("type") == "text" and page.locator("#password").input_value() == "123456" and page.get_by_role("button", name="Hide password", exact=True).get_attribute("aria-pressed") == "true" and not posts)
                page.get_by_role("button", name="Hide password", exact=True).click()
                expanded = metrics(page)
                demo_bounds = demo.bounding_box()
                check(f"{width}px expanded demo remains inside the viewport", not expanded["overflow"] and demo_bounds["x"] >= 0 and demo_bounds["x"] + demo_bounds["width"] <= width + 1, expanded)
                page.evaluate("document.getElementById('toast').classList.remove('show')")
                contrasts = text_contrast(page)
                check(f"{width}px visible login text meets focused contrast checks", all(item['ratio'] >= item['threshold'] for item in contrasts), contrasts)
                page.screenshot(path=str(destination / f"login_demo_{width}.png"), full_page=True)
                if zoom_equivalent:
                    check("200% zoom-equivalent login reflows and preserves all actions", initial["deviceScale"] == 2 and not expanded["overflow"] and page.locator("#use-demo").is_visible() and page.locator('main a[href="/forgot-password"]').is_visible() and page.locator('main a[href="/register"]').is_visible(), {"cssViewport": [width, 480], "physicalViewport": [1440, 960], "deviceScale": 2})
                check(f"{width}px helper controls never submit the login form", not posts)
                check(f"{width}px static login artwork queues no animation frames", initial["canvas"] == 0 and initial["raf"] == 0, initial)

                page.locator('main a[href="/forgot-password"]').first.click()
                page.wait_for_url("**/forgot-password")
                check(f"{width}px forgotten-password navigation remains intact", page.locator("#email").count() == 1)
                page.goto(base + "/login", wait_until="networkidle")
                page.locator('main a[href="/register"]').first.click()
                page.wait_for_url("**/register")
                check(f"{width}px account creation navigation remains intact", page.locator("#username").count() == 1 and page.locator("#confirm").count() == 1)
                check(f"{width}px login and helper pages have no JavaScript errors", not errors, errors)
                check(f"{width}px every login asset is local and loads successfully", not failed and not external, {"failed": failed, "external": external})
                report["pages"][str(width)] = {"initial": initial, "expandedDemo": expanded, "keyboard": trail, "contrast": contrasts, "errors": errors, "zoomEquivalent": zoom_equivalent}
                context.close()

            context = browser.new_context(viewport={"width": 1440, "height": 960})
            page = context.new_page()
            auth_errors = []
            page.on("pageerror", lambda error: auth_errors.append(str(error)))
            response = context.request.post(base + "/auth/login", form={"email": "", "password": ""}, max_redirects=0)
            page.goto(base + response.headers["location"], wait_until="networkidle")
            alert = page.locator(".flash[role='alert']")
            check("A server credential error returns a visible, accessible flash", response.status == 302 and alert.is_visible() and "Email and password are required." in alert.inner_text())
            check("The server-error page keeps the login form usable", page.locator("#email").is_enabled() and page.locator("#password").is_enabled() and page.locator('form[action="/auth/login"] button[type="submit"]').is_enabled())
            error_contrast = text_contrast(page)
            check("The flashed login error meets its text contrast threshold", all(item['ratio'] >= item['threshold'] for item in error_contrast), error_contrast)
            page.screenshot(path=str(destination / "login_error.png"), full_page=True)

            page.goto(base + "/register", wait_until="networkidle")
            page.locator("#username").fill("Login QA")
            page.locator("#email").fill("login-qa@example.test")
            page.locator("#password").fill("login-verification-password")
            page.locator("#confirm").fill("login-verification-password")
            page.get_by_role("button", name="Create account", exact=False).click()
            page.wait_for_url("**/lobby")
            check("Real development registration preserves its form and session flow", page.url.endswith("/lobby"))
            page.goto(base + "/login", wait_until="networkidle")
            check("An authenticated visitor still redirects away from login", page.url.endswith("/lobby"))
            page.goto(base + "/auth/logout", wait_until="networkidle")
            check("Logout clears the local session and returns to the guest page", urlparse(page.url).path == "/" and page.locator('.as-account-menu').count() == 0)
            page.goto(base + "/login", wait_until="networkidle")
            page.locator("#email").fill("login-qa@example.test")
            page.locator("#password").fill("login-verification-password")
            page.get_by_role("button", name="Sign in", exact=False).click()
            page.wait_for_url("**/lobby")
            check("Real development login submits the preserved form and opens the locker room", page.url.endswith("/lobby"))
            check("Registration, logout, and login produce no JavaScript exceptions", not auth_errors, auth_errors)
            context.close()

            context = browser.new_context(viewport={"width": 375, "height": 960}, reduced_motion="reduce")
            page = context.new_page()
            page.goto(base + "/login", wait_until="networkidle")
            motion = page.evaluate("""()=>[...document.querySelectorAll('main,main *')].filter(node=>{
              const style=getComputedStyle(node);return style.animationName!=='none'&&parseFloat(style.animationDuration)>0;
            }).map(node=>({tag:node.tagName,class:node.className}))""")
            check("Reduced-motion login contains no decorative animation", not motion, motion)
            context.close()
            browser.close()
    finally:
        server.shutdown()
        output = args.output or destination / "report.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    report = run(args)
    print(f"{sum(check['passed'] for check in report['checks'])}/{len(report['checks'])} checks passed")
    raise SystemExit(0 if all(check["passed"] for check in report["checks"]) else 1)
