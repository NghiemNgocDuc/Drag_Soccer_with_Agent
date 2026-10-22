# Soccer physics

The game keeps its turn-based play with [drag or explicit aim/kick controls](player-controls.md). Releasing a player
launches that pawn; its collision with the ball transfers momentum. Ordinary
kicks remain on the ground. The pitch, ball and players use stylized world
units, so the numbers below tune game feel rather than reproduce SI measurements.

## Simulation

`models/soccer_logic.py` builds an isolated Pymunk world for each move. Contacts
run at a fixed 180 Hz and trajectories record at 60 Hz, with actual timestamps.
Recorded impacts survive trajectory thinning. Playback does not resimulate
physics and remains independent of rendering frame rate.

AI matches reposition both teams during that same simulation. Each non-selected
AI pawn receives at most one support impulse from `models/team_movement.py`;
normal friction and contact response then govern its run. Planned free travel
is at most 120 units for outfield players and 45 for keepers, subject to their
stats and the power cap. Safe lanes and distinct destinations reduce crowding.
Formation jobs remain fixed: keepers guard goal, defenders recover to their own
half, midfield support offers passing lanes, and attackers stay upfield.
These are movement targets; physical contact can still push a player elsewhere.
Your striker is excluded from automatic movement, but remains a physical body
that can receive contact. Both squads' positions are recorded on the same
trajectory. Human-only matches, shootouts and explicit `team_movement=False`
fixtures retain their previous movement rules.

Bodies idle below 0.5 units/s sleep after 0.1 seconds and wake on contact or an
impulse. Sleeping player snapshots reuse cached coordinates with independent
frame dictionaries. Off-camera rendering does not alter physics outcomes.

Launch impulse is `mass * desired_velocity`, applied at the player's center.
Power determines desired speed. Weight affects momentum transferred during
collisions; Size determines contact radius; Agility determines braking. Contact
response supplies recoil, replacing the previous arbitrary reduction of launch
speed before any contact occurred.

| Material | Shape restitution | Ground deceleration |
| --- | ---: | ---: |
| Player | 0.90 | 700–1,100 units/s², from Agility |
| Normal ball | 0.96 | 250 units/s² |
| Field wall/post | 0.35 | — |
| Goal net | 0.20 | — |

