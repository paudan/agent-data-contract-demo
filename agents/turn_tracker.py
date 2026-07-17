"""Redis-backed negotiation turn counter, keyed by A2A context_id."""

import os

import redis

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_KEY_PREFIX = "agent-contracts:turns:"
DEFAULT_KEY_TTL_SECONDS = 3600


class TurnTracker:
    """Tracks how many negotiation turns a given A2A context_id has used."""

    def __init__(self, max_turns: int, redis_url: str | None = None,
                 key_prefix: str = DEFAULT_KEY_PREFIX, key_ttl_seconds: int = DEFAULT_KEY_TTL_SECONDS):
        self.max_turns = max_turns
        self._key_prefix = key_prefix
        self._key_ttl_seconds = key_ttl_seconds
        self._redis = redis.Redis.from_url(redis_url or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL))

    def _key(self, context_id: str) -> str:
        return f"{self._key_prefix}{context_id}"

    def record_turn(self, context_id: str) -> int:
        """Increments and returns the turn count for context_id. Key
        auto-expires after key_ttl_seconds as a backstop for `reset`."""
        key = self._key(context_id)
        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, self._key_ttl_seconds)
        return count

    def is_exceeded(self, turn_count: int) -> bool:
        return turn_count > self.max_turns

    def reset(self, context_id: str) -> None:
        """Clears the tracked count once a negotiation ends (success or give-up)."""
        self._redis.delete(self._key(context_id))
