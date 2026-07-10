"""
Bounce Board — in-process event broker for live session updates.

The pipeline runs as an asyncio task in the same process, so a simple
queue-per-subscriber pub/sub is enough to push stage changes and board
messages to connected WebSockets. Events are best-effort: if a subscriber's
queue is full it drops the oldest event (the WS client also has polling as
a fallback, and every event carries full state, not a delta).
"""
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 256

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe(session_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
    _subscribers[session_id].add(queue)
    return queue


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    subs = _subscribers.get(session_id)
    if not subs:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(session_id, None)


def publish(session_id: str, event: dict) -> None:
    """Fan an event out to all subscribers of a session (non-blocking)."""
    for queue in _subscribers.get(session_id, ()):  # copy not needed: no await
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop oldest — events carry full state
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.warning("Dropping bounce board event for %s", session_id)
