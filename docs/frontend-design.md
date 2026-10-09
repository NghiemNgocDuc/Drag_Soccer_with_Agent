# Frontend design

All 38 route templates use the same club interface. The redesign keeps Flask
routes, form actions, API payloads, editor integrations, and the game renderer.

## Shared components

- `templates/shared/_ui.html`: navigation, account menu, SVG icons, page introductions.
- `templates/shared/_ui_assets.html`: shared stylesheet and deferred interaction script.
- `static/css/frontend.css`: cream background, navy navigation, white panels, teal
  actions, lime accents, local fonts, responsive controls, and focus states.
- `static/js/ui/frontend.js`: mobile navigation, keyboard dismissal, skip link,
  password visibility controls, and status announcements. No polling or frame
  loop is introduced.
- `static/css/frontend-public.css`: landing, registration, recovery,
  locker room, and play overview layouts.
- `static/css/frontend-login.css`: the dedicated sign-in layout, compact account
  header, forest stadium illustration, and responsive form. Its styles do not
  change registration or password recovery.
- `static/css/frontend-game.css`: match HUD, controls, online settings, replay,
  highlights, loss analysis, and spectator chat.
- `static/css/frontend-workshop.css`: model library/editor, practice pitch, Arena,
  community models, and team/stadium settings.
- `static/css/frontend-compete.css`: tournaments, bracket, leaderboards, analytics,
  saved match history, and match summary.
- `static/css/frontend-social.css`: profile, achievements, friends/messages, clans,
  live-match index, feedback, lessons, research, about, and workflow.

The five hubs are direct destination overviews. They do not embed other app
pages or start a nested game renderer. Public/auth pitch artwork is inline SVG;
no new image files or remote font requests are needed. Decorative animated
backgrounds and backdrop blur are disabled. Game movement, weather, and goal
effects continue to use the existing demand renderer.

## Page behavior preserved

| Area | Actions |
| --- | --- |
| Account entry | Register, login, demo details, recovery email, token-based password reset |
| Match | Mode/opponent selection, new game, undo, pass type, speed, online matchmaking, invitations, ranked play, camera and graphics resolution, sound, voice, assistant, penalties |
| Replay | Per-move/full playback, highlight watch/share, live polling/chat, model loss review |
| Workshop | Model create/edit/validate/save/delete, resource links, opt-in benchmark, editor, practice setup/moves/reset, Arena comparisons, community search/likes/comments/shares, customization saving |
| Competition | Tournament create/add/generate/simulate/watch, player/model/ranked leaderboard tabs, pagination/sort/details, analytics filters/charts, history playback/sharing, match summaries |
| Club | Profile settings/photo/email, friend requests/invites/messages, clan membership/leadership/tournaments/chat, achievements, live-match discovery, feedback |
| Learning | Lessons and progress, tutorial checks, research search/saved papers/citations/notes, workflow image download/enlargement |

## Verification

Run checks with external integrations disabled. The browser harnesses do this
before importing the app, then use a temporary loopback Flask server and local
fixtures. They do not create production users, send email, or submit real
benchmarks. Screenshots and reports are written to the system temporary folder.

```powershell
python tools/browser/verify_frontend.py
python tools/browser/verify_login.py
python tools/browser/verify_frontend_dialogs.py
python tools/browser/verify_frontend_workshop.py
python tools/browser/verify_demand_rendering.py --idle-ms 500
```

The shared frontend harness exercises guest/account navigation, auth forms,
password reset validation and token payloads, feedback submission/focus,
375/768/1280 px layouts, match controls, and the 4K option. The dedicated
rendering harness checks actual drawing buffers, idle/hidden/offscreen behavior,
trajectory completion, queued moves, score changes, and resource handling.

Keep page styles scoped to their body class. Treat selected, disabled, error,
loading, empty, populated, and mobile states as part of the layout. Use native
links/buttons and labeled fields when adding actions. Do not add a decorative
animation loop to the shared shell.

The login refresh uses a compact account header, a static stadium tactics
illustration, and a form with larger touch targets. Its demo disclosure retains
copy controls and adds a button that fills the two fields without signing in.
Native form submission, password visibility, flashed errors, registration, and
recovery links remain available. The focused login verifier checks narrow phones,
tablets, desktop, keyboard access, clipboard failure, and development sign-in.
Its final isolated run passes 216 browser checks, including 200% zoom equivalent,
visible text contrast, and usable desktop footer targets in 900px-tall windows.

The follow-up [player and control redesign](player-controls.md) uses a shared
avatar module and an event-driven control panel for mouse, touch and keyboard.
The current isolated suites pass 504 Python tests (four skips) and 48 JavaScript
tests. [Stadium audience and audio details](stadium-experience.md) document the
crowd artwork, sound lifecycle, and dedicated browser checks.

The redesign was checked across all 38 pages at phone and desktop sizes, with
tablet checks for the shared/public/game layouts. More than 400 browser checks
passed, including 38 demand-rendering checks and an actual 4K buffer. Existing
isolated Python regression checks passed (501 tests, four skips), along with 28
JavaScript tests. Expensive Arena battles and research/tutorial service
responses use browser fixtures in UI checks; those checks validate controls and
payloads rather than model quality or production integrations.

Regression cleanup also covers nested game dialogs, focus after invitation
creation, and dismissed feedback requests that finish after the form is reopened.
Loss analysis uses the current built-in catalog and includes outcome descriptions.
Lessons use supported opponents while retaining saved lesson IDs and progress.
Arena, tutorial, and legacy benchmark simulations settle by score at their kick
limit; an existing engine result, including a forfeit, takes precedence. Leaderboard
progress totals come from the same catalog as the benchmark.
The final shared frontend rerun passed 89 browser checks; focused dialog,
lesson-choice, and loss-analysis runs passed another 49 checks.
