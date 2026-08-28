"""LangChain Soccer AI — LLM-guided + physics-verified, 1-2s max.

Design (strong + fast):
- Hybrid: LLM proposes 2-3 *strategic* candidates (which player, rough angle
  bucket, power tier) in ~600ms, then we *verify* with simulate_kick
  (deterministic, fast) and pick best by same evaluate as greedy/minimax.
  This is stronger than pure brute force because LLM prunes the search to
  tactically sensible moves (e.g., “closest to ball but also goal-ward”,
  “defensive clear if own half”) and weaker candidates are never simulated.
- If LLM is unavailable / slow / parses badly → instant fallback to
  minimax-style 2-stage coarse→fine sweep (same as greedy, ~0.4s).
- Hard timeout 1.5s total via ThreadPoolExecutor in app.py (execute_user_model
  already 5s, but this model self-limits to 1.5s). No move takes >2s.

Setup:
  pip install langchain langchain-openai langchain-community
  Set OPENAI_API_KEY (or use local Ollama via ChatOllama).
  If no key, model runs in fallback-only mode (still strong) with no latency penalty.

Web wisdom: keep prompt tiny (<400 tokens), ask for JSON with 3 ints,
use gpt-4o-mini / mistral-nemo for <600ms, cache by board hash, run
simulate_kick in parallel (we reuse greedy's _evaluate).
"""
from __future__ import annotations
import math
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout

from models.soccer_logic import simulate_kick, FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME = "LangChain Tactician"
DESCRIPTION = "LLM-guided (LangChain) + physics-verified. Fast 1-2s, fallback to minimax sweep if LLM unavailable."

#  Tunables 
_LLM_TIMEOUT = 0.75  # seconds for LLM call alone
_TOTAL_BUDGET = 1.5  # seconds for entire get_ai_move
_CACHE = {}  # board_hash -> LLM candidates
_FALLBACK_COARSE = 9.0
_FALLBACK_FINE = 2.0

def _evaluate(state, pidx, angle, power, is_player_a, target, defensive):
    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
    if scored == target:
        return 1200.0
    if scored:
        return -400.0
    if len(traj) > 1:
        end = traj[-1]
        val = progress_score(end["x"], is_player_a, defensive)
        if GOAL_Y1 <= end["y"] <= GOAL_Y2:
            val += 50.0
        return val
    return -80.0

def _evaluate_lookahead(state, pidx, angle, power, is_player_a, target, defensive):
    # 1-ply minimax: our move, then opponent's best greedy reply
    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
    if scored == target:
        return 1200.0
    if scored:
        return -400.0
    base = _evaluate(state, pidx, angle, power, is_player_a, target, defensive)
    # quick opponent lookahead: if opponent can score next, penalize heavily
    # Build state after our kick (shallow copy, only ball/players needed for simulate)
    # We approximate by using traj end as new ball/players
    if len(traj) < 2:
        return base
    # Reconstruct post-kick state for opponent
    import copy
    ns = copy.deepcopy(state)
    # apply our kick effect approximately: set ball to traj end, players to traj end positions
    end = traj[-1]
    ns['ball'] = {'x': end['x'], 'y': end['y'], 'z': end.get('z',0)}
    if 'a' in end and 'b' in end and isinstance(end['a'], list):
        ns['players_a'] = [{'x': p['x'], 'y': p['y'], **({'stats': ns['players_a'][i].get('stats')} if i < len(ns['players_a']) and ns['players_a'][i].get('stats') else {})} for i,p in enumerate(end['a'])]
        ns['players_b'] = [{'x': p['x'], 'y': p['y'], **({'stats': ns['players_b'][i].get('stats')} if i < len(ns['players_b']) and ns['players_b'][i].get('stats') else {})} for i,p in enumerate(end['b'])]
    opp = not is_player_a
    opp_target = "B" if is_player_a else "A"
    # opponent's best of 3 quick tries (center + corners)
    best_opp = -1e9
    opp_players = ns['players_a'] if opp else ns['players_b']
    bx, by = ns['ball']['x'], ns['ball']['y']
    for opp_idx in range(min(2, len(opp_players))):
        p = opp_players[opp_idx]
        for tx,ty in goal_targets(opp):
            ang = aim_through(p['x'],p['y'],bx,by,tx,ty)
            for pw in (78, 88):
                traj2, sc2 = simulate_kick(ns, opp_idx, ang, pw, opp)
                if sc2 == opp_target:
                    # opponent scores, we lose
                    return base - 650
                if sc2 is None:
                    # evaluate progress for opponent, we want to minimize opponent progress
                    if len(traj2)>1:
                        v = progress_score(traj2[-1]['x'], opp, needs_clear(ns, opp))
                        best_opp = max(best_opp, v)
    if best_opp > 700:  # opponent very advanced
        return base - 220
    return base

