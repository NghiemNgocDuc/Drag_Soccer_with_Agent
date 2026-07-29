import logging
from config import POSTHOG_API_KEY, POSTHOG_HOST

_client = None


def get_client():
    global _client
    if _client is None and POSTHOG_API_KEY:
        try:
            import posthog
            posthog.api_key = POSTHOG_API_KEY
            posthog.host = POSTHOG_HOST
            _client = posthog
        except Exception as e:
            logging.warning("Failed to init PostHog: %s", e)
    return _client


def capture(distinct_id: str, event: str, properties: dict | None = None):
    client = get_client()
    if not client:
        return
    try:
        client.capture(distinct_id, event, properties or {})
    except Exception as e:
        logging.debug("PostHog capture error: %s", e)


def identify(distinct_id: str, traits: dict | None = None):
    client = get_client()
    if not client:
        return
    try:
        client.identify(distinct_id, traits or {})
    except Exception as e:
        logging.debug("PostHog identify error: %s", e)


def track_game_start(user_id: str, mode: str, ai_model: str = ""):
    capture(user_id, "game_started", {"mode": mode, "ai_model": ai_model})


def track_game_end(user_id: str, mode: str, winner: str, score_a: int, score_b: int):
    capture(user_id, "game_ended", {
        "mode": mode, "winner": winner,
        "score_a": score_a, "score_b": score_b,
    })


def track_goal(user_id: str, scorer: str):
    capture(user_id, "goal_scored", {"scorer": scorer})


def track_signup(user_id: str, email: str):
    identify(user_id, {"email": email})
    capture(user_id, "signed_up", {"email": email})


def track_pageview(user_id: str, page: str):
    capture(user_id, "page_viewed", {"page": page})
