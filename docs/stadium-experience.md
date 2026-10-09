# Stadium audience and audio

Both the game and replay viewer use `static/js/game/stadium-crowd.js`. The stadium has
eight spectator variants, seated and cheering poses, garment-only team colors,
aisles, and occasional vacant seats. At the current 1400 × 875 field size there
are 2,117 spectators. Their reactions start at different times and settle after
a finite 4.8-second celebration. Camera-facing cutouts keep individual people
inexpensive; they are not separately lit 3D character models.

The [spectator atlas](../static/textures/stadium-spectators-v2.png) is a 1254 ×
1254 transparent image, approximately 1.2 MB. The built-in ImageGen tool created
and refined it; [the exact prompts](stadium-art-prompts.md) are recorded alongside
the asset metadata. A procedural fallback remains available if loading fails.
The renderer uses measured figure bounds because the generated atlas did not
follow a uniform cell layout.

Fans, seat backs, tiers, and walls use four instanced batches and three shared
geometries. Seat backs use two triangles each instead of twelve. Each batch filters
instances against the camera before submission. Returning the camera restores
the same figures and attributes. An idle audience schedules neither animation
frames nor ongoing JavaScript timers. Detailed artwork adds one downloaded
texture; drawing quality still follows the existing Auto/1080p/1440p/4K control.

`static/js/game/sound.js` creates stereo stadium murmur, voiced supporter groups, claps,
a goal roar, a breathy whistle, and short kick/bounce impacts. These are
procedural sounds, without audio downloads. Cached standard buffers occupy
about 2.41 MiB at a 44.1 kHz effect sample rate. Crowd buffers use 16 kHz stereo:
12 seconds for looping ambience and 4.8 seconds for a cheer.

Sound preparation starts during page setup in finite cooperative tasks. It
does not resume the context or start audible sources. A real pointer, touch,
or keyboard gesture unlocks playback. Each page shares one AudioContext between
SoundManager and Three.js spatial audio. One master controls mute for ambience,
active reactions, whistles, and spatial impacts. Cheer playback reduces ambient
gain to 35%, then restores it. Duplicate goal calls coalesce; at most two cheers
and six SoundManager effects remain active. Finished effects disconnect their
nodes, and context replacement cancels unfinished preparation.

## Referee

Live play and replay share `static/js/game/referee-avatar.js`, built from the
player geometry cache. The official has a human silhouette, facial details,
shirt pockets, a badge, black shorts and socks, a watch, radio earpiece, and
whistle lanyard. The saved `ref_color` controls his shirt. His stride follows
distance traveled, and his hand rises briefly for the whistle. These poses
preserve server positions and stop when the referee rests; penalties hide him.

The whistle uses an original three-tone synthesis with breath pressure,
pitch variation, and quiet stereo reflections. The design takes inspiration
from [three-chamber pealess referee whistles](https://www.fox40world.com/classic),
without using a recording or claiming an exact acoustic reproduction.
Duplicate triggers share one active whistle. Its 0.9-second buffer is cached,
and the one-second hand gesture returns demand rendering to idle.

## Verification

```powershell
node --test tests/frontend/test_stadium_crowd.mjs tests/frontend/test_frustum_culling.mjs tests/frontend/test_render_on_demand.mjs tests/frontend/test_trajectory_playback.mjs
node --test tests/frontend/test_player_avatar.mjs
python tools/browser/verify_stadium_audio.py
python tools/browser/verify_stadium_experience.py --output "$env:TEMP/agent-soccer-stadium-qa/report.json" --screenshots "$env:TEMP/agent-soccer-stadium-qa"
```

The full frontend JavaScript suite contains 62 checks. The audio verifier passes 36 checks
covering finite samples, stereo separation, loop seams, bounded overlap,
rendered mute, cache reuse, and cancellation. It also writes an 11-second
`stadium-mix.wav` preview and a report under
`$env:TEMP/agent-soccer-stadium-audio`. Its preview has peak amplitude 0.707;
the overlap stress render peaks at 0.685. No clipping occurred in either render.
These checks assess signal and lifecycle behavior, not human listening quality.

Integrated browser verification passes 48 checks across both pages, including
the referee's kit, exact movement coordinates, resting pose, penalty visibility,
and finite whistle gesture. It uses
real pointer and keyboard events, verifies a single shared context, inspects
garment recoloring on the GPU, and checks off-camera culling, camera return,
finite celebrations, and zero idle frames. All 960 natural-color probe pixels
remained unchanged while 769 shirt pixels changed. Prepared cheer scheduling
measured 0.1 ms on both pages. These timings measure source
scheduling, not speaker output latency; initial preparation still needs time
to finish.

The final seat-plane optimization removes 23,160 triangles per scene compared
with the first version's box seats. The more detailed stadium still has more
triangles than the earlier silhouette crowd, while requiring fewer draw calls:

| Broadcast view | Earlier crowd | Final audience |
| --- | ---: | ---: |
| Live triangles | 38,606 | 50,728 |
| Replay triangles | 39,888 | 49,850 |
| Live draw calls | 538 | 494 |
| Replay draw calls | 531 | 477 |

Facing away submits zero fans and leaves one scene draw on both pages. These
are scene resource measurements; no claim of hardware FPS improvement is made.

Browser screenshots use Chromium software rendering at 1080p. The harness
checks that 4K remains selectable; the earlier demand-rendering harness performs
the actual 4K allocation check. Temporary loopback servers disable external
integrations and do not read live service credentials. Python Playwright and
its Chromium installation are required.