Pymunk multiplies the restitution of the two contacting shapes, making the
normal ball/wall response 0.336 and ball/player response 0.864. The soft boundary
absorbs about two-thirds of the incoming normal speed. Player contacts
transfer momentum without an artificial speed boost, capped at 850 units/s even
when the contact impulse exceeds that cap. Rolling resistance uses a pivot constraint
with `max_force = mass * deceleration`; its bounded impulse brings the ball to
rest without reversing it. Global exponential damping is disabled. See the
[Pymunk reference](https://www.pymunk.org/en/latest/pymunk.html#pymunk.Space.step)
for fixed steps, impulses, material restitution and post-solve collision data.

Saved ball presets control mass, resistance, restitution and physical radius.
The normal ball has mass 1.5 (previously 0.75), with rolling deceleration raised
from 175 to 250 units/s². This reduces travel from the same pull while retaining
the existing player-disc glide and wall restitution. AI estimates read these
same material values.
The four corners have a small arcade bumper: within 26 units of both ball-center
boundary limits, a grounded ball gets at least 190 units/s inward on each axis.
It rebounds during the physics substep without teleporting, including when pawns
crowd an old stuck ball. Ordinary walls keep their soft restitution. AI prediction
and replay recording use this same rule (`test_corner_bumper.py`).
Even high-bounce presets cap ball restitution at 0.96. Small/normal/large radii
are 9.84/12/14.16. Gameplay, replay and live
spectator rendering use the authoritative size. The ball stays circular on impact.
The first trajectory frame stores size for future replays; older replays default
to normal. Cube/puck presets still use the existing circular contact geometry.

## Goals and penalties

New matches use five players per team. In Human vs AI the human controls the
last formation slot, striker #5; the teammate policy excludes that slot.
New local matches wait in setup until **Start match** applies the selected
formation and playstyle together. The 2-1-1, 1-2-1, 2-0-2 and 1-1-2 formations
all include one keeper plus four outfield players. They change actual starting
positions and stable role assignments; goals and halftime retain the formation.
Setup holds the clock at 00:00 and rejects kick/AI requests. Starting sets the
time origin once; duplicate starts cannot restart the clock. New Game returns
to setup with the last chosen tactics. Existing active saves resume normally.
Coverage: `test_match_formations.py`, `test_match_setup.py`, and
`python tools/browser/verify_match_setup.py`.
Power maps to 5–9 units/s per command point, further bounded so a free
maximum-strength approach travels at most 480 units (plus fixed-step tolerance).
The default build glides about 272 units on a full pull. Longer pulls increase
launch speed linearly and free stopping distance quadratically.
Players have solid pitch boundaries, including the goal mouths. A substep guard
repairs any residual penetration while reflecting outward velocity. Only actual
contact moves the ball: missed kicks cannot remotely loft or displace it.

New playable matches default to 90 minutes (`half_length=45`), at three real
seconds per game minute: 4½ minutes of regulation. The display is `MM:SS` with
no fractional suffix. Tied games continue through 105:00 and 120:00, then penalties.
Timed matches ignore the goal target; explicit untimed practice (`half_length=0`)
still uses it. Period boundaries advance on state refresh even without a kick,
with online resets serialized through the room lock. The clock freezes at full
time or during the shootout. Hidden tabs stop clock painting, not match time.

In local Human vs AI penalties, drag your goalkeeper toward the labeled LEFT or
RIGHT guide to dive, or forward toward the ball to stay central. Release commits
one choice; Escape cancels. Buttons provide the same choices. Human penalties
wait for input, with no automatic five-second save or ten-second shot. Shooting
still uses the existing pull-back gesture on the ball.

Live and replay 2D playback use the same roster and ball timestamps, capped at
four seconds per move at normal speed. AI moves use the selected playback speed.
Wall and player contact samples and their predecessors survive trajectory
thinning. Penalties also carry both full rosters, with active kicker/keeper tracks.

A goal requires the entire ball beyond the goal line, inside both posts and
below the 60-unit crossbar. This follows the whole-ball requirement in
[IFAB Law 10](https://www.theifab.com/laws/latest/determining-the-outcome-of-a-match/).
The rule is checked after each 180 Hz substep so a fast ball cannot cross and
rebound from the net before detection. Field segments terminate at the posts,
replacing the previously enlarged collision aperture.

Penalty kicks use the same field, ball material, impulse and goal detection.
Only the selected kicker, ball and opposing keeper are active. The selected
player's stats are preserved through shootout resets. Keeper Deflector styles
reduce restitution on contact instead of teleporting the last trajectory point.
Crossing the opposite goal never scores a penalty for the shooter.

`simulate_kick` and actual play produce identical trajectories, including
misses where the player moves without touching the ball. During a shootout,
prediction uses the same keeper dive and penalty simulation as execution.
Commands reject nonfinite numbers before mutation and clamp power to the saved
match cap, at most 100. Ball bounce flags come from real first-contact impulses,
so proximity to a wall does not create phantom impacts.

## Verification

`tests/backend/test_physics_redesign.py` checks analytic stopping distances, loss of collision
energy, momentum, goal geometry, preset materials, command validation, stats,
and prediction/execution parity. `tests/backend/test_physics_api.py` checks malformed commands,
saved caps and reset settings through HTTP. Existing physics/playback and penalty
tests also run; the legacy penalty checker now raises on failures.

Before the five-a-side tuning, the local miss/contact/wall benchmark across 3/7/11-player teams averaged
4.33 ms per executed kick before this redesign and 3.10 ms afterward (20 and
50 repetitions per case respectively). Predictions averaged 4.01 ms versus
3.08 ms. These are local timings, with different physical trajectories after
the redesign, and do not include network or rendering costs.

```powershell
python -m pytest -q tests/backend/test_physics_redesign.py tests/backend/test_physics_api.py tests/backend/test_stats.py tests/backend/test_penalty.py tests/backend/test_vertical.py tests/backend/test_physics_playback.py
python tools/benchmarks/profile_physics.py --repeats 50
```

Run app tests using the isolated environment described in `tools/README.md` to
avoid live service credentials. Stored replay trajectories stay unchanged;
future simulations and AI evaluations use the new coefficients.

`tests/backend/test_five_a_side.py` additionally checks grounded misses, kickoff
contact at the default strength, contact speed limits, wall reflection, complete
penalty tracks, prediction parity, identity persistence and 64 repeated extreme
kicks with all ten players inside the pitch throughout every recorded frame.
