# Local basketball

Open **Play**, select a local match mode, and choose **Basketball** in pre-match setup. Select **Start match** to open the court. Changing the local mode keeps your uncommitted sport choice. **New Game** returns to setup with the last sport selected; choose Soccer there to switch back.

Basketball supports human vs Court AI, two people taking turns on one device, and AI vs AI. Online rooms, ranked matches, tournaments and the Python model workshop currently use soccer. The basketball AI uses a dedicated court policy rather than the soccer opponent catalog.

## Controls and rules

- Select any of your five players by tapping their disc or using the player selector. The default selection follows your ball carrier or nearest rebounder.
- The selector names each basketball position and marks the ball carrier. The turn status identifies the attacking direction for each team, including two-player games.
- Pull the carrier backward and release to **throw**. Direction and power determine whether the arcing ball passes to a teammate or reaches a hoop. Use the direction/power controls and **Throw** as a keyboard alternative.
- Pull an off-ball player backward and release to **move**. Reach a loose ball to collect it, or reach the opposing carrier to steal it. AI matches also move supporting players during each action.
- Team A attacks the right basket; Team B attacks the left. A ball must pass downward through a hoop to score. A ground-level crossing cannot score.
- A basket is worth **2 points inside the arc** or **3 points from/on the arc** (360 world units from the hoop). The conceding side receives a quick backcourt inbound.
- The drawn arc uses that same circular distance all the way to the baseline. Soccer pass presets are hidden in basketball; the displayed power directly sets throw or movement strength.
- The first team to reach **21 with a lead of at least 2** wins. There is no game clock, halftime or shootout. Turns alternate between teams; after a basket, the receiving team acts next.
- Size and weight affect player contact, power affects movement and throwing range, and agility affects braking. Saved player stats and cosmetics survive sport changes and baskets.

This is turn-based arcade basketball: carriers throw on their action, off-ball players move, and possession persists until a throw, catch, steal or basket changes it. It does not implement real-time dribbling, fouls, free throws, a shot clock or regulation basketball quarters.

## Implementation and saves

`models/basketball_logic.py` owns court rules and the deterministic AI. Every action runs one pymunk world for at most 210 steps at 60 Hz, records both rosters, and resolves all movement before the next command. Throws use a vertical parabola with a descending rim-height check; prediction and application use the same simulation.

Rising throws clear nearby player discs before descending catches. The nearest rebounders continue toward loose balls on opposing turns instead of being pulled back into man marking. Complete-match regressions cover saved power limits from 60 to 120 and valid 200-point player builds.

Game state uses `sport: "basketball"`, `possession: {side, player_idx}` (or `null`), `last_points`, and a `basketball` rules catalog. Existing `/move`, `/ai_move`, `/state`, `/reset`, `/undo` and `/start_match` endpoints retain their response structure and carry the additional metadata. Basketball always uses five players and a 21-point, win-by-two target, irrespective of soccer customization defaults.

Basketball progress uses the existing autosave/session path. Undo restores possession as well as rosters, ball and scores. Completed human-vs-AI games are stored under the `basketball_hvai` mode in match history. Their Canvas replays preserve two/three-point scoring and inbound positions. Basketball points do not award soccer goal badges or add to database soccer goal totals. No database migration is needed.

Sessions and saved replays retain at most 60 recorded actions to bound payload size. A saved replay that begins mid-match retains the score before its first recorded action.

## Verification

With the normal project dependencies, pytest and Playwright installed:

```powershell
python tools/run_tests.py tests/backend/test_basketball.py tests/backend/test_basketball_api.py tests/backend/test_basketball_completion.py tests/backend/test_match_setup.py tests/backend/test_match_formations.py
node --test tests/frontend/*.mjs
python tools/browser/verify_basketball.py
python tools/browser/verify_match_setup.py
```

The basketball browser verifier checks keyboard and mobile setup, mode changes, draft isolation, throwing and real drag movement, scoring, AI return, a natural winning basket, the actual saved-history replay, switching back to soccer, two-player turns and browser errors. Screenshots and its report are saved under the system temporary directory in `agent-soccer-basketball`.

`test_basketball_completion.py` finishes matches through the authoritative HTTP handlers in human-vs-AI, two-player and AI-vs-AI modes, checks the winning margin and rejects further play after completion. Human-vs-AI coverage also verifies one persisted result and its real replay route.
