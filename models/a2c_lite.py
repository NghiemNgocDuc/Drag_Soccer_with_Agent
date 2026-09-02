"""A2C-lite — Advantage Actor-Critic without training (synchronous, shared features).

Actor: 7° sweep, bias from critic advantage = V_next - V_cur.
Critic: V = w1*progress + w2*goal_proximity + w3*spacing (no NN).
Lightweight vs ppo_actor_critic: fewer powers (1) and 5° sweep -> ~60 sims, ~120ms.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers, needs_clear

MODEL_NAME="A2C Lite"
DESCRIPTION="A2C without NN: actor sweep + critic advantage V_next-V_cur, 5° sweep."

def _critic(state, is_player_a):
    bx,by=state["ball"]["x"], state["ball"]["y"]
    v=progress_score(bx, is_player_a, needs_clear(state,is_player_a))
    if GOAL_Y1 <= by <= GOAL_Y2:
        gx=FIELD_W if is_player_a else 0
        v+=max(0,40-abs(bx-gx)*0.08)
    # spacing: variance of my players y
    my=state["players_a"] if is_player_a else state["players_b"]
    if len(my)>1:
        ys=[p["y"] for p in my]
        mean=sum(ys)/len(ys)
        var=sum((y-mean)**2 for y in ys)/len(ys)
        v+=max(0,18 - var*0.002)
    return v

def _pick(players,bx,by,is_player_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.12
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    V_cur=_critic(state, is_player_a)
    tgts=goal_targets(is_player_a)
    pi=_pick(players,bx,by,is_player_a)
    p=players[pi]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    pw_all=suggested_powers(dist)
    # single best power for speed
    pw=pw_all[-1]
    best_adv=float("-inf")
    best=(pi,0.0,pw)
    seen=set()
    for tx,ty in tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-28,29,5):
            ang=base+off
            key=round(ang)
            if key in seen: continue
            seen.add(key)
            traj,scored=simulate_kick(state,pi,ang,pw,is_player_a)
            if scored==target:
                return (pi,ang,pw)
            if scored:
                continue
            if len(traj)<=1:
                continue
            end=traj[-1]
            nxt=dict(state)
            nxt=dict(state)
            tmp=dict(state)
            tmp["ball"]={"x":end["x"],"y":end["y"]}
            V_next=_critic(tmp, is_player_a)
            adv= (V_next - V_cur)
            # add small on-target bonus to advantage
            if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                adv+=22
            if adv>best_adv:
                best_adv=adv
                best=(pi,ang,pw)
            if len(seen)>=60:
                break
        if len(seen)>=60:
            break
    return best
