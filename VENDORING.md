# External Resources → Vendored / Self-Hosted

This project previously depended on CDNs and SaaS. All are now vendored or gracefully degraded so the game runs fully offline in `DEV_MODE=1`.

| External Resource | Original URL / Package | GitHub Repo | Local Implementation |
|---|---|---|---|
| **Three.js** | `https://unpkg.com/three@0.160.0` | `mrdoob/three.js` | `static/vendor/three/three.module.js` + `static/vendor/three/addons/controls/OrbitControls.js` via importmap `/static/vendor/three/addons/` |
| **CodeMirror 5** | `cdnjs.cloudflare.com/.../codemirror` | `codemirror/codemirror5` | `static/vendor/codemirror/codemirror.min.*` + `python.min.js` |
| **Chart.js 4.4.1** | `cdn.jsdelivr.net/npm/chart.js` | `chartjs/Chart.js` | `static/vendor/chartjs/chart.umd.min.js` |
| **Google Fonts** (Space Grotesk, Be Vietnam Pro) | `fonts.googleapis.com` | `google/fonts` | `static/vendor/fonts/be-vietnam.css` + `space-grotesk.css` + `*.ttf` (self-hosted, no external fetch) |
| **Flask** | `pip flask` | `pallets/flask` | pip, no CDN |
| **PyMunk** | `pip pymunk` | `viblo/pymunk` (Chipmunk2D) | pip |
| **Supabase** (auth/db) | `supabase.co` | `supabase/supabase` | `DEV_MODE` in-memory `_MEM` + `service` fallback; self-host via `supabase/supabase` docker if needed |
| **Upstash Redis** | `upstash.com` | `redis/redis` / `redis/redis-py` | `db/redis_client.py` `_InMemoryFallback` (dict) + local `redis://localhost:6379` |
| **Pinecone** | `pinecone.io` | `pinecone/pinecone-python-client` | `services/pinecone.py` in-memory fallback; local alternative `qdrant/qdrant` or `chromadb` |
| **Resend** | `resend.com` | `resend/resend-python` | `services/resend.py` no-op if no `RESEND_API_KEY` (logs) |
| **PostHog** | `posthog.com` | `PostHog/posthog-python` | `services/posthog.py` no-op if no key |
| **Sentry** | `sentry.io` | `getsentry/sentry-python` | `services/sentry.py` no-op if no `SENTRY_DSN` |
| **Clerk** | `clerk.com` | `clerk/clerk-sdk-python` | `services/clerk.py` no-op, uses `dev:*` / `guest:*` sessions |
| **ProductBridge** | `productbridge` | — | `services/productbridge.py` no-op |
| **Semantic Scholar** | `semanticscholar.org` | `allenai/scholar` API | `services/paper_search.py` fallback to no API (requests) |
| **Better Profanity** | `better-profanity` | `snguyenthanh/better_profanity` | pip, local filter |
| **LangChain** | `langchain` | `langchain-ai/langchain` | `models/langchain_model.py` graceful fallback if not installed / no `OPENAI_API_KEY` (pruned sweep <0.1s) |
| **Mem0** | `mem0ai/mem0` | `mem0ai/mem0` | `services/memory.py` re-implements `add/search/get_all` with Redis+Supabase, no external LLM needed |

**How to run fully offline:**
```bash
DEV_MODE=1 python app.py  # no Supabase/Redis/Pinecone/Clerk/Resend/PostHog/Sentry needed
# frontend libs already in static/vendor, no CDN hits
```

All `templates/*.html` now reference `/static/vendor/*` instead of `unpkg`/`cdnjs`/`cdn.jsdelivr.net`/`fonts.googleapis.com`.
