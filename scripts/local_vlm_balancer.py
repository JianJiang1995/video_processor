#!/usr/bin/env python3
"""Small local proxy that balances image VLM calls across vLLM instances."""

import argparse
import asyncio
import logging
from typing import List

from aiohttp import ClientSession, ClientTimeout, web


LOGGER = logging.getLogger("local_vlm_balancer")


class VlmBalancer:
    def __init__(self, upstreams: List[str], timeout: float) -> None:
        self.upstreams = [url.rstrip("/") for url in upstreams]
        self.timeout = timeout
        self.active = [0 for _ in upstreams]
        self.cursor = 0
        self.lock = asyncio.Lock()
        self.session: ClientSession | None = None

    async def start(self) -> None:
        self.session = ClientSession(timeout=ClientTimeout(total=self.timeout))

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def _select_image_upstream(self) -> int:
        async with self.lock:
            minimum = min(self.active)
            candidates = [index for index, count in enumerate(self.active) if count == minimum]
            index = candidates[self.cursor % len(candidates)]
            self.cursor += 1
            self.active[index] += 1
            return index

    async def _forward(self, request: web.Request, index: int, body: bytes) -> web.Response:
        assert self.session is not None
        upstream = self.upstreams[index]
        path = request.path
        if path.startswith("/v1") and upstream.endswith("/v1"):
            path = path[3:] or "/"
        url = f"{upstream}{path}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in {"authorization", "content-type", "accept"}
        }
        async with self.session.request(
            request.method,
            url,
            params=request.query,
            data=body if body else None,
            headers=headers,
        ) as response:
            payload = await response.read()
            return web.Response(
                status=response.status,
                body=payload,
                content_type=response.content_type,
            )

    async def handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        is_image_request = request.method == "POST" and b'"image_url"' in body
        if not is_image_request:
            return await self._forward(request, 0, body)

        index = await self._select_image_upstream()
        try:
            try:
                return await self._forward(request, index, body)
            except Exception as exc:
                alternate = 1 - index if len(self.upstreams) == 2 else 0
                if alternate == index:
                    raise
                LOGGER.warning("upstream %s failed (%s); retrying %s", index, exc, alternate)
                return await self._forward(request, alternate, body)
        finally:
            async with self.lock:
                self.active[index] = max(0, self.active[index] - 1)


def build_app(upstreams: List[str], timeout: float) -> web.Application:
    balancer = VlmBalancer(upstreams, timeout)
    app = web.Application(client_max_size=128 * 1024 * 1024)
    app.router.add_route("*", "/{tail:.*}", balancer.handle)
    app.on_startup.append(lambda _: balancer.start())
    app.on_cleanup.append(lambda _: balancer.close())
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--upstream", action="append", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(build_app(args.upstream, args.timeout), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
