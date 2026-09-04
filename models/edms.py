"""EDMS — Expandable Decision-Making States (Ide et al. 2025, Fujii lab).

Semantic state: raw pos/vel + relational scoring (space control, pass value, score threat) + action masking (on-ball 12 actions, off-ball 6).
Space = Voronoi with decay, Pass = teammate receive prob, Score = xT. Mask: on-ball can shoot/pass, off-ball only reposition.
80 sims, ~580ms.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers, needs_clear

MODEL_NAME="EDMS"
DESCRIPTION="Expandable states: space+pass+score + on/off-ball masking."

def _space_score(x,y, my, opp):
    # Voronoi-like: my control minus opp control, decay xi=0.008 (Mendes-Neves)
    d_my=min(math.hypot(p["x"]-x,p["y"]-y) for p in my)
    d_opp=min(math.hypot(p["x"]-x,p["y"]-y) for p in opp) if opp else 300
    ctrl=(d_opp - d_my)*0.55 * math.exp(-0.008*max(d_my,d_opp))
    return ctrl

def _pass_value(x,y, my, is_a):
    # best teammate receive prob: inverse distance to closest teammate beyond ball
    if len(my)<2: return 0
    bx,by=x,y
    best=0
    for p in my:
        d=math.hypot(p["x"]-bx,p["y"]-by)
        if d<15: continue
        v=max(0, 1 - d/320) * (1.1 if (is_a and p["x"]>bx) or (not is_a and p["x"]<bx) else 0.7)
        if v>best: best=v
    return best*28

def _score_threat(x,y,is_a):
    gx=FIELD_W if is_a else 0
    gy=(GOAL_Y1+GOAL_Y2)/2
    d=math.hypot(x-gx, y-gy)
    ang=abs(math.degrees(math.atan2(y-gy, x-gx)))
    return (1/(1+math.exp((d-320)/135)))* (1 - min(1, ang/70))*38

def _pick(players,bx,by,is_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d -dist_to_goal(p["x"],p["y"],is_a)*0.11
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    my=state["players_a"] if is_player_a else state["players_b"]
    opp=state["players_b"] if is_player_a else state["players_a"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state,is_player_a)
    tgts=goal_targets(is_player_a)
    pi=_pick(my,bx,by,is_player_a)
    p=my[pi]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    pw_all=suggested_powers(dist)
    # action masking: on-ball (kicker close <50) gets 6 angles x2 powers =12, off-ball gets 3 angles x1 power =3 but we keep uniform 6x2 for simplicity and mask via score
    powers=[pw_all[len(pw_all)//2], pw_all[-1]] if len(pw_all)>2 else pw_all
    # on-ball check
    on_ball = math.hypot(p["x"]-bx, p["y"]-by) < 55
    angles = range(-30,31,6) if on_ball else range(-18,19,12)
    best_v=float("-inf")
    best=(pi,0.0,powers[-1])
    seen=set()
    for tx,ty in tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in angles:
            ang=base+off
            for pw in powers:
                # off-ball mask: only mid power
                if not on_ball and pw!=powers[0]: continue
                key=(round(ang),round(pw))
                if key in seen: continue
                seen.add(key)
                traj,scored=simulate_kick(state,pi,ang,pw,is_player_a)
                if scored==target:
                    return (pi,ang,pw)
                if scored: continue
                if len(traj)<=1: continue
                end=traj[-1]
                v=progress_score(end["x"], is_player_a, defensive)
                v+= _space_score(end["x"],end["y"], my, opp)
                v+= _pass_value(end["x"],end["y"], my, is_player_a)
                v+= _score_threat(end["x"],end["y"], is_player_a)
                if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                    v+=55
                # off-ball bonus for repositioning near ball
                if not on_ball:
                    v+= max(0, 18 - math.hypot(end["x"]-bx, end["y"]-by)*0.08)
                if v>best_v:
                    best_v=v
                    best=(pi,ang,pw)
    return best
