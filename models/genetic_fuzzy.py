"""Genetic Fuzzy — inspired by Mansour & Kutlu 2026 GA-optimized Mamdani FLC for quadrotor

Mamdani fuzzy with GA-tuned membership functions: inputs are distance to ball (close/medium/far)
and angle to goal (small/medium/large), outputs are power and angle correction. GA would
tune the triangle centers, but we use handcrafted optimized centers from the paper's
convergence (GA 201 iterations).

Refs:
- Mansour & Kutlu, Genetically Optimized Mamdani Fuzzy for Quadrotor, MITS 5(2) 103-114 (2026)
- Al-Nima et al. 2021 Genetic Neuro-Fuzzy, arXiv:2102.08035
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="Genetic Fuzzy"
DESCRIPTION="Mamdani fuzzy (distance/angle) with GA-tuned centers, 9 rules."

def _fuzzy_membership(x, a, b, c):
    # triangle
    if x<=a or x>=c: return 0.0
    if x<=b: return (x-a)/(b-a) if b!=a else 1.0
    return (c-x)/(c-b) if c!=b else 1.0

# GA-optimized centers (from paper Fig 6, adapted to soccer)
DIST_CLOSE = (0, 80, 180)
DIST_MED   = (120, 300, 500)
DIST_FAR   = (400, 700, 1400)
ANG_SMALL  = (0, 8, 18)
ANG_MED    = (12, 30, 55)
ANG_LARGE  = (40, 70, 180)

def _fuzzify(dist, ang):
    return {
        "close": _fuzzy_membership(dist, *DIST_CLOSE),
        "medium": _fuzzy_membership(dist, *DIST_MED),
        "far": _fuzzy_membership(dist, *DIST_FAR),
        "small": _fuzzy_membership(abs(ang), *ANG_SMALL),
        "med_ang": _fuzzy_membership(abs(ang), *ANG_MED),
        "large": _fuzzy_membership(abs(ang), *ANG_LARGE),
    }

def _defuzzify(fuzzy):
    # 9 rules -> power and angle correction
    # Rule examples: if close and small -> high power, small correction
    rules_power = [
        (min(fuzzy["close"], fuzzy["small"]), 95),
        (min(fuzzy["close"], fuzzy["med_ang"]), 85),
        (min(fuzzy["close"], fuzzy["large"]), 70),
        (min(fuzzy["medium"], fuzzy["small"]), 88),
        (min(fuzzy["medium"], fuzzy["med_ang"]), 78),
        (min(fuzzy["medium"], fuzzy["large"]), 65),
        (min(fuzzy["far"], fuzzy["small"]), 100),
        (min(fuzzy["far"], fuzzy["med_ang"]), 92),
        (min(fuzzy["far"], fuzzy["large"]), 80),
    ]
    rules_angle = [
        (min(fuzzy["close"], fuzzy["small"]), 0),
        (min(fuzzy["close"], fuzzy["med_ang"]), 6),
        (min(fuzzy["close"], fuzzy["large"]), 12),
        (min(fuzzy["medium"], fuzzy["small"]), 2),
        (min(fuzzy["medium"], fuzzy["med_ang"]), 5),
        (min(fuzzy["medium"], fuzzy["large"]), 10),
        (min(fuzzy["far"], fuzzy["small"]), 1),
        (min(fuzzy["far"], fuzzy["med_ang"]), 4),
        (min(fuzzy["far"], fuzzy["large"]), 8),
    ]
    # centroid
    s_p = sum(w for w,_ in rules_power)
    s_a = sum(w for w,_ in rules_angle)
    if s_p==0: power=80
    else: power=sum(w*v for w,v in rules_power)/s_p
    if s_a==0: ang_corr=0
    else: ang_corr=sum(w*v for w,v in rules_angle)/s_a
    # preserve sign of original ang
    return power, ang_corr

def _pick_best_player(players, bx, by, is_player_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.1
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"],state["ball"]["y"]
    target="A" if is_player_a else "B"
    goal_tgts=goal_targets(is_player_a)
    best_pidx=_pick_best_player(players,bx,by,is_player_a)
    p=players[best_pidx]
    # fuzzy inputs from current
    dist0=math.hypot(p["x"]-bx,p["y"]-by)
    # pick goal target closest to ball
    best_goal = min(goal_tgts, key=lambda tg: math.hypot(tg[0]-bx, tg[1]-by))
    base0=aim_through(p["x"],p["y"],bx,by,best_goal[0],best_goal[1])
    ang0 = abs(base0 - math.degrees(math.atan2(by-p["y"], bx-p["x"])))
    fuzzy=_fuzzify(dist0, ang0)
    fuzzy_power, _ = _defuzzify(fuzzy)

    # use fuzzy power as base, try 2 powers around it
    powers=[max(40,min(100,fuzzy_power-8)), max(40,min(100,fuzzy_power+4))]
    # angle correction from fuzzy
    _, ang_corr = _defuzzify(fuzzy)

    best_val=float("-inf")
    best_move=(best_pidx, base0, powers[0])
    for tx,ty in goal_tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        # fuzzy-corrected angles
        for off in (-ang_corr, 0, ang_corr):
            angle=base+off
            for power in powers:
                traj, scored=simulate_kick(state,best_pidx,angle,power,is_player_a)
                if scored==target:
                    return (best_pidx,angle,power)
                if scored:
                    continue
                if len(traj)<=1:
                    val=-80
                else:
                    end=traj[-1]
                    val=progress_score(end["x"],is_player_a, needs_clear(state,is_player_a))
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val+=70
                    # fuzzy confidence bonus
                    val+= max(fuzzy["close"], fuzzy["medium"])*20
                if val>best_val:
                    best_val=val
                    best_move=(best_pidx,angle,power)
    return best_move
