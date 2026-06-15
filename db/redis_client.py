import redis
from config import UPSTASH_REDIS_URL


class _InMemoryFallback:
    """Dict-backed stand-in when Redis is unreachable (local dev)."""
    def __init__(self):
        self._store: dict[str, str] = {}

    def get(self, key: str):
        return self._store.get(key)

    def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    def delete(self, key: str):
        self._store.pop(key, None)


try:
    _pool = redis.from_url(
        UPSTASH_REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
    )
    _pool.ping()
    r = _pool
except Exception:
    r = _InMemoryFallback()
