"""DQN Relative Coordinate — inspired by Park et al. 2022 Kick-motion Training with DQN in AI Soccer

Curse of dimensionality: absolute coordinates need 1400x875 state. Relative Coordinate System (RCS)
transforms to kicker-centric frame: ball (dx,dy), goal (dx,dy), teammates relative — reduces
state dim from 14 to 6, alleviates COD. DQN with target network + experience replay approximated
by handcrafted Q-table: Q = w·features, w tuned to mimic trained DQN.

Refs:
- Park et al. arXiv:2212.00389 Kick-motion Training with DQN, RCS vs ACS
- Kim et al. arXiv:2209.09491 Deep Q-Network for AI Soccer, 5:5, WCG 2019
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="DQN Relative"
DESCRIPTION="Relative coordinate DQN — RCS reduces COD, handcrafted Q weights."

# Handcrafted Q weights (would be learned via DQN in paper)
W_BALL_DIST = -0.8
W_GOAL_DIST = -0.15
W_GOAL_ANGLE = -0.05
W_PROGRESS = 1.2
W_GOAL_BONUS = 90

def _to_relative(px, py, bx, by):
    return (bx-px)/400, (by-py)/300  # normalize

def _q_value(state, pidx, angle, power, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    p=players[pidx]
    bx,by=state["ball"]["x"],state["ball"]["y"]
    gx = FIELD_W if is_player_a else 0
    gy=(GOAL_Y1+GOAL_Y2)/2
    # RCS features
    rdx, rdy = _to_relative(p["x"], p["y"], bx, by)
    ball_dist = math.hypot(rdx*400, rdy*300)
    goal_dx, goal_dy = _to_relative(bx, by, gx, gy)
    goal_dist = math.hypot(goal_dx*400, goal_dy*300)
    goal_angle = abs(math.degrees(math.atan2(goal_dy, goal_dx)) - angle)
    goal_angle = min(goal_angle, 360-goal_angle)
    # Q approx
    q = W_BALL_DIST*ball_dist + W_GOAL_DIST*goal_dist + W_GOAL_ANGLE*goal_angle
    return q

def _pick_best_player(players, bx, by, is_player_a):
    best, bs=0, float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx,p["y"]-by)
        # RCS: prefer close
        s=-d*0.9 - dist_to_goal(p["x"],p["y"],is_player_a)*0.08
        if s>bs: best,bs=i,s
    return best

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"],state["ball"]["y"]
    target="A" if is_player_a else "B"
    goal_tgts=goal_targets(is_player_a)
    best_pidx=_pick_best_player(players,bx,by,is_player_a)
    p=players[best_pidx]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    powers=suggested_powers(dist)
    powers=[powers[len(powers)//2], powers[-1]] if len(powers)>2 else powers
    best_q=float("-inf")
    best_move=(best_pidx,0.0,powers[-1])
    for tx,ty in goal_tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-30,31,6):
            angle=base+off
            for power in powers:
                # Q prior
                q_prior=_q_value(state,best_pidx,angle,power,is_player_a)
                traj,scored=simulate_kick(state,best_pidx,angle,power,is_player_a)
                if scored==target:
                    return (best_pidx,angle,power)
                if scored:
                    continue
                if len(traj)<=1:
                    val=q_prior -50
                else:
                    end=traj[-1]
                    val=q_prior + progress_score(end["x"],is_player_a, needs_clear(state,is_player_a))*W_PROGRESS*0.4
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val+=W_GOAL_BONUS*0.5
                    # DQN target: r + gamma*max Q_next (gamma 0.9)
                    # approx: if ball near goal, higher Q
                    gx=FIELD_W if is_player_a else 0
                    d_goal_next=math.hypot(end["x"]-gx, end["y"]-(GOAL_Y1+GOAL_Y2)/2)
                    val+= max(0, 50 - d_goal_next*0.08)
                if val>best_q:
                    best_q=val
                    best_move=(best_pidx,angle,power)
    return best_move
