"""Runtime patches for 4 bugs in a2a_redis 0.2.1's Redis Streams queue
manager that surface under a2a-sdk's DefaultRequestHandler (hangs / silently
dropped events). Applied in-process at import time from agents/supplier.py;
nothing on disk is touched. See README.md for the full writeup of each bug.
"""

import asyncio

from a2a_redis.model_utils import deserialize_event, deserialize_from_json
from a2a_redis.streams_queue import RedisStreamsEventQueue
from a2a_redis.streams_queue_manager import RedisStreamsQueueManager

# Must stay below EventConsumer._timeout (0.5s, hardcoded in a2a-sdk).
_BLOCK_MS = 200

_patched = False


async def _dequeue_event_drain_before_closed(self, no_wait: bool = False):
    if not self._consumer_group_ensured:
        await self._ensure_consumer_group()
        self._consumer_group_ensured = True

    timeout = 0 if no_wait else _BLOCK_MS  # 0 = non-blocking

    try:
        result = await self.redis.xreadgroup(
            self.consumer_group,
            self.consumer_id,
            {self._stream_key: ">"},
            count=1,
            block=timeout,
        )

        if not result or not result[0][1]:  # No messages available right now
            if self._closed:
                raise asyncio.QueueEmpty("Queue is closed")
            raise RuntimeError("No events available")

        _, messages = result[0]
        message_id, fields = messages[0]

        event_structure = {
            "event_type": fields[b"event_type"].decode() if b"event_type" in fields else None,
            "event_data": deserialize_from_json(fields[b"event_data"]),
        }

        await self.redis.xack(self._stream_key, self.consumer_group, message_id)

        return deserialize_event(event_structure)

    except asyncio.QueueEmpty:
        raise
    except Exception as e:
        if "NOGROUP" in str(e):
            await self._ensure_consumer_group()
            raise RuntimeError("Consumer group recreated, try again")
        if self._closed:
            raise asyncio.QueueEmpty("Queue is closed")
        raise RuntimeError(f"Error reading from stream: {e}")


async def _close_accepting_immediate_arg(self, immediate: bool = False) -> None:
    self._closed = True
    try:
        pending = await self.redis.xpending_range(
            self._stream_key,
            self.consumer_group,
            min="-",
            max="+",
            count=100,
            consumername=self.consumer_id,
        )
        if pending:
            message_ids = [msg["message_id"] for msg in pending]
            await self.redis.xack(self._stream_key, self.consumer_group, *message_ids)
    except Exception:
        # Consumer group might not exist yet, ignore -- matches the original
        # RedisStreamsEventQueue.close() behavior.
        pass


async def _create_or_tap_recreating_if_closed(self, task_id: str):
    existing = self._queues.get(task_id)
    if existing is None or existing.is_closed():
        self._queues[task_id] = self._create_queue(task_id)
    return self._queues[task_id]


def apply() -> None:
    """Applies the `RedisStreamsEventQueue`/`RedisStreamsQueueManager` patches, once."""
    global _patched
    if _patched:
        return
    RedisStreamsEventQueue.dequeue_event = _dequeue_event_drain_before_closed
    RedisStreamsEventQueue.close = _close_accepting_immediate_arg
    RedisStreamsQueueManager.create_or_tap = _create_or_tap_recreating_if_closed
    _patched = True
