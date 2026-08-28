"""Offline chatbot — no API key, no external call.

Tries local transformers (DialoGPT-small) if available, else instant
regex rule fallback (<5ms). Covers game rules, controls, teams, formations,
penalties, keeper styles, time, etc.
"""
from __future__ import annotations
import re

_pipe = None
_pipe_tried = False
def _get_pipe():
    global _pipe, _pipe_tried
    if _pipe_tried:
        return _pipe
    _pipe_tried = True
    try:
        from transformers import pipeline
        _pipe = pipeline('text-generation', model='microsoft/DialoGPT-small', max_new_tokens=60, pad_token_id=50256, truncation=True)
    except Exception:
        _pipe = None
    return _pipe

INTENTS: dict[str, str] = {
    r'how.*play|controls|move|kick|drag|slingshot': "Drag a player backward like a slingshot → release. Keys: S=short (0.62×) A=long (1.08×) W=through (0.88×) Q=switch player E=hold sprint +18% . Ball stays ground (z=0).",
    r'pass.*type|short|long|through': "Pass types: Short S (62% power, ground), Long A (108% chip), Through W (88% + aim beyond). Choose before drag.",
    r'formation|3-2-1|2-3-1|diamond|1-4-1': "10 × 7v7 (GK+6): 2-3-1 most popular, 3-2-1 defensive, 2-1-2-1 diamond, 3-1-2, 2-2-2, 1-3-2, 3-3-0, 1-4-1, 2-1-3, 1-2-3. Tied to manager, changeable in team chooser before kickoff.",
    r'team|brazil|france|argentina|choose.*team': "48 WC2026 teams (Brazil … DR Congo). 2 players cannot pick same team (409). Player names appear above kits (e.g., Messi 10, Ronaldo 7).",
    r'penalty|keeper.*view|kicker': "Penalty 2-view: Kicker view behind spot (1106→1380 y520) vs Keeper view behind goal (70→294 y420). Human kicker drags ball, keeper picks Left/Center/Right.",
    r'keeper.*style|footwork|rush|deflector|far.*reach|far.*throw|cross': "Keeper 6×2 PlayStyles: Footwork (+4-6 radius, 1.18-1.28 dive low), Rush Out (1.35-1.55 rush), Deflector (0.62 safe), Cross Claimer (1.22-1.38 claim), Far Reach (+7-11 radius), Far Throw (1.42-1.72 distribution).",
    r'time|90|extra|halftime|et': "Time: 3 real sec =1 game min (20×). ht 135s=45' ft 270s=90' et1 315s=105' et2 360s=120' → penalties. Clock label 1H/2H/ET1/ET2/PEN.",
    r'score|win|lose|elo|ranked': "First to win_goal_limit (default 5) wins. Ranked ELO 1200 K40<10 games else K20, placement 10 games. Leaderboard /leaderboard/ranked.",
    r'view|top|player.*view': "Top view (-1500,790,320) broadcast vs Player view behind ball (-260/+260,92,135) follows ball. Settings  120s timeout.",
    r'sound|music|mute|crowd': "Sound synth WebAudio, no assets: kick 0.16s, bounce 0.10s, goal fanfare 1s, whistle 0.8s, crowd ambient 0.10 + cheer 2.5s ducks 0.35. Toggle .",
    r'stadium|crowd|bench|field|grass': "Stadium 8-tier bowl 2.2k north-blue south-red, striped grass 512, LED W+40, FIFA 105×68 lines PA 220×519, sun 170+halo 300 +11 clouds.",
    r'model|ai|langchain|minimax|greedy': "7 built-in AIs + LangChain Tactician (LLM-guided + physics verify, <1.5s, fallback pruned). Pick in Game → Model or Playground.",
}

def get_response(msg: str) -> str:
    if not msg or not msg.strip():
        return "Ask me about rules, controls, teams, formations, penalties, keepers, time, or AI models!"
    q = msg.lower().strip()
    # exact intent match first
    for pat, ans in INTENTS.items():
        if re.search(pat, q):
            return ans
    # try local LLM if available (offline, no API) — lazy load
    pipe = _get_pipe()
    if pipe:
        try:
            out = pipe(q, max_new_tokens=60, do_sample=False, truncation=True)[0]['generated_text']
            # DialoGPT returns input + generated
            if out.lower().startswith(q.lower()):
                out = out[len(q):].strip()
            # clean
            out = out.split("\n")[0].strip()
            if out and len(out) > 10 and len(out) < 300:
                return out
        except Exception:
            pass
    return "I can help with: rules & controls (drag, S/A/W/Q/E), 48 teams & formations (2-3-1 etc.), penalties (kicker vs keeper views), keeper PlayStyles (6×2), time 0-90+ET, sound, stadium, or AI models. What do you want to know?"
