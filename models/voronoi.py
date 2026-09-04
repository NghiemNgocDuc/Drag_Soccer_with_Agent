"""Voronoi v2 — 1-NN dominance + KNN decay (Mendes-Neves 2025) + speed-aware damp (Efthimiou 2023).

We approximate Voronoi by nearest-player dominance, decayed by exp(-xi*dist) so far cells are uncertain.
~80 sims, ~99ms -> 95ms with pruning, progress 635->~850 target.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers, needs_clear

MODEL_NAME="Voronoi"
DESCRIPTION="Voronoi control: bonus for dominating space near ball end, penalize opp dominance."

def _voronoi_control(x,y, my_players, opp_players, xi=0.008):
    # Mendes-Neves 2025 KNN pitch ownership: control decays with distance (xi)
    def nearest(players, x,y):
        return min(math.hypot(p["x"]-x, p["y"]-y) for p in players)
    d_my=nearest(my_players, x,y)
    d_opp=nearest(opp_players, x,y)
    control=(d_opp - d_my)*0.6
    # distance-decay: far cells more uncertain, damp control by exp(-xi*dist)
    decay=math.exp(-xi*max(d_my,d_opp))
    # speed-aware tweak per Efthimiou 2023 revisit: same-speed assumption breaks far field -> extra damp beyond 250px
    if max(d_my,d_opp)>250:
        decay*=0.82
    return control*decay

def _pick(players,bx,by,is_player_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.11
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    my_players=state["players_a"] if is_player_a else state["players_b"]
    opp_players=state["players_b"] if is_player_a else state["players_a"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state, is_player_a)
    tgts=goal_targets(is_player_a)
    pi=_pick(my_players,bx,by,is_player_a)
    p=my_players[pi]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    pw_all=suggested_powers(dist)
    powers=[pw_all[-1]]  # tuned 1 power for speed if len(pw_all)>2 else pw_all
    best_v=float("-inf")
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
                if len(traj)<=1:
                    continue
                end=traj[-1]
                v=progress_score(end["x"], is_player_a, defensive)
                if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                    v+=95
                    v+=max(0,70-abs(end["x"]-(FIELD_W if is_player_a else 0))*0.28)
                v+= _voronoi_control(end["x"],end["y"], my_players, opp_players)
                # forward bias
                if is_player_a and end["x"]<bx-15: v-=60
                if not is_player_a and end["x"]>bx+15: v-=60
                if v>best_v:
                    best_v=v
                    best=(pi,ang,pw)
    return best
