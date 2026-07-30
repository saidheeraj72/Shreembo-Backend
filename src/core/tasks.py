"""Fire-and-forget background tasks that actually survive.

``asyncio.create_task`` only holds a weak reference to the task it returns, so
a task nobody keeps a name for can be garbage collected while it is still
running — the coroutine simply stops, with no error anywhere. Document
embedding is launched exactly that way, and a document whose embedding vanishes
mid-flight is left non-terminal forever: the UI polls it indefinitely and RAG
searches come back empty for a file the repository says is there.

``spawn`` keeps the reference until the task finishes.
"""
import asyncio
import logging
from typing import Coroutine, Set

logger = logging.getLogger(__name__)

_background: Set[asyncio.Task] = set()


def spawn(coro: Coroutine, *, name: str = None) -> asyncio.Task:
    """Run *coro* in the background, holding a reference until it completes.

    Exceptions are logged rather than raised — nobody awaits these tasks, and
    an unretrieved exception would otherwise surface only at interpreter exit.
    """
    task = asyncio.create_task(coro, name=name)
    _background.add(task)

    def _done(t: asyncio.Task) -> None:
        _background.discard(t)
        if t.cancelled():
            logger.warning("Background task %s was cancelled", t.get_name())
            return
        exc = t.exception()
        if exc:
            logger.error(
                "Background task %s failed: %s", t.get_name(), exc, exc_info=exc
            )

    task.add_done_callback(_done)
    return task
