# Table studio

The table studio is not exposed in the soccer-only app navigation or Flask routes.
Its implementation and tests remain in the repository as archived reference code.
The instructions below describe that retained implementation, not a game available
from the current Agent Soccer interface.

Choose a preset, optionally open **Tune the physics**, then preview or start a
match. JSON descriptions override the editor while the import box contains text.
Previewing and editing never replace the saved match and cannot submit shots.
**Play the bot** explicitly starts a new match. Reloading restores a saved match;
**Resume saved match** restores it after edits or a failed request.

Drag from your mallet or cue ball in the desired direction, then release.
Keyboard controls on the focused canvas: left/right aim, up/down power, Enter
or Space shoot, Escape cancel. Clicking empty table space does not shoot.
The responsive cream/burgundy studio keeps optional settings collapsed; starting
a match on a phone brings the table into view. Reduced-motion users see final
shot positions without trajectory animation.

## State integrity

- `games/pack_spec.py` accepts flat descriptions and canonical returned specs.
  Editing a preset preserves hidden materials, colors, and custom bumpers.
  Unknown preset IDs reject rather than silently selecting another table.
- Starts and moves share a per-player Redis lock in production. Local fallback
  uses 64 fixed locks instead of an unbounded per-user lock dictionary.
- Each new match has a unique `match_id`. `/api/packs/move` requires that ID and
  the integer `expected_shot`. A stale or repeated shot returns 409 without
  changing state. This also fences shots from a replaced match in another tab.
- Controls stay disabled during requests and playback. Requests have a 15-second
  deadline. Failed moves are never automatically resubmitted; the player must
  restore authoritative state using Resume saved match.
- Bot own goals can retain its kickoff. The points target bounds consecutive bot
  turns so that a live match cannot get stranded after two bot shots.

The Redis match expires after six hours. The development in-memory Redis fallback
does not implement expiry or durability. This is local bot play, not online
multiplayer. Redis lock behavior follows the existing online-write pattern;
cross-worker production Redis behavior was not exercised by the local tests.

## Verification — November 17, 2026

- `python tools/run_tests.py tests/backend/test_game_packs.py`: 29 passed.
  Physics presets, validation, canonical round trips, HTTP lifecycle, duplicate
  concurrency, previous-match rejection, preview isolation, and bot own goals.
- `python tools/browser/verify_packs.py`: isolated Chrome server; drag, preview,
  reload, lost-response recovery, invalid JSON, responsive studio/hub/setup pages.
  Screenshots and JSON report are saved under the temporary
  `agent-soccer-pack-review` directory.
- `python tools/browser/verify_basketball.py`: 40 checks passed, including a
  completed match, history/replay, phone controls, and switching back to soccer.
- Match setup, player controls, and match clock Node suites: 22 passed.

Browser verification disables external integrations and uses temporary dev
sessions. It does not modify production accounts or publish a deployment.
