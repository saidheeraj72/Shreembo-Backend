"""
Bounce Board — in-process event broker for live session updates.

The pipeline and ingest jobs run as asyncio tasks in the same process, so a
simple queue-per-subscriber pub/sub is enough to push stage changes, board
messages and ingest progress to connected WebSockets. Channels are keyed by
session id or ingest job id. If a subscriber's queue is full the oldest event
is dropped — safe because every event carries full state, not a delta.
"""
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 256

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe(channel: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
    _subscribers[channel].add(queue)
    return queue


def unsubscribe(channel: str, queue: asyncio.Queue) -> None:
    subs = _subscribers.get(channel)
    if not subs:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(channel, None)


def publish(channel: str, event: dict) -> None:
    """Fan an event out to all subscribers of a channel (non-blocking)."""
    for queue in _subscribers.get(channel, ()):  # copy not needed: no await
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop oldest — events carry full state
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.warning("Dropping bounce board event for %s", channel)
