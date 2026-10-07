import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from backend.services.preview_pixels import HEADER, pack_bgr_frame, pack_i420_frame
from backend.routers import video


class PreviewPixelTests(unittest.IsolatedAsyncioTestCase):
    def test_packet_preserves_color_and_noncontiguous_crop(self):
        frame = np.arange(5 * 7 * 3, dtype=np.uint8).reshape(5, 7, 3)[:, 2:5]
        packet = pack_bgr_frame(frame, 7, 1234.5, 50)
        self.assertEqual(HEADER.size, 32)
        self.assertEqual(HEADER.unpack(packet[:32]), (b'SRP1', 7, 3, 5, 1234.5, 50, 45))
        np.testing.assert_array_equal(np.frombuffer(packet[32:], np.uint8).reshape(5, 3, 3), frame)

    def test_1080p_packet_does_not_resize_or_encode(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        packet = pack_bgr_frame(frame, 2 ** 32 + 9, 100, 59.94)
        self.assertEqual(len(packet), 32 + 1920 * 1080 * 3)
        self.assertEqual(HEADER.unpack(packet[:32])[1], 9)
        with self.assertRaises(ValueError):
            pack_bgr_frame(frame.astype(np.float32), 1, 100, 50)

    def test_i420_preserves_luma_resolution_and_shader_color_matrix(self):
        for bgr in [(0, 0, 0), (255, 255, 255), (0, 0, 255), (0, 255, 0), (255, 0, 0), (113, 64, 180)]:
            frame = np.full((2, 4, 3), bgr, dtype=np.uint8)
            packet = pack_i420_frame(frame, 1, 100, 50)
            self.assertEqual(HEADER.unpack(packet[:32])[:4], (b'SRP2', 1, 4, 2))
            self.assertEqual(len(packet), 32 + 12)
            y = (packet[32] - 16) / 219
            u = (packet[40] - 128) / 255
            v = (packet[42] - 128) / 255
            rgb = np.clip(np.array([y + 1.596027 * v, y - .391762 * u - .812968 * v, y + 2.017232 * u]) * 255, 0, 255)
            np.testing.assert_allclose(rgb, bgr[::-1], atol=2)

    def test_odd_crops_fall_back_without_cutting_pixels(self):
        frame = np.zeros((3, 5, 3), dtype=np.uint8)
        self.assertEqual(pack_i420_frame(frame, 1, 2, 50), pack_bgr_frame(frame, 1, 2, 50))

    async def test_raw_requires_loopback_and_ack_and_capture(self):
        for host, ack, path in [('10.0.0.1', True, 'decklink://0'),
                                ('127.0.0.1', False, 'decklink://0'),
                                ('127.0.0.1', True, 'simulator://test')]:
            ws = Mock()
            ws.client.host = host
            ws.close = AsyncMock()
            ws.accept = AsyncMock()
            with patch.object(video, 'get_video_session', return_value={'video_path': path}):
                await video.websocket_display_stream(ws, 'test', fps=0, quality=78,
                                                     max_width=0, ack=ack, pixel_format='bgr24', db=None)
            ws.close.assert_awaited_once()
            ws.accept.assert_not_awaited()

    async def test_raw_disconnect_releases_capture(self):
        ws = Mock()
        ws.client.host = '127.0.0.1'
        ws.accept = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=video.WebSocketDisconnect())
        state = video.DisplayStreamState('test', 'decklink://0', 0, 78, 0)
        state.start = Mock(side_effect=lambda: setattr(state, 'running', True))
        state.release = Mock(wraps=state.release)
        with patch.object(video, 'get_video_session', return_value={'video_path': 'decklink://0'}), \
                patch.object(video, 'display_streams', {}), \
                patch.object(video, 'DisplayStreamState', return_value=state):
            await video.websocket_display_stream(ws, 'test', fps=0, quality=78,
                                                 max_width=0, ack=True, pixel_format='bgr24', db=None)
        self.assertEqual(state.pixel_format, 'bgr24')
        state.release.assert_called_once()
        self.assertFalse(state.running)
