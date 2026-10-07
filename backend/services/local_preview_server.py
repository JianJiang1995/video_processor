"""Loopback preview transport isolated from inference's HTTP event loop.

The capture registry remains shared in this process: opening a preview does not
open a second GStreamer pipeline. Each connection has one acknowledged packet.
"""
import asyncio
import logging
import secrets
import threading
from urllib.parse import parse_qs, urlparse, quote, unquote

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class LocalPreviewServer:
    def __init__(self, state_factory):
        self.state_factory = state_factory
        self.token = secrets.token_urlsafe(24)
        self.port = None
        self.loop = None
        self.shutdown_event = None
        self.ready = threading.Event()
        self.thread = None
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name='local-preview-server', daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            raise RuntimeError(f'Local preview startup failed: {self.error or "timeout"}')

    def endpoint(self, session_id):
        if not self.port:
            return None
        return f'ws://127.0.0.1:{self.port}/preview/{quote(session_id, safe="")}?token={self.token}'

    def stop(self):
        if self.loop and self.shutdown_event and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.shutdown_event.set)
            except RuntimeError:
                pass  # The serving thread may already have exited.
        if self.thread:
            self.thread.join(timeout=5)
        self.port = None

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as error:
            self.error = error
            logger.exception('Local preview server failed')
            self.ready.set()

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.shutdown_event = asyncio.Event()
        async with serve(self._client, '127.0.0.1', 0, compression=None,
                         max_size=64, max_queue=1, close_timeout=1) as server:
            self.port = server.sockets[0].getsockname()[1]
            self.ready.set()
            logger.info('Isolated local preview ready on 127.0.0.1:%s', self.port)
            await self.shutdown_event.wait()

    async def _client(self, socket):
        parsed = urlparse(socket.request.path)
        if parse_qs(parsed.query).get('token') != [self.token] or not parsed.path.startswith('/preview/'):
            await socket.close(1008, 'Invalid preview access')
            return
        session_id = unquote(parsed.path.removeprefix('/preview/'))
        state = await asyncio.to_thread(self.state_factory, session_id)
        if state is None:
            await socket.close(1008, 'Capture session unavailable')
            return
        state.start()
        changed = state.subscribe()
        closed = asyncio.create_task(socket.wait_closed())
        frame_task = None
        sequence = -1
        try:
            while True:
                frame_task = asyncio.create_task(state.next_frame(sequence, changed))
                done, _ = await asyncio.wait((frame_task, closed), return_when=asyncio.FIRST_COMPLETED)
                if closed in done:
                    break
                sequence, packet = frame_task.result()
                await socket.send(packet)
                if await asyncio.wait_for(socket.recv(), timeout=10) != 'ready':
                    await socket.close(1008, 'Frame acknowledgement required')
                    break
        except (ConnectionClosed, EOFError, asyncio.TimeoutError):
            pass
        finally:
            for task in (closed, frame_task):
                if task:
                    task.cancel()
            await asyncio.gather(*(task for task in (closed, frame_task) if task), return_exceptions=True)
            state.unsubscribe(changed)
            state.release()


server = None


def capture_state(session_id):
    from ..database import SessionLocal, get_video_session
    from ..routers.video import DisplayStreamState
    with SessionLocal() as db:
        session = get_video_session(db, session_id)
    path = (session or {}).get('video_path', '')
    if not path.startswith('decklink://'):
        return None
    return DisplayStreamState(session_id, path, 0, 78, 0, pixel_format='i420')


def start_local_preview():
    global server
    if server is None:
        server = LocalPreviewServer(capture_state)
        try:
            server.start()
        except Exception:
            server = None
            logger.exception('Using shared HTTP preview fallback')


def stop_local_preview():
    global server
    if server:
        server.stop()
        server = None
