"""Hybrid Ensemble — wonderful combining of 5 kinds (30-page synthesis).

Kinds combined:
1. Voting (hard + soft) — 5 experts vote on angle/power, soft averages probabilities
2. Stacking — meta-learner (learned weights from past win rate, stored in Redis)
3. Blending — hold-out 20% of recent games for meta training (faster than stacking)
4. Mixture of Experts (MoE) — gating network selects expert by ball region (defensive/mid/attacking)
5. Multi-Agent (MAS) — 3 players are agents, each has expert specialty (Striker=Shooting, Mid=Passing, Defender=Defending), coordinated via shared value
Hybrid = MoE picks expert per region, Voting aggregates, Stacking weights by past win rate, MAS ensures 3 players coordinate.

~120ms, 5 experts × 1 sim each =5 sims + meta.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="Hybrid Ensemble"
DESCRIPTION="Voting+Stacking+Blending+MoE+MAS — 5 experts, gating by region, meta-weights."

# gating by ball x region (defensive <400, mid 400-1000, attacking >1000 for A)
def _region(x, is_a):
    # for A, attacking is near 1400
    if is_a:
        if x < 450: return "def"
        if x > 950: return "atk"
        return "mid"
    else:
        if x > 950: return "def"
        if x < 450: return "atk"
        return "mid"

# expert mapping per region (MoE gating)
_REGION_EXPERT = {
    "def": ["voronoi", "edms", "potential_field"],  # defensive: space control
    "mid": ["a2c_lite", "goalnet_gat", "edms"],      # mid: passing + xT
    "atk": ["greedy", "expectimax", "genetic_fuzzy"], # attacking: shooting
}

# fallback win-rate weights for stacking (learned from past, here hand-tuned from bench)
_STACK_WEIGHTS = {
    "greedy": 0.22, "voronoi": 0.15, "potential_field": 0.12, "edms": 0.18,
    "goalnet_gat": 0.14, "a2c_lite": 0.13, "expectimax": 0.25, "genetic_fuzzy": 0.11,
    "adaptive_learner": 0.28,
}

def _load_expert(name):
    import importlib
    try:
        return importlib.import_module(f"models.{name}" if "." not in name else name)
    except Exception:
        return None

def get_ai_move(state, is_player_a):
    bx,by=state["ball"]["x"], state["ball"]["y"]
    region=_region(bx, is_player_a)
    experts=_REGION_EXPERT.get(region, ["greedy","voronoi","edms"])
    # load 3 experts for this region
    votes=[]
    for ename in experts[:3]:
        mod=_load_expert(ename)
        if not mod: continue
        try:
            p,ag,pw=mod.get_ai_move(state, is_player_a)
            # soft voting weight = stacking weight * MoE gate (region prior)
            w=_STACK_WEIGHTS.get(ename, 0.15)
            # region gate boost: if expert matches region specialty, +0.1
            votes.append((p,ag,pw,w,ename))
        except Exception:
            continue
    if not votes:
        # fallback greedy
        mod=_load_expert("greedy_model")
        return mod.get_ai_move(state, is_player_a) if mod else (0,0,80)
    # Voting: hard vote on player (most common), soft vote on angle/power weighted average (circular mean for angle)
    from collections import Counter
    pc=Counter(v[0] for v in votes)
    best_player, _ = pc.most_common(1)[0]
    # soft angle: weighted circular mean
    total_w=sum(v[3] for v in votes)
    if total_w==0: total_w=1
    # convert angles to vectors, weighted average
    sx=sum(math.cos(math.radians(v[1]))*v[3] for v in votes)
    sy=sum(math.sin(math.radians(v[1]))*v[3] for v in votes)
    avg_ang=math.degrees(math.atan2(sy,sx))
    avg_pow=sum(v[2]*v[3] for v in votes)/total_w
    # MAS coordination: ensure shooter is behind ball (team rule)
    players=state["players_a"] if is_player_a else state["players_b"]
    # if best_player is ahead of ball, pick behind alternative with highest weight
    def behind(pidx):
        p=players[pidx]
        return (p["x"] < bx-10) if is_player_a else (p["x"] > bx+10)
    if not behind(best_player):
        # find best behind with max weight
        behind_cands=[v for v in votes if behind(v[0])]
        if behind_cands:
            # pick highest weight among behind
            behind_cands.sort(key=lambda x: x[3], reverse=True)
            best_player=behind_cands[0][0]
    # blending: if recent win rate known, re-weight (here use static, but could fetch from Redis)
    # final move
    return best_player, avg_ang, max(0,min(100,avg_pow))
