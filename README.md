# Agent Soccer

A top-down slingshot soccer game where you code your own AI to play against built-in agents, challenge friends online, or watch AIs battle each other in real time.

Built with Flask, HTML5 Canvas, Redis, and Supabase.

---

## How it works

Each turn you drag a player **backward** from their body — like a slingshot — then release. The player rockets forward, hits the ball on contact, and the ball flies across the 800×500 field. First team to 5 goals wins, or whoever leads after 60 kicks.

Physics include wall bounces, player-player collisions (with billiard-style push), friction, and goal-tunnel detection so the ball only scores if it reaches the back wall through the goal opening.

---

## Features

- **Slingshot input** — drag-to-aim on desktop and mobile (touch supported)
- **7 built-in AI agents** — each a distinct algorithm, swappable mid-game
- **3 game modes** — Human vs AI, Human vs Human, AI vs AI (auto-loop)
- **Online multiplayer** — real-time 1v1 via invite link or username search, no account needed to join
- **Friend system** — send/accept friend requests, invite friends to games directly from the lobby
- **Tournaments** — create and join single-elimination tournaments; AI vs AI matches auto-advance
- **Replays** — watch recorded match replays with playback controls
- **AI Playground** — write a custom Python AI in the browser, import a `.py` file from disk, validate and test it live against any built-in agent
- **My Models** — save, edit, and publish your custom AIs; use them as opponents in the main game
- **Leaderboard & Profile** — win/loss stats tracked per user
- **Auth** — email/password via Supabase; guests can join online rooms without an account

---

## AI Agents

| Agent | Algorithm | Description |
|---|---|---|
| `minimax` | Search | Evaluates kicks considering opponent block positions (ball bounces off them) |
| `monte_carlo` | Sampling | Samples random kicks centred on the player-to-ball direction |
| `q_learning` | Zone search | Zone-aware angle search centred on player-to-ball direction |
| `bayesian` | Gaussian prior | Gaussian prior over kick angles centred on the player-to-ball direction |
| `value_iteration` | Centrality | Picks most centrally positioned player, searches angles through ball toward goal |
| `policy_iteration` | Lane scoring | Picks player with clearest path to ball, aims through ball toward goal |
| `greedy` | Heuristic | Picks the player nearest the ball, aims through it toward goal |

---

## Writing a Custom AI

Open `/playground`, write Python in the editor (or click **↑ Import .py** to load a file from your computer), then hit **Start** to play it live.

Your code must define one function:

```python
def get_ai_move(state, is_player_a):
    # state["ball"]        → {"x": float, "y": float}
    # state["players_a"]  → [{"x":…, "y":…}, …]   # 3 players, Team A
    # state["players_b"]  → [{"x":…, "y":…}, …]   # 3 players, Team B
    # state["score_a"]    → int
    # state["score_b"]    → int
    # state["kick_count"] → int
    # state["max_kicks"]  → int  (game ends here)
    #
    # is_player_a = True  → you are Team A, attack RIGHT (goal at x ≈ 800)
    # is_player_a = False → you are Team B, attack LEFT  (goal at x ≈ 0)

    return player_idx, angle_degrees, power
    # player_idx    → 0 / 1 / 2   (which of your 3 players kicks)
    # angle_degrees → 0=right, 90=down, 180=left, 270=up
    # power         → 0–100
```

`math`, `random`, `numpy`, and `copy` are available. Network access, file I/O, and dangerous builtins are blocked. Execution is sandboxed with a 5-second timeout per move.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask 3 |
| Game state | Upstash Redis (rooms, sessions, friends, tournaments) |
| Auth & DB | Supabase (profiles, games, user models) |
| Frontend | Vanilla JS, HTML5 Canvas, CSS glass-morphism theme |
| Code editor | CodeMirror 5 |
| Deployment | Render (`render.yaml` included) |

---

## Project Structure

```
├── app.py                   # Flask routes (game, online, playground, friends, auth, tournaments)
├── config.py                # Environment variable loading
├── render.yaml              # Render deployment config
├── requirements.txt
├── supabase_schema.sql      # Run once in Supabase SQL Editor to create tables
│
├── models/
│   ├── soccer_logic.py      # Physics engine (kicks, collisions, goals)
│   ├── minimax.py
│   ├── monte_carlo.py
│   ├── q_learning.py
│   ├── bayes.py
│   ├── value_iteration.py
│   ├── policy_iteration.py
│   └── greedy_model.py
│
├── game/
│   └── session.py           # Redis-backed game/playground state
│
├── db/
│   ├── redis_client.py      # Upstash Redis client with in-memory fallback for dev
│   ├── supabase_client.py   # Supabase anon + service clients
│   ├── games.py             # Save results, leaderboard queries
│   ├── user_models.py       # CRUD for user-uploaded AI models
│   └── tournaments.py       # Tournament creation, bracket generation, match results
│
├── user_models/
│   └── runner.py            # Sandboxed Python execution for custom AI (AST scan + timeout)
│
├── static/
│   └── style.css            # Glass-morphism theme, light mode
│
└── templates/
    ├── index.html           # Main game (slingshot canvas)
    ├── online.html          # Online multiplayer lobby + game
    ├── playground.html      # Code editor + live game
    ├── my_models.html       # Manage saved AI models
    ├── leaderboard.html
    ├── profile.html
    ├── login.html
    ├── register.html
    ├── tournaments.html     # Tournament listings
    ├── tournament_view.html # Tournament bracket view
    └── replay.html          # Match replay viewer
```

---

## Quick Start

```bash
git clone https://github.com/NghiemNgocDuc/Drag_Soccer_with_Agent.git
cd Drag_Soccer_with_Agent
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
SECRET_KEY=your-secret-key

# Supabase (create a project at supabase.com)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...

# Upstash Redis (create a database at upstash.com)
UPSTASH_REDIS_URL=rediss://...
UPSTASH_REDIS_TOKEN=...
```

Run the schema once in your Supabase SQL Editor:

```sql
-- paste contents of supabase_schema.sql
```

Then start the server:

```bash
python app.py
# opens http://localhost:5000
```

The game runs fully without Supabase/Redis in offline dev mode — auth is bypassed and state is held in-memory.

---

## Online Multiplayer

1. Go to `/online` and click **Create Game** — you get a shareable link
2. Send the link to anyone; they join instantly, no account required
3. Or search by username and send an invite — it appears in their lobby in real time
4. Add friends to invite them faster next time

Rooms are stored in Redis and expire after 6 hours.

---

## Tournaments

Create a tournament at `/tournaments`, invite participants, and generate a single-elimination bracket. AI vs AI matches run automatically and the bracket advances as matches complete. Tournament data is stored in Redis.

---

## Deployment

The repo includes `render.yaml` for one-click deploy on [Render](https://render.com):

```bash
# Just connect the repo in Render and set your environment variables.
# The start command is: gunicorn app:app
```

---

## Author

**Ngoc Duc Nghiem**  
GitHub: [NghiemNgocDuc](https://github.com/NghiemNgocDuc)
