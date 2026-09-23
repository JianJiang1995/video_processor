import asyncio
import unittest

from backend.services import summary_notifications as events


class SummaryNotificationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_threaded_commit_wakes_all_clients_only_for_its_session(self):
        async with events.subscribe('one') as first, events.subscribe('one') as second, events.subscribe('two') as other:
            await asyncio.to_thread(events.notify, 'one')
            await asyncio.wait_for(events.wait_for_update(first), .2)
            await asyncio.wait_for(events.wait_for_update(second), .2)
            self.assertTrue(other.empty())
        self.assertEqual(events._subscribers, {})

    async def test_wakeups_coalesce_without_losing_the_next_write(self):
        async with events.subscribe('one') as queue:
            for _ in range(100):
                events.notify('one')
            await asyncio.sleep(0)
            self.assertEqual(queue.qsize(), 1)
            await events.wait_for_update(queue)
            events.notify('one')
            await asyncio.wait_for(events.wait_for_update(queue), .2)

    async def test_no_subscribers_and_timeout_are_safe(self):
        events.notify('missing')
        async with events.subscribe('empty') as queue:
            await events.wait_for_update(queue, timeout=.001)
