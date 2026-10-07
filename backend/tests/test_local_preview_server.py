import asyncio
import unittest
from unittest.mock import Mock

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from backend.routers.video import DisplayStreamState
from backend.services.local_preview_server import LocalPreviewServer


class LocalPreviewServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = DisplayStreamState('test', 'decklink://0', 0, 78, 0, 'bgr24')
        self.state.start = Mock(side_effect=lambda: setattr(self.state, 'running', True))
        self.state.release = Mock(wraps=self.state.release)
        self.server = LocalPreviewServer(lambda _: self.state)
        await asyncio.to_thread(self.server.start)

    async def asyncTearDown(self):
        await asyncio.to_thread(self.server.stop)
        self.assertFalse(self.server.thread.is_alive())

    async def test_local_transport_backpressure_drops_stale_frames(self):
        self.state.publish(b'first')
        async with connect(self.server.endpoint('test')) as ws:
            self.assertEqual(await ws.recv(), b'first')
            self.state.publish(b'stale')
            self.state.publish(b'latest')
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), .04)
            await ws.send('ready')
            self.assertEqual(await ws.recv(), b'latest')
        await asyncio.sleep(.05)
        self.state.release.assert_called_once()

    async def test_disconnect_without_signal_releases_lease(self):
        async with connect(self.server.endpoint('test')):
            await asyncio.sleep(.05)
        await asyncio.sleep(.05)
        self.state.release.assert_called_once()
        self.assertFalse(self.state.listeners)

    async def test_bad_token_does_not_open_capture(self):
        async with connect(self.server.endpoint('test').split('?')[0] + '?token=invalid') as ws:
            with self.assertRaises(ConnectionClosed):
                await ws.recv()
        self.state.start.assert_not_called()

    async def test_stop_closes_active_client_and_releases_capture(self):
        async with connect(self.server.endpoint('test')) as ws:
            await asyncio.sleep(.05)
            await asyncio.to_thread(self.server.stop)
            with self.assertRaises(ConnectionClosed):
                await ws.recv()
        self.state.release.assert_called_once()
