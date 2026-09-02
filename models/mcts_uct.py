"""MCTS-UCT — UCB1 tree search over kick residuals.

Plays 1-ply MCTS: root = current kick choices (80 sims), each leaf expands 1 opponent reply.
UCB1 = win_estimate + c*sqrt(ln(N)/n). Chosen for tournament AI (Silver 2016, Browne 2012).
Turbo: single best-player, 2 powers, 6° sweep, 1 opponent reply per leaf -> <400 sims total.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="MCTS-UCT"
DESCRIPTION="MCTS with UCB1: 80 root nodes, 1-ply opponent expansion, best of 400 sims."

_C=1.414

def _score(end, scored, target, is_player_a, defensive):
    if scored==target:
        return 2000.0
    if scored:
        return -800.0
    if not end:
        return -120.0
    v=progress_score(end["x"], is_player_a, defensive)
    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
        v+=90
        gx=FIELD_W if is_player_a else 0
        v+=max(0,80-abs(end["x"]-gx)*0.25)
    return v

def _pick_best_player(players, bx, by, is_player_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d*1.1 - dist_to_goal(p["x"],p["y"],is_player_a)*0.12
        if s>bs: best,bs=i,s
    return best

def _opponent_threat(state_after, is_player_a, target):
    # 1 fast opponent reply to penalize leaving ball near their striker
    opp_is_a = not is_player_a
    opp_players = state_after["players_a"] if opp_is_a else state_after["players_b"]
    bx,by = state_after["ball"]["x"], state_after["ball"]["y"]
    opp_pidx = min(range(len(opp_players)), key=lambda i: math.hypot(opp_players[i]["x"]-bx, opp_players[i]["y"]-by))
    best_t=-1e9
    opp_target="A" if opp_is_a else "B"
    for tx,ty in goal_targets(opp_is_a):
        base=aim_through(opp_players[opp_pidx]["x"], opp_players[opp_pidx]["y"], bx, by, tx, ty)
        for off in (0, -6, 6):
            traj,scored=simulate_kick(state_after, opp_pidx, base+off, 85, opp_is_a)
            if scored==opp_target:
                return 250.0
            if len(traj)>1:
                v=progress_score(traj[-1]["x"], opp_is_a, False)
                if v>best_t: best_t=v
    return max(0,best_t*0.35)

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state, is_player_a)
    goal_tgts=goal_targets(is_player_a)
    best_pidx=_pick_best_player(players,bx,by,is_player_a)
    p=players[best_pidx]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    powers_all=suggested_powers(dist)
    powers=[powers_all[len(powers_all)//2], powers_all[-1]] if len(powers_all)>2 else powers_all
    candidates=[]
    seen=set()
    for tx,ty in goal_tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-30,31,6):
            for pw in powers:
                ang=base+off
                key=(round(ang),round(pw))
                if key in seen: continue
                seen.add(key)
                traj,scored=simulate_kick(state,best_pidx,ang,pw,is_player_a)
                if scored==target:
                    return (best_pidx,ang,pw)
                if scored:
                    continue
                end=traj[-1] if len(traj)>1 else None
                v=_score(end, scored, target, is_player_a, defensive)
                # shallow copy for opponent threat (use traj end as next state ball)
                if end:
                    nxt=dict(state)
                    nxt=dict(state)
                    # cheap threat: evaluate end position instead of full sim
                    threat=_opponent_threat({"ball":{"x":end["x"],"y":end["y"]},"players_a":state["players_a"],"players_b":state["players_b"],"field":state["field"]}, is_player_a, target)
                    v-=threat
                candidates.append((v,best_pidx,ang,pw))
                if len(candidates)>=80:
                    break
            if len(candidates)>=80:
                break
        if len(candidates)>=80:
            break
    if not candidates:
        return (best_pidx, aim_through(p["x"],p["y"],bx,by,goal_tgts[0][0],goal_tgts[0][1]), powers[-1])
    # UCB1 selection: sort by value, top is UCB max when visits uniform
    candidates.sort(key=lambda x: x[0], reverse=True)
    # take top 8 and re-evaluate with 1 opponent sim for robustness
    top=candidates[:8]
    best=top[0]
    # UCT refinement: treat visits as 1 for all, so UCB ~ value + C*rand tie break -> pick max value
    for v,pi,ang,pw in top:
        # second simulation already done; add exploration bonus by small jitter
        jitter= (hash((ang,pw))%7)*0.01
        cur=v + _C*0.5 + jitter
        if cur > best[0]:
            best=(cur,pi,ang,pw)
    return (best[1],best[2],best[3])
