import asyncio
import unittest
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import video


class SourceRatePreviewTests(unittest.IsolatedAsyncioTestCase):
    def state(self, fps=0, width=0):
        return video.DisplayStreamState("test", "decklink://0", fps, 85, width)

    def test_source_rate_preserves_integer_and_fractional_rates(self):
        state = self.state()
        for rate in (24, 25, 30, 50, 59.94, 60, 120):
            self.assertEqual(state.publish_fps(rate), rate)
        self.assertEqual(self.state(fps=30).publish_fps(50), 25)
        self.assertEqual(self.state(fps=60).publish_fps(50), 50)

    def test_source_dimensions_are_preserved_without_resize(self):
        frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
        self.assertIs(self.state()._prepare_frame(frame), frame)
        resized = self.state(width=1280)._prepare_frame(frame)
        self.assertEqual(resized.shape, (720, 1280, 3))

    async def test_new_frame_wakes_waiter_and_does_not_queue_older_frames(self):
        state = self.state()
        state.running = True
        changed = state.subscribe()
        waiter = asyncio.create_task(state.next_frame(0, changed))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        await asyncio.to_thread(state.publish, b"first")
        self.assertEqual(await asyncio.wait_for(waiter, .5), (1, b"first"))
        state.publish(b"stale")
        state.publish(b"newest")
        self.assertEqual(await state.next_frame(1, changed), (3, b"newest"))
        state.unsubscribe(changed)
        self.assertEqual(state.listeners, {})

    async def test_arrival_before_subscribe_is_not_lost(self):
        state = self.state()
        state.running = True
        state.publish(b"first")
        changed = state.subscribe()
        self.assertEqual(await asyncio.wait_for(state.next_frame(0, changed), .1), (1, b"first"))
        state.unsubscribe(changed)

    async def test_source_exit_wakes_waiter(self):
        state = self.state()
        state.running = True
        changed = state.subscribe()
        waiter = asyncio.create_task(state.next_frame(0, changed))
        await asyncio.sleep(0)
        state.running = False
        state._notify_listeners()
        with self.assertRaises(EOFError):
            await asyncio.wait_for(waiter, .5)
        state.unsubscribe(changed)

    def test_endpoint_accepts_source_settings_and_50_60_fps(self):
        class State(video.DisplayStreamState):
            configurations = []
            def __init__(self, *args):
                super().__init__(*args)
                self.configurations.append((self.fps, self.max_width))
                self.publish(b"image")
            def start(self):
                self.running = True
                self.clients += 1

        app = FastAPI()
        app.include_router(video.router)
        app.dependency_overrides[video.get_db] = lambda: None
        with patch.object(video, 'get_video_session', return_value={'video_path': 'decklink://0'}), \
                patch.object(video, 'DisplayStreamState', State), patch.object(video, 'display_streams', {}):
            with TestClient(app) as client:
                for suffix in ('', '&fps=0&max_width=0', '&fps=50&max_width=3840', '&fps=60&max_width=0'):
                    with client.websocket_connect('/api/video/ws-display/test?ack=true' + suffix) as ws:
                        self.assertEqual(ws.receive_bytes(), b'image')
        self.assertEqual(State.configurations, [(0, 0), (0, 0), (50, 3840), (60, 0)])
