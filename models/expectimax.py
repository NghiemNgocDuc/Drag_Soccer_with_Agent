"""Expectimax — chance node for opponent reply distribution.

Root = our kick (max), chance node = opponent's 3 weighted replies (prob 0.5/0.3/0.2),
leaf = progress_score - expected threat. Inspired by Russell & Norvig Ch.5.
Turbo: single player, 2 powers, 6° sweep, 3 opponent samples per leaf -> ~240 sims.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="Expectimax"
DESCRIPTION="Expectimax chance node: our kick minus weighted opponent replies (0.5/0.3/0.2)."

def _leaf(end, scored, target, is_player_a, defensive):
    if scored==target:
        return 2200.0
    if scored:
        return -900.0
    if not end:
        return -120.0
    v=progress_score(end["x"], is_player_a, defensive)
    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
        v+=100
        gx=FIELD_W if is_player_a else 0
        v+=max(0,70-abs(end["x"]-gx)*0.28)
    return v

def _pick_player(players,bx,by,is_player_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.12
        if s>bs: best,bs=i,s
    return best

def _expected_threat(state_next, is_player_a):
    opp_is_a=not is_player_a
    opp_players=state_next["players_a"] if opp_is_a else state_next["players_b"]
    bx,by=state_next["ball"]["x"], state_next["ball"]["y"]
    opp_target="A" if opp_is_a else "B"
    # pick closest opponent
    pi=min(range(len(opp_players)), key=lambda i: math.hypot(opp_players[i]["x"]-bx, opp_players[i]["y"]-by))
    p=opp_players[pi]
    tgts=goal_targets(opp_is_a)
    base=aim_through(p["x"],p["y"],bx,by,tgts[0][0],tgts[0][1])
    weights=[0.5,0.3,0.2]
    offs=[0,-7,7]
    exp=0.0
    for w,off in zip(weights,offs):
        traj,scored=simulate_kick(state_next, pi, base+off, 88, opp_is_a)
        if scored==opp_target:
            return 300.0
        if len(traj)>1:
            v=progress_score(traj[-1]["x"], opp_is_a, False)
            exp+=w*max(0,v)
    return exp*0.45

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state, is_player_a)
    tgts=goal_targets(is_player_a)
    pi=_pick_player(players,bx,by,is_player_a)
    p=players[pi]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    pw_all=suggested_powers(dist)
    powers=[pw_all[len(pw_all)//2], pw_all[-1]] if len(pw_all)>2 else pw_all
    best_val=float("-inf")
    best=(pi,0.0,powers[-1])
    seen=set()
    for tx,ty in tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-30,31,6):
            ang=base+off
            for pw in powers:
                key=(round(ang),round(pw))
                if key in seen: continue
                seen.add(key)
                traj,scored=simulate_kick(state,pi,ang,pw,is_player_a)
                if scored==target:
                    return (pi,ang,pw)
                if scored:
                    continue
                end=traj[-1] if len(traj)>1 else None
                v=_leaf(end, scored, target, is_player_a, defensive)
                # expectation over opponent
                if end:
                    nxt={"ball":{"x":end["x"],"y":end["y"]},"players_a":state["players_a"],"players_b":state["players_b"],"field":state["field"],"is_player_a":is_player_a}
                    # minimal state for opponent sim
                    tmp=dict(state)
                    tmp["ball"]={"x":end["x"],"y":end["y"]}
                    v -= _expected_threat(tmp, is_player_a)
                if v>best_val:
                    best_val=v
                    best=(pi,ang,pw)
    return best