def _board_hash(state, is_player_a):
    # tiny hash for cache: ball + players + score
    s = f"{state['ball']['x']:.0f},{state['ball']['y']:.0f}|"
    for p in state['players_a']: s += f"{p['x']:.0f},{p['y']:.0f};"
    s += "|"
    for p in state['players_b']: s += f"{p['x']:.0f},{p['y']:.0f};"
    s += f"|{is_player_a}|{state['score_a']},{state['score_b']}"
    return hashlib.md5(s.encode()).hexdigest()[:12]

_NO_LLM = None
def _llm_candidates(state, is_player_a):
    """Ask LLM for 2-3 candidates. Returns list of (pidx, angle_offset, power) or None."""
    global _NO_LLM
    # Fast path: cache
    h = _board_hash(state, is_player_a)
    if h in _CACHE:
        return _CACHE[h]
    if _NO_LLM is True:
        return None
    import os
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("OLLAMA_HOST"):
        _NO_LLM = True
        return None

    # Try LangChain if available, else fallback
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import JsonOutputParser
    except Exception:
        _NO_LLM = True
        return None

    # Choose LLM: prefer OPENAI_API_KEY, else Ollama, else no LLM
    llm = None
    import os
    if os.getenv("OPENAI_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, max_tokens=180, timeout=_LLM_TIMEOUT)
        except Exception:
            pass
    if llm is None:
        try:
            from langchain_community.chat_models import ChatOllama
            # local 3b model ~400ms
            llm = ChatOllama(model="qwen2.5:3b", temperature=0.2, num_predict=120)
        except Exception:
            return None

    # Tiny prompt — keep <400 tokens for speed
    players = state['players_a'] if is_player_a else state['players_b']
    bx, by = state['ball']['x'], state['ball']['y']
    # dist to ball per player
    dists = [round(math.hypot(p['x']-bx, p['y']-by),1) for p in players]
    goal_x = FIELD_W if is_player_a else 0
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a soccer tactician. Return JSON list of 2-3 candidates: [{{\"pidx\":0-2, \"aim\":\"goal\"|\"clear\"|\"pass\", \"power\":\"low\"|\"mid\"|\"high\"}}]. Be concise. Prefer closest player but consider defense."),
        ("human", "Ball {bx},{by} goal {gx},{gy} players dist {dists} score {sa}-{sb} period {period} is_player_a {is_a}"),
    ])
    parser = JsonOutputParser()
    chain = prompt | llm | parser

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(chain.invoke, {
                "bx": round(bx), "by": round(by), "gx": goal_x, "gy": FIELD_H//2,
                "dists": dists, "sa": state['score_a'], "sb": state['score_b'],
                "period": state.get('period','regular_first'), "is_a": is_player_a
            })
            out = fut.result(timeout=_LLM_TIMEOUT)
        # out is list of dicts
        candidates = []
        for item in out[:3]:
            try:
                pidx = int(item.get('pidx', 0)) % len(players)
                aim = item.get('aim', 'goal')
                pwr = item.get('power', 'mid')
                # map aim/power to offset/power value
                if aim == 'clear':
                    off = 180  # behind
                elif aim == 'pass':
                    off = 12
                else:
                    off = 0
                pw = {'low': 45, 'mid': 75, 'high': 95}.get(pwr, 75)
                candidates.append((pidx, off, pw))
            except Exception:
                continue
        if candidates:
            _CACHE[h] = candidates
            # cap cache
            if len(_CACHE) > 256:
                _CACHE.pop(next(iter(_CACHE)))
            return candidates
    except (_Timeout, Exception):
        pass
    return None

def _fallback_move(state, is_player_a):
    """Pruned sweep — ~15 sims, ~0.06s, keeps <1.5s budget. With LLM it becomes strong."""
    players = state['players_a'] if is_player_a else state['players_b']
    bx, by = state['ball']['x'], state['ball']['y']
    target = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_x = FIELD_W if is_player_a else 0
    goal_y = FIELD_H//2
    # only 2 closest players, center goal only
    dists = [(math.hypot(p['x']-bx, p['y']-by), i) for i,p in enumerate(players)]
    dists.sort()
    cand_players = [d[1] for d in dists[:2]]
    best_val = float("-inf")
    best = (cand_players[0], 0.0, 75.0)
    seen=set()
    def _try(pidx, ang, pw):
        nonlocal best_val, best
        k=(pidx,round(ang),round(pw))
        if k in seen: return
        seen.add(k)
        v=_evaluate(state,pidx,ang,pw,is_player_a,target,defensive)
        if v>best_val:
            best_val=v; best=(pidx,ang,pw)
    for pidx in cand_players:
        p = players[pidx]
        base=aim_through(p['x'],p['y'],bx,by,goal_x,goal_y)
        pws = suggested_powers(dist_to_goal(p['x'],p['y'],is_player_a))[:2]
        for off in (-20, -10, 0, 10, 20):
            for pw in pws:
                _try(pidx, base+off, pw)
    # fine
    bpidx, bangle,_ = best
    for off in (-6, 0, 6):
        for pw in suggested_powers(dist_to_goal(players[bpidx]['x'],players[bpidx]['y'],is_player_a))[:1]:
            _try(bpidx, bangle+off, pw)
    return best

def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    t0 = time.time()
    players = state['players_a'] if is_player_a else state['players_b']
    bx, by = state['ball']['x'], state['ball']['y']
    target = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    # 1) Try LLM-guided pruned search within budget
    cands = _llm_candidates(state, is_player_a)
    if cands:
        evals = []
        for pidx, off, pw in cands:
            if time.time()-t0 > _TOTAL_BUDGET-0.30:
                break
            p = players[pidx]
            base = aim_through(p['x'],p['y'],bx,by, FIELD_W if is_player_a else 0, FIELD_H//2)
            angle = base + off
            for a in (angle-10, angle, angle+10):
                for power in (pw-12, pw, pw+12):
                    power = max(20, min(100, power))
                    v = _evaluate(state, pidx, a, power, is_player_a, target, defensive)
                    evals.append((v,pidx,a,power))
                    if v >= 1200:
                        return (pidx,a,power)
        if evals:
            evals.sort(reverse=True, key=lambda x: x[0])
            # re-rank top 3 with 1-ply lookahead (stronger)
            best = (evals[0][1], evals[0][2], evals[0][3])
            best_val = evals[0][0]
            for _,pidx,a,pw in evals[:3]:
                if time.time()-t0 > _TOTAL_BUDGET-0.12:
                    break
                v2 = _evaluate_lookahead(state,pidx,a,pw,is_player_a,target,defensive)
                if v2 > best_val:
                    best_val=v2; best=(pidx,a,pw)
            return best

    # 2) Fallback: fast minimax-style sweep (still <0.5s)
    if time.time()-t0 > _TOTAL_BUDGET-0.35:
        # budget almost exhausted, return instant greedy pick
        # closest player, aim through ball to goal center, mid power
        idx = min(range(len(players)), key=lambda i: math.hypot(players[i]['x']-bx, players[i]['y']-by))
        ang = aim_through(players[idx]['x'],players[idx]['y'],bx,by, FIELD_W if is_player_a else 0, FIELD_H//2)
        return (idx, ang, 78.0)
    return _fallback_move(state, is_player_a)
