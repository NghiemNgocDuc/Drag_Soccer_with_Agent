"""PPO Actor-Critic — inspired by Kernbach 2026 Actor-Critic Pretraining for PPO + Kich 2024 KAN for PPO

Actor pretraining via behavioral cloning on greedy expert, critic pretraining via returns from rollouts.
We implement a lightweight version: actor is greedy with clipped noise (PPO clip 0.2), critic is
value = progress + cohesion + goal bonus, advantage = critic_next - critic.

No training needed — handcrafted actor/critic that mimics PPO's clipped objective and GAE.

Refs:
- Kernbach arXiv:2602.23804 Actor-Critic Pretraining for PPO (2026)
- Kich et al. KAN for PPO, arXiv:2408.04841 (2024)
- Haarnoja et al. Science Robotics 2024 MPO (PPO variant)
"""
from __future__ import annotations
import math, random
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME="PPO Actor-Critic"
DESCRIPTION="Clipped PPO-style actor (behavioral cloning + noise) + critic value, 1-ply advantage."

def _pick_best_player(players, bx, by, is_player_a):
    best, bs = 0, float("-inf")
    for i,p in enumerate(players):
        d=math.hypot(p["x"]-bx, p["y"]-by)
        s=-d - dist_to_goal(p["x"],p["y"],is_player_a)*0.1
        if s>bs: best,bs=i,s
    return best

def _critic(state, is_player_a):
    # value of board for player A/B
    bx=state["ball"]["x"]
    defensive=needs_clear(state, is_player_a)
    base=progress_score(bx, is_player_a, defensive)
    # cohesion bonus
    players=state["players_a"] if is_player_a else state["players_b"]
    if players:
        cx=sum(p["x"] for p in players)/len(players)
        var=sum((p["x"]-cx)**2 for p in players)/len(players)
        base+= max(0, 30 - var*0.01)
    # goal proximity
    by=state["ball"]["y"]
    if GOAL_Y1 <= by <= GOAL_Y2:
        base+=40
    return base

def get_ai_move(state, is_player_a):
    players=state["players_a"] if is_player_a else state["players_b"]
    bx,by=state["ball"]["x"],state["ball"]["y"]
    target="A" if is_player_a else "B"
    goal_tgts=goal_targets(is_player_a)
    best_pidx=_pick_best_player(players, bx, by, is_player_a)
    p=players[best_pidx]
    dist=dist_to_goal(p["x"],p["y"],is_player_a)
    powers=suggested_powers(dist)
    powers=[powers[len(powers)//2], powers[-1]] if len(powers)>2 else powers
    # actor: behavioral cloning from greedy (aim_through) + clipped PPO noise
    best_val=float("-inf")
    best_move=(best_pidx,0.0,powers[-1])
    for tx,ty in goal_tgts:
        base=aim_through(p["x"],p["y"],bx,by,tx,ty)
        for off in range(-28,29,7):
            # PPO clip: limit off to [-10,10] with 0.2 clip
            clipped_off = max(-10, min(10, off)) * 0.8 + off*0.2
            angle=base+clipped_off + random.uniform(-2,2)
            for power in powers:
                # clipped power noise
                pw = max(40, min(100, power + random.uniform(-3,3)))
                traj, scored=simulate_kick(state,best_pidx,angle,pw,is_player_a)
                if scored==target:
                    return (best_pidx,angle,pw)
                if scored:
                    continue
                if len(traj)<=1:
                    val=-100
                else:
                    end=traj[-1]
                    fake={"ball":{"x":end["x"],"y":end["y"]},"players_a":state["players_a"],"players_b":state["players_b"]}
                    # advantage = V(next) - V(current) + progress
                    v_next=_critic(fake, is_player_a)
                    v_cur=_critic(state, is_player_a)
                    adv = (v_next - v_cur) * 0.9 + progress_score(end["x"], is_player_a, needs_clear(state, is_player_a))*0.5
                    # PPO clip on advantage
                    adv = max(-50, min(50, adv))
                    val=adv
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val+=30
                if val>best_val:
                    best_val=val
                    best_move=(best_pidx,angle,pw)
    return best_move
