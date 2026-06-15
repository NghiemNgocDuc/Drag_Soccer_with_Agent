"""
game_logic.py — Core Ô Ăn Quan game engine.

Board layout (12 positions):
  [0]  = Quan (A's mandarin pit)
  [1-5] = Player A's small pits
  [6-10] = Player B's small pits
  [11]  = Quan (B's mandarin pit)

Movement order (circular):  0→1→2→3→4→5→11→10→9→8→7→6→0 ...
"""
from __future__ import annotations
import copy
import json
from dataclasses import dataclass, field, asdict
from typing import Optional

MOVEMENT_ORDER = [0, 1, 2, 3, 4, 5, 11, 10, 9, 8, 7, 6]
QUAN_SCORE = 5
RESEED_COST = 5

#  Board representation 

def initialize_board() -> list[list[str]]:
    """Return a fresh 12-pit board: 2 Quan pits + 10 small pits."""
    return (
        [["quan"]]
        + [["dan"] * 5 for _ in range(5)]  # pits 1–5 (Player A)
        + [["dan"] * 5 for _ in range(5)]  # pits 6–10 (Player B)
        + [["quan"]]                        # pit 11
    )

def board_to_counts(board: list[list[str]]) -> list[int]:
    """Convert board to count-per-pit for lightweight representation."""
    return [len(pit) for pit in board]

def board_to_state_key(board: list[list[str]]) -> tuple:
    """Immutable hashable state for memoization / Q-table keys."""
    return tuple(tuple(pit) for pit in board)

def calculate_score(dan: int, quan: int) -> int:
    return dan + quan * QUAN_SCORE

#  Movement helpers

def _next_pos(current_pos: int, step: int) -> int:
    idx = MOVEMENT_ORDER.index(current_pos)
    return MOVEMENT_ORDER[(idx + step) % len(MOVEMENT_ORDER)]

def is_valid_move(board: list[list[str]], pos: int, is_player_a: bool) -> bool:
    if not board[pos]:
        return False
    return (1 <= pos <= 5) if is_player_a else (6 <= pos <= 10)

def get_valid_moves(board: list[list[str]], is_player_a: bool) -> list[tuple[int, int]]:
    """Return [(pos, step)] for all legal moves for current player."""
    prange = range(1, 6) if is_player_a else range(6, 11)
    return [(pos, step) for pos in prange for step in (1, -1) if board[pos]]

#  Core move mechanics 

def _sow(board: list[list[str]], pos: int, step: int, count: int | None = None) -> int:
    """Scatter seeds from pit `pos` in direction `step`. Returns last pit sown.
    If count is given, only that many seeds are picked up; the rest remain in pos."""
    all_seeds = board[pos][:]
    if count is None or count >= len(all_seeds):
        seeds = all_seeds
        board[pos] = []
    else:
        seeds = all_seeds[:count]
        board[pos] = all_seeds[count:]
    cur = pos
    while seeds:
        cur = _next_pos(cur, step)
        board[cur].append(seeds.pop(0))
    return cur

def _relay_sow(board: list[list[str]], start: int, step: int, count: int | None = None) -> int:
    """
    Relay rule: if the pit immediately after the last seed lands is non-empty
    (and not a Quan), keep sowing from there. Return final resting pit.
    count only applies to the first (player-chosen) sow.
    """
    cur = start
    first = True
    while True:
        last = _sow(board, cur, step, count if first else None)
        first = False
        nxt = _next_pos(last, step)
        if board[nxt] and nxt not in (0, 11):
            cur = nxt
        else:
            return last

#  Capture & full turn 

@dataclass
class TurnResult:
    captured_dan: int = 0
    captured_quan: int = 0
    reseeded: bool = False
    relay_positions: list[int] = field(default_factory=list)

def execute_capture(
    board: list[list[str]],
    start_pos: int,
    is_player_a: bool,
    step: int,
    count: int | None = None,
) -> TurnResult:
    """
    Execute a full turn: relay sow + multi-capture sweep.
    Returns TurnResult describing what was captured.
    """
    result = TurnResult()
    last = _relay_sow(board, start_pos, step, count)
    result.relay_positions.append(last)

    # Multi-capture: keep hopping over empty pits and grabbing
    cur = last
    while True:
        skip = _next_pos(cur, step)
        target = _next_pos(skip, step)
        if board[skip] or not board[target]:
            break
        captured = board[target][:]
        board[target] = []
        result.captured_dan += captured.count("dan")
        result.captured_quan += captured.count("quan")
        cur = target

    return result

def restore_player_side(
    board: list[list[str]],
    score: dict,
    is_player_a: bool,
) -> bool:
    """
    If a player's side is empty, spend 5 dan to reseed 5 pits (1 each).
    Returns True if reseeding happened.
    """
    prange = range(1, 6) if is_player_a else range(6, 11)
    if all(not board[i] for i in prange):
        cost = min(score["dan"], RESEED_COST)
        score["dan"] -= cost
        for i in prange:
            board[i] = ["dan"]
        return True
    return False

#  End-game logic 

def is_game_over(board: list[list[str]], score_a: dict, score_b: dict) -> bool:
    both_quan_gone = (score_a["quan"] + score_b["quan"]) >= 2
    all_small_empty = all(not board[i] for i in range(1, 11))
    return both_quan_gone or all_small_empty

def finalize_scores(board: list[list[str]], score_a: dict, score_b: dict) -> None:
    """Award remaining small-pit seeds to their owner. Mutates scores."""
    score_a["dan"] += sum(pit.count("dan") for pit in board[1:6])
    score_b["dan"] += sum(pit.count("dan") for pit in board[6:11])
    for i in range(1, 11):
        board[i] = []

def determine_winner(score_a: dict, score_b: dict) -> str:
    ta = calculate_score(score_a["dan"], score_a["quan"])
    tb = calculate_score(score_b["dan"], score_b["quan"])
    if ta > tb:   return "Player A wins!"
    if tb > ta:   return "Player B wins!"
    return "It's a tie!"

#  Serialization 

def serialize_state(board, score_a, score_b, is_player_a, move_history=None) -> dict:
    return {
        "board": board,
        "score_A": score_a,
        "score_B": score_b,
        "is_player_A": is_player_a,
        "move_history": move_history or [],
        "game_over": is_game_over(board, score_a, score_b),
    }

#  Move history / snapshot 

@dataclass
class GameSnapshot:
    board: list
    score_a: dict
    score_b: dict
    is_player_a: bool
    move_desc: str = ""

    def clone(self) -> "GameSnapshot":
        return GameSnapshot(
            board=copy.deepcopy(self.board),
            score_a=self.score_a.copy(),
            score_b=self.score_b.copy(),
            is_player_a=self.is_player_a,
            move_desc=self.move_desc,
        )