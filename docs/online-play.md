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

## Lobby and match recovery

The served `/play2d` lobby separates **Play with a friend** from **Find a match**.
Friends can create a room with an optional password, share its invitation, or
join with a code or full link. Casual matchmaking retains its eight-second AI
fallback; ranked matchmaking uses registered accounts. Native tabs support
arrow keys, Home and End, with one scrolling dialog on small screens.

`GET /online/current` recovers the current session's waiting room, active match,
or ongoing matchmaking search. Opening the lobby offers **Open invitation** or
**Resume match**, or restores the search controls. Recovery depends on the same
browser session/account; the endpoint does not expose room passwords.

Room membership and matchmaking changes share one Redis-backed lock. A player
cannot enter two queues or create another room while already playing.
`online:room:<user_id>` tracks validated membership without scanning rooms.
Room and handoff pointers have a six-hour expiry, refreshed when the room is
saved. Completed/cancelled rooms clear only pointers still referring to that
room. Repeating Create after a lost response returns the existing waiting room;
rejoining an assigned room does not require its password again.

## Requests and connection state

`static/js/game/online-transport.js` bounds each request, including response-body
decoding, to 12 seconds. Read requests can retry transient failures; mutations
are sent once. A missing kick response may mean the server accepted the move,
so the client blocks input and reads authoritative state before enabling play.
It never automatically repeats the kick.

Live match polling runs sequentially, normally every 1.5 seconds. Failed polls
back off to 3, 6, then 12 seconds. The room bar shows connection state and a
manual reconnect action. Browser reconnect events trigger another sync.
Waiting invitations survive transient failures, and queued searches retain
recovery/cancel controls. Old session responses cannot replace a newer match;
older revisions cannot roll the same match backward.

This uses the existing HTTP transport and cached Canvas renderer. There is no
additional animation loop or socket service.

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
room. Quick and ranked matchmaking remove expired waiters. A ranked room already
joined by either player is not reclaimed by the unjoined-room timeout.
Voice signaling retains at most 100 messages while keeping a monotonic
sequence, so trimming ICE messages does not strand a peer's cursor. Voice remains
STUN-only, and restrictive NATs can still require a TURN service.

## Verification

`python tools/run_tests.py` runs isolated Python regression tests. The focused
online HTTP suite covers validation, membership, invitations, concurrent room
changes, queues, stale commands, cancellation/forfeit, and bounded voice cursors.
`node --test tests/frontend/test_player_controls.mjs tests/frontend/test_online_transport.mjs`
checks turn gates, revision planning, timeouts, cancellation and read-only retries.

Run `python tools/browser/verify_online_reliability.py` for the current Canvas 2D
lobby. It uses independent browser sessions against real local Flask routes and
physics. Faults are injected into a verifier-only template copy, covering failed
polls, hung requests, lost responses after server acceptance, stale responses,
room recovery, password entry, cancellation and desktop/mobile layout. Reports
and screenshots are written to `%TEMP%/agent-soccer-online-reliability/`.

The completed redesign passed 1,050 backend tests (four skipped), 95 JavaScript
tests, and 41 online browser checks with no uncaught JavaScript exceptions.
The existing 2D, match setup, model picker and dialog browser suites also passed
207 checks. The shared frontend/auth verifier passed another 89 checks after
updating its fixtures to the served 2D renderer and mandatory match setup.
Browser verification includes both desktop and mobile layouts.

`verify_online_controls.py` and `verify_online_result.py` retain earlier 3D
renderer assumptions. Their historical results are not verification of the
current 2D lobby. Production Redis latency, Render cold starts and external NAT
traversal are outside the local browser tests.

## Repository cleanup

`static/images/workflow.png` remains the served/downloadable diagram. The editable SVG
page is preserved in `docs/diagrams/workflow-source.html` with local fonts.
Two unused, byte-identical root exports were removed; `workflow.pdf` had PNG
bytes rather than PDF content. This removes 2,948,504 duplicate bytes without
changing the served diagram. The current supported model catalog is maintained
in `app.py` and the README.
