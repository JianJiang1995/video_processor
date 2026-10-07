import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import WebSocketDisconnect

from backend.routers import video


class CaptureDisplayDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def check_disconnect(self, *, ack, had_frame):
        incoming = asyncio.Queue()
        websocket = Mock()
        websocket.accept = AsyncMock()
        async def receive():
            message = await incoming.get()
            if isinstance(message, Exception):
                raise message
            return message
        async def send(_frame):
            if ack:
                incoming.put_nowait("ready")
            asyncio.get_running_loop().call_later(.01, incoming.put_nowait, WebSocketDisconnect())
        websocket.send_bytes = AsyncMock(side_effect=send)
        websocket.receive_text = AsyncMock(side_effect=receive)
        state = video.DisplayStreamState("test", "decklink://0", 0, 85, 0)
        state.start = Mock(side_effect=lambda: setattr(state, "running", True))
        state.release = Mock(wraps=state.release)
        if had_frame:
            state.publish(b"frame")
        else:
            incoming.put_nowait(WebSocketDisconnect())
        with patch.object(video, "get_video_session", return_value={"video_path": "decklink://0"}), \
                patch.object(video, "DisplayStreamState", return_value=state), \
                patch.object(video, "display_streams", {}):
            await asyncio.wait_for(video.websocket_display_stream(
                websocket, "test", fps=25, quality=78, max_width=1280, ack=ack, db=None,
            ), timeout=1.0)
        state.start.assert_called_once()
        state.release.assert_called_once()
        self.assertEqual(state.listeners, {})
        self.assertEqual(websocket.send_bytes.await_count, int(had_frame))

    async def test_disconnect_before_first_frame_with_ack(self):
        await self.check_disconnect(ack=True, had_frame=False)

    async def test_disconnect_before_first_frame_without_ack(self):
        await self.check_disconnect(ack=False, had_frame=False)

    async def test_disconnect_when_source_stalls_after_acknowledged_frame(self):
        await self.check_disconnect(ack=True, had_frame=True)

    async def test_disconnect_when_source_stalls_without_ack(self):
        await self.check_disconnect(ack=False, had_frame=True)

    def test_last_preview_releases_physical_input_without_idle_grace(self):
        state = video.DisplayStreamState("test", "decklink://0?connection=sdi", 25, 78, 1280)
        state.running = True
        state.clients = 2
        state.release()
        self.assertTrue(state.running)
        state.release()
        self.assertFalse(state.running)

    def test_non_capture_preview_retains_existing_reconnect_grace(self):
        state = video.DisplayStreamState("test", "simulator://file/video.mp4", 25, 78, 1280)
        state.running = True
        state.clients = 1
        state.release()
        self.assertTrue(state.running)
