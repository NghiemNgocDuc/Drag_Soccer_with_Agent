# Project folders

The root contains the application entry point, environment example, package
requirements, deployment configuration, test configuration, and project guidance.
Run the app with `python app.py` as before; Render still loads `app:app`.

| Folder | Contents |
| --- | --- |
| `game/` | Match sessions and snapshots |
| `models/` | Shared physics API and built-in AI agents |
| `user_models/` | User-code validation, sandbox, and model runner |
| `db/` | Persistence and database clients |
| `db/sql/` | Baseline schema and feature migrations |
| `services/` | Integrations, analytics, tutorials, and match orchestration |
| `templates/` | HTML grouped by feature; reusable partials in `shared/` |
| `static/css/` | Common styles and styles for individual page groups |
| `static/js/game/` | Rendering, inputs, playback, culling, and audio |
| `static/js/ui/` | Navigation, chat, forms, feedback, and autosave |
| `static/images/`, `static/textures/` | Images and game texture assets |
| `static/vendor/` | Vendored libraries and local fonts |
| `tests/backend/`, `tests/frontend/` | Python and JavaScript regression suites |
| `tools/browser/`, `tools/benchmarks/` | Browser verification and profiling |
| `docs/` | Design, gameplay, setup, analytics, and vendoring references |

Templates use nine folders: `shared`, `auth`, `game`, `competition`, `workshop`,
`social`, `account`, `public`, and `hubs`. For example, the match view is
`templates/game/index_3d.html`, and login is `templates/auth/login.html`.
HTTP routes keep their existing URLs.

`models/soccer_logic.py` stays at its established import path because AI agents
and user-written models share that physics API.

## Commands

Run these from the repository root:

```powershell
python app.py
python tools/run_tests.py
python tools/run_tests.py tests/backend/test_online_controls.py
node --test tests/frontend/*.mjs
python tools/browser/verify_frontend.py
python tools/browser/verify_online_controls.py
python tools/browser/verify_online_result.py
python tools/benchmarks/profile_physics.py --repeats 20
python tools/benchmarks/profile_balance.py --physics-only
```

The browser scripts disable external integrations before importing Flask.
They require Python Playwright and its Chromium browser installation; reports
and screenshots go to the system temporary directory. The standalone
`tests/backend/test_integration.py` script checks a separately running server.

The database baseline is `db/sql/schema.sql`. Feature SQL files are in
`db/sql/migrations/`; their contents and filenames are preserved. Apply the
baseline first and follow the prerequisites stated in each feature migration.
The balance benchmark can run longer than the test suites; `--physics-only`
checks its deterministic physics path without running model tournaments.
