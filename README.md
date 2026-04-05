# O An Quan — AI Strategy Engine

> A full-stack implementation of the traditional Vietnamese mancala game, powered by five AI agents built from scratch.


## Overview

**O An Quan** (*Mandarin's Square*) is a classic Vietnamese strategy game where players sow and capture stones across a 2x5 board. This project digitizes the game with a production-grade web interface and five AI agents — each representing a distinct paradigm of decision-making in AI/ML, implemented from scratch without ML libraries.

---

## Features

- 3 Game Modes — Human vs. AI, Human vs. Human, AI vs. AI (auto-play)
- 5 AI Agents — hot-swappable at runtime without restarting the server
- AI Benchmark System — run head-to-head tournaments between any two agents
- Undo / Move History — up to 50 moves back with full replay log
- 26 Unit Tests — full pytest coverage across all game logic and agents
- REST API — 10 endpoints, fully scriptable from outside the browser

---

## AI Agents

| Agent | Algorithm | Notes |
|---|---|---|
| `minimax` | Alpha-Beta Minimax | Depth-5 search, move-ordering cuts search space ~50% |
| `monte_carlo` | UCB1-guided MCTS | 300 simulations/move with heuristic rollouts |
| `q_learning` | Tabular Q-Learning | 50k self-play episodes, e-greedy decay, reward shaping |
| `greedy` | Greedy Heuristic | Maximizes immediate capture reward |
| `random` | Random | Baseline for benchmarking |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask 3 |
| AI | Custom implementations — no ML libraries |
| Frontend | Vanilla JS, CSS Grid |
| Testing | pytest |

---

## Project Structure

```
.
├── app.py                  # Flask REST API (10 endpoints)
├── game_logic.py           # Core engine: sowing, capture, scoring
├── models/
│   ├── minimax.py          # Alpha-Beta Minimax with move ordering
│   ├── monte_carlo.py      # MCTS with UCB1 + heuristic rollouts
│   ├── q_learning.py       # Tabular RL with self-play training
│   ├── greedy_model.py     # Greedy baseline
│   └── value_iteration.py  # Value iteration baseline
├── templates/
│   └── index.html          # Single-page game UI
└── tests/
    └── test_game_logic.py  # 26 unit tests
```

---

## Quick Start

```bash
# Clone
git clone https://github.com/NghiemNgocDuc/Playing-O-An-Quan.git
cd Playing-O-An-Quan

# Install
pip install -r requirements.txt

# Run
python app.py
# Open http://localhost:5000
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/state` | Full game state (board, scores, valid moves) |
| POST | `/move` | Submit a human move |
| POST | `/ai_move` | Trigger one AI move |
| POST | `/switch_model` | Swap AI agent at runtime |
| POST | `/set_mode` | Change game mode |
| POST | `/reset` | Restart the game |
| POST | `/undo` | Undo last move |
| GET | `/history` | Full move log |
| POST | `/benchmark` | Run N AI vs. AI games, returns win rates |
| GET | `/models` | List all available agents |

---

## Author

**Ngoc Duc Nghiem**
GitHub: https://github.com/NghiemNgocDuc
Email: nghiemngocduc7@gmail.com
