import os
from dotenv import load_dotenv

load_dotenv()

DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"

_SECRET_KEY = os.environ.get("SECRET_KEY")
if _SECRET_KEY:
    SECRET_KEY = _SECRET_KEY
elif DEV_MODE:
    SECRET_KEY = "dev-secret-change-me"
else:
    raise RuntimeError("SECRET_KEY must be set in production (or use DEV_MODE=1)")

SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
UPSTASH_REDIS_URL = os.environ.get("UPSTASH_REDIS_URL", "redis://localhost:6379")

# Absolute base URL for auth emails (password reset / email change links)
SITE_URL = os.environ.get("SITE_URL", "http://localhost:5000")

# Resend — transactional email
RESEND_API_KEY    = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "noreply@socceragent.dev")

# ProductBridge — feedback collection
PRODUCTBRIDGE_API_KEY = os.environ.get("PRODUCTBRIDGE_API_KEY", "")
PRODUCTBRIDGE_BOARD_ID = os.environ.get("PRODUCTBRIDGE_BOARD_ID", "")

# Clerk — auth verification
CLERK_SECRET_KEY      = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")

# PostHog — product analytics
POSTHOG_API_KEY  = os.environ.get("POSTHOG_API_KEY", "")
POSTHOG_HOST     = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")

# Sentry — error logging
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")

# Pinecone — vector database
PINECONE_API_KEY  = os.environ.get("PINECONE_API_KEY", "")
PINECONE_ENV      = os.environ.get("PINECONE_ENV", "")
PINECONE_INDEX    = os.environ.get("PINECONE_INDEX", "soccer-agent")

# Semantic Scholar — academic paper search
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# About feedback — recipient is server-side only, never exposed to client
# Set FEEDBACK_TO_EMAIL on Render dashboard to your inbox (e.g. via env var). No default in repo to avoid leaking.
FEEDBACK_TO_EMAIL = os.environ.get("FEEDBACK_TO_EMAIL", "")
