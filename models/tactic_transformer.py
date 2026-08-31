"""Tactic Transformer — inspired by Wang et al. 2024 TacticAI (Nature Comm) + Yeung 2025 TranSPORTmer.

Idea: treat players + ball as tokens, use Set Attention Blocks to capture
spatial + team interactions. For this game we implement a lightweight,
training-free transformer that still uses simulate_kick for candidate evaluation
but scores boards via attention-weighted team cohesion (no learned weights, so
it runs <200ms and stays <2s).

Refs:
- Wang et al. TacticAI: an AI assistant for football tactics, Nature Comm 15:1906 (2024)
- Yeung et al. TranSPORTmer, ACCV 2024 — Set Attention + CLS token for state classification
- Ide et al. Expandable Decision-Making States for Multi-Agent DRL, arXiv 2510.00480
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME = "Tactic Transformer"
DESCRIPTION = "Transformer-inspired attention over players + ball, scoring via team cohesion."

def _token_features(state, is_player_a):
    """Tokens: 6 players + ball = 7. Each token = [x_norm, y_norm, d_ball, d_goal, is_ball, is_keeper]."""
    players_a = state["players_a"]
    players_b = state["players_b"]
    bx, by = state["ball"]["x"], state["ball"]["y"]
    gx = FIELD_W if is_player_a else 0
    gy = (GOAL_Y1+GOAL_Y2)/2
    toks=[]
    for i, p in enumerate(players_a + players_b):
        x, y = p["x"], p["y"]
        toks.append([
            x/FIELD_W, y/875,
            math.hypot(x-bx, y-by)/800,
            math.hypot(x-gx, y-gy)/1400,
            0.0,
            1.0 if i%3==0 else 0.0  # keeper approx
        ])
    # ball token
    toks.append([bx/FIELD_W, by/875, 0.0, math.hypot(bx-gx, by-gy)/1400, 1.0, 0.0])
    return toks

def _attention_scores(tokens, temp=0.35):
    """Simple attention: query=ball token, keys=players, scores = softmax(-dist). """
    import math as _m
    ball = tokens[-1]
    scores=[]
    for t in tokens[:-1]:
        # distance in feature space
        d = sum((a-b)**2 for a,b in zip(t, ball))**0.5
        scores.append(_m.exp(-d/temp))
    s = sum(scores)
    return [x/s for x in scores] if s>0 else [1/len(scores)]*len(scores)

def _team_cohesion(state, is_player_a, attn):
    """Cohesion = 1 - variance of player x positions weighted by attention."""
    players = state["players_a"] if is_player_a else state["players_b"]
    if not players:
        return 0
    # weighted centroid
    w = attn[:len(players)]
    # normalize w for team
    s=sum(w) or 1
    w=[x/s for x in w]
    cx=sum(p["x"]*wi for p,wi in zip(players,w))
    var=sum(((p["x"]-cx)**2)*wi for p,wi in zip(players,w))
    # lower variance = tighter shape = higher cohesion
    return max(0, 100 - var*0.02)

def _pick_best_player(players, bx, by, is_player_a):
    best, best_s = 0, float("-inf")
    for i,p in enumerate(players):
        d_ball = math.hypot(p["x"]-bx, p["y"]-by)
        power=(p.get("stats") or {}).get("power",50)
        s=-d_ball/max(0.6,power/50) - dist_to_goal(p["x"],p["y"],is_player_a)*0.12
        if s>best_s:
            best=i
            best_s=s
    return best

def get_ai_move(state, is_player_a):
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by = state["ball"]["x"], state["ball"]["y"]
    target = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    # attention for current board
    toks = _token_features(state, is_player_a)
    attn = _attention_scores(toks)

    best_pidx = _pick_best_player(players, bx, by, is_player_a)
    p = players[best_pidx]
    dist = dist_to_goal(p["x"], p["y"], is_player_a)
    powers = suggested_powers(dist)
    powers = [powers[len(powers)//2], powers[-1]] if len(powers)>2 else powers

    best_val=float("-inf")
    best_move=(best_pidx, 0.0, powers[-1])
    for tx, ty in goal_tgts:
        base = aim_through(p["x"], p["y"], bx, by, tx, ty)
        for off in range(-28, 29, 6):
            angle = base + off
            for power in powers:
                traj, scored = simulate_kick(state, best_pidx, angle, power, is_player_a)
                if scored==target:
                    return (best_pidx, angle, power)
                if scored:
                    continue
                if len(traj)<=1:
                    val=-100
                else:
                    end=traj[-1]
                    # build next-state tokens for cohesion
                    # quick approx: move ball to end, move kicker near ball
                    fake = {"ball": {"x": end["x"], "y": end["y"]}, "players_a": state["players_a"], "players_b": state["players_b"]}
                    # reuse same players but updated ball
                    ntoks=_token_features(fake, is_player_a)
                    nattn=_attention_scores(ntoks)
                    val = progress_score(end["x"], is_player_a, defensive)
                    val += _team_cohesion(fake, is_player_a, nattn)*0.6
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val+=90
                        val+= max(0, 80 - abs(end["x"]-(FIELD_W if is_player_a else 0))*0.25)
                    # attention-weighted progress
                    val += sum(a*10 for a in nattn[:3])
                if val>best_val:
                    best_val=val
                    best_move=(best_pidx, angle, power)
    return best_move
