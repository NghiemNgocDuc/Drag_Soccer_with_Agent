from config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY

anon = None
service = None

if SUPABASE_URL and SUPABASE_ANON_KEY:
    from supabase import create_client
    anon    = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    svc_key = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
    service = create_client(SUPABASE_URL, svc_key)
