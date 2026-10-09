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
| Player | 0.60 | 1,000–2,000 units/s², from Agility |
| Normal ball | 0.80 | 300 units/s² |
| Field wall/post | 0.80 | — |
| Goal net | 0.20 | — |

Pymunk multiplies the restitution of the two contacting shapes, making the
normal ball/wall response 0.64 and ball/player response 0.48. This loses impact
energy while transferring momentum. Rolling resistance uses a pivot constraint
with `max_force = mass * deceleration`; its bounded impulse brings the ball to
rest without reversing it. Global exponential damping is disabled. See the
[Pymunk reference](https://www.pymunk.org/en/latest/pymunk.html#pymunk.Space.step)
for fixed steps, impulses, material restitution and post-solve collision data.

Saved ball presets control mass, resistance, restitution and physical radius.
Even high-bounce presets cap ball restitution at 0.95 to avoid contact energy
creation. Small/normal/large radii are 9.84/12/14.16. Gameplay, replay and live
spectator rendering use the authoritative size, including squash and spin.
The first trajectory frame stores size for future replays; older replays default
to normal. Cube/puck presets still use the existing circular contact geometry.

## Goals and penalties

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

The local miss/contact/wall benchmark across 3/7/11-player teams averaged
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
