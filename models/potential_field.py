"""Potential Field — APF from robotics (Khatib 1986) adapted to soccer.

Attractive: opponent goal, repulsive: own goal + nearby defenders (1/r^2), plus ball-progress.
We sample field vectors at candidate end positions; pick kick whose trajectory end minimizes potential.
Fast: ~80 sims, no lookahead -> ~90ms.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="Potential Field"
DESCRIPTION="Artificial Potential Field: attract goal, repel defenders/own goal."

def _potential(x,y,is_player_a, opp_players):
    gx = FIELD_W if is_player_a else 0.0
    gy = (GOAL_Y1+GOAL_Y2)/2
    # attractive to goal
    d_goal=math.hypot(x-gx, y-gy)
    U_att= d_goal*0.18
    # repulsive from own goal (avoid own-goal)
    own_x=0 if is_player_a else FIELD_W
    d_own=math.hypot(x-own_x, y-gy)
    U_rep_own= 300.0/max(30.0, d_own)
    # repulsive from closest 2 defenders
    U_rep_def=0.0
    for p in sorted(opp_players, key=lambda pp: math.hypot(pp["x"]-x, pp["y"]-y))[:2]:
        d=math.hypot(p["x"]-x, p["y"]-y)
        if d<180:
            U_rep_def += 600.0 / max(12.0, d) - 600.0/180.0
    # wall repulsion
    if y<40: U_rep_def+= (40-y)*1.2
    if y>FIELD_H-40: U_rep_def+= (y-(FIELD_H-40))*1.2
    return U_att + U_rep_own + U_rep_def

def _pick_player(players,bx,by,is_player_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.1
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    opp_players=state["players_b"] if is_player_a else state["players_a"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    tgts=goal_targets(is_player_a)
    pi=_pick_player(players,bx,by,is_player_a)
    p=players[pi]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    pw_all=suggested_powers(dist)
    powers=[pw_all[len(pw_all)//2], pw_all[-1]] if len(pw_all)>2 else pw_all
    best_U=float("inf")
    best=(pi,0.0,powers[-1])
    seen=set()
    for tx,ty in tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-28,29,6):
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
                if len(traj)<=1:
                    continue
                end=traj[-1]
                U=_potential(end["x"],end["y"],is_player_a, opp_players)
                # bonus if end on target
                if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                    U-=55
                # slight progress incentive already in U_att, add travel
                travel=math.hypot(end["x"]-bx, end["y"]-by)
                U-=travel*0.04
                if U<best_U:
                    best_U=U
                    best=(pi,ang,pw)
    return best
