import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY        = os.environ.get("SECRET_KEY", "dev-secret-change-me")
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
UPSTASH_REDIS_URL = os.environ.get("UPSTASH_REDIS_URL", "redis://localhost:6379")
