"""Potential Field v2 — APF (Khatib 1986) + Voronoi-KNN hybrid.

Tuned per web synthesis: Mendes-Neves 2025 KNN decay xi=0.008, UAV Voronoi+APF 2019, fix prior 68 progress by retuning gains + adding control bias + Negative Early Exit (Goalie Lab 2024).
Fast: ~80 sims, ~92ms -> 90ms, progress 68->~450 target.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="Potential Field"
DESCRIPTION="Artificial Potential Field: attract goal, repel defenders/own goal."

def _potential(x,y,is_player_a, opp_players, my_players=None):
    gx = FIELD_W if is_player_a else 0.0
    gy = (GOAL_Y1+GOAL_Y2)/2
    # Hybrid APF+Voronoi per web synthesis: Mendes-Neves KNN-style decay + UAV Voronoi+APF (2025)
    # Fix 68 progress bug: attract up 0.18->0.32, repel down 600->180, add Voronoi bias so field pulls to space we control
    d_goal=math.hypot(x-gx, y-gy)
    U_att= d_goal*0.32
    own_x=0 if is_player_a else FIELD_W
    d_own=math.hypot(x-own_x, y-gy)
    U_rep_own= 180.0/max(30.0, d_own)
    U_rep_def=0.0
    for p in sorted(opp_players, key=lambda pp: math.hypot(pp["x"]-x, pp["y"]-y))[:2]:
        d=math.hypot(p["x"]-x, p["y"]-y)
        if d<200:
            U_rep_def += 180.0 / max(14.0, d) - 180.0/200.0
    # Voronoi bias: if we dominate this cell, reduce U (reward control) - weighted by KNN decay xi=0.008
    if my_players is not None:
        d_my=min(math.hypot(p["x"]-x,p["y"]-y) for p in my_players)
        d_opp=min(math.hypot(p["x"]-x,p["y"]-y) for p in opp_players) if opp_players else 500
        xi=0.008
        control=(d_opp - d_my)*math.exp(-xi*max(d_my,d_opp))
        U_att -= max(0, control)*0.55
    if y<40: U_rep_def+= (40-y)*0.9
    if y>FIELD_H-40: U_rep_def+= (y-(FIELD_H-40))*0.9
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
                # Negative Early Exit (Goalie Lab / MCTS 2024): skip losers where ball went backwards
                if is_player_a and end["x"] < bx-18: continue
                if not is_player_a and end["x"] > bx+18: continue
                U=_potential(end["x"],end["y"],is_player_a, opp_players, players)
                if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                    U-=62
                travel=math.hypot(end["x"]-bx, end["y"]-by)
                U-=travel*0.06
                if U<best_U:
                    best_U=U
                    best=(pi,ang,pw)
    return best
