# Online play and remote setup

Git has one `origin` remote pointing to
`https://github.com/NghiemNgocDuc/Drag_Soccer_with_Agent.git`; `main` tracks
`origin/main`. Removing the duplicate `drag-soccer` alias preserved all commits
and working files. Use `git fetch origin` before publishing changes to check
whether the remote branch has advanced.

For local development, copy `.env.example`, set `DEV_MODE=1`, and leave unused
service keys empty. The default `python app.py` server listens on loopback.
To test two devices on a LAN, run
`python -m flask --app app run --host 0.0.0.0 --port 5000`
and open the host's LAN address. Deployed online matches need a
shared Redis instance so all Gunicorn workers read the same rooms and queues.
Set `SITE_URL` to the public browser-accessible base URL for account email links.

## Controls and lifecycle

Entering a room first loads its authoritative state, roster, names, and side.
The initial snapshot does not replay older moves. Controls are disabled while
loading, disconnected, leaving, waiting for an opponent, or during the other
player's turn. A player can select their own eligible team members; shootouts
use the goalkeeper and the dedicated penalty simulation.
The room badge, voice controls, and match chat occupy their own page space,
so they cannot cover the kick controls on desktop or mobile.

Online commands include `expected_move_count`. The server rejects a stale
command and serializes room changes. Online `move_count` advances independently
of regulation `kick_count`, including penalties. Both participants and live
spectators use the same revision planner: each complete batch plays once;
missing retained history snaps to the latest authoritative state.
Guest spectator pages initialize the default stadium scene before attempting
optional account customization, keeping the sky and sun correctly positioned.

Closing a waiting lobby cancels its unjoined room. If an opponent joined at
the same moment, `waiting_only:true` returns 409 rather than forfeiting the
match. Explicitly leaving an active room forfeits to the opponent through the
same completion path used by played matches. Ranked results remain authoritative
and idempotent. Finished or cancelled rooms leave the active-match index;
client polling, chat timers, requests, and voice connections are cleaned up.
If a completed ranked match has no rating result yet, a separate state-only
request retries at five-second intervals, at most six times. It stops when the
result arrives or the player exits; the winner panel reports a pending update
if recovery is still needed.

Room invitations cannot replace an existing opponent or reactivate a finished
room. Quick and ranked matchmaking serialize queue changes and remove expired
waiters. Voice signaling retains at most 100 messages while keeping a monotonic
sequence, so trimming ICE messages does not strand a peer's cursor. Voice remains
STUN-only, and restrictive NATs can still require a TURN service.

## Verification

`python tools/run_tests.py` runs isolated Python regression tests. The focused
online HTTP suite covers validation, membership, invitations, concurrent room
changes, queues, stale commands, cancellation/forfeit, and bounded voice cursors.
`node --test tests/frontend/test_player_controls.mjs` checks turn gates and revision
planning. `python tools/browser/verify_online_controls.py` uses two local browser
contexts plus a spectator and disables external integrations before importing
Flask. Reports and screenshots are written to the system temporary directory.
`python tools/browser/verify_online_result.py` checks delayed ranked-result recovery,
bounded retries, and cleanup when a player leaves.

The regression run passed 686 Python tests (four skipped), all 62 JavaScript
tests, 55 existing player-control browser checks, 57 integrated online browser
checks, 10 ranked-recovery browser checks, and 21 final layout checks. Browser checks had no JavaScript
exceptions. Voice checks used real local Chromium peers with fake microphones
and host-only ICE; they did not test production NAT traversal.

Use `python tools/browser/verify_online_controls.py --layout-only` for the final room
badge, chat, and control layout checks at desktop and mobile widths.

## Repository cleanup

`static/images/workflow.png` remains the served/downloadable diagram. The editable SVG
page is preserved in `docs/diagrams/workflow-source.html` with local fonts.
Two unused, byte-identical root exports were removed; `workflow.pdf` had PNG
bytes rather than PDF content. This removes 2,948,504 duplicate bytes without
changing the served diagram. README model descriptions now match the 12 supported
IDs, and the missing benchmark-script reference was removed.
