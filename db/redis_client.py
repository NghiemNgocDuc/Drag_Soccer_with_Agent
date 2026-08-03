import redis
from config import UPSTASH_REDIS_URL


class _InMemoryFallback:
    """Dict-backed stand-in when Redis is unreachable (local dev)."""
    def __init__(self):
        self._store: dict[str, object] = {}

    def get(self, key: str):
        return self._store.get(key)

    def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    def delete(self, key: str):
        self._store.pop(key, None)

    # Set ops (live-match index for spectator mode)
    def sadd(self, key: str, *members):
        st = self._store.get(key)
        if not isinstance(st, set):
            st = set()
            self._store[key] = st
        before = len(st)
        st.update(members)
        return len(st) - before  # mirrors redis: count of newly added

    def srem(self, key: str, *members):
        st = self._store.get(key)
        if isinstance(st, set):
            st.difference_update(members)

    def smembers(self, key: str):
        st = self._store.get(key)
        return set(st) if isinstance(st, set) else set()

    def sismember(self, key: str, member) -> bool:
        st = self._store.get(key)
        return member in st if isinstance(st, set) else False

    # List ops (invites) + expire — kept as no-op-ish so dev mode mirrors prod calls
    def lpush(self, key: str, *values):
        vals = list(values)
        lst = self._store.get(key)
        if not isinstance(lst, list):
            lst = []
            self._store[key] = lst
        for v in vals:
            lst.insert(0, v)

    def lrange(self, key: str, start: int, end: int):
        lst = self._store.get(key)
        if not isinstance(lst, list):
            return []
        stop = None if end == -1 else end + 1
        return lst[start:stop]

    def lrem(self, key: str, count: int, value: str):
        lst = self._store.get(key)
        if not isinstance(lst, list):
            return 0
        removed = 0
        result = []
        for v in lst:
            if v == value and (count == 0 or removed < count):
                removed += 1
            else:
                result.append(v)
        if removed:
            self._store[key] = result
        return removed

    def expire(self, key: str, ttl: int):
        pass


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
