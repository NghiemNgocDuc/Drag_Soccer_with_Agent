"""Team-Coordinated AI — 3 players coordinate like Rumble/Extra Modes team play.

Each AI player shares intent: shooter aims, supporters run to receive. Uses Voronoi control + pass lanes + spacing.
If shooter far (>400px from goal), prefer pass to best-placed teammate (highest space+receive prob).
If near, shoot. Supporters after kick reposition slightly toward ball (simulated via next state bias).
Coordination value = space_control + pass_value + EPV.
~90ms, 1 power, 6 angles, 3 players -> 18 sims.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers, needs_clear

MODEL_NAME="Team Coordinated"
DESCRIPTION="3-player coordination: shooter + 2 supporters, Voronoi + pass lanes."

def _space(x,y, my, opp):
    d_my=min(math.hypot(p["x"]-x,p["y"]-y) for p in my)
    d_opp=min(math.hypot(p["x"]-x,p["y"]-y) for p in opp) if opp else 300
    return (d_opp - d_my)*0.55 * math.exp(-0.008*max(d_my,d_opp))

def _pass_lane(x,y, my, is_a):
    # best teammate receive
    best=0
    for p in my:
        d=math.hypot(p["x"]-x,p["y"]-y)
        if d<20: continue
        forward = (p["x"]>x) if is_a else (p["x"]<x)
        v = max(0, 1 - d/320) * (1.25 if forward else 0.75)
        if v>best: best=v
    return best*32

def _epv(x,y,is_a, opp):
    gx=FIELD_W if is_a else 0
    gy=(GOAL_Y1+GOAL_Y2)/2
    d=math.hypot(x-gx, y-gy)
    ang=abs(math.degrees(math.atan2(y-gy, x-gx)))
    p_dist=1/(1+math.exp((d-320)/140))
    p_ang=max(0,1-ang/75)
    p_press=1.0
    if opp:
        d_opp=min(math.hypot(p["x"]-x,p["y"]-y) for p in opp)
        if d_opp<80: p_press=0.55+0.45*(d_opp/80)
    return p_dist*0.6 + p_ang*0.25 + p_press*0.15

def get_ai_move(state, is_player_a):
    my=state["players_a"] if is_player_a else state["players_b"]
    opp=state["players_b"] if is_player_a else state["players_a"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state,is_player_a)
    tgts=goal_targets(is_player_a)
    # team coordination: shooter must be behind ball (A: x<bx, B: x>bx) else pass
    best_v=float("-inf")
    best=None
    seen=set()
    for pi,p in enumerate(my):
        # must be behind ball to shoot (else would kick backwards)
        if is_player_a and p["x"] > bx+18: continue
        if not is_player_a and p["x"] < bx-18: continue
        d_ball=math.hypot(p["x"]-bx,p["y"]-by)
        power_stat=(p.get("stats") or {}).get("power",50)
        reach=d_ball / max(0.6, power_stat/50)
        if reach>180: continue
        dist=dist_to_goal(p["x"],p["y"],is_player_a)
        pw_all=suggested_powers(dist)
        powers=[pw_all[-1]]  # 1 max for speed
        for tx,ty in tgts:
            base=aim_through(p["x"],p["y"],bx,by,tx,ty)
            for off in (-18,-6,6,18):
                ang=base+off
                for pw in powers:
                    key=(pi,round(ang),round(pw))
                    if key in seen: continue
                    seen.add(key)
                    traj,scored=simulate_kick(state,pi,ang,pw,is_player_a)
                    if scored==target:
                        return (pi,ang,pw)
                    if scored: continue
                    if len(traj)<=1: continue
                    end=traj[-1]
                    # team value: progress + EPV + space + pass + support spacing
                    v=progress_score(end["x"], is_player_a, defensive)*0.85
                    v+= _epv(end["x"],end["y"], is_player_a, opp)*58
                    v+= _space(end["x"],end["y"], my, opp)*0.45
                    v+= _pass_lane(end["x"],end["y"], my, is_player_a)*0.35
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        v+=95
                        v+= max(0, 80 - abs(end["x"]-(FIELD_W if is_player_a else 0))*0.25)
                    # coordination: if far from goal (>400), reward pass to teammate more than shot
                    far = dist_to_goal(bx,by,is_player_a) > 400
                    if far:
                        # if end near teammate, bonus
                        team_near = min(math.hypot(q["x"]-end["x"], q["y"]-end["y"]) for j,q in enumerate(my) if j!=pi)
                        if team_near < 70: v+=18
                    else:
                        # near goal, reward central finish
                        v+= max(0, 22 - abs(end["y"]-(GOAL_Y1+GOAL_Y2)/2)*0.18)
                    # spacing: don't cluster
                    ys=[q["y"] for q in my]
                    spread=max(ys)-min(ys) if ys else 0
                    v+= max(0, min(12, spread*0.04))
                    if v>best_v:
                        best_v=v
                        best=(pi,ang,pw)
                    if len(seen)>=54: break
                if len(seen)>=54: break
            if len(seen)>=54: break
        if len(seen)>=54: break
    return best if best else (0, aim_through(my[0]["x"],my[0]["y"],bx,by,tgts[0][0],tgts[0][1]), 80)
