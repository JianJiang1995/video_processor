"""Coalesced, process-local wakeups after committed window writes."""
import asyncio
from contextlib import asynccontextmanager
import threading

_subscribers = {}
_lock = threading.Lock()


@asynccontextmanager
async def subscribe(session_id):
    queue = asyncio.Queue(maxsize=1)
    item = (asyncio.get_running_loop(), queue)
    with _lock:
        _subscribers.setdefault(session_id, set()).add(item)
    try:
        yield queue
    finally:
        with _lock:
            subscribers = _subscribers.get(session_id, set())
            subscribers.discard(item)
            if not subscribers:
                _subscribers.pop(session_id, None)


def notify(session_id):
    with _lock:
        subscribers = tuple(_subscribers.get(session_id, ()))

    def offer(queue):
        if queue.empty():
            queue.put_nowait(True)

    for loop, queue in subscribers:
        try:
            loop.call_soon_threadsafe(offer, queue)
        except RuntimeError:
            pass


async def wait_for_update(queue, timeout=2.0):
    try:
        await asyncio.wait_for(queue.get(), timeout)
    except asyncio.TimeoutError:
        pass
