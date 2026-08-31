"""GNN Soccer — inspired by Ding et al. 2020 GAT + Xiao et al. 2024 GPPO (GNN+PPO) + MACNS.

Graph: nodes = 6 players + ball (7), edges = complete, weight = exp(-dist/120).
One GAT-like layer: h_i' = sum_j alpha_ij * W h_j, alpha = softmax(LeakyReLU(a^T [Wh_i||Wh_j])).
We use handcrafted W (identity) and a (distance-based) so no training needed, still
captures “team shape” and runs <200ms.

Refs:
- Ding & Huang 2020 GAT for trajectory prediction in soccer, arXiv:2012.10531
- Xiao et al. 2024 MACNS / GPPO, Information Fusion 102:102250 (GNN+PPO)
- Capellera et al. 2024 FootBots Transformer, ICIP
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="Graph GNN"
DESCRIPTION="GAT-like message passing over player-ball graph, PPO-style value."

def _node_features(state, is_player_a):
    feats=[]
    bx,by=state["ball"]["x"],state["ball"]["y"]
    gx = FIELD_W if is_player_a else 0
    gy=(GOAL_Y1+GOAL_Y2)/2
    for p in state["players_a"]+state["players_b"]:
        feats.append([p["x"]/FIELD_W, p["y"]/875, math.hypot(p["x"]-bx,p["y"]-by)/800, math.hypot(p["x"]-gx,p["y"]-gy)/1400])
    feats.append([bx/FIELD_W, by/875, 0.0, math.hypot(bx-gx,by-gy)/1400])
    return feats

def _gat_layer(feats):
    n=len(feats)
    # W = identity (keep 4 dims)
    # compute attention
    out=[]
    for i in range(n):
        # compute raw e_ij
        exps=[]
        for j in range(n):
            dist = sum((feats[i][k]-feats[j][k])**2 for k in range(4))**0.5
            # LeakyReLU-like: -dist
            e = -dist*2.0
            # boost ball node (last)
            if j==n-1: e+=0.5
            exps.append(math.exp(e))
        s=sum(exps)
        alphas=[e/s for e in exps]
        # aggregate
        agg=[0.0]*4
        for j,a in enumerate(alphas):
            for k in range(4):
                agg[k]+= a * feats[j][k]
        # residual
        out.append([(feats[i][k]+agg[k])*0.5 for k in range(4)])
    return out

def _graph_value(feats, is_player_a):
    # value = -ball dist to goal + team spread
    ball=feats[-1]
    d_goal=ball[3]  # normalized
    # spread of team A vs B
    n=len(feats)-1
    xs=[feats[i][0]*FIELD_W for i in range(n)]
    # team A are first 3, B next 3 (approx, since we concatenated A+B)
    team_a_x=xs[:3]
    team_b_x=xs[3:6]
    spread_a = max(team_a_x)-min(team_a_x) if team_a_x else 0
    spread_b = max(team_b_x)-min(team_b_x) if team_b_x else 0
    # lower spread = better shape
    shape = 50 - (spread_a if is_player_a else spread_b)*0.3
    return (1-d_goal)*120 + shape

def _pick_best_player(players, bx, by, is_player_a):
    best, bs = 0, float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.1
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    players = state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"],state["ball"]["y"]
    target="A" if is_player_a else "B"
    defensive=needs_clear(state, is_player_a)
    goal_tgts=goal_targets(is_player_a)
    best_pidx=_pick_best_player(players,bx,by,is_player_a)
    p=players[best_pidx]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    powers=suggested_powers(dist)
    powers=[powers[len(powers)//2], powers[-1]] if len(powers)>2 else powers
    # pre GAT for current board
    base_feats=_node_features(state, is_player_a)
    base_gat=_gat_layer(base_feats)
    base_val=_graph_value(base_gat, is_player_a)

    best_val=float("-inf")
    best_move=(best_pidx,0.0,powers[-1])
    for tx,ty in goal_tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-28,29,6):
            angle=base+off
            for power in powers:
                traj, scored=simulate_kick(state,best_pidx,angle,power,is_player_a)
                if scored==target:
                    return (best_pidx,angle,power)
                if scored:
                    continue
                if len(traj)<=1:
                    val=-100
                else:
                    end=traj[-1]
                    fake={"ball":{"x":end["x"],"y":end["y"]},"players_a":state["players_a"],"players_b":state["players_b"]}
                    feats=_node_features(fake,is_player_a)
                    gat=_gat_layer(feats)
                    val=_graph_value(gat,is_player_a) - base_val
                    val+=progress_score(end["x"],is_player_a,defensive)
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val+=80
                if val>best_val:
                    best_val=val
                    best_move=(best_pidx,angle,power)
    return best_move
