# Agent Soccer — Build AI That Scores

[![Live](https://img.shields.io/badge/Live-agent.onrender.com-blue)](https://drag-soccer-with-agent.onrender.com/) [![Models](https://img.shields.io/badge/models-18-blueviolet)](#ai-agents) [![Physics](https://img.shields.io/badge/physics-pymunk%20%2B%20Three.js-0ea5e9)](#tech-stack)

Turn-based drag soccer on a **1400×875** pitch. Pull to aim, release to shoot — then watch your **Python AI** do the same. Tune **4 stats**, pick **48 nations** and **10 formations**, customize every kit and stadium color, then climb **ranked ELO**, **tournaments** and the **global leaderboard**.

> **Live:** https://drag-soccer-with-agent.onrender.com/ — `DEV_MODE=1` for local in-memory play, no keys needed.

---

## How it Works

Drag a player **backward** from the ball like a slingshot, then release. The player rockets forward, hits the ball on contact, and pymunk carries it across the field with wall bounces, billiard collisions, friction and goal-tunnel detection. First to **5 goals** wins (or leader after 60 kicks). Penalties are first-person: **5s keeper timer, auto-kick** if you wait.

Rendering is **Three.js** — broadcast + player views, 8-tier bowl, **2.2k** instanced crowd, sun/clouds/rain, synth WebAudio and goal particles. Physics stays **2D pymunk**, rendering is **3D**.

---

## Features

- **Slingshot input** — drag-to-aim on desktop + touch, power slider, undo
- **18 built-in AI agents** — Greedy → MCTS-UCT, each a distinct algorithm, swappable mid-game, all `<600ms`
- **3 game modes** — Human vs AI, Human vs Human, AI vs AI (auto-loop) on `/play3d`
- **Online 1v1** — create/join via link or username, spectate at `/spectate`, **WebRTC voice** + match chat
- **Friends & Clans** — 32 cap, presence `online/in_match/offline`, favorites, nicknames, invite with password, clan tournaments with timezone + DQ guard
- **Tournaments** — single-elimination at `/tournaments`, AI vs AI auto-advance, 3D replay + highlights
- **AI Playground** — write `get_ai_move` in-browser on `/playground`, import `.py`, validate, bench **5 vs Greedy** with live progress bar
- **My Models** — auto **Model N**, rename, add **paper links (5)**, public toggle, **Submit to Leaderboard** vs 7 core built-ins (mean win% + `avg_latency`)
- **Learn (7 lessons)** — machine-checked milestones from *First Kick* → *Beat Minimax* → *Capstone: Into the Arena* at `/learn`
- **AI Arena** — head-to-head matrix, shot accuracy, heatmaps at `/arena`
- **Analytics** — possession, shot zones, Elo history at `/analytics`
- **Research Hub** — Semantic Scholar search at `/research`
- **Customization** — per-player **name + kit color** (GK is `GK`), **200pt** across Size/Power/Weight/Agility, `6×2` keeper styles, crowd palettes, **stadium seat color**, sky scenes, ball designs at `/customize`
- **Social** — global / DM / clan chat, spectate live, highlights share `/highlight/<id>`, achievements (**49** badges), seasons + soft ELO reset
- **Replays** — full `replay_3d.html` viewer with CatmullRom arcs, spin, dust
- **Workflow** — high-level frontend → backend → database → engine → AI diagram at `/about#workflow` + `/workflow` (`/static/workflow.png`, `workflow.pdf`)
- **Auth** — Supabase email + Clerk optional, `/profile` + `/history` (last 5)

---

## AI Agents (18)

| Agent | Latency | Family | Idea |
|---|---|---|---|
| `genetic_fuzzy` | 74 ms | Fuzzy | GA-tuned Mamdani 9 rules |
| `greedy` | 88 ms | Heuristic | Best player, 6°/3° sweep, goal bonus |
| `potential_field` v2 | 88 ms | Field | APF + KNN Voronoi `xi=0.008`, NEE |
| `voronoi` v2 | 97 ms | Control | KNN decay + speed damp |
| `a2c_lite` | 142 ms | A2C | Advantage `V_next-V_cur`, 5° |
| `monte_carlo` | 158 ms | Sampling | 12 Gaussian samples |
| `expectimax` v2 | 232 ms | Search | Chance 0.5/0.3/0.2 + policy prune |
| `ppo_actor_critic` | 258 ms | RL | PPO actor + critic |
| `dqn_relative` | 276 ms | RL | DQN RCS Park 2022 |
| `q_learning` | 536 ms | RL | Zone-aware Q |
| `mcts_uct` v2 | 562 ms | MCTS | UCB1 WU-UCT + NEE + boosting top-3 |
| `minimax` | 689 ms | Search | 2° dense |
| `bayesian` | 806 ms | Bayes | Gaussian prior |
| `tactic_transformer` | 922 ms | Attention | TacticAI 7 tokens |
| `graph_gnn` | 962 ms | GNN | GAT `α=softmax` |
| `policy_iteration` | 983 ms | DP | Lane scoring |
| `value_iteration` | 1049 ms | DP | Centrality |
| `langchain` | ~800 ms | LLM | Tactician + physics verify |

All via `models/*.py:get_ai_move(state, is_player_a) -> (idx, angle, power)` → `simulate_kick(state, ...)`, sandboxed with `5s` timeout, fallback to greedy via `_ai_pool` (`gthread 4×2`). Bench: `DEV_MODE=1 python -u bench_new_models.py`.

---

## Writing a Custom AI

`/playground` → write once, run everywhere (`/playground` and `/my-models` share the same `TEMPLATE` block):

```python
def get_ai_move(state, is_player_a):
    # state["ball"]        -> {"x": float, "y": float}
    # state["players_a"]   -> [{"x":…, "y":…, "stats":{size,power,weight,agility}, "name":str}, …]  # 3
    # state["players_b"]   -> same for Team B
    # state["field"]       -> {"width": 1400, "height": 875}  # read, never hardcode!
    # state["score_a/b"]   -> int, state["kick_count"] -> int
    # is_player_a True  -> attack RIGHT (x=1400, y 356-519), False -> LEFT (x=0)
    return player_idx, angle_degrees, power  # 0/1/2, 0=right 90=down, 0-100
```

`math`, `random`, `copy` + builtins available. No net/fs. The `TEMPLATE` in `user_models/runner.py:177` includes `benchmark_vs_greedy` helper (`_bench_progress` bar).

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11+, Flask 3, gunicorn `gthread 4×2` |
| Physics | pymunk (Chipmunk2D) 6.5, `FIELD_W 1400` `FIELD_H 875` |
| Rendering | Three.js `r160` + OrbitControls, `static/workflow.png` |
| Editor | CodeMirror 5 + Python mode |
| State | Redis (rooms, presence, bench) with in-memory fallback |
| DB/Auth | Supabase (profiles, games, models, leaderboard) + Clerk optional |
| Realtime | WebRTC voice (STUN), HTTP poll `1.5s` for state/voice |
| Vector | Pinecone (paper embeddings) |
| Frontend | Vanilla JS, `design-system.css` glass, HTML5 |
| Email/Analytics | Resend, PostHog, Sentry, ProductBridge |
| Deploy | Render `render.yaml` |

---

## Quick Start

```bash
git clone https://github.com/NghiemNgocDuc/Drag_Soccer_with_Agent.git
cd Drag_Soccer_with_Agent
pip install -r requirements.txt
cp .env.example .env  # fill SUPABASE_URL, SUPABASE_ANON_KEY, SERVICE_KEY, UPSTASH_REDIS_URL, SECRET_KEY
python app.py  # http://localhost:5000/play3d
```

Run without keys: `DEV_MODE=1 python app.py` (in-memory Redis/Supabase, anon `dev:*`). Run schema once in Supabase SQL Editor: `supabase_schema.sql` + each `migration_*.sql`.

Tests: `DEV_MODE=1 python -m pytest -q --ignore=test_integration.py --ignore=balance_test.py` → `~280` passed.

---

## Project Structure

```
app.py                 # 5k+ routes: play3d, playground, friends, clans, leaderboard, spectate, ranked, seasons
config.py              # DEV_MODE fallback
models/soccer_logic.py # 1400x875 engine, simulate_kick, loft, recoil, referee
models/*.py            # 10 agents (greedy … a2c_lite)
game/session.py        # Redis game session
db/*.py                # 16 modules (friends, clans, leaderboard, customization, etc.)
services/*.py          # clerk, resend, pinecone, paper_search, game_analytics, loss_analysis
user_models/runner.py  # AST scan + timeout sandbox + TEMPLATE
static/                # design-system.css, sound.js, workflow.png, vendor/ (Three, CodeMirror, Chart, fonts)
templates/             # 30+ pages: landing, play, playground, my_models, leaderboard, about, workflow, learn, etc.
migration_*.sql        # per-feature Supabase migrations
```

---

## Deploy

`render.yaml` one-click on Render. Set `SECRET_KEY`, `SUPABASE_*`, `UPSTASH_REDIS_URL`, `FEEDBACK_TO_EMAIL`, `MODERATION_*` in dashboard.

---

## Author

**Ngoc Duc Nghiem** — [NghiemNgocDuc](https://github.com/NghiemNgocDuc)
