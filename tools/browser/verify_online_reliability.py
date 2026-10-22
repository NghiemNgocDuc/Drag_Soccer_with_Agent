"""Exercise the served 2D online lobby and recovery with independent sessions.

Real Flask routes and physics run on an isolated loopback server. Fetch faults
are injected into this verifier's template copy only. No external integrations,
production data or production testing hooks are used. Artifacts go to TEMP.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.browser.verify_game_performance import isolated_environment


DIAGNOSTICS = r"""
const qaFetch=window.fetch.bind(window);
let qaFault=null;
const qaPlayback=[];
const qaAnimation=animateOnlineMove;
animateOnlineMove=async(move,game,...rest)=>{
  qaPlayback.push({room:ONLINE.roomId,revision:move.kick_count,mover:move.mover});
  return qaAnimation(move,game,...rest);
};
window.fetch=async(input,options={})=>{
  const url=typeof input==='string'?input:input.url;
  const method=(options.method||input.method||'GET').toUpperCase();
  const fault=qaFault;
  if(!fault||fault.used||!url.includes(fault.pattern)||method!==fault.method)return qaFetch(input,options);
  fault.used=true;
  if(fault.kind==='503')return new Response(JSON.stringify({error:'Temporary test outage'}),{
    status:503,headers:{'Content-Type':'application/json'}});
  const response=fault.kind==='hang'?null:await qaFetch(input,options);
  if(fault.kind==='lost-fast'){fault.ready=true;throw new TypeError('Response lost after server commit');}
  if(fault.kind==='regress'){
    const data=await response.json();
    data.move_count=Math.max(0,data.move_count-1);
    data.game.online_move_count=data.move_count;
    data.game.kick_count=data.move_count;
    data.game.ball={x:1,y:1};
    return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
  }
  fault.ready=true;
  return new Promise((resolve,reject)=>{
    fault.release=()=>resolve(response);
    if(fault.kind!=='stale'){
      const abort=()=>{fault.aborted=true;reject(new DOMException('Request aborted','AbortError'));};
      if(options.signal?.aborted)abort();else options.signal?.addEventListener('abort',abort,{once:true});
    }
  });
};
window.__onlineQA={
  snapshot:()=>({online:{active:ONLINE.active,room:ONLINE.roomId,side:ONLINE.side,
    revision:ONLINE.lastKick,ready:ONLINE.ready,status:ONLINE.status,polling:ONLINE.polling,
    connectionError:ONLINE.connectionError,generation:ONLINE.generation,
    waiting:ONLINE_LOBBY.waitRoom,resume:ONLINE_LOBBY.resumeRoom},
    game:gameState?{count:gameState.online_move_count??gameState.kick_count,
      turnA:gameState.is_player_a,over:gameState.game_over,ball:gameState.ball,
      a:gameState.players_a,b:gameState.players_b}:null,
    control:window.__playerControlState(),busy:isAnimating,playback:qaPlayback.slice(),
    queues:{ranked:{active:RANKED.active,joining:RANKED.joining,cancelling:RANKED.cancelling},
      quick:{active:QUICK.active,joining:QUICK.joining,cancelling:QUICK.cancelling}},
    fault:qaFault?{used:qaFault.used,ready:qaFault.ready,aborted:qaFault.aborted}:null,
    chatPanels:document.querySelectorAll('#match-chat-wrap .chat-panel').length}),
  poll:()=>pollOnline(),
  fault:(kind,pattern,method='GET')=>{qaFault={kind,pattern,method,used:false,ready:false};},
  release:()=>{qaFault?.release?.();qaFault=null;},
  clearPlayback:()=>{qaPlayback.length=0;},
};
"""


def main():
    isolated_environment()
    from flask import jsonify, redirect, session
    from jinja2 import ChoiceLoader, DictLoader
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    import app as application
    from game.session import save_game
    from models.soccer_logic import new_soccer_state

    source = (ROOT / 'templates/game/index_2d.html').read_text(encoding='utf-8')
    source, count = re.subn(r'(<script type="module">)(.*?)(</script>)',
                          lambda m: m[1] + m[2] + DIAGNOSTICS + m[3], source, flags=re.S)
    if count != 1:
        raise RuntimeError('Expected one served game module script')
    application.app.jinja_loader = ChoiceLoader([
        DictLoader({'game/index_2d.html': source}), application.app.jinja_loader])

    @application.app.route('/__reliability/login/<label>')
    def login(label):
        session['user_id'] = 'dev:online-reliability-' + label
        session['username'] = 'Online QA ' + label.upper()
        game = new_soccer_state(mode='hvh', player_count=5, half_length=9999)
        game['match_started'] = True
        save_game(session['user_id'], game)
        return redirect('/play2d')

    @application.app.route('/__reliability/room/<room_id>')
    def inspect_room(room_id):
        return jsonify(application._get_room(room_id) or {'missing': True})

    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    out = Path(tempfile.gettempdir()) / 'agent-soccer-online-reliability'
    out.mkdir(exist_ok=True)
    checks, errors, posts = [], {}, {}

    def check(name, passed, detail=None):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise AssertionError(f'{name}: {detail}')

    def snapshot(page):
        return page.evaluate('window.__onlineQA.snapshot()')

    def settled(page, revision=None, room_id=None):
        page.wait_for_function('''({revision,room})=>{
          const s=window.__onlineQA?.snapshot();return s?.online.active&&s.online.ready
            &&!s.busy&&!s.online.polling&&!s.online.connectionError
            &&(revision===null||s.game.count===revision)&&(!room||s.online.room===room);
        }''', arg={'revision': revision, 'room': room_id}, timeout=60000)

    def local(page):
        page.wait_for_function('''()=>{const s=window.__onlineQA?.snapshot();
          return s?.game&&!s.online.active&&!s.busy;}''', timeout=30000)

    def room(context, room_id):
        return context.request.get(base + '/__reliability/room/' + room_id).json()

    def wait_room(context, room_id, status):
        deadline = time.monotonic() + 15
        result = {}
        while time.monotonic() < deadline:
            result = room(context, room_id)
            if result.get('status') == status:
                return result
            time.sleep(.05)
        raise AssertionError(f'Room did not reach {status}: {result.get("status")}')

    def show_lobby(page, tab='friends'):
        page.locator('#online-btn').click()
        page.locator('#online-lobby').wait_for(state='visible')
        if tab:
            page.evaluate("tab=>window.setOnlineLobbyTab(tab)", tab)

    def lobby_screenshot(page, name):
        page.locator('.online-lobby-card').evaluate('node=>{node.scrollTop=0;}')
        page.wait_for_timeout(150)
        page.screenshot(path=str(out / name))

    def create(page, password=''):
        if not page.locator('#online-lobby').is_visible():
            show_lobby(page)
        page.locator('#online-create-password').fill(password)
        page.locator('[onclick="createOnlineMatch()"]').click()
        page.wait_for_function('window.__onlineQA.snapshot().online.waiting')
        return snapshot(page)['online']['waiting']

    def join(page, room_id, password=''):
        if not page.locator('#online-lobby').is_visible():
            show_lobby(page)
        page.locator('#online-join-code').fill(base + '/play2d?room=' + room_id)
        page.locator('#online-room-password').fill(password)
        page.locator('[onclick="joinOnlineByCode()"]').click()

    def kick(page):
        page.wait_for_function('window.__playerControlState().canKick')
        page.locator('#player-select').select_option('4')
        page.locator('#aim-angle').fill('0')
        page.locator('#kick-power').evaluate('''node=>{
          node.value='6';node.dispatchEvent(new Event('input',{bubbles:true}));}''')
        page.locator('#kick-btn').click()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--disable-webgl'])

            def page_for(label, *, mobile=False):
                context = browser.new_context(viewport={'width': 390 if mobile else 1440,
                                                        'height': 844 if mobile else 1000})
                context.route('**/*', lambda route: route.continue_()
                              if urlparse(route.request.url).hostname == '127.0.0.1' else route.abort())
                context.add_init_script("localStorage.setItem('agent-soccer-render-quality','auto');")
                page = context.new_page()
                errors[label], posts[label] = [], []
                page.on('pageerror', lambda error: errors[label].append(str(error)))
                page.on('request', lambda req: posts[label].append(urlparse(req.url).path)
                        if req.method == 'POST' else None)
                page.on('dialog', lambda dialog: dialog.accept())
                page.goto(base + '/__reliability/login/' + label)
                local(page)
                return context, page

            ca, a = page_for('a')
            cb, b = page_for('b')
            cm, mobile = page_for('mobile', mobile=True)
            show_lobby(a)
            check('Friends lobby has separate create and invitation join actions',
                  a.locator('#online-create-password').is_visible()
                  and a.locator('#online-join-code').is_visible())
            lobby_screenshot(a, 'lobby-desktop.png')
            show_lobby(mobile)
            check('Mobile lobby fits the viewport', mobile.evaluate(
                'document.documentElement.scrollWidth<=innerWidth'))
            check('Mobile invitation input is readable without automatic zoom',
                  float(mobile.locator('#online-join-code').evaluate(
                      'node=>parseFloat(getComputedStyle(node).fontSize)')) >= 16)
            lobby_screenshot(mobile, 'lobby-mobile.png')
            check('Mobile lobby header and close action can be reached', mobile.evaluate('''()=>{
              const header=document.querySelector('.online-lobby-heading').getBoundingClientRect();
              const close=document.querySelector('#online-lobby .as-dialog-close').getBoundingClientRect();
              const title=document.querySelector('.online-lobby-heading h3').getBoundingClientRect();
              const intro=document.querySelector('.online-lobby-heading p').getBoundingClientRect();
              return header.top>=0&&close.top>=0&&close.bottom<=innerHeight
                &&title.top>=header.top&&intro.bottom<=header.bottom&&intro.bottom<=innerHeight;
            }'''))
            mobile.locator('#online-join-code').scroll_into_view_if_needed()
            mobile.locator('[onclick="joinOnlineByCode()"]').scroll_into_view_if_needed()
            check('Mobile invitation submit is reachable in the lobby scroll area', mobile.evaluate('''()=>{
              const button=document.querySelector('[onclick="joinOnlineByCode()"]').getBoundingClientRect();
              return button.top>=0&&button.bottom<=innerHeight;
            }'''))
            mobile.evaluate("window.setOnlineLobbyTab('matchmaking')")
            check('Matchmaking tab exposes quick and ranked choices',
                  mobile.locator('#quick-play-btn').is_visible() and mobile.locator('#ranked-btn').is_visible())
            check('Matchmaking tab hides friend invitation fields', mobile.locator('#online-join-code').is_hidden())
            lobby_screenshot(mobile, 'matchmaking-mobile.png')
            mobile.locator('[aria-label="Close online lobby"]').click()

            first = create(a, 'qa-only-password')
            a.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})")
            a.locator('#online-copy-btn').click()
            check('Invitation copy remains usable when clipboard access is unavailable', a.evaluate('''()=>{
              const input=document.getElementById('online-invite-link');
              return document.activeElement===input&&input.selectionStart===0
                &&input.selectionEnd===input.value.length
                &&document.getElementById('online-lobby-status').textContent.includes('copy');
            }'''))
            a.evaluate("room=>window.__onlineQA.fault('503','/online/'+room+'/state')", first)
            a.wait_for_function('window.__onlineQA.snapshot().fault?.used')
            a.wait_for_timeout(250)
            check('A failed waiting poll preserves the invitation',
                  snapshot(a)['online']['waiting'] == first and room(ca, first)['status'] == 'waiting')
            check('Failed waiting poll does not send a cancellation',
                  f'/online/{first}/leave' not in posts['a'])
            check('Waiting room can be recovered through the membership endpoint',
                  ca.request.get(base + '/online/current').json().get('room_id') == first)

            join(b, first)
            b.wait_for_function("document.getElementById('online-lobby-status').textContent.includes('password')")
            check('Protected invitation requires its password',
                  not snapshot(b)['online']['active'] and room(ca, first)['status'] == 'waiting')
            join(b, first, 'qa-only-password')
            settled(a, 0, first)
            settled(b, 0, first)
            check('Two independent sessions join the same five-a-side match',
                  snapshot(a)['online']['side'] == 'a' and snapshot(b)['online']['side'] == 'b'
                  and len(snapshot(a)['game']['a']) == len(snapshot(b)['game']['b']) == 5)
            check('Only the active side can kick',
                  snapshot(a)['control']['canKick'] and not snapshot(b)['control']['canKick'])
            check('Joining snaps to current positions without replaying history',
                  not snapshot(a)['playback'] and not snapshot(b)['playback'])

            kick(a)
            settled(a, 1, first)
            settled(b, 1, first)
            check('A real kick synchronizes both clients', snapshot(a)['game']['ball'] == snapshot(b)['game']['ball'])
            kick(b)
            settled(a, 2, first)
            settled(b, 2, first)
            check('The return turn synchronizes full rosters',
                  snapshot(a)['game']['a'] == snapshot(b)['game']['a']
                  and snapshot(a)['game']['b'] == snapshot(b)['game']['b'])
            played = len(snapshot(a)['playback'])
            a.evaluate('window.__onlineQA.poll()')
            a.evaluate('window.__onlineQA.poll()')
            check('Repeated state polls do not replay a completed kick', len(snapshot(a)['playback']) == played)

            a.evaluate("room=>window.__onlineQA.fault('503','/online/'+room+'/state')", first)
            a.evaluate('window.__onlineQA.poll()')
            a.wait_for_function('window.__onlineQA.snapshot().online.connectionError')
            check('Network failure blocks commands until the state is synchronized',
                  not snapshot(a)['control']['canKick'] and a.locator('#online-retry-btn').is_visible())
            check('Connection status explains the recovery', bool(a.locator('#online-connection-status').inner_text().strip()))
            expected_ball = snapshot(a)['game']['ball']
            a.evaluate("room=>window.__onlineQA.fault('regress','/online/'+room+'/state')", first)
            a.locator('#online-retry-btn').click()
            a.wait_for_function('''()=>{const s=window.__onlineQA.snapshot();
              return s.fault?.used&&!s.online.polling&&!!s.online.connectionError;}''')
            check('An older same-room response cannot roll back a recovering match',
                  snapshot(a)['online']['revision'] == 2 and snapshot(a)['game']['count'] == 2
                  and snapshot(a)['game']['ball'] == expected_ball and not snapshot(a)['control']['canKick'])
            a.locator('#online-retry-btn').click()
            settled(a, 2, first)
            check('Retry restores controls without replacing the match', snapshot(a)['control']['canKick'])

            a.evaluate("room=>window.__onlineQA.fault('hang','/online/'+room+'/state')", first)
            a.evaluate('void window.__onlineQA.poll()')
            a.wait_for_function('window.__onlineQA.snapshot().fault?.ready')
            a.wait_for_function('window.__onlineQA.snapshot().fault?.aborted', timeout=30000)
            check('A hung state request has a bounded timeout', snapshot(a)['fault']['aborted'])
            settled(a, 2, first)
            check('Polling recovers after a hung request', snapshot(a)['control']['canKick'])

            before_moves = posts['a'].count(f'/online/{first}/move')
            a.evaluate("room=>window.__onlineQA.fault('lost','/online/'+room+'/move','POST')", first)
            kick(a)
            a.wait_for_function('window.__onlineQA.snapshot().fault?.ready')
            a.wait_for_function('window.__onlineQA.snapshot().fault?.aborted', timeout=30000)
            settled(a, 3, first)
            settled(b, 3, first)
            check('Lost move response recovers the authoritative result without a duplicate kick',
                  room(ca, first)['move_count'] == 3
                  and posts['a'].count(f'/online/{first}/move') == before_moves + 1)
            check('Recovered clients agree on the next player',
                  not snapshot(a)['control']['canKick'] and snapshot(b)['control']['canKick'])

            a.reload()
            settled(a, 3, first)
            check('Refresh resumes the match without re-playing its moves', not snapshot(a)['playback'])
            a.goto(base + '/play2d')
            local(a)
            show_lobby(a)
            a.locator('#online-resume-card').wait_for(state='visible')
            check('Opening the game without a room URL offers the existing match',
                  ca.request.get(base + '/online/current').json().get('room_id') == first)
            a.locator('#online-resume-btn').click()
            settled(a, 3, first)
            check('Resume restores membership and revision', snapshot(a)['online']['side'] == 'a')
            a.screenshot(path=str(out / 'match-desktop.png'), full_page=True)

            a.evaluate("room=>window.__onlineQA.fault('stale','/online/'+room+'/state')", first)
            a.evaluate('void window.__onlineQA.poll()')
            a.wait_for_function('window.__onlineQA.snapshot().fault?.ready')
            a.locator('#online-leave-btn').click()
            local(a)
            old = wait_room(ca, first, 'done')
            check('Explicit leaving forfeits the match once', old['game']['game_over'] and old['game']['winner'].lower() == 'b')
            b.wait_for_function('window.__onlineQA.snapshot().game.over')
            b.locator('#online-leave-btn').click()
            local(b)
            second = create(a)
            join(b, second)
            settled(a, 0, second)
            settled(b, 0, second)
            a.evaluate('window.__onlineQA.release()')
            a.wait_for_timeout(250)
            check('Delayed old-room response cannot overwrite a new match',
                  snapshot(a)['online']['room'] == second and snapshot(a)['game']['count'] == 0
                  and not snapshot(a)['game']['over'] and snapshot(a)['control']['canKick'])
            check('Room transitions leave one chat panel per player',
                  snapshot(a)['chatPanels'] == snapshot(b)['chatPanels'] == 1)
            a.locator('#online-leave-btn').click()
            local(a)
            b.wait_for_function('window.__onlineQA.snapshot().game.over')
            b.locator('#online-leave-btn').click()
            local(b)
            waiting = create(a)
            a.locator('[aria-label="Close online lobby"]').click()
            cancelled = wait_room(ca, waiting, 'cancelled')
            check('Cancelling an unjoined invitation cannot award a win',
                  cancelled['game']['game_over'] and not cancelled['game'].get('winner'))
            check('Cancelled invitation is absent from recoverable membership',
                  not ca.request.get(base + '/online/current').json().get('room_id'))
            for queue, join_url, cancel_url, join_button, cancel_button in (
                ('ranked', '/ranked/join', '/ranked/cancel', '#ranked-btn', '#ranked-cancel-btn'),
                ('quick', '/api/quick/join', '/api/quick/cancel', '#quick-play-btn', '#quick-cancel-btn'),
            ):
                show_lobby(mobile)
                mobile.locator('#online-tab-matchmaking').click()
                before_joins = posts['mobile'].count(join_url)
                mobile.evaluate("url=>window.__onlineQA.fault('lost-fast',url,'POST')", join_url)
                mobile.locator(join_button).click()
                mobile.wait_for_function('''name=>{const s=window.__onlineQA.snapshot();
                  return s.fault?.used&&s.queues[name].active&&!s.queues[name].joining;}''', arg=queue)
                check(f'Lost {queue} join response preserves the server search without a duplicate join',
                      cm.request.get(base + '/online/current').json().get('queue') == queue
                      and posts['mobile'].count(join_url) == before_joins + 1
                      and mobile.locator(cancel_button).is_visible())
                before_cancels = posts['mobile'].count(cancel_url)
                mobile.evaluate("url=>window.__onlineQA.fault('lost-fast',url,'POST')", cancel_url)
                mobile.locator(cancel_button).click()
                mobile.wait_for_function('''name=>{const s=window.__onlineQA.snapshot();
                  return s.fault?.used&&!s.queues[name].active&&!s.queues[name].cancelling;}''', arg=queue)
                check(f'Lost {queue} cancellation response reconciles to an idle queue',
                      cm.request.get(base + '/online/current').json().get('status') == 'idle'
                      and posts['mobile'].count(cancel_url) == before_cancels + 1
                      and mobile.locator(join_button).is_enabled())
                mobile.locator(join_button).click()
                mobile.wait_for_function('''name=>{const q=window.__onlineQA.snapshot().queues[name];
                  return q.active&&!q.joining;}''', arg=queue)
                mobile.reload()
                local(mobile)
                show_lobby(mobile, tab=None)
                mobile.wait_for_function('''name=>{const q=window.__onlineQA.snapshot().queues[name];
                  return q.active&&!q.joining;}''', arg=queue)
                check(f'Reopening the lobby after refresh restores the {queue} search',
                      mobile.locator(cancel_button).is_visible()
                      and mobile.locator('#online-tab-matchmaking').get_attribute('aria-selected') == 'true'
                      and cm.request.get(base + '/online/current').json().get('queue') == queue)
                mobile.locator(cancel_button).click()
                mobile.wait_for_function('''name=>{const q=window.__onlineQA.snapshot().queues[name];
                  return !q.active&&!q.cancelling;}''', arg=queue)
                mobile.locator('[aria-label="Close online lobby"]').click()
            check('Online flow has no uncaught JavaScript exceptions', not any(errors.values()), errors)
            for context in (ca, cb, cm):
                context.close()
            browser.close()
    finally:
        server.shutdown()
        (out / 'report.json').write_text(json.dumps({'checks': checks, 'errors': errors,
                                                   'posts': posts}, indent=2), encoding='utf-8')
    print(f'{sum(item["passed"] for item in checks)}/{len(checks)} checks passed; artifacts: {out}')


if __name__ == '__main__':
    main()
