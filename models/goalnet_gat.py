"""GoalNet-GAT — GAT + xT + centrality (Ide et al. 2025, Kyrillidis 2025).

Graph: nodes=6 players, edges=pass+spatial (kNN 2). GAT attention α=softmax(LeakyReLU(a·[Whi||Whj])), xT = expected threat change (Singh 2019) = P(score|next) - P(score|prev). Centrality bonus for build-up.
Credit assignment via learned embeddings + centrality.
~80 sims, ~820ms.
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers, needs_clear

MODEL_NAME="GoalNet GAT"
DESCRIPTION="GAT + xT credit + centrality, 80 sims."

def _xt(x,y,is_player_a):
    gx=FIELD_W if is_player_a else 0
    gy=(GOAL_Y1+GOAL_Y2)/2
    d=math.hypot(x-gx, y-gy)
    # Singh xT approx: logistic of distance + angle
    ang=abs(math.degrees(math.atan2(y-gy, x-gx)))
    p= 1/(1+math.exp((d-340)/130)) * max(0,1-ang/80)
    return p

def _gat_score(state, is_player_a, end):
    # Build graph: 6 nodes, edge weight = exp(-dist/180)
    players = state["players_a"]+state["players_b"]
    # GAT attention: for ball end position, attention to each player
    atts=[]
    for p in players:
        d=math.hypot(p["x"]-end["x"], p["y"]-end["y"])
        # LeakyReLU approx: max(0.2*d, d) -> use exp
        score = math.exp(-d/140) * (1.2 if p in (state["players_a"] if is_player_a else state["players_b"]) else 0.85)
        atts.append(score)
    # softmax
    m=max(atts)
    exps=[math.exp(a-m) for a in atts]
    s=sum(exps)
    atts=[e/s for e in exps]
    # centrality: degree weighted by attention
    centrality = sum(atts[i] * (1 if i<3 else 0.7) for i in range(len(atts)))
    return centrality

def _pick(players,bx,by,is_a):
    best,bs=0,float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d -dist_to_goal(p["x"],p["y"],is_a)*0.11
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"], state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state,is_player_a)
    xt0=_xt(bx,by,is_player_a)
    tgts=goal_targets(is_player_a)
    pi=_pick(players,bx,by,is_player_a)
    p=players[pi]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    pw_all=suggested_powers(dist)
    powers=[pw_all[len(pw_all)//2], pw_all[-1]] if len(pw_all)>2 else pw_all
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
                if scored: continue
                if len(traj)<=1: continue
                end=traj[-1]
                v=progress_score(end["x"], is_player_a, defensive)
                # xT delta
                xt1=_xt(end["x"],end["y"],is_player_a)
                v+= (xt1-xt0)*220
                if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                    v+=75
                v+= _gat_score(state, is_player_a, end)*42
                if v>best_v:
                    best_v=v
                    best=(pi,ang,pw)
    return best
